#!/usr/bin/env python3
"""Hybrid candidate-frame extraction for /summarize-video.

Candidate union (per the plan):
    scene changes  ∪  cue-offset grabs  ∪  pinned timestamps
    ∪  final frame  ∪  sparse safety grid  ∪  chapter-coverage fill

Architecture note: scene detection runs as a metadata-only pass
(`select=gt(scene,T)` + showinfo → `-f null -`) that yields a list of pts,
and EVERY frame — scene, cue, pin, safety, coverage — is then extracted by
seeking to its own timestamp. Label == content by construction. (The earlier
single-pass design paired showinfo stamps with written files positionally,
which mislabeled scene frames when the two streams drifted; a fast `-ss`
seek was verified frame-identical to an accurate output-side seek, so
per-timestamp grabs are both exact and cheap.)

Before any model tokens are spent: blank/black filter + near-duplicate
removal (16x16 grayscale thumbs) and an even-sample cap that never evicts
cue/pin/final/coverage frames.

Scene detection and thumbnail dedup adapted from
bradautomates/claude-video (MIT).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

SCENE_THRESHOLD = 0.15   # lower than claude-video's 0.20: slide flips are small changes
CUE_OFFSETS = (0.5, 1.5)  # "now I click" is said BEFORE the screen changes
SAFETY_GAP = 90.0        # insert a safety frame in any gap longer than this
MERGE_EPS = 0.35         # points closer than this collapse into one grab
MIN_PER_CHAPTER = 2      # coverage floor for chapters that need frames (--chapters)
DEDUP_THUMB = 16
DEDUP_THRESHOLD = 2.0
BLANK_STD = 2.0          # thumb std below this = uniform frame
BLANK_DARK = 10.0        # ...and mean below this = black
BLANK_BRIGHT = 245.0     # ...or mean above this = white flash
MAX_READ_DIMENSION = 1998
REASON_PRIORITY = ("cue", "pin", "final", "coverage", "scene", "safety")
PROTECTED = {"cue", "pin", "final", "coverage"}
SHOWINFO_TS_RE = re.compile(r"pts_time:([0-9.]+)")

TOOL_HINT = "Install with: brew install ffmpeg yt-dlp"


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def parse_time(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def parse_times(value: str | None) -> list[float]:
    if not value:
        return []
    out = []
    for token in value.split(","):
        token = token.strip()
        if token:
            out.append(float(parse_time(token)))
    return sorted(set(out))


def parse_ranges(value: str | None) -> list[tuple[float, float]]:
    """Parse "45-120,300-420" (each side SS / MM:SS / HH:MM:SS)."""
    if not value:
        return []
    out = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        lo, _, hi = token.partition("-")
        start, end = parse_time(lo), parse_time(hi)
        if start is None or end is None or end <= start:
            raise SystemExit(f"Bad section range: {token!r}")
        out.append((start, end))
    return sorted(out)


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def _scale_filter(resolution: int) -> str:
    return (
        f"scale=w='min({resolution},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {result.stderr.strip()}")
    return float(json.loads(result.stdout or "{}").get("format", {}).get("duration") or 0)


# ---------------------------------------------------------------- video parts

def resolve_parts(source: str | None, work: Path,
                  sections: list[tuple[float, float]] | None = None) -> list[dict]:
    """Return [{path, offset, duration}] covering the available video.

    A full download is one part at offset 0. `--sections` downloads each range
    to its own file whose offset is the requested range start (keyframe snap
    makes this approximate — pad section requests by a few seconds).
    Local files are used in place. Results are cached via parts.json.
    """
    dl_dir = work / "download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    parts_file = dl_dir / "parts.json"
    if parts_file.exists():
        parts = json.loads(parts_file.read_text(encoding="utf-8"))
        if all(Path(p["path"]).exists() for p in parts):
            return parts

    if source and not is_url(source):
        local = Path(source).expanduser().resolve()
        if not local.exists():
            raise SystemExit(f"File not found: {local}")
        parts = [{"path": str(local), "offset": 0.0, "duration": probe_duration(str(local))}]
        parts_file.write_text(json.dumps(parts, indent=2), encoding="utf-8")
        return parts

    if not source:
        raise SystemExit("No downloaded video found in work dir and no source given")
    if shutil.which("yt-dlp") is None:
        raise SystemExit(f"yt-dlp is not installed. {TOOL_HINT}")

    fmt = "bv*[height<=720]+ba/b[height<=720]/bv+ba/b"
    parts = []
    if sections:
        for start, end in sections:
            name = f"sec_{int(start):06d}_{int(end):06d}"
            out_tpl = str(dl_dir / f"{name}.%(ext)s")
            print(f"[vsum] downloading section {format_time(start)}-{format_time(end)}…",
                  file=sys.stderr)
            cmd = [
                "yt-dlp", "-N", "8", "-f", fmt,
                "--merge-output-format", "mp4",
                "--download-sections", f"*{start:.0f}-{end:.0f}",
                "--no-playlist", "--ignore-errors",
                "-o", out_tpl, "--", source,
            ]
            subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
            files = [p for p in dl_dir.glob(f"{name}.*") if p.suffix.lower() != ".json"]
            if not files:
                print(f"[vsum] section {format_time(start)}-{format_time(end)} failed — skipped",
                      file=sys.stderr)
                continue
            parts.append({"path": str(files[0]), "offset": start,
                          "duration": probe_duration(str(files[0]))})
    else:
        print("[vsum] downloading video (<=720p) via yt-dlp…", file=sys.stderr)
        cmd = [
            "yt-dlp", "-N", "8", "-f", fmt,
            "--merge-output-format", "mp4",
            "--no-playlist", "--ignore-errors",
            "-o", str(dl_dir / "video.%(ext)s"), "--", source,
        ]
        subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
        vids = [p for p in sorted(dl_dir.glob("video.*"))
                if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
        if not vids:
            raise SystemExit(f"yt-dlp did not produce a video file in {dl_dir}")
        parts = [{"path": str(vids[0]), "offset": 0.0, "duration": probe_duration(str(vids[0]))}]

    if not parts:
        raise SystemExit("No video parts available after download")
    parts_file.write_text(json.dumps(parts, indent=2), encoding="utf-8")
    return parts


def part_for(parts: list[dict], t: float) -> dict | None:
    for part in parts:
        if part["offset"] - 0.5 <= t <= part["offset"] + part["duration"] + 0.5:
            return part
    return None


# ------------------------------------------------------------- frame engines

def scene_detect_pts(part: dict, threshold: float) -> list[float]:
    """Metadata-only scene pass: decode, select scene changes (plus frame 0),
    and read their pts from showinfo — nothing is written, so there is no
    file/stamp pairing to drift. Returns absolute timestamps."""
    vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',showinfo"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-i", part["path"],
        "-vf", vf, "-an", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg scene detection failed: {result.stderr.strip()}")
    return [round(part["offset"] + float(m.group(1)), 2)
            for m in SHOWINFO_TS_RE.finditer(result.stderr)]


def point_grab(parts: list[dict], t: float, out_dir: Path, resolution: int,
               reasons: set[str], seq: int) -> dict | None:
    """Extract exactly one frame at absolute time ``t`` (fast seek — verified
    frame-identical to an accurate output-side seek)."""
    part = part_for(parts, t)
    if part is None:
        return None
    local_t = min(max(0.0, t - part["offset"]), max(0.0, part["duration"] - 0.05))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pt_{seq:04d}.jpg"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{local_t:.3f}", "-i", part["path"],
        "-frames:v", "1", "-vf", _scale_filter(resolution), "-q:v", "4",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not path.exists():
        return None
    return {"t": round(t, 2), "path": str(path), "reasons": set(reasons)}


def merge_points(points: list[tuple[float, str]], eps: float = MERGE_EPS) -> list[tuple[float, set[str]]]:
    """Collapse timestamps closer than ``eps`` into one grab, unioning reasons
    (keeps the earliest time of each cluster)."""
    merged: list[list] = []
    for t, reason in sorted(points):
        if merged and t - merged[-1][0] <= eps:
            merged[-1][1].add(reason)
        else:
            merged.append([t, {reason}])
    return [(t, reasons) for t, reasons in merged]


# ------------------------------------------------------------------ chapters

def load_chapters(path: str | None) -> list[dict]:
    if not path:
        return []
    chapters = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    out = []
    for ch in chapters:
        out.append({
            "chapter_id": ch.get("chapter_id", f"ch{len(out) + 1:02d}"),
            "start": float(ch["start"]),
            "end": float(ch["end"]),
            "needs_frames": bool(ch.get("needs_frames", True)),
        })
    return out


def chapter_counts(chapters: list[dict], times: list[float]) -> dict[str, int]:
    return {
        ch["chapter_id"]: sum(1 for t in times if ch["start"] <= t < ch["end"])
        for ch in chapters
    }


def coverage_fill(chapters: list[dict], merged: list[tuple[float, set[str]]],
                  min_per_chapter: int) -> list[tuple[float, str]]:
    """Extra grab points for needs_frames chapters holding fewer than
    ``min_per_chapter`` candidates: midpoint first, then quarter points."""
    times = [t for t, _ in merged]
    extras: list[tuple[float, str]] = []
    for ch in chapters:
        if not ch["needs_frames"]:
            continue
        have = sum(1 for t in times if ch["start"] <= t < ch["end"])
        span = ch["end"] - ch["start"]
        fills = [ch["start"] + span * f for f in (0.5, 0.25, 0.75)]
        for f in fills:
            if have >= min_per_chapter:
                break
            if all(abs(f - t) > MERGE_EPS for t in times):
                extras.append((round(f, 2), "coverage"))
                times.append(f)
                have += 1
    return extras


# ------------------------------------------------------- thumbs + filtering

def thumb(path: str) -> bytes:
    """16x16 grayscale thumbnail bytes; b'' on failure (fail-open)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-vf", f"scale={DEDUP_THUMB}:{DEDUP_THUMB},format=gray",
        "-frames:v", "1", "-f", "rawvideo", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    data = result.stdout
    return data if result.returncode == 0 and len(data) == DEDUP_THUMB * DEDUP_THUMB else b""


