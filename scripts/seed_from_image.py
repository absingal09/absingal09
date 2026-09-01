#!/usr/bin/env python3
"""Turn an image into scripts/seed_art.txt, using only the canvas palette.

    python scripts/seed_from_image.py picture.jpg --size 64
    python scripts/seed_from_image.py picture.jpg --contrast 1.15 --exclude magenta,lime

Requires Pillow (`pip install pillow`). Seed-time helper only — the GitHub Action
never runs it. After generating the file:

    python scripts/paint.py --seed

Each output character is a palette letter from paint._SEED_COLORS ("." is never
emitted here — every cell gets a colour). By default the image is Floyd-Steinberg
dithered against the palette so tonal gradients survive the tiny colour set; pass
--no-dither for a flat nearest-colour map instead.
"""

import argparse
import os
import sys
from collections import Counter

try:
    from PIL import Image, ImageEnhance, ImageOps
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paint import PALETTE, _SEED_COLORS  # noqa: E402

_LETTER = {name: letter for letter, name in _SEED_COLORS.items()}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _redmean(c1, c2) -> float:
    r1, g1, b1 = c1
    r2, g2, b2 = c2
    rmean = (r1 + r2) / 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return (2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db


def _palette_image(colours: list[tuple[int, int, int]]) -> "Image.Image":
    flat = [v for rgb in colours for v in rgb]
    flat += flat[:3] * (256 - len(colours))  # pad to 256 entries with the first colour
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("image")
    ap.add_argument("--size", type=int, default=64, help="canvas size in cells (default 64)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "seed_art.txt"))
    ap.add_argument("--contrast", type=float, default=1.08)
    ap.add_argument("--brightness", type=float, default=1.0)
    ap.add_argument("--saturation", type=float, default=1.12)
    ap.add_argument("--no-dither", dest="dither", action="store_false",
                    help="flat nearest-colour map instead of Floyd-Steinberg dithering")
    ap.add_argument("--exclude", default="",
                    help="comma-separated palette names to keep out of the result")
    args = ap.parse_args(argv)

    names = [n for n in PALETTE if n not in
             {s.strip() for s in args.exclude.split(",") if s.strip()}]
    if not names:
        return _fail("every colour was excluded")
    rgbs = [_hex_to_rgb(PALETTE[n]) for n in names]

    try:
        img = Image.open(args.image).convert("RGB")
    except (FileNotFoundError, OSError) as err:
        return _fail(f"can't open {args.image!r}: {err}")

    img = ImageOps.fit(img, (args.size, args.size), Image.LANCZOS)
    if args.brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(args.brightness)
    if args.contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(args.contrast)
    if args.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(args.saturation)

    if args.dither:
        quant = img.quantize(palette=_palette_image(rgbs), dither=Image.Dither.FLOYDSTEINBERG)
        idx = quant.load()
        grid = [[names[idx[x, y]] for x in range(args.size)] for y in range(args.size)]
    else:
        px = img.load()
        cache: dict[tuple, str] = {}
        grid = []
        for y in range(args.size):
            row = []
            for x in range(args.size):
                rgb = px[x, y]
                name = cache.get(rgb)
                if name is None:
                    name = cache[rgb] = min(names, key=lambda n: _redmean(rgb, _hex_to_rgb(PALETTE[n])))
                row.append(name)
            grid.append(row)

    rows = ["".join(_LETTER[name] for name in row) for row in grid]
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")

    used = Counter(name for row in grid for name in row)
    print(f"Wrote {args.out}  ({args.size}x{args.size}, {sum(used.values())} cells, "
          f"{'dithered' if args.dither else 'flat'})")
    print("Palette usage:", ", ".join(f"{name} {n}" for name, n in used.most_common()))
    return 0


def _fail(msg: str) -> int:
    print(f"seed_from_image: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
