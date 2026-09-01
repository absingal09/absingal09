#!/usr/bin/env python3
"""Collaborative pixel canvas — all logic lives here. Standard library only.

Usage:
    python scripts/paint.py --issue-body-file body.txt --user octocat --issue-number 42
    python scripts/paint.py --seed          # paint the seed art as OWNER

On a successful paint: writes canvas.json, canvas.png and the README block,
prints a one-line confirmation to stdout, exits 0.
On any validation failure: prints a friendly one-line reason to stdout, exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zlib
from collections import Counter
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Configuration — see the "Customising" table in README.md
# --------------------------------------------------------------------------- #

OWNER = "absingal09"          # cooldown-exempt account, used to seed the canvas
COOLDOWN_MINUTES = 5        # one pixel per user per this many minutes

# Named colours, keyed by name so the issue form stays readable. The first block
# is the general-purpose set; the second adds warm earth tones so painterly seeds
# (skin, hair, sfumato landscape) map well. All chosen to read on both GitHub
# themes. Keep names lowercase and unique; mirror any change in pixel.yml.
PALETTE = {
    # core
    "white":   "#ffffff",
    "silver":  "#c9d1d9",
    "gray":    "#6e7681",
    "slate":   "#48535d",
    "black":   "#1b1f24",
    "red":     "#f85149",
    "orange":  "#ff8c42",
    "yellow":  "#f2cc60",
    "lime":    "#7ee787",
    "green":   "#2ea043",
    "teal":    "#39c5cf",
    "sky":     "#58a6ff",
    "blue":    "#388bfd",
    "indigo":  "#6e5ef8",
    "purple":  "#bc8cff",
    "magenta": "#f778ba",
    # earth tones
    "cream":   "#efe3c6",
    "tan":     "#d8b98c",
    "skin":    "#e7b58a",
    "rose":    "#cd8f6d",
    "ochre":   "#c69749",
    "gold":    "#9a7b33",
    "sienna":  "#a15c3a",
    "brown":   "#a4694f",
    "umber":   "#6b4a2f",
    "bister":  "#45301c",
    "espresso":"#2a1e13",
    "olive":   "#6d6a3a",
    "moss":    "#46512f",
    "fern":    "#33442d",
    "pine":    "#24352a",
    "mist":    "#b7c2c4",
}

# PNG rendering. The canvas is drawn as a small indexed-ish truecolour PNG — a few
# tens of KB, versus ~1.6 MB for a per-cell SVG at 150x150 — so every paint commits
# almost nothing. Coordinates come from the picker page, so the image has no rulers.
EMPTY_RGB = (0x16, 0x1b, 0x22)   # unpainted cell
TARGET_PX = 540                  # rough on-screen size; cell scale derives from it


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


_PALETTE_RGB = {name: _rgb(hexv) for name, hexv in PALETTE.items()}

CANVAS_START = "<!-- CANVAS:START -->"
CANVAS_END = "<!-- CANVAS:END -->"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Seed art. Each character is a palette colour (see _SEED_COLORS); "." leaves a
# cell unpainted. `--seed` reads scripts/seed_art.txt when it exists (generate it
# with scripts/seed_from_image.py), otherwise it falls back to _SEED_ART below.
# Uppercase = the core palette, lowercase = the earth tones.
_SEED_COLORS = {
    "W": "white", "S": "silver", "G": "gray", "A": "slate", "K": "black",
    "R": "red", "O": "orange", "Y": "yellow", "L": "lime", "N": "green",
    "T": "teal", "C": "sky", "B": "blue", "I": "indigo", "P": "purple", "M": "magenta",
    "e": "cream", "t": "tan", "s": "skin", "r": "rose", "o": "ochre", "g": "gold",
    "i": "sienna", "n": "brown", "u": "umber", "b": "bister", "x": "espresso",
    "v": "olive", "m": "moss", "f": "fern", "p": "pine", "h": "mist",
}
_SEED_ART = [
    ".RR.RR.",
    "RRRRRRR",
    "RRRRRRR",
    ".RRRRR.",
    "..RRR..",
    "...R...",
]
_SEED_ART_FILE = os.path.join(_ROOT, "scripts", "seed_art.txt")


def load_seed_art() -> list[str]:
    if os.path.exists(_SEED_ART_FILE):
        with open(_SEED_ART_FILE, encoding="utf-8") as fh:
            return [line.rstrip("\n") for line in fh if line.strip()]
    return list(_SEED_ART)


def seed_pixels(canvas: dict) -> list[tuple[int, int, str]]:
    rows = load_seed_art()
    art_h = len(rows)
    art_w = max((len(r) for r in rows), default=0)
    if art_w != canvas["width"] or art_h != canvas["height"]:
        raise PaintError(
            f"Seed art is {art_w}x{art_h} but the canvas is "
            f"{canvas['width']}x{canvas['height']}. Regenerate scripts/seed_art.txt "
            f"(python scripts/seed_from_image.py <image> --size {canvas['width']})."
        )
    out: list[tuple[int, int, str]] = []
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in (".", " "):
                continue
            if ch not in _SEED_COLORS:
                raise PaintError(f"Seed art: unknown colour letter {ch!r} at ({x}, {y}).")
            out.append((x, y, _SEED_COLORS[ch]))
    return out


class PaintError(Exception):
    """A user-facing validation failure. The message is posted as an issue comment."""


# --------------------------------------------------------------------------- #
# Issue-form parsing
# --------------------------------------------------------------------------- #

_HEADING = re.compile(r"\s*#{2,4}\s+(.*\S)\s*$")
_KEYVAL = re.compile(r"^\s*\**\s*([A-Za-z][\w ()/]*?)\s*\**\s*[:=]\s*(\S.*?)\s*$")


def parse_issue_body(body: str) -> dict[str, str]:
    """Parse an issue body into a dict of label -> value. Handles both shapes:

    * the issue **form** GitHub renders as ``### <Label>`` then a blank line then
      the value;
    * plain ``Key: value`` lines, as produced by the pixel-picker's prefilled
      issue link.

    Field ordering is not relied on; unexpected extra sections are kept but
    ignored by :func:`extract_fields`.
    """
    result: dict[str, str] = {}
    label: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            if label is not None:
                result[label] = "\n".join(buf).strip()
            label = m.group(1).strip()
            buf = []
        elif label is not None:
            buf.append(line)
        else:
            kv = _KEYVAL.match(line)
            if kv:
                result.setdefault(kv.group(1).strip(), kv.group(2).strip())
    if label is not None:
        result[label] = "\n".join(buf).strip()

    for key, value in list(result.items()):
        if value.strip().lower() in ("_no response_", "no response"):
            result[key] = ""
    return result


def extract_fields(parsed: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    """Pull X, Y and Colour out of a parsed form, matching labels loosely
    ("X (column)", "Y (row)", "Colour"/"Color")."""
    x = y = colour = None
    for key, value in parsed.items():
        k = key.strip().lower()
        if k.startswith("x"):
            x = value
        elif k.startswith("y"):
            y = value
        elif k.startswith("colour") or k.startswith("color"):
            colour = value
    return x, y, colour


# --------------------------------------------------------------------------- #
# Validation + placement
# --------------------------------------------------------------------------- #

def _parse_iso(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_cooldown(canvas: dict, user: str, now: datetime) -> None:
    if user.lower() == OWNER.lower():
        return
    last = None
    for placement in canvas["placements"]:
        if placement["user"].lower() == user.lower():
            last = placement
    if last is None:
        return
    elapsed = (now - _parse_iso(last["at"])).total_seconds() / 60
    if elapsed < COOLDOWN_MINUTES:
        remaining = int(COOLDOWN_MINUTES - elapsed) + 1
        raise PaintError(
            f"@{user} already painted {int(elapsed)} min ago — one pixel per "
            f"{COOLDOWN_MINUTES} min. Try again in ~{remaining} min."
        )


def validate(canvas: dict, x_raw, y_raw, colour_raw, user: str, now: datetime):
    """Return ``(x, y, colour)`` or raise :class:`PaintError` with a one-line reason."""
    width, height = canvas["width"], canvas["height"]
    try:
        x = int(str(x_raw).strip())
    except (TypeError, ValueError):
        raise PaintError(f"X must be a whole number from 0 to {width - 1}.")
    try:
        y = int(str(y_raw).strip())
    except (TypeError, ValueError):
        raise PaintError(f"Y must be a whole number from 0 to {height - 1}.")
    if not 0 <= x < width:
        raise PaintError(f"X={x} is off the canvas (valid range 0–{width - 1}).")
    if not 0 <= y < height:
        raise PaintError(f"Y={y} is off the canvas (valid range 0–{height - 1}).")
    colour = str(colour_raw).strip().lower()
    if colour not in PALETTE:
        raise PaintError(
            f"'{colour_raw}' isn't a palette colour. Pick one of: {', '.join(PALETTE)}."
        )
    check_cooldown(canvas, user, now)
    return x, y, colour


def place(canvas: dict, x: int, y: int, colour: str, user: str, now: datetime):
    """Record a placement. Returns the previous colour at ``(x, y)`` or ``None``.
    Bumps ``version`` (the cache-buster) on every call.

    ``pixels`` maps ``"x,y" -> colour name`` — nothing more; the per-paint who/when
    lives in ``placements``, so the grid stays small."""
    key = f"{x},{y}"
    previous = canvas["pixels"].get(key)
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canvas["pixels"][key] = colour
    canvas["placements"].append({"x": x, "y": y, "color": colour, "user": user, "at": stamp})
    canvas["version"] = int(canvas["version"]) + 1
    return previous


# --------------------------------------------------------------------------- #
# PNG rendering — stdlib only (zlib + struct), no third-party imaging
# --------------------------------------------------------------------------- #

def _png_bytes(width: int, height: int, rows: list[bytes]) -> bytes:
    """Encode 8-bit truecolour rows (each ``width * 3`` bytes) as a PNG."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # colour type 2 = RGB
    raw = b"".join(b"\x00" + r for r in rows)                     # filter byte 0 per scanline
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _cell_scale(canvas: dict) -> int:
    return max(1, TARGET_PX // max(canvas["width"], canvas["height"]))


def render_png(canvas: dict) -> bytes:
    width, height = canvas["width"], canvas["height"]
    pixels = canvas["pixels"]
    scale = _cell_scale(canvas)
    rows: list[bytes] = []
    for cy in range(height):
        line = bytearray()
        for cx in range(width):
            name = pixels.get(f"{cx},{cy}")
            line += bytes(_PALETTE_RGB.get(name, EMPTY_RGB)) * scale
        row = bytes(line)
        rows.extend([row] * scale)
    return _png_bytes(width * scale, height * scale, rows)


# --------------------------------------------------------------------------- #
# README block
# --------------------------------------------------------------------------- #

def _cooldown_phrase() -> str:
    minutes = COOLDOWN_MINUTES
    if minutes <= 0:
        return "no cooldown"
    if minutes == 1:
        return "once a minute"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "once a day" if days == 1 else f"once every {days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "once an hour" if hours == 1 else f"once every {hours} hours"
    return f"once every {minutes} minutes"


def build_readme_block(canvas: dict) -> str:
    version = canvas["version"]
    count = len(canvas["placements"])  # pixels visitors have painted since the seed
    picker = f"https://{OWNER}.github.io/{OWNER}/pick.html"
    form = f"https://github.com/{OWNER}/{OWNER}/issues/new?template=pixel.yml"

    lines = [
        f'<p><img src="canvas.png?v={version}" width="512" alt="Pixel canvas"></p>',
        "",
        f"### [🎨 Paint a pixel]({picker})",
        "",
        f"Click the pixel you want on the [picker]({picker}), pick a colour, and it "
        f"opens a pre-filled issue — or use the [plain form]({form}) if you already "
        f"know the X/Y. Each person may paint **{_cooldown_phrase()}**; overpainting "
        f"an existing pixel is fine.",
        "",
        f"**{count} pixel{'' if count == 1 else 's'} painted by visitors.**",
        "",
    ]

    recent = list(reversed(canvas["placements"]))[:5]
    if recent:
        lines += ["**Latest pixels**", "", "| Pixel | Colour | By |", "| --- | --- | --- |"]
        lines += [f"| ({p['x']}, {p['y']}) | {p['color']} | @{p['user']} |" for p in recent]
        lines.append("")

    counts = Counter(p["user"] for p in canvas["placements"])
    if counts:
        lines += ["**Leaderboard**", "", "| # | Painter | Pixels |", "| --- | --- | --- |"]
        lines += [
            f"| {rank} | @{user} | {n} |"
            for rank, (user, n) in enumerate(counts.most_common(10), 1)
        ]
        lines.append("")

    return "\n".join(lines).rstrip()


def update_readme_text(text: str, block: str) -> str:
    pattern = re.compile(re.escape(CANVAS_START) + r".*?" + re.escape(CANVAS_END), re.S)
    if not pattern.search(text):
        raise PaintError("README.md is missing the CANVAS:START / CANVAS:END markers.")
    replacement = f"{CANVAS_START}\n{block}\n{CANVAS_END}"
    return pattern.sub(lambda _m: replacement, text)


# --------------------------------------------------------------------------- #
# Disk IO
# --------------------------------------------------------------------------- #

def load_canvas(root: str) -> dict:
    with open(os.path.join(root, "canvas.json"), encoding="utf-8") as fh:
        canvas = json.load(fh)
    # Tolerate the old fat pixel format ({"x,y": {"color": ...}}); collapse to
    # {"x,y": "colour"} so one paint upgrades the file.
    px = canvas.get("pixels", {})
    if px and isinstance(next(iter(px.values())), dict):
        canvas["pixels"] = {k: v.get("color") for k, v in px.items()}
    return canvas


def write_all(canvas: dict, root: str) -> None:
    with open(os.path.join(root, "canvas.json"), "w", encoding="utf-8") as fh:
        # Machine-managed and rewritten on every paint — keep it compact.
        json.dump(canvas, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    with open(os.path.join(root, "canvas.png"), "wb") as fh:
        fh.write(render_png(canvas))
    readme_path = os.path.join(root, "README.md")
    with open(readme_path, encoding="utf-8") as fh:
        text = fh.read()
    text = update_readme_text(text, build_readme_block(canvas))
    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Paint one pixel on the collaborative canvas.")
    parser.add_argument("--issue-body-file")
    parser.add_argument("--user")
    parser.add_argument("--issue-number")
    parser.add_argument("--seed", action="store_true", help="paint the seed art as OWNER")
    parser.add_argument(
        "--render",
        action="store_true",
        help="re-render canvas.png and the README block from canvas.json without painting",
    )
    parser.add_argument("--root", default=_ROOT)
    args = parser.parse_args(argv)

    canvas = load_canvas(args.root)

    if args.render:
        write_all(canvas, args.root)
        print("Re-rendered canvas.png and the README block.")
        return 0

    if args.seed:
        try:
            pixels = seed_pixels(canvas)
        except PaintError as err:
            print(str(err))
            return 1
        # The seed is the baseline image, not player history: set the pixels
        # directly and leave `placements` empty so the counter and leaderboard
        # reflect what visitors actually paint.
        canvas["pixels"] = {f"{x},{y}": colour for x, y, colour in pixels}
        canvas["placements"] = []
        canvas["version"] = int(canvas["version"]) + 1
        write_all(canvas, args.root)
        print(f"Seeded {len(pixels)} pixels as @{OWNER}.")
        return 0

    if not args.issue_body_file or not args.user:
        print("Internal error: --issue-body-file and --user are required.")
        return 1

    try:
        with open(args.issue_body_file, encoding="utf-8") as fh:
            body = fh.read()
        x_raw, y_raw, colour_raw = extract_fields(parse_issue_body(body))
        if x_raw in (None, "") or y_raw in (None, "") or colour_raw in (None, ""):
            raise PaintError(
                "Couldn't read X, Y and Colour from the form — please use the "
                "\"Paint a pixel\" template."
            )
        now = datetime.now(timezone.utc)
        x, y, colour = validate(canvas, x_raw, y_raw, colour_raw, args.user, now)
        previous = place(canvas, x, y, colour, args.user, now)
        write_all(canvas, args.root)
    except PaintError as err:
        print(str(err))
        return 1

    count = len(canvas["placements"])
    tail = f"{count} pixel{'' if count == 1 else 's'} painted by visitors so far."
    if previous and previous != colour:
        print(f"@{args.user} painted ({x}, {y}) **{colour}** — was {previous} before. {tail}")
    elif previous:
        print(f"@{args.user} repainted ({x}, {y}) **{colour}**. {tail}")
    else:
        print(f"@{args.user} painted ({x}, {y}) **{colour}**. {tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