def stats(data: bytes) -> tuple[float, float]:
    n = len(data)
    mean = sum(data) / n
    var = sum((x - mean) ** 2 for x in data) / n
    return mean, var ** 0.5


def frame_delta(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return float("inf")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def drop(frame: dict) -> None:
    try:
        Path(frame["path"]).unlink()
    except OSError:
        pass


def main_reason(frame: dict) -> str:
    for r in REASON_PRIORITY:
        if r in frame["reasons"]:
            return r
    return "scene"


def is_protected(frame: dict) -> bool:
    return bool(frame["reasons"] & PROTECTED)


def even_indices(count: int, n: int) -> list[int]:
    if n >= count:
        return list(range(count))
    if n <= 1:
        return [0]
    return sorted(set(round(i * (count - 1) / (n - 1)) for i in range(n)))


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="candidates",
        description="Extract hybrid candidate frames (512px) for model triage.",
    )
    ap.add_argument("source", nargs="?", default=None,
                    help="Video URL or local path (needed only if not yet downloaded)")
    ap.add_argument("--work", required=True, help="Working directory from transcript.py")
    ap.add_argument("--cues", default=None,
                    help="Comma-separated deictic/action moments (SS or MM:SS); each is "
                         f"expanded to +{CUE_OFFSETS[0]}s and +{CUE_OFFSETS[1]}s grabs")
    ap.add_argument("--pins", default=None,
                    help="Comma-separated exact timestamps (chapter starts, segment ends)")
    ap.add_argument("--chapters", default=None,
                    help="Path to chapters.json — enables the per-chapter coverage table "
                         "and fills chapters that need frames but have fewer than "
                         f"{MIN_PER_CHAPTER} candidates")
    ap.add_argument("--min-per-chapter", type=int, default=MIN_PER_CHAPTER,
                    help="Coverage floor for needs_frames chapters (with --chapters)")
    ap.add_argument("--sections", default=None,
                    help="Comma-separated ranges 'S-E' to download instead of the full video "
                         "(long videos; pad each range by ~5s — keyframe snap is approximate)")
    ap.add_argument("--scene-threshold", type=float, default=SCENE_THRESHOLD)
    ap.add_argument("--resolution", type=int, default=512)
    ap.add_argument("--max-candidates", type=int, default=60)
    ap.add_argument("--no-dedup", action="store_true")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit(f"ffmpeg/ffprobe not installed. {TOOL_HINT}")

    work = Path(args.work).expanduser().resolve()
    if not work.exists():
        raise SystemExit(f"Work dir not found: {work} — run transcript.py first")
    raw_dir = work / "raw"
    cand_dir = work / "candidates"
    for d in (raw_dir, cand_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    sections = parse_ranges(args.sections)
    parts = resolve_parts(args.source, work, sections)
    total_end = max(p["offset"] + p["duration"] for p in parts)
    chapters = load_chapters(args.chapters)

    # 1. candidate point union: scene pts (metadata-only pass) + cues + pins + final
    points: list[tuple[float, str]] = []
    for part in parts:
        pts = scene_detect_pts(part, args.scene_threshold)
        points += [(t, "scene") for t in pts]
    n_scene = len(points)
    print(f"[vsum] scene detection: {n_scene} change points", file=sys.stderr)

    for cue in parse_times(args.cues):
        for off in CUE_OFFSETS:
            points.append((cue + off, "cue"))
    for pin in parse_times(args.pins):
        points.append((pin, "pin"))
    points.append((max(0.0, total_end - 0.5), "final"))

    merged = merge_points(points)

    # 2. safety grid over gaps no other candidate covers
    filled: list[tuple[float, str]] = []
    prev = parts[0]["offset"]
    for t in [t for t, _ in merged] + [total_end]:
        gap_start = prev
        while t - gap_start > SAFETY_GAP:
            gap_start += SAFETY_GAP
            filled.append((round(gap_start, 2), "safety"))
        prev = max(prev, t)
    # 3. chapter-coverage floor (only with --chapters)
    merged = merge_points([(t, r) for t, rs in merged for r in rs] + filled)
    if chapters:
        extras = coverage_fill(chapters, merged, args.min_per_chapter)
        if extras:
            print(f"[vsum] coverage fill: +{len(extras)} frame(s) for starved chapters",
                  file=sys.stderr)
            merged = merge_points([(t, r) for t, rs in merged for r in rs] + extras)

    # 4. extract every point by its own timestamp (label == content)
    frames: list[dict] = []
    for seq, (t, reasons) in enumerate(merged):
        grabbed = point_grab(parts, t, raw_dir, args.resolution, reasons, seq)
        if grabbed:
            frames.append(grabbed)
    frames.sort(key=lambda f: f["t"])
    n_raw = len(frames)
    print(f"[vsum] extracted {n_raw} candidate frames", file=sys.stderr)

    # 5. thumbs once → blank filter + sequential dedup (reasons accumulate)
    thumbs = {f["path"]: thumb(f["path"]) for f in frames}
    n_blank = 0
    survivors: list[dict] = []
    for f in frames:
        tb = thumbs[f["path"]]
        if tb:
            mean, std = stats(tb)
            if std < BLANK_STD and (mean < BLANK_DARK or mean > BLANK_BRIGHT):
                drop(f)
                n_blank += 1
                continue
        survivors.append(f)
    frames = survivors

    n_dedup = 0
    if not args.no_dedup and len(frames) > 1:
        kept = [frames[0]]
        last_tb = thumbs[frames[0]["path"]]
        for f in frames[1:]:
            tb = thumbs[f["path"]]
            if frame_delta(tb, last_tb) <= DEDUP_THRESHOLD:
                kept[-1]["reasons"] |= f["reasons"]
                drop(f)
                n_dedup += 1
            else:
                kept.append(f)
                last_tb = tb
        frames = kept

    # 6. cap: protected (cue/pin/final/coverage) frames are never evicted
    n_capped = 0
    if len(frames) > args.max_candidates:
        protected = [f for f in frames if is_protected(f)]
        others = [f for f in frames if not is_protected(f)]
        slots = max(0, args.max_candidates - len(protected))
        keep_idx = set(even_indices(len(others), slots)) if slots else set()
        for i, f in enumerate(others):
            if i not in keep_idx:
                drop(f)
                n_capped += 1
        frames = sorted(protected + [f for i, f in enumerate(others) if i in keep_idx],
                        key=lambda f: f["t"])

    # 7. final naming + manifest
    records = []
    for i, f in enumerate(frames):
        reason = main_reason(f)
        final_path = cand_dir / f"c_{i:04d}_t{f['t']:07.1f}_{reason}.jpg"
        Path(f["path"]).rename(final_path)
        records.append({
            "frame_id": f"c_{i:04d}",
            "t": f["t"],
            "path": str(final_path),
            "reasons": sorted(f["reasons"]),
        })
    shutil.rmtree(raw_dir, ignore_errors=True)
    (work / "candidates.json").write_text(json.dumps({
        "parts": parts,
        "counts": {"scene": n_scene, "raw": n_raw, "blank_dropped": n_blank,
                   "dedup_dropped": n_dedup, "cap_dropped": n_capped,
                   "final": len(records)},
        "candidates": records,
    }, indent=2), encoding="utf-8")

    # --- report ---
    print()
    print("# candidate frames report")
    print()
    print(f"- **Candidates:** {len(records)} "
          f"(scene points {n_scene}, raw union {n_raw}; dropped: {n_blank} blank, "
          f"{n_dedup} near-duplicate, {n_capped} over cap)")
    print(f"- **Dir:** `{cand_dir}`")
    print(f"- **Manifest:** `{work / 'candidates.json'}`")
    if chapters:
        counts = chapter_counts(chapters, [r["t"] for r in records])
        print()
        print("## Per-chapter coverage")
        print()
        print("| chapter | window | candidates |")
        print("|---|---|---|")
        for ch in chapters:
            n = counts[ch["chapter_id"]]
            note = "" if not ch["needs_frames"] else (" ⚠ starved" if n < args.min_per_chapter else "")
            skip = " (no frames needed)" if not ch["needs_frames"] else ""
            print(f"| {ch['chapter_id']} | {format_time(ch['start'])}-{format_time(ch['end'])} "
                  f"| {n}{note}{skip} |")
        print()
        print("A starved chapter usually means a static stretch — consider a focused re-run "
              "with extra `--cues`/`--pins` inside its window before triage.")
    print()
    print("**Read ALL candidate paths below in a single message (parallel Read calls), "
          "then triage per the skill rubric.**")
    print()
    for r in records:
        print(f"- `{r['path']}` (t={format_time(r['t'])}, {'+'.join(r['reasons'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
