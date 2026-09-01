"""Tests for scripts/paint.py — plain unittest, no third-party deps."""

import json
import os
import struct
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paint  # noqa: E402


def blank_canvas():
    return {"width": 32, "height": 32, "version": 1, "pixels": {}, "placements": []}


NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

WELL_FORMED_FORM = """### X (column)

5

### Y (row)

9

### Colour

blue
"""


class ParseTests(unittest.TestCase):
    def test_basic_form(self):
        parsed = paint.parse_issue_body(WELL_FORMED_FORM)
        self.assertEqual(paint.extract_fields(parsed), ("5", "9", "blue"))

    def test_unexpected_extra_sections_and_reordered_fields(self):
        body = (
            "### Colour\n\nlime\n\n"
            "### Notes\n\nsome `backticked` text and $(cmd) that must be ignored\n\n"
            "### Y (row)\n\n0\n\n"
            "### X (column)\n\n31\n"
        )
        parsed = paint.parse_issue_body(body)
        self.assertEqual(paint.extract_fields(parsed), ("31", "0", "lime"))
        self.assertIn("Notes", parsed)  # kept, but not used

    def test_no_response_becomes_empty(self):
        body = "### X (column)\n\n_No response_\n\n### Y (row)\n\n2\n\n### Colour\n\nred\n"
        parsed = paint.parse_issue_body(body)
        x, y, colour = paint.extract_fields(parsed)
        self.assertEqual(x, "")
        self.assertEqual((y, colour), ("2", "red"))

    def test_key_value_body_from_picker(self):
        parsed = paint.parse_issue_body("X: 73\nY: 42\nColour: teal\n")
        self.assertEqual(paint.extract_fields(parsed), ("73", "42", "teal"))

    def test_key_value_tolerates_bold_and_equals(self):
        parsed = paint.parse_issue_body("**X** = 5\n**Y** = 9\n**Colour** = blue\n")
        self.assertEqual(paint.extract_fields(parsed), ("5", "9", "blue"))


class ValidateTests(unittest.TestCase):
    def test_x_out_of_range(self):
        with self.assertRaises(paint.PaintError):
            paint.validate(blank_canvas(), "32", "3", "red", "alice", NOW)

    def test_y_negative(self):
        with self.assertRaises(paint.PaintError):
            paint.validate(blank_canvas(), "3", "-1", "red", "alice", NOW)

    def test_non_numeric_coordinate(self):
        with self.assertRaises(paint.PaintError):
            paint.validate(blank_canvas(), "left", "3", "red", "alice", NOW)

    def test_unknown_colour(self):
        with self.assertRaises(paint.PaintError):
            paint.validate(blank_canvas(), "1", "1", "chartreuse", "alice", NOW)

    def test_in_range_ok(self):
        self.assertEqual(
            paint.validate(blank_canvas(), "0", "31", "Magenta", "alice", NOW),
            (0, 31, "magenta"),
        )

    def test_cooldown_enforced(self):
        cd = paint.COOLDOWN_MINUTES
        canvas = blank_canvas()
        paint.place(canvas, 1, 1, "red", "alice", NOW)
        with self.assertRaises(paint.PaintError):
            paint.validate(canvas, 2, 2, "blue", "alice", NOW + timedelta(seconds=1))
        with self.assertRaises(paint.PaintError):  # case-insensitive, still inside the window
            paint.validate(canvas, 2, 2, "blue", "ALICE", NOW + timedelta(minutes=cd) - timedelta(seconds=1))

    def test_cooldown_clears_after_window(self):
        cd = paint.COOLDOWN_MINUTES
        canvas = blank_canvas()
        paint.place(canvas, 1, 1, "red", "alice", NOW)
        paint.validate(canvas, 2, 2, "blue", "alice", NOW + timedelta(minutes=cd + 1))

    def test_owner_is_exempt(self):
        canvas = blank_canvas()
        paint.place(canvas, 1, 1, "red", paint.OWNER, NOW)
        # No cooldown wait for the owner — seeding must work.
        paint.validate(canvas, 2, 2, "blue", paint.OWNER, NOW + timedelta(minutes=1))
        paint.place(canvas, 2, 2, "blue", paint.OWNER, NOW + timedelta(minutes=1))
        paint.validate(canvas, 3, 3, "lime", paint.OWNER, NOW + timedelta(minutes=2))


