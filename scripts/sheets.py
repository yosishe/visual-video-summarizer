#!/usr/bin/env python3
"""Contact sheets for the first triage read: ≤16 tiles per sheet, each with
its candidate id and time burned into a caption bar, in chronological order,
plus one sentinel tile the model must report as blank.

Why this shape (CollagePrompt, NAACL 2025; "VLMs are blind", ACCV 2024):
accuracy falls ~10 % per grid step beyond 2×2 and models mis-count grid
rows/columns, so the id is *written on the tile* and the answer is keyed by
that id, never by position; 4×4 is the largest grid worth using; a sentinel
catches the "right answer, wrong cell" failure. A 4×4 sheet of 320×180 tiles
with an 18 px bar is 1280×792 → 46×29 = 1,334 visual tokens (83 per tile)
against 209 for one 512×288 candidate read alone.

    python3 sheets.py --work <work> [--tiles 16] [--tile-width 320]

Reads `<work>/candidates.json`, writes `<work>/sheets/sheet_NN.jpg` and a
`sheets` block back into candidates.json. Needs PIL; without it the command
exits 0 with a note and the skill reads candidates individually.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import format_time  # noqa: E402
from hostenv import mono_font_candidates  # noqa: E402
from safety import atomic_write  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    Image = None
    ImageDraw = ImageFont = None  # type: ignore

COLS = 4
BAR_H = 18
SENTINEL_SHADE = 118
PATCH = 28
# The font the last sheet run actually used: the burned-in ids are the whole
# point of a contact sheet, so the report must say when only the bitmap
# fallback was available.
FONT_USED: dict[str, str] = {"name": "default"}


def image_tokens(width: int, height: int) -> int:
    return -(-width // PATCH) * -(-height // PATCH)


def _font(size: int):
    for name in mono_font_candidates():
        try:
            font = ImageFont.truetype(name, size)
        except OSError:
            continue
        FONT_USED["name"] = name
        return font
    FONT_USED["name"] = "default"
    print("[vsum] warning: no TrueType monospace font found; contact-sheet ids use PIL's bitmap font — "
          "if an id is not legible, read that candidate individually", file=sys.stderr)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def plan_sheets(candidates: list[dict], tiles_per_sheet: int, seed: int = 7) -> list[dict]:
    """Chronological groups of ≤ tiles_per_sheet−1 candidates plus a sentinel
    slot at a random position (never the first tile, which is the model's
    most accurate position and should carry a real candidate)."""
    rng = random.Random(seed)
    ordered = sorted(candidates, key=lambda c: float(c["actual_t"]))
    real_per_sheet = max(1, tiles_per_sheet - 1)
    sheets = []
    for number, start in enumerate(range(0, len(ordered), real_per_sheet)):
        group = ordered[start:start + real_per_sheet]
        sentinel_index = rng.randint(1, len(group)) if len(group) > 1 else 1
        tiles = []
        position = 0
        for candidate in group:
            if position == sentinel_index:
                tiles.append({"index": position, "sentinel": True})
                position += 1
            tiles.append({"index": position, "candidate_id": candidate["candidate_id"],
                          "actual_t": candidate["actual_t"], "path": candidate["path"]})
            position += 1
        if not any(t.get("sentinel") for t in tiles):
            tiles.append({"index": position, "sentinel": True})
        sheets.append({"sheet_id": f"sheet_{number:02d}", "tiles": tiles,
                       "sentinel_id": f"x_{number:02d}{rng.randint(10, 99)}"})
    return sheets


def render_sheet(sheet: dict, out_path: Path, tile_width: int) -> dict:
    tile_height = round(tile_width * 9 / 16)
    rows = -(-len(sheet["tiles"]) // COLS)
    canvas = Image.new("RGB", (COLS * tile_width, rows * (tile_height + BAR_H)), "black")
    draw = ImageDraw.Draw(canvas)
    font = _font(max(12, tile_width // 22))
    for tile in sheet["tiles"]:
        col, row = tile["index"] % COLS, tile["index"] // COLS
        x, y = col * tile_width, row * (tile_height + BAR_H)
        if tile.get("sentinel"):
            draw.rectangle((x, y, x + tile_width, y + tile_height), fill=(SENTINEL_SHADE,) * 3)
            label = f"{sheet['sentinel_id']}  --:--"
        else:
            image = Image.open(tile["path"]).convert("RGB")
            image.thumbnail((tile_width, tile_height))
            canvas.paste(image, (x + (tile_width - image.width) // 2, y + (tile_height - image.height) // 2))
            label = f"{tile['candidate_id']}  {format_time(float(tile['actual_t']))}"
        draw.rectangle((x, y + tile_height, x + tile_width, y + tile_height + BAR_H), fill=(24, 24, 24))
        draw.text((x + 6, y + tile_height + 2), label, fill=(245, 245, 245), font=font)
    canvas.save(out_path, quality=88)
    return {"width": canvas.width, "height": canvas.height, "tokens": image_tokens(canvas.width, canvas.height)}


def build_sheets(work: Path, tiles_per_sheet: int = 16, tile_width: int = 320) -> dict:
    payload_path = work / "candidates.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", [])
    if Image is None:
        payload["sheets"] = {"status": "unavailable", "reason": "PIL not importable", "sheets": []}
        atomic_write(payload_path, json.dumps(payload, indent=2) + "\n")
        return payload["sheets"]
    out_dir = work / "sheets"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("sheet_*.jpg"):
        old.unlink()
    plan = plan_sheets(candidates, tiles_per_sheet)
    total = 0
    for sheet in plan:
        path = out_dir / f"{sheet['sheet_id']}.jpg"
        info = render_sheet(sheet, path, tile_width)
        sheet.update({"path": str(path), **info})
        total += info["tokens"]
        for tile in sheet["tiles"]:
            tile.pop("path", None)
    block = {"status": "ok", "tiles_per_sheet": tiles_per_sheet, "tile_width": tile_width,
             "sheets": plan, "image_tokens": total, "font": FONT_USED["name"],
             "individual_tokens": sum(image_tokens(int(c.get("width", 512)), int(c.get("height", 288))) for c in candidates)}
    payload["sheets"] = block
    atomic_write(payload_path, json.dumps(payload, indent=2) + "\n")
    return block


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", required=True)
    parser.add_argument("--tiles", type=int, default=16, help="tiles per sheet incl. the sentinel (max 16)")
    parser.add_argument("--tile-width", type=int, default=320)
    args = parser.parse_args()
    if args.tiles > 16:
        raise SystemExit("more than 4×4 tiles per sheet costs accuracy; use --tiles 16 or fewer")
    block = build_sheets(Path(args.work).expanduser().resolve(), args.tiles, args.tile_width)
    if block["status"] != "ok":
        print(f"[vsum] sheets unavailable ({block['reason']}) — read the candidates individually", file=sys.stderr)
        return 0
    print(f"# contact sheets\n\n- **{len(block['sheets'])} sheets**, {block['tiles_per_sheet'] - 1} candidates + 1 sentinel each, "
          f"{block['tile_width']}px tiles ≈ {block['image_tokens']:,} image tokens for the whole pool "
          f"(reading every candidate individually would cost {block['individual_tokens']:,}).\n")
    print("Read ALL sheets in one message. For every tile report its burned-in id: keep / drop (and why); "
          "report the sentinel tile as `blank` — if you cannot find it, say so and read the candidates individually.\n")
    for sheet in block["sheets"]:
        ids = ", ".join(t["candidate_id"] for t in sheet["tiles"] if not t.get("sentinel"))
        print(f"- `{sheet['path']}` → {ids}; sentinel `{sheet['sentinel_id']}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
