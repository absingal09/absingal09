# Pixel canvas — how it works & how to change it

The canvas is **150×150** and ships with a pixel-art Ferrari F40 (a photo dithered to
the palette by `scripts/seed_from_image.py`). Change `width` / `height` in `canvas.json`
to resize it. `scripts/seed_art_generator.py` holds an alternative procedural seed.

**Speed / size:** the cooldown is 5 minutes, and every paint commits `canvas.json`
(~350 KB — mostly the static seed grid, so git deltas are tiny) plus `canvas.png`
(~10 KB). The Action costs ~15–30 s of GitHub overhead per run; on top of that the
profile image takes 1–3 min to refresh because it passes through raw.githubusercontent
and the Camo image proxy, each with its own cache. `concurrency` serialises runs, so
near-simultaneous paints queue. To make paints show faster, pin the README image to the
commit SHA (`raw.githubusercontent.com/<owner>/<repo>/<sha>/canvas.png`) instead of
`?v=` — a never-before-seen URL skips the CDN caches — at the cost of the workflow
amending each commit to embed its own SHA and force-pushing.

## How it works

1. The profile README shows `canvas.png` — a small truecolour PNG (~10 KB) regenerated
   from `canvas.json` by a stdlib-only encoder in `paint.py` (`render_png`). It has no
   rulers; coordinates come from the picker.
2. Visitors choose a pixel one of two ways:
   - the **[pixel picker](../pick.html)** (`pick.html`) — a static page that draws the
     current canvas from `canvas.json`, lets you click the exact pixel and a colour, then
     opens a **pre-filled issue** (`X: / Y: / Colour:` in the body, `pixel` label applied);
   - the plain **issue form** ([`.github/ISSUE_TEMPLATE/pixel.yml`](../.github/ISSUE_TEMPLATE/pixel.yml))
     with **X**/**Y** number fields and a **colour** dropdown, for when you already know
     the coordinates.
3. On issue open, a GitHub Action ([`.github/workflows/paint.yml`](../.github/workflows/paint.yml))
   parses the body (`parse_issue_body` handles both the form's `### Heading` shape and the
   picker's `Key: value` lines), validates it, updates `canvas.json`, re-renders
   `canvas.png`, rewrites the block between the `CANVAS:START` / `CANVAS:END` markers in
   `README.md`, commits as `github-actions[bot]`, comments on the issue, and closes it.

`canvas.json` stores `pixels` as `{"x,y": "colour"}` — the per-paint who/when lives only
in `placements`, which grows solely from real visitor paints (the seed doesn't log). An
old `{"x,y": {"color": …}}` file is auto-upgraded by `load_canvas` on the next write.

### Enabling the picker

`pick.html` needs to be served next to `canvas.json`. Turn on **GitHub Pages** for the
repo (Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/`). The
picker then lives at `https://<username>.github.io/<username>/pick.html`, which is where
the README's "Paint a pixel" link points. Until Pages is on, that link 404s — visitors
can still use the plain form. `pick.html` has `OWNER` / `REPO` and a copy of `PALETTE`
near the top; keep the palette in sync with `paint.py` if you change colours.

Notes:

- **One pixel per user per `COOLDOWN_MINUTES`.** The repo owner (`OWNER`) is exempt so the
  canvas can be seeded.
- **Overpainting is allowed** — the confirmation comment notes the previous colour.
- The `?v=` on the image URL is a cache-buster, bumped on every write, so GitHub's image
  proxy (camo) doesn't serve a stale canvas.
- The job is gated on `if: contains(github.event.issue.labels.*.name, 'pixel')`, so the
  `pixel` label must exist in the repo and unrelated issues are ignored.
- `concurrency: { group: canvas }` serialises runs so two near-simultaneous paints can't
  race on the push.

## Palette

32 named colours (`PALETTE` in `paint.py`): a general-purpose core plus warm earth
tones so painterly seeds map well.

- core: `white` `silver` `gray` `slate` `black` `red` `orange` `yellow` `lime` `green`
  `teal` `sky` `blue` `indigo` `purple` `magenta`
- earth: `cream` `tan` `skin` `rose` `ochre` `gold` `sienna` `brown` `umber` `bister`
  `espresso` `olive` `moss` `fern` `pine` `mist`

Each maps to a single character in `scripts/seed_art.txt` via `_SEED_COLORS`
(uppercase = core, lowercase = earth).

## Customising

Everything is driven by constants at the top of [`scripts/paint.py`](../scripts/paint.py):

| Change | What to edit |
| --- | --- |
| **Canvas size** | Set `width` / `height` in `canvas.json`, reset `"pixels"` to `{}` and `"placements"` to `[]`, then re-seed. The PNG scales from those two numbers. X/Y are free-text fields in the issue form and picker, so no dropdown edits are needed — just update the `0-<n>` hints in `.github/ISSUE_TEMPLATE/pixel.yml` and `pick.html`. |
| **Palette** | Edit the `PALETTE` dict (`name -> #hex`, names lowercase, unique). Mirror the names in the **Colour** dropdown options in `.github/ISSUE_TEMPLATE/pixel.yml` and the `PALETTE` object in `pick.html`, and give any new colour a letter in `_SEED_COLORS`. |
| **Cooldown** | `COOLDOWN_MINUTES` (minutes). The README wording updates itself from this value. |
| **Seeder / exempt account** | `OWNER`. |
| **Seed art** | `scripts/seed_art.txt` — one character per cell (`_SEED_COLORS` in `paint.py`; `.` = unpainted). The committed file is written by `scripts/seed_from_image.py` (or `scripts/seed_art_generator.py`), or hand-edit. If the file is missing, `--seed` falls back to the small `_SEED_ART` heart. |
| **Image size / unpainted colour** | `TARGET_PX` (rough on-screen px; the cell scale derives from it) and `EMPTY_RGB` in `paint.py`. |

### Re-seeding

The shipped artwork is procedural — edit and re-run the generator:

```bash
python scripts/seed_art_generator.py --preview /tmp/seed.png   # tweak scene, eyeball PNG
python scripts/paint.py --seed
```

Or seed from an image instead:

```bash
pip install pillow
python scripts/seed_from_image.py path/to/picture.jpg --size 64
python scripts/paint.py --seed
```

`seed_from_image.py` resizes the image to the canvas size and, by default,
Floyd-Steinberg dithers it against the palette (`--no-dither` for a flat map). Useful
flags: `--contrast`, `--brightness`, `--saturation`, and `--exclude name,name` to keep
jarring colours out. Both scripts only write `scripts/seed_art.txt`; nothing is painted
until you run `paint.py --seed`.

After changing size, palette or seed, re-seed and run the tests:

```bash
python scripts/paint.py --seed
python -m unittest discover -s scripts -p "test_*.py"
```

## Running locally

```bash
# Simulate a paint from an issue-form body (### Label / blank line / value)
python scripts/paint.py --issue-body-file body.txt --user octocat --issue-number 1

# Re-paint the seed art (owner only, no cooldown)
python scripts/paint.py --seed

# Re-render canvas.png + the README block from canvas.json without painting
python scripts/paint.py --render
```

Undo local test writes with `git restore canvas.json canvas.png README.md`.