class PlaceTests(unittest.TestCase):
    def test_overpaint_returns_previous_colour(self):
        canvas = blank_canvas()
        self.assertIsNone(paint.place(canvas, 3, 4, "red", "alice", NOW))
        previous = paint.place(canvas, 3, 4, "blue", "bob", NOW + timedelta(hours=2))
        self.assertEqual(previous, "red")
        self.assertEqual(canvas["pixels"]["3,4"], "blue")   # pixels store just the colour name
        self.assertEqual(len(canvas["placements"]), 2)       # placements keep the full record

    def test_load_canvas_upgrades_old_fat_pixel_format(self):
        workdir = tempfile.mkdtemp()
        legacy = {"width": 4, "height": 4, "version": 7,
                  "pixels": {"1,1": {"color": "red", "user": "x", "at": "t"}},
                  "placements": []}
        with open(os.path.join(workdir, "canvas.json"), "w", encoding="utf-8") as fh:
            json.dump(legacy, fh)
        canvas = paint.load_canvas(workdir)
        self.assertEqual(canvas["pixels"]["1,1"], "red")

    def test_version_increments_on_each_write(self):
        canvas = blank_canvas()
        start = canvas["version"]
        paint.place(canvas, 0, 0, "red", "alice", NOW)
        self.assertEqual(canvas["version"], start + 1)
        paint.place(canvas, 1, 0, "blue", paint.OWNER, NOW)
        self.assertEqual(canvas["version"], start + 2)
        paint.place(canvas, 1, 0, "lime", paint.OWNER, NOW)  # overpaint still counts
        self.assertEqual(canvas["version"], start + 3)


class PaletteTests(unittest.TestCase):
    def test_palette_entries_are_valid_hex(self):
        for name, value in paint.PALETTE.items():
            self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", name)
            self.assertEqual(name, name.lower())

    def test_seed_letters_map_to_palette_and_are_unique(self):
        names = list(paint._SEED_COLORS.values())
        self.assertEqual(len(names), len(set(names)), "duplicate colour in _SEED_COLORS")
        for letter, name in paint._SEED_COLORS.items():
            self.assertEqual(len(letter), 1)
            self.assertIn(name, paint.PALETTE)

    def test_fallback_seed_art_uses_known_letters(self):
        for row in paint._SEED_ART:
            for ch in row:
                self.assertTrue(ch == "." or ch in paint._SEED_COLORS, ch)


class PngTests(unittest.TestCase):
    def _ihdr(self, png):
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png[12:16], b"IHDR")
        w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
        return w, h, depth, ctype

    def test_render_png_is_valid_and_scaled(self):
        canvas = blank_canvas()
        paint.place(canvas, 5, 5, "red", "alice", NOW)
        png = paint.render_png(canvas)
        scale = paint._cell_scale(canvas)
        w, h, depth, ctype = self._ihdr(png)
        self.assertEqual((w, h), (canvas["width"] * scale, canvas["height"] * scale))
        self.assertEqual((depth, ctype), (8, 2))          # 8-bit truecolour RGB
        self.assertLess(len(png), 200_000)                # tiny compared with the old SVG

    def test_render_png_handles_a_large_canvas(self):
        canvas = {"width": 150, "height": 150, "version": 1, "pixels": {}, "placements": []}
        png = paint.render_png(canvas)
        self._ihdr(png)
        self.assertLess(len(png), 100_000)


class ReadmeAndWriteTests(unittest.TestCase):
    def test_full_write_cycle_replaces_block_and_busts_cache(self):
        workdir = tempfile.mkdtemp()
        with open(os.path.join(workdir, "canvas.json"), "w", encoding="utf-8") as fh:
            json.dump(blank_canvas(), fh)
        with open(os.path.join(workdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Title\n\n<!-- CANVAS:START -->\nstale\n<!-- CANVAS:END -->\n\nfooter\n")

        canvas = paint.load_canvas(workdir)
        paint.place(canvas, 2, 2, "red", "alice", NOW)
        version_after = canvas["version"]
        paint.write_all(canvas, workdir)

        png_path = os.path.join(workdir, "canvas.png")
        self.assertTrue(os.path.exists(png_path))
        with open(png_path, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        with open(os.path.join(workdir, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn(f"canvas.png?v={version_after}", readme)
        self.assertIn("@alice", readme)
        self.assertIn("1 pixel painted", readme)
        self.assertIn("footer", readme)
        self.assertNotIn("stale", readme)

    def test_missing_markers_is_a_paint_error(self):
        with self.assertRaises(paint.PaintError):
            paint.update_readme_text("no markers here", "block")

    def test_readme_block_mentions_the_cooldown(self):
        canvas = blank_canvas()
        paint.place(canvas, 1, 1, "red", "alice", NOW)
        block = paint.build_readme_block(canvas)
        self.assertIn(paint._cooldown_phrase(), block)

    def test_cooldown_phrase_formats(self):
        cases = {15: "once every 15 minutes", 60: "once an hour",
                 120: "once every 2 hours", 1440: "once a day", 0: "no cooldown"}
        for minutes, expected in cases.items():
            with unittest.mock.patch.object(paint, "COOLDOWN_MINUTES", minutes):
                self.assertEqual(paint._cooldown_phrase(), expected)


if __name__ == "__main__":
    unittest.main()
