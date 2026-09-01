#!/usr/bin/env python3
"""High-quality re-extraction of SELECTED frames for /summarize-video.

The model triages candidates at 512px; this script re-grabs only the chosen
timestamps from the source video at full deliverable quality — a 1280px
`-full.jpg` plus a 640px `-thumb.jpg` per selection — into the summary's
assets dir. Zero extra image tokens: the model never re-reads these.

Spec file (written by the model during triage), JSON array:
    [{"t": 133.4, "name": "ch03_export",
      "crop": "w:h:x:y" (optional, ffmpeg crop syntax, applied to full only)}]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from candidates import format_time, parse_time, part_for, resolve_parts  # noqa: E402

MAX_READ_DIMENSION = 1998


def grab_one(parts: list[dict], t: float, out_base: Path, full_width: int,
             thumb_width: int, crop: str | None) -> list[Path]:
    part = part_for(parts, t)
    if part is None:
        print(f"[vsum] t={format_time(t)} is outside every downloaded part — skipped",
              file=sys.stderr)
        return []
    local_t = min(max(0.0, t - part["offset"]), max(0.0, part["duration"] - 0.05))
    made: list[Path] = []
    for suffix, width, quality in (("full", full_width, 2), ("thumb", thumb_width, 4)):
        vf_parts = []
        if crop and suffix == "full":
            vf_parts.append(f"crop={crop}")
        vf_parts.append(
            f"scale=w='min({width},iw)':h='min({MAX_READ_DIMENSION},ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        path = out_base.parent / f"{out_base.name}-{suffix}.jpg"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{local_t:.3f}", "-i", part["path"],
            "-frames:v", "1", "-vf", ",".join(vf_parts), "-q:v", str(quality),
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and path.exists():
            made.append(path)
        else:
            print(f"[vsum] grab failed for {path.name}: {result.stderr.strip()}",
                  file=sys.stderr)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="grab",
        description="Re-extract selected frames at deliverable quality (full + thumb).",
    )
    ap.add_argument("--work", required=True, help="Working directory (holds the video parts)")
    ap.add_argument("--spec", required=True, help="JSON spec file of selections")
    ap.add_argument("--out-dir", required=True, help="Assets dir (e.g. summary-<id>/assets)")
    ap.add_argument("--full-width", type=int, default=1280)
    ap.add_argument("--thumb-width", type=int, default=640)
    args = ap.parse_args()

    work = Path(args.work).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8"))
    if not isinstance(spec, list) or not spec:
        raise SystemExit("Spec must be a non-empty JSON array")

    parts = resolve_parts(None, work)

    print()
    print("# grab report")
    print()
    n_ok = 0
    for item in spec:
        t = float(parse_time(item["t"]))
        name = str(item["name"]).strip()
        if not name or "/" in name:
            raise SystemExit(f"Bad selection name: {item['name']!r}")
        made = grab_one(parts, t, out_dir / name, args.full_width, args.thumb_width,
                        item.get("crop"))
        if made:
            n_ok += 1
            print(f"- `{name}` (t={format_time(t)}): " + ", ".join(f"`{p.name}`" for p in made))
    print()
    print(f"**{n_ok}/{len(spec)} selections extracted to `{out_dir}`.**")
    return 0 if n_ok == len(spec) else 1


if __name__ == "__main__":
    raise SystemExit(main())
