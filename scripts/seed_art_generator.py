#!/usr/bin/env python3
"""Compose scripts/seed_art.txt procedurally — the starting artwork for the canvas.

    python scripts/seed_art_generator.py                       # writes scripts/seed_art.txt
    python scripts/seed_art_generator.py --preview seed.png    # + a PNG to eyeball

Scene: a Ferrari F40, head-on — flat Rosso Corsa with bold black outlines,
closed pop-up headlights, corner light clusters and the three lower intakes.
Built as a symmetric left half and mirrored. Palette-only, no image input.
Standard library + (optionally) Pillow for --preview. Each output character is a
palette letter from paint._SEED_COLORS.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paint import PALETTE, _SEED_COLORS  # noqa: E402

W = H = 64
LETTER = {name: ch for ch, name in _SEED_COLORS.items()}
MID = 63                       # mirror axis: x -> MID - x  (seam between 31 and 32)
BG = "silver"
BODY = "red"
INK = "black"


class Grid:
    def __init__(self, w, h, fill):
        self.w, self.h = w, h
        self.cells = [[fill] * w for _ in range(h)]

    def set(self, x, y, name):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.cells[y][x] = name

    def sym(self, x, y, name):
        self.set(x, y, name)
        self.set(MID - x, y, name)

    def span(self, x0, x1, y, name):
        for x in range(x0, x1 + 1):
            self.sym(x, y, name)

    def box(self, x0, y0, x1, y1, fill, outline=None):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                edge = x in (x0, x1) or y in (y0, y1)
                self.sym(x, y, outline if (edge and outline) else fill)

    def text_rows(self):
        return ["".join(LETTER[n] for n in row) for row in self.cells]


# half-width of the bodywork from the centre seam, per row
SIL = {
    9: 13, 10: 17, 11: 20, 12: 23, 13: 25, 14: 26, 15: 27, 16: 27,
    17: 27, 18: 28, 19: 28, 20: 28, 21: 28, 22: 28, 23: 27, 24: 27,
    25: 26, 26: 25, 27: 25, 28: 24, 29: 24, 30: 23, 31: 23, 32: 23,
    33: 22, 34: 22, 35: 22, 36: 22, 37: 22, 38: 21, 39: 21, 40: 21,
    41: 21, 42: 20, 43: 20, 44: 18, 45: 16,
}
# half-width of the black glasshouse: a raked windscreen, straight sides
GLASS = {8: 9, 9: 9, 10: 10, 11: 11, 12: 12, 13: 13, 14: 14,
         15: 15, 16: 16, 17: 17, 18: 17, 19: 15, 20: 12}


def build():
    g = Grid(W, H, BG)

    # soft ground shadow, tucked under the car
    for y in range(45, 49):
        for x in range(W):
            if ((x - 31.5) / 21) ** 2 + ((y - 45) / 3) ** 2 < 1:
                g.set(x, y, "gray")

    # --- body: flat red with a black silhouette outline -----------------
    for y, hw in SIL.items():
        for x in range(31 - hw, 32):
            g.sym(x, y, BODY)
        g.sym(31 - hw - 1, y, INK)

    # --- glasshouse: black, thick red A-pillars, high roofline --------
    for y, hw in GLASS.items():
        for x in range(31 - hw, 32):
            g.sym(x, y, "slate" if y == 8 else INK)
        g.sym(31 - hw - 1, y, INK)          # windscreen frame
    g.span(31 - 12, 31, 21, INK)            # cowl below the windscreen

    # --- wing mirrors on short stalks off the A-pillars ------------
    g.span(31 - 28, 31 - 23, 13, INK)       # stalk, rooted on the fender
    g.box(31 - 31, 11, 31 - 28, 14, INK)    # mirror head

    # --- closed pop-up headlight covers ----------------------------
    g.box(31 - 25, 24, 31 - 14, 28, BODY, outline=INK)
    g.span(31 - 23, 31 - 16, 26, "slate")   # lens seam

    # --- twin bonnet vents ---------------------------------------
    g.box(31 - 9, 21, 31 - 6, 22, INK)

    # --- Ferrari shield on the nose ---------------------------
    for y in range(24, 28):
        g.span(30, 31, y, "yellow")
    g.set(31, 25, INK); g.set(30, 26, INK)

    # --- corner light clusters (indicator + fog) -------------
    g.box(31 - 25, 36, 31 - 14, 40, "silver", outline=INK)
    g.box(31 - 24, 37, 31 - 20, 39, "orange")
    g.box(31 - 18, 37, 31 - 15, 39, "gray")

    # --- lower fascia: red valance, three clean black intakes -----
    g.box(21, 41, 31, 44, INK)              # central intake (spans the seam)
    g.box(31 - 27, 41, 31 - 20, 44, INK)    # side intake

    # --- splitter ------------------------------------------
    g.span(31 - 26, 31, 45, INK)
    g.span(31 - 22, 31, 46, INK)

    return g


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "seed_art.txt"))
    ap.add_argument("--preview", help="also write a PNG here (needs Pillow)")
    ap.add_argument("--scale", type=int, default=9)
    args = ap.parse_args(argv)

    g = build()
    rows = g.text_rows()
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")
    print(f"Wrote {args.out}  ({W}x{H})")

    if args.preview:
        from PIL import Image, ImageDraw
        s = args.scale
        im = Image.new("RGB", (W * s, H * s))
        d = ImageDraw.Draw(im)
        for y, row in enumerate(g.cells):
            for x, name in enumerate(row):
                c = PALETTE[name].lstrip("#")
                d.rectangle([x * s, y * s, x * s + s - 1, y * s + s - 1],
                            fill=tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)))
        im.save(args.preview)
        print(f"Wrote {args.preview}")


if __name__ == "__main__":
    main()
