#!/usr/bin/env python3
"""Annotation sheets from YouTube's own storyboard track — zero video download.

For a video id this fetches the `sb0` storyboard fragments (320×180 tiles, one
every ~2–10 s depending on duration), burns the tile timestamp into each tile,
re-tiles them into printable sheets, and writes an annotation *draft* with
coarse visual states found from tile statistics (luma / edge density /
consecutive difference). The draft is a starting point for the human
annotator — every `class` is "unreviewed" until edited.

Usage:
    python3 bench/storyboard.py <video_id_or_url> [--out bench/sheets] [--cols 4] [--rows 3]

Outputs (under --out/<id>/):
    sb0_NN.jpg              raw storyboard sheets as served by YouTube
    sheet_NN.jpg            annotation sheets (cols×rows tiles, timestamps burned in)
    tiles.json              per-tile stats and times
    ../../annotations/<id>.draft.json   coarse-state draft (never overwrites a non-draft file)

Dependencies: yt-dlp (metadata only), PIL. No ffmpeg, no download of the video.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id_of(source: str) -> str:
    if VIDEO_ID_RE.match(source):
        return source
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", source)
    if not match:
        raise SystemExit(f"cannot find a YouTube video id in {source!r}")
    return match.group(1)


def fetch_info(video_id: str) -> dict:
    cmd = ["yt-dlp", "--dump-single-json", "--skip-download", "--no-playlist",
           f"https://www.youtube.com/watch?v={video_id}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit(f"yt-dlp failed for {video_id}: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def storyboard_format(info: dict) -> dict | None:
    boards = [f for f in info.get("formats", []) if str(f.get("format_id", "")).startswith("sb")]
    if not boards:
        return None
    # sb0 is the highest resolution level; fall back to whatever is largest.
    boards.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), reverse=True)
    return boards[0]


def download_sheets(fmt: dict, out_dir: Path) -> list[Path]:
    paths = []
    for index, fragment in enumerate(fmt.get("fragments", [])):
        path = out_dir / f"sb0_{index:02d}.jpg"
        if not path.exists():
            urllib.request.urlretrieve(fragment["url"], path)
        paths.append(path)
    return paths


def split_tiles(sheet_paths: list[Path], fmt: dict, duration: float) -> list[dict]:
    rows, cols, fps = int(fmt["rows"]), int(fmt["columns"]), float(fmt["fps"])
    step = 1.0 / fps if fps else 0.0
    tiles: list[dict] = []
    t = 0.0
    for sheet_index, path in enumerate(sheet_paths):
        image = Image.open(path).convert("RGB")
        sheet_w, sheet_h = image.size
        tile_w, tile_h = sheet_w // cols, sheet_h // rows
        for r in range(rows):
            for c in range(cols):
                if t > duration:
                    break
                box = (c * tile_w, r * tile_h, (c + 1) * tile_w, (r + 1) * tile_h)
                tiles.append({"index": len(tiles), "t": round(t, 3), "sheet": sheet_index,
                              "box": box, "image": image.crop(box)})
                t += step
    return tiles


def tile_stats(tiles: list[dict]) -> None:
    prev = None
    for tile in tiles:
        gray = tile["image"].convert("L")
        small = list(gray.resize((32, 18), Image.BILINEAR).getdata())
        edges = list(gray.filter(ImageFilter.FIND_EDGES).resize((32, 18), Image.BILINEAR).getdata())
        mean = sum(small) / len(small)
        tile["luma"] = round(mean, 1)
        tile["contrast"] = round(math.sqrt(sum((v - mean) ** 2 for v in small) / len(small)), 1)
        tile["edge_density"] = round(sum(1 for v in edges if v > 40) / len(edges), 3)
        tile["diff_prev"] = round(sum(abs(a - b) for a, b in zip(small, prev)) / len(small), 1) if prev else 0.0
        prev = small


def coarse_states(tiles: list[dict], *, luma_tau: float = 25.0, diff_tau: float = 18.0) -> list[dict]:
    """Split the tile timeline into coarse visual states: a new state starts when
    the tile differs strongly from the state's anchor (luma mean) or from the
    previous tile (consecutive MAD). Coarse on purpose — the storyboard is
    2–10 s apart; this is a map for the annotator, not the engine."""
    states: list[dict] = []
    for tile in tiles:
        if not states:
            states.append({"start": tile["t"], "end": tile["t"], "anchor_luma": tile["luma"], "tiles": [tile["index"]]})
            continue
        state = states[-1]
        if abs(tile["luma"] - state["anchor_luma"]) > luma_tau or tile["diff_prev"] > diff_tau:
            states.append({"start": tile["t"], "end": tile["t"], "anchor_luma": tile["luma"], "tiles": [tile["index"]]})
        else:
            state["end"] = tile["t"]
            state["tiles"].append(tile["index"])
    return states


def mode_guess(tiles_in_state: list[dict]) -> str:
    luma = statistics.median(t["luma"] for t in tiles_in_state)
    edge = statistics.median(t["edge_density"] for t in tiles_in_state)
    diff = statistics.median(t["diff_prev"] for t in tiles_in_state) if len(tiles_in_state) > 1 else 0.0
    if luma < 90 and edge < 0.15:
        return "talking_head_or_dark"
    if luma < 120 and edge >= 0.15:
        return "code_or_terminal"
    if diff > 8:
        return "dynamic_ui"
    if luma > 190:
        return "slide_or_canvas"
    return "unknown"


def _font(size: int):
    for name in ("Menlo.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def fmt_time(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def render_sheets(tiles: list[dict], out_dir: Path, cols: int, rows: int, states: list[dict]) -> list[Path]:
    if not tiles:
        return []
    tile_w, tile_h = tiles[0]["image"].size
    label_h = 22
    font = _font(15)
    state_of_tile = {}
    for number, state in enumerate(states):
        for index in state["tiles"]:
            state_of_tile[index] = number
    paths = []
    per_sheet = cols * rows
    for sheet_number in range(math.ceil(len(tiles) / per_sheet)):
        chunk = tiles[sheet_number * per_sheet:(sheet_number + 1) * per_sheet]
        canvas = Image.new("RGB", (cols * tile_w, rows * (tile_h + label_h)), "black")
        draw = ImageDraw.Draw(canvas)
        for position, tile in enumerate(chunk):
            x = (position % cols) * tile_w
            y = (position // cols) * (tile_h + label_h)
            canvas.paste(tile["image"], (x, y))
            state_no = state_of_tile.get(tile["index"], -1)
            label = f"#{tile['index']:03d}  {fmt_time(tile['t'])}  s{state_no:02d}"
            draw.rectangle((x, y + tile_h, x + tile_w, y + tile_h + label_h), fill=(20, 20, 20))
            draw.text((x + 6, y + tile_h + 3), label, fill=(240, 240, 240), font=font)
            first_of_state = states[state_no]["tiles"][0] == tile["index"] if state_no >= 0 else False
            if first_of_state:
                draw.rectangle((x, y, x + tile_w - 1, y + tile_h - 1), outline=(255, 80, 80), width=3)
        path = out_dir / f"sheet_{sheet_number:02d}.jpg"
        canvas.save(path, quality=88)
        paths.append(path)
    return paths


def write_draft(video: dict, tiles: list[dict], states: list[dict], draft_path: Path, source: str) -> None:
    by_index = {t["index"]: t for t in tiles}
    records = []
    for number, state in enumerate(states):
        members = [by_index[i] for i in state["tiles"]]
        records.append({
            "state_id": f"s{number:02d}",
            "start": state["start"],
            "end": round(state["end"] + (tiles[1]["t"] - tiles[0]["t"] if len(tiles) > 1 else 0.0), 3),
            "class": "unreviewed",
            "kind": mode_guess(members),
            "label": "",
            "spoken_seg_ids": [],
            "duplicates_of": None,
            "tiles": state["tiles"],
        })
    payload = {
        "video_id": video["id"],
        "title": video.get("title"),
        "duration": video.get("duration"),
        "source": source,
        "annotator": None,
        "status": "draft",
        "storyboard": {"interval_s": round(tiles[1]["t"] - tiles[0]["t"], 3) if len(tiles) > 1 else None,
                       "tiles": len(tiles)},
        "youtube_chapters": [
            {"start": c.get("start_time"), "end": c.get("end_time"), "title": c.get("title")}
            for c in (video.get("chapters") or [])
        ],
        "states": records,
        "summary_checklist": [],
    }
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="YouTube video id or URL")
    parser.add_argument("--out", default=None, help="Sheets root (default: bench/sheets)")
    parser.add_argument("--annotations", default=None, help="Annotations dir (default: bench/annotations)")
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--rows", type=int, default=3)
    args = parser.parse_args()

    bench_dir = Path(__file__).resolve().parent
    out_root = Path(args.out) if args.out else bench_dir / "sheets"
    ann_dir = Path(args.annotations) if args.annotations else bench_dir / "annotations"
    video_id = video_id_of(args.source)
    info = fetch_info(video_id)
    fmt = storyboard_format(info)
    out_dir = out_root / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "info.json").write_text(json.dumps({
        k: info.get(k) for k in ("id", "title", "duration", "language", "channel", "chapters", "heatmap")
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if fmt is None:
        print(f"[bench] {video_id}: no storyboard track (short video?) — annotate from a local scan instead",
              file=sys.stderr)
        return 3
    sheets = download_sheets(fmt, out_dir)
    tiles = split_tiles(sheets, fmt, float(info.get("duration") or 0))
    tile_stats(tiles)
    states = coarse_states(tiles)
    rendered = render_sheets(tiles, out_dir, args.cols, args.rows, states)
    (out_dir / "tiles.json").write_text(json.dumps([
        {k: v for k, v in tile.items() if k not in ("image", "box")} for tile in tiles
    ], indent=2), encoding="utf-8")
    draft_path = ann_dir / f"{video_id}.draft.json"
    final_path = ann_dir / f"{video_id}.json"
    if final_path.exists():
        print(f"[bench] {final_path} exists — draft not written", file=sys.stderr)
    else:
        write_draft(info, tiles, states, draft_path, source=f"storyboard:{fmt['format_id']}")
    interval = tiles[1]["t"] - tiles[0]["t"] if len(tiles) > 1 else 0
    print(f"[bench] {video_id}: {len(tiles)} tiles {fmt['width']}x{fmt['height']} every {interval:.1f}s "
          f"-> {len(rendered)} sheets, {len(states)} coarse states; draft: {draft_path}")
    for path in rendered:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
