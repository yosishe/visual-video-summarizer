#!/usr/bin/env python3
"""Transcript-aligned candidate-frame extraction for /summarize-video.

The default light mode limits visual work to transcript-derived visual target
windows. Advanced mode scans the same windows more densely and uses adaptive
scene scores. Both modes preserve actual decoded timestamps, chapter/segment
provenance, post-filter coverage, and a provider-neutral visual-pixel budget.

Scene detection concepts are adapted from bradautomates/claude-video (MIT).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import (  # noqa: E402
    chapter_for_time,
    compare_signatures,
    detect_faces,
    faces_available,
    format_time,
    is_near_duplicate,
    ocr_text_density,
    parse_metadata_series,
    parse_time,
    probe_media,
    public_quality,
    segment_ids_for_time,
    visual_signature,
)
from layout import detect_static_overlays, mask_fraction, overlay_mask  # noqa: E402
import time  # noqa: E402

ENGINE_VERSION = "1.3.0"

# Every tier-dependent number lives here. `standard` is the default: it
# reserves up to two frames per target plus one per chapter before any
# unplanned scene change gets a slot (36 left too few for those, so a slide
# nobody predicted in chapters.json could be capped out; 48 keeps the pool
# bounded while leaving room for what the transcript did not foresee). `high`
# spends more CPU and image tokens for accuracy: adaptive scene scoring, denser
# sampling, three alternatives per target, blurdetect refinement at grab time,
# optional face demotion (cv2) and OCR text density as a ranking signal.
PROFILES: dict[str, dict] = {
    "standard": {
        "cap": 48, "per_target": 2, "unplanned_floor": 12,
        "scene": "fixed", "scene_threshold": 0.15, "scene_floor": None,
        "action_offsets": (0.20, 0.80, 1.60),
        "state_offsets": (0.0, 0.60), "state_fractions": (),
        "slide_fractions": (),  # slide/diagram = anchor + measured terminal
        "coverage_min": 1, "cue_offsets": (0.5, 1.5),
        "refine": "none", "faces": "off", "ocr": "off", "resolution": 512,
        "pip_mask": "on", "dedup_scope": "family",
    },
    "high": {
        "cap": 64, "per_target": 3, "unplanned_floor": 16,
        "scene": "adaptive", "scene_threshold": None, "scene_floor": 0.04,
        "action_offsets": (0.10, 0.35, 0.70, 1.20, 1.80, 2.40),
        "state_offsets": (), "state_fractions": (0.10, 0.30, 0.50, 0.70, 0.90),
        "slide_fractions": (0.30, 0.70),  # + anchor + measured terminal
        "coverage_min": 2, "cue_offsets": (0.2, 0.5, 1.0, 1.5, 2.0),
        "refine": "sharpness", "faces": "auto", "ocr": "on", "resolution": 512,
        "pip_mask": "on", "dedup_scope": "family",
    },
}
MODE_ALIASES = {"light": "standard", "advanced": "high"}
TIER_TO_MODE = {tier: mode for mode, tier in MODE_ALIASES.items()}
# Read-through aliases kept for older imports.
LIGHT_CAP = PROFILES["standard"]["cap"]
ADVANCED_CAP = PROFILES["high"]["cap"]
UNPLANNED_FLOOR = PROFILES["standard"]["unplanned_floor"]
LIGHT_SCENE_THRESHOLD = PROFILES["standard"]["scene_threshold"]
ADVANCED_SCENE_FLOOR = PROFILES["high"]["scene_floor"]
IMAGE_TOKEN_DIVISOR = 750  # Anthropic: ≈ w×h/750 tokens per image
MERGE_EPS = 0.20
LONG_VIDEO_SECONDS = 20 * 60
SECTION_PADDING = 5.0
MAX_READ_DIMENSION = 1998
REASON_PRIORITY = ("target", "cue", "pin", "coverage", "scene", "final", "safety")
PROTECTED = {"target", "cue", "pin", "coverage"}
SHOWINFO_TS_RE = re.compile(r"pts_time:([-0-9.]+)")
SCENE_SCORE_RE = re.compile(
    r"pts_time:([-0-9.]+).*?lavfi\.scene_score=([0-9.]+)", re.DOTALL
)
TOOL_HINT = "Install with: brew install ffmpeg yt-dlp"


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def parse_times(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        return sorted({float(parse_time(token.strip())) for token in value.split(",") if token.strip()})
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def parse_ranges(value: str | None) -> list[tuple[float, float]]:
    if not value:
        return []
    ranges: list[tuple[float, float]] = []
    for token in value.split(","):
        lo, separator, hi = token.strip().partition("-")
        if not separator:
            raise SystemExit(f"Bad section range: {token!r}")
        try:
            start = float(parse_time(lo))
            end = float(parse_time(hi))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Bad section range: {token!r}") from exc
        if end <= start:
            raise SystemExit(f"Bad section range: {token!r}")
        ranges.append((start, end))
    return merge_windows(ranges)


def _scale_filter(resolution: int) -> str:
    return (
        f"scale=w='min({resolution},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def merge_windows(windows: list[tuple[float, float]], gap: float = 0.5) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(windows):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _source_identity(source: str, sections: list[tuple[float, float]], exact: bool) -> dict:
    if is_url(source):
        source_value: dict | str = source
    else:
        local = Path(source).expanduser().resolve()
        stat = local.stat()
        source_value = {"path": str(local), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return {
        "source": source_value,
        "sections": [[round(start, 3), round(end, 3)] for start, end in sections],
        "exact_sections": exact,
        "format": "video<=720p:v2",
    }


def _cache_key(identity: dict) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_cached_parts(parts_file: Path, expected_key: str | None) -> list[dict] | None:
    if not parts_file.exists():
        return None
    try:
        payload = json.loads(parts_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):  # v1 compatibility for grab.py on an in-flight run
        if expected_key is not None:
            return None
        parts = payload
    else:
        if expected_key is not None and payload.get("cache_key") != expected_key:
            return None
        parts = payload.get("parts", [])
    if parts and all(Path(part["path"]).exists() for part in parts):
        normalized = []
        for part in parts:
            media = probe_media(part["path"])
            normalized.append({
                **part,
                "source_start": float(part.get("source_start", part.get("offset", 0.0))),
                "offset": float(part.get("source_start", part.get("offset", 0.0))),
                "media_start": float(part.get("media_start", media["start_time"])),
                "duration": float(part.get("duration", media["duration"])),
                "frame_duration": float(part.get("frame_duration", media["frame_duration"])),
                "mapping_confidence": part.get("mapping_confidence", "legacy"),
            })
        return normalized
    return None


def resolve_parts(
    source: str | None,
    work: Path,
    sections: list[tuple[float, float]] | None = None,
    *,
    exact_sections: bool = False,
) -> list[dict]:
    """Resolve local/full/section media with a source-and-options cache key."""
    dl_dir = work / "download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    parts_file = dl_dir / "parts.json"
    sections = sections or []
    identity = _source_identity(source, sections, exact_sections) if source else None
    expected_key = _cache_key(identity) if identity else None
    cached = _load_cached_parts(parts_file, expected_key)
    if cached:
        return cached
    if source is None:
        raise SystemExit("No valid cached video parts found; rerun candidates.py with the source")

    if not is_url(source):
        local = Path(source).expanduser().resolve()
        if not local.exists():
            raise SystemExit(f"File not found: {local}")
        media = probe_media(local)
        parts = [{
            "path": str(local), "source_start": 0.0, "offset": 0.0,
            "media_start": media["start_time"], "duration": media["duration"],
            "frame_duration": media["frame_duration"], "mapping_confidence": "exact",
        }]
        parts_file.write_text(json.dumps({
            "schema_version": 2, "cache_key": expected_key, "identity": identity, "parts": parts,
        }, indent=2), encoding="utf-8")
        return parts

    if shutil.which("yt-dlp") is None:
        raise SystemExit(f"yt-dlp is not installed. {TOOL_HINT}")
    fmt = "bv*[height<=720]+ba/b[height<=720]/bv+ba/b"
    parts: list[dict] = []
    prefix = expected_key[:10] if expected_key else "video"
    if sections:
        for index, (start, end) in enumerate(sections):
            name = f"sec_{prefix}_{index:03d}"
            out_tpl = str(dl_dir / f"{name}.%(ext)s")
            print(f"[vsum] downloading section {format_time(start)}-{format_time(end)}…", file=sys.stderr)
            cmd = [
                "yt-dlp", "-N", "8", "-f", fmt, "--merge-output-format", "mp4",
                "--download-sections", f"*{start:.3f}-{end:.3f}",
            ]
            if exact_sections:
                cmd.append("--force-keyframes-at-cuts")
            cmd += ["--no-playlist", "--ignore-errors", "-o", out_tpl, "--", source]
            result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
            files = [
                path for path in dl_dir.glob(f"{name}.*")
                if path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")
            ]
            if result.returncode != 0 or not files:
                print(f"[vsum] section {format_time(start)}-{format_time(end)} failed", file=sys.stderr)
                continue
            media = probe_media(files[0])
            if exact_sections:
                requested = end - start
                if abs(media["duration"] - requested) > max(0.4, media["frame_duration"] * 4):
                    raise SystemExit(
                        f"section {format_time(start)}-{format_time(end)}: decoded duration "
                        f"{media['duration']:.2f}s does not match the requested {requested:.2f}s — "
                        "the exact cut failed, so source timestamps would be untrustworthy. "
                        "Retry, or run without --sections for a full download."
                    )
            parts.append({
                "path": str(files[0]), "source_start": start, "offset": start,
                "media_start": media["start_time"], "duration": media["duration"],
                "frame_duration": media["frame_duration"],
                "mapping_confidence": "exact-cut" if exact_sections else "padded-request",
            })
    else:
        print("[vsum] downloading video (<=720p) via yt-dlp…", file=sys.stderr)
        out_tpl = str(dl_dir / f"video_{prefix}.%(ext)s")
        cmd = [
            "yt-dlp", "-N", "8", "-f", fmt, "--merge-output-format", "mp4",
            "--no-playlist", "--ignore-errors", "-o", out_tpl, "--", source,
        ]
        result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
        files = [
            path for path in dl_dir.glob(f"video_{prefix}.*")
            if path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")
        ]
        if result.returncode != 0 or not files:
            raise SystemExit(f"yt-dlp did not produce a video file in {dl_dir}")
        media = probe_media(files[0])
        parts = [{
            "path": str(files[0]), "source_start": 0.0, "offset": 0.0,
            "media_start": media["start_time"], "duration": media["duration"],
            "frame_duration": media["frame_duration"], "mapping_confidence": "exact",
        }]
    if not parts:
        raise SystemExit("No video parts available after download")
    parts_file.write_text(json.dumps({
        "schema_version": 2, "cache_key": expected_key, "identity": identity, "parts": parts,
    }, indent=2), encoding="utf-8")
    return parts


def part_for(parts: list[dict], timestamp: float) -> dict | None:
    matches = [
        part for part in parts
        if float(part["source_start"]) - 0.05
        <= timestamp
        <= float(part["source_start"]) + float(part["duration"]) + 0.05
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda part: abs(
            timestamp - (float(part["source_start"]) + float(part["duration"]) / 2)
        ),
    )


def _absolute_from_media_pts(part: dict, pts: float) -> float:
    return float(part["source_start"]) + (pts - float(part.get("media_start", 0.0)))


def _media_timestamp(part: dict, absolute_timestamp: float) -> float:
    return float(part.get("media_start", 0.0)) + (
        absolute_timestamp - float(part["source_start"])
    )


def _part_window(part: dict, window: tuple[float, float]) -> tuple[float, float] | None:
    start = max(window[0], float(part["source_start"]))
    end = min(window[1], float(part["source_start"]) + float(part["duration"]))
    return (start, end) if end - start >= 0.05 else None


def scene_detect_light(
    part: dict, windows: list[tuple[float, float]], threshold: float
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for window in windows:
        overlap = _part_window(part, window)
        if overlap is None:
            continue
        start, end = overlap
        media_start = _media_timestamp(part, start)
        vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',showinfo"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-copyts",
            "-ss", f"{media_start:.3f}", "-t", f"{end - start:.3f}", "-i", part["path"],
            "-vf", vf, "-an", "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"ffmpeg scene detection failed: {result.stderr.strip()}")
        for match in SHOWINFO_TS_RE.finditer(result.stderr):
            absolute = _absolute_from_media_pts(part, float(match.group(1)))
            if start - 0.05 <= absolute <= end + 0.05:
                points.append((round(absolute, 3), threshold))
    return points


def scene_score_series(
    part: dict, window: tuple[float, float], width: int | None = None
) -> list[tuple[float, float]]:
    """Per-frame lavfi.scene_score over an absolute window (already clamped to
    the part). `width` downscales before scoring — cheaper for short probes."""
    start, end = window
    if end - start <= 0:
        return []
    vf = "select='gte(scene,0)',metadata=print:key=lavfi.scene_score"
    if width:
        vf = f"scale={int(width)}:-2," + vf
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-copyts",
        "-ss", f"{_media_timestamp(part, start):.3f}",
        "-t", f"{end - start:.3f}", "-i", part["path"],
        "-vf", vf, "-an", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene scoring failed: {result.stderr.strip()}")
    return [
        (_absolute_from_media_pts(part, pts), score)
        for pts, score in parse_metadata_series(result.stderr, "scene_score")
    ]


def adaptive_maxima(scores: list[tuple[float, float]], floor: float) -> list[tuple[float, float]]:
    """Local maxima above an adaptive threshold (median + 8·MAD, never below
    `floor`), with 0.35 s non-maximum suppression."""
    if not scores:
        return []
    values = [score for _, score in scores]
    center = median(values)
    deviations = [abs(score - center) for score in values]
    adaptive = max(floor, center + 8 * median(deviations))
    local_maxima: list[tuple[float, float]] = []
    for index, (timestamp, score) in enumerate(scores):
        before = scores[index - 1][1] if index else -1.0
        after = scores[index + 1][1] if index + 1 < len(scores) else -1.0
        if score >= adaptive and score >= before and score >= after:
            if local_maxima and timestamp - local_maxima[-1][0] < 0.35:
                if score > local_maxima[-1][1]:
                    local_maxima[-1] = (timestamp, score)
            else:
                local_maxima.append((timestamp, score))
    return [(round(timestamp, 3), round(score, 6)) for timestamp, score in local_maxima]


def scene_detect_advanced(
    part: dict, windows: list[tuple[float, float]], floor: float = ADVANCED_SCENE_FLOOR
) -> list[tuple[float, float]]:
    selected: list[tuple[float, float]] = []
    for window in windows:
        overlap = _part_window(part, window)
        if overlap is None:
            continue
        try:
            scores = scene_score_series(part, overlap)
        except RuntimeError as exc:
            raise SystemExit(f"ffmpeg adaptive scene scoring failed: {exc}") from exc
        selected.extend(adaptive_maxima(scores, floor))
    return selected


def stable_terminal_from_scores(
    scores: list[tuple[float, float]],
    anchor: float,
    window: tuple[float, float],
    *,
    step_tau: float | None = None,
    flip_tau: float = LIGHT_SCENE_THRESHOLD,
    settle: float = 0.20,
) -> dict | None:
    """Measure where a slide/board stops being built up.

    A board is drawn WHILE the speaker talks about it; its most complete state
    is the last frame before the screen flips to something else. Scores above
    `step_tau` (median + 6·MAD, min 0.015) are events: a *build step* when
    below `flip_tau`, a *flip* at or above it. Starting from the stable run
    containing `anchor`, walk forward across build steps and stop at the first
    flip (minus `settle`) or at the window end (minus 0.25 s).
    """
    if not scores:
        return None
    window_start, window_end = float(window[0]), float(window[1])
    values = [score for _, score in scores]
    if step_tau is None:
        center = median(values)
        deviation = median(abs(score - center) for score in values)
        step_tau = max(0.015, center + 6 * deviation)
    events = [(timestamp, score) for timestamp, score in scores if score >= step_tau]
    flips = [timestamp for timestamp, score in events if score >= flip_tau]
    run_start = max([window_start] + [timestamp for timestamp in flips if timestamp <= anchor])
    later = [timestamp for timestamp in flips if timestamp > anchor]
    if later:
        stop = later[0]
        flipped = True
        terminal = stop - settle
    else:
        stop = window_end
        flipped = False
        terminal = window_end - 0.25
    terminal = max(run_start, min(terminal, window_end))
    build_steps = [
        timestamp for timestamp, score in events
        if run_start <= timestamp < stop and score < flip_tau
    ]
    return {
        "terminal_t": round(terminal, 3),
        "run_start": round(run_start, 3),
        "stop": round(stop, 3),
        "flipped": flipped,
        "build_steps": len(build_steps),
        "events": len(events),
        "step_tau": round(step_tau, 4),
    }


def measure_stable_terminal(parts: list[dict], target: dict) -> dict | None:
    """Probe one slide/diagram target window (cheap 192px scene pass, zero
    tokens). Returns None — and the caller keeps `end-0.25` — on any failure."""
    anchor = float(target["anchor_t"])
    part = part_for(parts, anchor)
    if part is None:
        return None
    overlap = _part_window(part, (float(target["window"][0]), float(target["window"][1])))
    if overlap is None:
        return None
    try:
        scores = scene_score_series(part, overlap, width=192)
    except RuntimeError as exc:
        print(f"[vsum] terminal probe failed for {target['target_id']}: {exc}; using window end", file=sys.stderr)
        return None
    return stable_terminal_from_scores(scores, anchor, overlap)


def load_transcript(path: str | None, work: Path) -> dict:
    candidate = Path(path).expanduser().resolve() if path else work / "transcript.json"
    if not candidate.exists():
        return {"video": {}, "segments": []}
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload.get("segments", []), list):
        raise SystemExit("transcript.json must contain a segments array")
    return payload


def _target_from_raw(
    raw: dict, chapter: dict, segment_map: dict[str, dict], index: int
) -> dict:
    seg_ids = [str(seg_id) for seg_id in raw.get("seg_ids", [])]
    if raw.get("seg_id") and str(raw["seg_id"]) not in seg_ids:
        seg_ids.append(str(raw["seg_id"]))
    segments = [segment_map[seg_id] for seg_id in seg_ids if seg_id in segment_map]
    kind = str(raw.get("kind") or raw.get("type") or "state")
    if kind not in {"state", "action_result", "diagram", "slide"}:
        raise SystemExit(f"Unsupported visual target kind: {kind!r}")
    explicit_t = raw.get("t")
    if segments:
        seg_start = min(float(segment["start"]) for segment in segments)
        seg_end = max(float(segment["end"]) for segment in segments)
    elif explicit_t is not None:
        seg_start = seg_end = float(parse_time(explicit_t))
    else:
        seg_start = float(chapter["start"])
        seg_end = float(chapter["end"])
    explicit_anchor = raw.get("anchor_t")
    if explicit_anchor is not None:
        anchor = float(parse_time(explicit_anchor))
    elif kind == "action_result" and segments:
        action_seg_id = str(raw.get("action_seg_id") or raw.get("cue_seg_id") or "")
        action_segment = segment_map.get(action_seg_id)
        anchor = float(action_segment["end"]) if action_segment else min(
            float(segment["end"]) for segment in segments
        )
    else:
        anchor = seg_end if kind == "action_result" else (seg_start + seg_end) / 2
    window = raw.get("window")
    if isinstance(window, (list, tuple)) and len(window) == 2:
        window_start, window_end = float(window[0]), float(window[1])
    elif kind == "action_result":
        window_start, window_end = anchor - 0.15, anchor + 2.5
    else:
        window_start, window_end = seg_start - 0.25, seg_end + 0.75
    window_start = max(float(chapter["start"]), window_start)
    chapter_last_frame = max(float(chapter["start"]), float(chapter["end"]) - 0.001)
    window_end = min(chapter_last_frame, max(window_start + 0.05, window_end))
    return {
        "target_id": str(raw.get("target_id") or f"{chapter['chapter_id']}_vt{index + 1:02d}"),
        "chapter_id": chapter["chapter_id"],
        "kind": kind,
        "seg_ids": seg_ids,
        "why": str(raw.get("why") or "visual evidence"),
        "anchor_t": round(anchor, 3),
        "window": [round(window_start, 3), round(window_end, 3)],
    }


def load_chapters(
    path: str | None, transcript: dict, duration: float
) -> list[dict]:
    if not path:
        return []
    raw_chapters = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw_chapters, list):
        raise SystemExit("chapters.json must be an array")
    segment_map = {str(seg["seg_id"]): seg for seg in transcript.get("segments", [])}
    chapters: list[dict] = []
    for index, raw in enumerate(raw_chapters):
        chapter = {
            **raw,
            "chapter_id": str(raw.get("chapter_id") or f"ch{index + 1:02d}"),
            "start": float(raw["start"]),
            "end": float(raw["end"]),
            "needs_frames": bool(raw.get("needs_frames", True)),
        }
        target_rows = list(raw.get("visual_targets") or [])
        if not target_rows:
            target_rows = [
                {**cue, "kind": cue.get("kind", "state")}
                for cue in raw.get("cues", [])
            ]
        chapter["visual_targets"] = [
            _target_from_raw(target, chapter, segment_map, target_index)
            for target_index, target in enumerate(target_rows)
        ]
        chapters.append(chapter)
    for index, chapter in enumerate(chapters):
        if chapter["end"] <= chapter["start"]:
            raise SystemExit(f"Bad chapter window: {chapter['chapter_id']}")
        if index and chapter["start"] < chapters[index - 1]["end"]:
            raise SystemExit(f"Overlapping chapter windows: {chapters[index - 1]['chapter_id']} / {chapter['chapter_id']}")
    chapter_ids = [chapter["chapter_id"] for chapter in chapters]
    if len(chapter_ids) != len(set(chapter_ids)):
        raise SystemExit("chapters.json contains duplicate chapter_id values")
    target_ids = [
        target["target_id"] for chapter in chapters for target in chapter.get("visual_targets", [])
    ]
    if len(target_ids) != len(set(target_ids)):
        raise SystemExit("chapters.json contains duplicate target_id values")
    if chapters and duration and chapters[-1]["end"] > duration + 1.0:
        raise SystemExit("Last chapter extends beyond the transcript video duration")
    return chapters


def visual_windows(chapters: list[dict]) -> list[tuple[float, float]]:
    """Scene-detection windows: every chapter that needs frames, in full.

    Targets only say where the transcript *predicts* a visual; they must not
    become the only places we look. A slide flip or demo step nobody flagged
    in advance still has to reach the candidate pool, so the whole chapter is
    scanned for scene changes (the decode cost is the same as scanning the
    video once) and target windows add dense sampling on top.
    """
    windows: list[tuple[float, float]] = []
    for chapter in chapters:
        if not chapter["needs_frames"]:
            continue
        windows.append((chapter["start"], chapter["end"]))
    return merge_windows(windows)


def derive_sections(chapters: list[dict], duration: float) -> list[tuple[float, float]]:
    if duration <= LONG_VIDEO_SECONDS:
        return []
    padded = [
        (max(0.0, start - SECTION_PADDING), min(duration, end + SECTION_PADDING))
        for start, end in visual_windows(chapters)
    ]
    return merge_windows(padded, gap=2 * SECTION_PADDING)


def targets_for_time(chapters: list[dict], timestamp: float) -> list[dict]:
    chapter = chapter_for_time(chapters, timestamp)
    if not chapter:
        return []
    return [
        target for target in chapter.get("visual_targets", [])
        if target["window"][0] - 0.05 <= timestamp <= target["window"][1] + 0.05
    ]


def make_point(
    timestamp: float,
    reason: str,
    chapters: list[dict],
    segments: list[dict],
    *,
    target: dict | None = None,
    scene_score: float = 0.0,
) -> dict:
    chapter = chapter_for_time(chapters, timestamp) if chapters else None
    target_rows = [target] if target else targets_for_time(chapters, timestamp)
    seg_ids = {str(seg_id) for row in target_rows for seg_id in row.get("seg_ids", [])}
    if not seg_ids:
        seg_ids.update(segment_ids_for_time(segments, timestamp))
    priority = {"target": 100, "cue": 90, "pin": 80, "coverage": 70, "scene": 40, "final": 20, "safety": 10}.get(reason, 0)
    return {
        "requested_t": round(timestamp, 3),
        "reasons": {reason},
        "chapter_id": chapter["chapter_id"] if chapter else None,
        "target_ids": {row["target_id"] for row in target_rows},
        "target_kinds": {row["kind"] for row in target_rows},
        "target_anchors": {row["target_id"]: row["anchor_t"] for row in target_rows},
        "seg_ids": seg_ids,
        "scene_score": scene_score,
        "priority": priority,
    }


def parse_profile_override(raw: str | None, profile: dict) -> dict:
    """`--profile-override` JSON: only keys the tier already has, so an ablation
    cannot silently invent a knob the engine never reads."""
    if not raw:
        return {}
    try:
        override = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--profile-override is not valid JSON: {exc}")
    if not isinstance(override, dict):
        raise SystemExit("--profile-override must be a JSON object")
    unknown = sorted(set(override) - set(profile))
    if unknown:
        raise SystemExit(f"--profile-override: unknown profile keys {unknown}; known: {sorted(profile)}")
    for key, value in override.items():
        if isinstance(value, list):
            override[key] = tuple(value)
    return override


def profile_digest(profile: dict) -> str:
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_profile(tier: str | None, mode: str | None) -> tuple[str, dict]:
    """`--tier standard|high` is the user-facing switch; `--mode light|advanced`
    stays as an alias. Both given and disagreeing is an error, not a guess."""
    from_mode = MODE_ALIASES.get(mode) if mode else None
    if mode and from_mode is None:
        raise SystemExit(f"Unknown --mode {mode!r}; use --tier standard|high")
    if tier and from_mode and tier != from_mode:
        raise SystemExit(f"--tier {tier} conflicts with --mode {mode} (an alias of --tier {from_mode})")
    name = tier or from_mode or "standard"
    if name not in PROFILES:
        raise SystemExit(f"Unknown tier {name!r}; use standard or high")
    return name, dict(PROFILES[name])


def _profile_arg(profile: dict | str) -> dict:
    if isinstance(profile, str):  # legacy callers passed the mode name
        return PROFILES[MODE_ALIASES.get(profile, profile)]
    return profile


def target_sample_times(target: dict, profile: dict | str) -> list[float]:
    profile = _profile_arg(profile)
    start, end = target["window"]
    anchor = target["anchor_t"]
    span = max(0.05, end - start)

    def clamp(value: float) -> float:
        return min(end, max(start, value))

    if target["kind"] == "action_result":
        return [clamp(anchor + offset) for offset in profile["action_offsets"]]
    times: list[float] = []
    if target["kind"] in ("diagram", "slide"):
        # A board or slide is built up WHILE the speaker talks about it, so its
        # most complete state is the last frame before the screen moves on.
        # `terminal_t` is that moment when the probe measured it; otherwise the
        # end of the referenced segments stands in. Sampling only the midpoint
        # returned half-typed titles and diagrams missing their last labels.
        terminal = target.get("terminal_t")
        times = [clamp(anchor), clamp(float(terminal)) if terminal is not None else max(start, end - 0.25)]
        times += [start + span * fraction for fraction in profile["slide_fractions"]]
    else:
        times = [clamp(anchor + offset) for offset in profile["state_offsets"]]
        times += [start + span * fraction for fraction in profile["state_fractions"]]
    ordered: list[float] = []
    for value in times:
        if not any(abs(value - seen) < 1e-6 for seen in ordered):
            ordered.append(value)
    return ordered


def merge_points(points: list[dict], epsilon: float = MERGE_EPS) -> list[dict]:
    merged: list[dict] = []
    for point in sorted(points, key=lambda item: item["requested_t"]):
        if (
            merged
            and point["chapter_id"] == merged[-1]["chapter_id"]
            and point["requested_t"] - merged[-1]["requested_t"] <= epsilon
        ):
            current = merged[-1]
            winner = point if _point_rank(point) > _point_rank(current) else current
            loser = current if winner is point else point
            combined = {**winner}
            for field in ("reasons", "target_ids", "target_kinds", "seg_ids"):
                combined[field] = set(winner[field]) | set(loser[field])
            combined["target_anchors"] = {**loser["target_anchors"], **winner["target_anchors"]}
            combined["scene_score"] = max(winner["scene_score"], loser["scene_score"])
            combined["priority"] = max(winner["priority"], loser["priority"])
            merged[-1] = combined
        else:
            merged.append({**point})
    return merged


def _point_rank(point: dict) -> tuple:
    action = "action_result" in point.get("target_kinds", set())
    timestamp_rank = point["requested_t"] if action else -point["requested_t"]
    return point["priority"], point.get("scene_score", 0.0), timestamp_rank


def point_grab(
    parts: list[dict], point: dict, out_dir: Path, resolution: int, sequence: str
) -> dict | None:
    requested = float(point["requested_t"])
    part = part_for(parts, requested)
    if part is None:
        return None
    media_t = _media_timestamp(part, requested)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pt_{sequence}.jpg"
    vf = f"showinfo,{_scale_filter(resolution)}"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
        "-ss", f"{media_t:.3f}", "-i", part["path"], "-frames:v", "1",
        "-vf", vf, "-q:v", "4", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = SHOWINFO_TS_RE.search(result.stderr)
    if result.returncode != 0 or not path.exists() or not match:
        return None
    actual = _absolute_from_media_pts(part, float(match.group(1)))
    tolerance = max(0.10, float(part.get("frame_duration", 0.04)) * 2.5)
    timestamp_error = abs(actual - requested)
    if timestamp_error > tolerance:
        path.unlink(missing_ok=True)
        print(
            f"[vsum] rejected seek {requested:.3f}s -> {actual:.3f}s "
            f"(error {timestamp_error:.3f}s > {tolerance:.3f}s)",
            file=sys.stderr,
        )
        return None
    chapter = chapter_for_time(point.get("chapters", []), actual) if point.get("chapters") else None
    return {
        **point,
        "actual_t": round(actual, 3),
        "timestamp_error": round(timestamp_error, 4),
        "path": str(path),
        "chapter_id": chapter["chapter_id"] if chapter else point.get("chapter_id"),
        "part_mapping_confidence": part.get("mapping_confidence", "unknown"),
    }


DROP_LOG: list[dict] = []


def drop_frame(frame: dict, reason: str = "unspecified", kept_by: dict | None = None) -> None:
    """Delete a raw frame and remember why. `dropped.json` (written by main)
    is the benchmark's loss-attribution input: a missed essential visual is
    then classified as blank / dedup / cap dropped rather than "not in pool"."""
    DROP_LOG.append({
        "t": frame.get("actual_t"),
        "requested_t": frame.get("requested_t"),
        "reason": reason,
        "reasons": sorted(frame.get("reasons", ())),
        "chapter_id": frame.get("chapter_id"),
        "target_ids": sorted(frame.get("target_ids", ())),
        "kept_by_t": kept_by.get("actual_t") if kept_by else None,
    })
    try:
        Path(frame["path"]).unlink()
    except OSError:
        pass


def _frame_score(frame: dict) -> float:
    quality = frame.get("quality", {})
    score = float(frame.get("priority", 0))
    score += min(float(quality.get("sharpness", 0.0)), 50.0) * 0.8
    score += min(float(quality.get("contrast", 0.0)), 80.0) * 0.2
    score += min(float(frame.get("scene_score", 0.0)), 1.0) * 20
    if frame.get("target_ids"):
        score += 30
    # High tier signals. A people frame loses 25 — below the +30 target bonus,
    # so a face-bearing target frame still outranks an unplanned scene but loses
    # to its own faceless alternative. OCR text density rewards the most
    # complete build state of a slide (ranking only; never a text source).
    faces = quality.get("faces")
    if isinstance(faces, dict) and faces.get("people_frame"):
        score -= 25
    text_chars = quality.get("text_chars")
    if text_chars is not None:
        score += min(float(text_chars), 200.0) / 200.0 * 15
    if "action_result" in frame.get("target_kinds", set()):
        score += frame["actual_t"] * 0.0001
    else:
        anchors = frame.get("target_anchors", {})
        if anchors:
            score -= min(abs(frame["actual_t"] - anchor) for anchor in anchors.values())
    return score


def _same_dedup_scope(first: dict, second: dict) -> bool:
    if first.get("chapter_id") != second.get("chapter_id"):
        return False
    first_targets = set(first.get("target_ids", set()))
    second_targets = set(second.get("target_ids", set()))
    return bool(first_targets & second_targets) or (not first_targets and not second_targets)


def _merge_into(representative: dict, member: dict) -> None:
    for field in ("reasons", "target_ids", "target_kinds", "seg_ids"):
        representative[field] |= member[field]
    representative["target_anchors"].update(member["target_anchors"])


def deduplicate_frames(
    frames: list[dict], *, cluster_hook=None, scope: str = "family"
) -> tuple[list[dict], int]:
    """Cluster near-duplicates and keep the best member of each cluster.

    `scope="family"` (default) compares every frame with every other frame of
    the video — a slide reached by a target and by a scene cut, or shown again
    three chapters later, lands in one *family*. Inside a family one
    representative survives per chapter that holds a protected frame (target,
    coverage, cue, pin), so chapter coverage and per-chapter frame ownership
    are untouched; unprotected members in other chapters are dropped as
    revisits and listed on the keepers as `family_revisits`.

    `scope="chapter"` is the pre-1.4 behaviour (same chapter and shared target,
    or both target-less), kept for ablation.

    `cluster_hook(cluster)` runs on multi-member clusters before the
    representative is chosen — the place for signals that only matter when
    there is a choice to make (OCR text density in `high`)."""
    clusters: list[list[dict]] = []
    for frame in sorted(frames, key=lambda item: item["actual_t"]):
        cluster = next(
            (
                row for row in clusters
                if (scope == "family" or _same_dedup_scope(row[0], frame))
                and any(is_near_duplicate(member["_signature"], frame["_signature"]) for member in row)
            ),
            None,
        )
        if cluster is None:
            clusters.append([frame])
        else:
            cluster.append(frame)
    kept: list[dict] = []
    dropped = 0
    family_number = 0
    for cluster in clusters:
        if cluster_hook is not None and len(cluster) > 1:
            cluster_hook(cluster)
        family_id = None
        if len(cluster) > 1:
            family_number += 1
            family_id = f"f_{family_number:03d}"
        if scope != "family":
            keepers = {None: max(cluster, key=_frame_score)}
        else:
            protected_chapters = {
                member.get("chapter_id") for member in cluster if member["reasons"] & PROTECTED
            }
            if protected_chapters:
                keepers = {
                    chapter: max((m for m in cluster if m.get("chapter_id") == chapter), key=_frame_score)
                    for chapter in protected_chapters
                }
            else:
                best = max(cluster, key=_frame_score)
                keepers = {best.get("chapter_id"): best}
        keeper_ids = {id(k) for k in keepers.values()}
        primary = max(keepers.values(), key=_frame_score)
        for keeper in keepers.values():
            keeper["family_id"] = family_id
            keeper.setdefault("family_revisits", [])
        for member in cluster:
            if id(member) in keeper_ids:
                continue
            keeper = keepers.get(member.get("chapter_id")) if scope == "family" else primary
            if keeper is None:
                # a revisit in a chapter that keeps nothing: remember where, drop the frame
                primary["family_revisits"].append(round(float(member["actual_t"]), 3))
                drop_frame(member, "dedup", primary)
            else:
                _merge_into(keeper, member)
                drop_frame(member, "dedup", keeper)
            dropped += 1
        kept.extend(keepers.values())
    return sorted(kept, key=lambda item: item["actual_t"]), dropped


def _recover_blank(
    frame: dict,
    parts: list[dict],
    raw_dir: Path,
    resolution: int,
    sequence: int,
    chapters: list[dict],
    mask: bytes | None = None,
) -> dict | None:
    if not (frame["reasons"] & PROTECTED):
        return None
    base = frame["requested_t"]
    chapter = chapter_for_time(chapters, base) if chapters else None
    for retry, offset in enumerate((0.25, 0.50, 1.00, -0.25, -0.50)):
        timestamp = base + offset
        if chapter and not (chapter["start"] <= timestamp < chapter["end"]):
            continue
        point = {**frame, "requested_t": round(timestamp, 3), "chapters": chapters}
        recovered = point_grab(parts, point, raw_dir, resolution, f"{sequence:04d}_r{retry}")
        if not recovered:
            continue
        signature = visual_signature(recovered["path"], mask)
        recovered["_signature"] = signature
        recovered["quality"] = public_quality(signature)
        if not signature["blank"]:
            recovered["reasons"].add("recovered")
            recovered.pop("chapters", None)
            return recovered
        drop_frame(recovered, "blank")
    return None


def select_with_budget(
    frames: list[dict],
    chapters: list[dict],
    cap: int,
    per_target: int,
    *,
    unplanned_floor: int = UNPLANNED_FLOOR,
    hard_cap: bool = False,
) -> tuple[list[dict], int, int]:
    """Returns (selected, dropped, trimmed_reserved). With `hard_cap` the cap is
    a ceiling even for reserved target/coverage frames (an explicit
    --max-candidates); otherwise reserved frames always keep `unplanned_floor`
    slots on top."""
    keep_paths: set[str] = set()
    targets = [target for chapter in chapters for target in chapter.get("visual_targets", [])]
    for target in targets:
        matches = [frame for frame in frames if target["target_id"] in frame["target_ids"]]
        for frame in sorted(matches, key=_frame_score, reverse=True)[:per_target]:
            keep_paths.add(frame["path"])
    for chapter in chapters:
        if not chapter["needs_frames"]:
            continue
        matches = [frame for frame in frames if frame.get("chapter_id") == chapter["chapter_id"]]
        if matches and not any(frame["path"] in keep_paths for frame in matches):
            keep_paths.add(max(matches, key=_frame_score)["path"])
    selected = [frame for frame in frames if frame["path"] in keep_paths]
    # Planned frames (targets + chapter coverage) must never crowd out the
    # unplanned ones: a run with many targets would otherwise leave zero slots
    # for the scene changes the transcript did not predict. Guarantee a floor of
    # `unplanned_floor` slots on top of whatever is reserved — unless the user
    # asked for a hard ceiling with --max-candidates.
    reserved = len(selected)
    if not hard_cap:
        cap = max(cap, reserved + unplanned_floor)
    select_with_budget.last = {"reserved": reserved, "cap_effective": cap}
    trimmed = 0
    if len(selected) > cap:
        trimmed = len(selected) - cap
        selected = sorted(selected, key=_frame_score, reverse=True)[:cap]
        keep_paths = {frame["path"] for frame in selected}
    slots = cap - len(selected)
    remaining = [
        frame for frame in frames
        if frame["path"] not in keep_paths and not frame.get("target_ids")
    ]
    if slots > 0 and remaining:
        if len(remaining) <= slots:
            selected.extend(remaining)
        elif slots == 1:
            selected.append(max(remaining, key=_frame_score))
        else:
            ordered = sorted(remaining, key=lambda item: item["actual_t"])
            indices = sorted({round(i * (len(ordered) - 1) / (slots - 1)) for i in range(slots)})
            selected.extend(ordered[index] for index in indices)
    selected_paths = {frame["path"] for frame in selected}
    for frame in frames:
        if frame["path"] not in selected_paths:
            drop_frame(frame, "cap")
    return sorted(selected, key=lambda item: item["actual_t"]), len(frames) - len(selected), trimmed


def _scaled_dimensions(width: int, height: int, resolution: int) -> tuple[int, int]:
    """Candidate size after `_scale_filter` (aspect kept, even dimensions)."""
    if width <= 0 or height <= 0:
        return resolution, round(resolution * 9 / 16)
    out_w = min(resolution, width)
    out_h = min(MAX_READ_DIMENSION, round(height * out_w / width))
    return out_w - out_w % 2, out_h - out_h % 2


def cost_estimate(
    records: list[dict],
    tier: str,
    profile: dict,
    *,
    scene_seconds: float,
    terminal_probes: int,
    seeks: int,
    faces_status: str,
    ocr_frames: int,
    refine_selections: int = 20,
    overlays: int = 0,
    overlay_seconds: float = 0.0,
) -> dict:
    """An honest cost line: image tokens use each candidate's real dimensions
    (w×h/750, the Anthropic estimate — other providers differ), CPU passes are
    listed, and the other tier's ceiling is quoted for comparison."""
    dimensions: Counter[str] = Counter()
    tokens = 0
    for record in records:
        width, height = int(record.get("width") or 0), int(record.get("height") or 0)
        if width <= 0 or height <= 0:
            width, height = profile["resolution"], round(profile["resolution"] * 9 / 16)
        dimensions[f"{width}x{height}"] += 1
        tokens += max(1, round(width * height / IMAGE_TOKEN_DIVISOR))
    per_image = round(tokens / len(records)) if records else 0
    other_tier = "high" if tier == "standard" else "standard"
    other_cap = PROFILES[other_tier]["cap"]
    return {
        "tier": tier,
        "candidates": len(records),
        "image_tokens_estimate": tokens,
        "image_tokens_per_candidate": per_image,
        "token_formula": f"w*h/{IMAGE_TOKEN_DIVISOR} per image (Anthropic estimate); one batched Read",
        "frame_dimensions": dict(dimensions),
        "cpu": {
            "scene_pass": profile["scene"],
            "scene_seconds": round(scene_seconds, 1),
            "terminal_probes": terminal_probes,
            "seeks": seeks,
            "ocr_frames": ocr_frames,
            "faces": faces_status,
            "refine": profile["refine"],
            "refine_max_decodes": refine_selections if profile["refine"] != "none" else 0,
            "pip_mask": profile["pip_mask"],
            "overlays": overlays,
            "overlay_seconds": overlay_seconds,
            "dedup_scope": profile["dedup_scope"],
        },
        "other_tier": {
            "tier": other_tier,
            "cap": other_cap,
            "max_image_tokens": other_cap * (per_image or round(512 * 288 / IMAGE_TOKEN_DIVISOR)),
        },
    }


def coverage_report(chapters: list[dict], records: list[dict]) -> dict:
    chapter_rows: list[dict] = []
    target_rows: list[dict] = []
    for chapter in chapters:
        matches = [record for record in records if record.get("chapter_id") == chapter["chapter_id"]]
        status = "not-required" if not chapter["needs_frames"] else ("covered" if matches else "unresolved")
        chapter_rows.append({
            "chapter_id": chapter["chapter_id"], "status": status,
            "candidate_ids": [record["candidate_id"] for record in matches],
        })
        for target in chapter.get("visual_targets", []):
            target_matches = [
                record for record in records if target["target_id"] in record.get("target_ids", [])
            ]
            target_rows.append({
                "target_id": target["target_id"], "chapter_id": chapter["chapter_id"],
                "status": "covered" if target_matches else "unresolved",
                "candidate_ids": [record["candidate_id"] for record in target_matches],
            })
    return {"chapters": chapter_rows, "targets": target_rows}


def _strip_for_group(paths: list[str], output: Path) -> bool:
    if not paths:
        return False
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in paths:
        cmd += ["-i", path]
    filters: list[str] = []
    labels: list[str] = []
    for index in range(len(paths)):
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]scale=256:144:force_original_aspect_ratio=decrease,"
            f"pad=256:144:(ow-iw)/2:(oh-ih)/2:color=black[{label}]"
        )
    filters.append("".join(labels) + f"hstack=inputs={len(paths)}[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", "-q:v", "4", str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and output.exists()


def generate_strips(work: Path, chapters: list[dict], records: list[dict]) -> list[dict]:
    strip_dir = work / "triage-strips"
    if strip_dir.exists():
        shutil.rmtree(strip_dir)
    strip_dir.mkdir(parents=True)
    groups: list[tuple[str, list[dict]]] = []
    for chapter in chapters:
        targets = chapter.get("visual_targets", [])
        if targets:
            for target in targets:
                groups.append((
                    target["target_id"],
                    [record for record in records if target["target_id"] in record["target_ids"]],
                ))
        else:
            groups.append((
                chapter["chapter_id"],
                [record for record in records if record.get("chapter_id") == chapter["chapter_id"]],
            ))
    strips: list[dict] = []
    for index, (group_id, group_records) in enumerate(groups):
        ordered = sorted(group_records, key=lambda record: record["actual_t"])[:4]
        if not ordered:
            continue
        output = strip_dir / f"strip_{index:03d}.jpg"
        if _strip_for_group([record["path"] for record in ordered], output):
            strips.append({
                "group_id": group_id,
                "path": str(output),
                "candidate_ids": [record["candidate_id"] for record in ordered],
                "pixel_area": len(ordered) * 256 * 144,
            })
    return strips


def _write_empty_manifest(work: Path, tier: str, profile: dict, chapters: list[dict]) -> None:
    payload = {
        "schema_version": 2, "engine_version": ENGINE_VERSION,
        "tier": tier, "mode": TIER_TO_MODE[tier], "profile": profile, "parts": [],
        "counts": {
            "scene": 0, "raw": 0, "blank_or_seek_dropped": 0, "recovered": 0,
            "dedup_dropped": 0, "cap_dropped": 0, "final": 0,
        },
        "coverage": coverage_report(chapters, []), "triage": {"strips": [], "pixel_area": 0},
        "candidates": [],
    }
    (work / "candidates.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="candidates", description="Extract transcript-aligned candidate frames for model triage."
    )
    parser.add_argument("source", nargs="?", default=None, help="Video URL or local path")
    parser.add_argument("--work", required=True, help="Working directory from transcript.py")
    parser.add_argument("--transcript", default=None, help="Path to transcript.json (default: <work>/transcript.json)")
    parser.add_argument("--chapters", default=None, help="Path to chapters.json with optional visual_targets")
    parser.add_argument("--tier", choices=tuple(PROFILES), default=None,
                        help="standard (default) or high: adaptive scene scoring, denser sampling, "
                             "3 alternatives per target, blurdetect refinement at grab time, face "
                             "demotion when cv2 is importable, OCR text density as a ranking signal.")
    parser.add_argument("--mode", choices=tuple(MODE_ALIASES), default=None,
                        help="Legacy alias: light = --tier standard, advanced = --tier high")
    parser.add_argument("--cues", default=None, help="Legacy comma-separated cue timestamps")
    parser.add_argument("--pins", default=None, help="Legacy comma-separated pinned timestamps")
    parser.add_argument("--sections", default=None, help="Explicit comma-separated source ranges S-E")
    parser.add_argument("--scene-threshold", type=float, default=None,
                        help="standard: fixed scene threshold (default 0.15); high: floor of the "
                             "adaptive threshold (default 0.04)")
    parser.add_argument("--resolution", type=int, default=512,
                        help="Candidate width, capped at 512: legibility is bought at grab time "
                             "(1280px re-grab), not at triage.")
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="Hard ceiling on the pool (reserved target frames included).")
    parser.add_argument("--min-per-chapter", type=int, default=None)
    parser.add_argument("--profile-override", default=None,
                        help="JSON object merged over the tier's PROFILES entry (benchmark ablations, "
                             "e.g. '{\"scene_threshold\": 0.10}'). Keys must exist in the profile; the "
                             "effective profile and its sha256 are recorded in candidates.json.")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--strips", action="store_true",
                        help="Also render 256px temporal strips per target for a cheaper first "
                             "look. Off by default: slides and UI text are not reliably legible "
                             "at strip size, so accuracy-first triage reads the 512px candidates.")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit(f"ffmpeg/ffprobe not installed. {TOOL_HINT}")
    if args.resolution > 512:
        raise SystemExit("Candidate resolution is capped at 512px; use grab.py for deliverable quality")
    tier, profile = resolve_profile(args.tier, args.mode)
    mode_alias = TIER_TO_MODE[tier]
    profile_override = parse_profile_override(args.profile_override, profile)
    profile = {**profile, **profile_override}
    DROP_LOG.clear()

    work = Path(args.work).expanduser().resolve()
    if not work.exists():
        raise SystemExit(f"Work dir not found: {work} — run transcript.py first")
    transcript = load_transcript(args.transcript, work)
    duration = float(transcript.get("video", {}).get("duration") or 0)
    chapters = load_chapters(args.chapters, transcript, duration)
    segments = transcript.get("segments", [])
    legacy_cues = parse_times(args.cues)
    legacy_pins = parse_times(args.pins)

    if chapters and not any(chapter["needs_frames"] for chapter in chapters) and not legacy_cues and not legacy_pins:
        _write_empty_manifest(work, tier, profile, chapters)
        print("[vsum] no visual chapters or legacy cues: no video downloaded", file=sys.stderr)
        return 0
    if args.source is None and not (work / "download" / "parts.json").exists():
        raise SystemExit("Video source is required because no cached parts exist")

    sections = parse_ranges(args.sections)
    if not sections and args.source and is_url(args.source) and chapters:
        sections = derive_sections(chapters, duration)
    parts = resolve_parts(args.source, work, sections, exact_sections=bool(sections))
    total_end = duration or max(float(part["source_start"]) + float(part["duration"]) for part in parts)

    # Persistent overlays (webcam PiP, tab/subtitle bars) are masked in every
    # signature so dedup and the re-grab gate compare the content, not the
    # presenter. Sections of one video share a layout: one mask for all parts.
    overlays: list[dict] = []
    overlay_seconds = 0.0
    if profile["pip_mask"] == "on":
        started = time.monotonic()
        for part in parts:
            for overlay in detect_static_overlays(part):
                if not any(overlay["bbox"] == known["bbox"] for known in overlays):
                    overlays.append(overlay)
        overlay_seconds = round(time.monotonic() - started, 2)
    mask = overlay_mask(overlays)

    raw_dir = work / "raw"
    candidate_dir = work / "candidates"
    for directory in (raw_dir, candidate_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    windows = visual_windows(chapters) if chapters else [
        (min(float(part["source_start"]) for part in parts), total_end)
    ]
    scene_seconds = sum(end - start for start, end in windows)
    points: list[dict] = []
    scene_points: list[tuple[float, float]] = []
    if profile["scene"] == "adaptive":
        scene_floor = args.scene_threshold or profile["scene_floor"]
        scene_setting = f"adaptive (median+8·MAD, floor {scene_floor})"
        for part in parts:
            scene_points.extend(scene_detect_advanced(part, windows, scene_floor))
    else:
        scene_threshold = args.scene_threshold or profile["scene_threshold"]
        scene_setting = f"fixed threshold {scene_threshold}"
        for part in parts:
            scene_points.extend(scene_detect_light(part, windows, scene_threshold))
    for timestamp, score in scene_points:
        points.append(make_point(timestamp, "scene", chapters, segments, scene_score=score))

    # Slide/diagram targets: measure where the build-up ends instead of assuming
    # the end of the referenced segments (both tiers; one cheap probe each).
    terminal_probes: list[dict] = []
    for chapter in chapters:
        if not chapter["needs_frames"]:
            continue
        for target in chapter.get("visual_targets", []):
            if target["kind"] not in ("slide", "diagram"):
                continue
            measured = measure_stable_terminal(parts, target)
            if measured:
                target["terminal_t"] = measured["terminal_t"]
                target["terminal"] = measured
                terminal_probes.append({"target_id": target["target_id"], **measured})

    for chapter in chapters:
        if not chapter["needs_frames"]:
            continue
        for target in chapter.get("visual_targets", []):
            for timestamp in target_sample_times(target, profile):
                points.append(make_point(timestamp, "target", chapters, segments, target=target))
        minimum = args.min_per_chapter if args.min_per_chapter is not None else profile["coverage_min"]
        span = chapter["end"] - chapter["start"]
        for fraction in ((0.5,) if minimum == 1 else (0.5, 0.25, 0.75))[:minimum]:
            points.append(make_point(chapter["start"] + span * fraction, "coverage", chapters, segments))
    for cue in legacy_cues:
        for offset in profile["cue_offsets"]:
            points.append(make_point(cue + offset, "cue", chapters, segments))
    for pin in legacy_pins:
        points.append(make_point(pin, "pin", chapters, segments))
    if not chapters or (chapters and chapters[-1]["needs_frames"]):
        points.append(make_point(max(0.0, total_end - 0.5), "final", chapters, segments))

    merged = merge_points(points)
    frames: list[dict] = []
    faces_status = "off"
    if profile["faces"] == "auto":
        faces_status = "on" if faces_available() else "unavailable"
    ocr_state = {
        "budget": profile["cap"] * 2 if profile["ocr"] == "on" else 0,
        "frames": 0,
        "status": "on" if profile["ocr"] == "on" else "off",
    }

    def _ocr_cluster(cluster: list[dict]) -> None:
        # OCR text density decides only which build state of a slide/diagram
        # represents its cluster — it is never a text source, and it never runs
        # where there is no choice to make.
        if not ocr_state["budget"]:
            return
        kinds = set().union(*(frame.get("target_kinds", set()) for frame in cluster))
        if not kinds & {"slide", "diagram"}:
            return
        for frame in cluster:
            if ocr_state["frames"] >= ocr_state["budget"]:
                return
            text = ocr_text_density(frame["path"])
            if text is None:
                ocr_state["status"] = "unavailable"
                ocr_state["budget"] = 0
                return
            ocr_state["frames"] += 1
            frame["quality"] = public_quality(
                frame["_signature"], faces=frame["quality"].get("faces"), text_chars=text,
            )

    seeks = 0
    for sequence, point in enumerate(merged):
        point["chapters"] = chapters
        frame = point_grab(parts, point, raw_dir, args.resolution, f"{sequence:04d}")
        seeks += 1
        if not frame:
            continue
        frame.pop("chapters", None)
        signature = visual_signature(frame["path"], mask)
        frame["_signature"] = signature
        frame["quality"] = public_quality(signature)
        if signature["blank"]:
            recovered = _recover_blank(frame, parts, raw_dir, args.resolution, sequence, chapters, mask)
            drop_frame(frame, "blank")
            if recovered:
                frames.append(recovered)
            continue
        frames.append(frame)
    # The face signal runs on the surviving pool (blank frames never pay for it).
    if faces_status != "off":
        for frame in frames:
            faces: dict | str = "unavailable"
            if faces_status == "on":
                faces = detect_faces(frame["path"]) or {"count": 0, "area_ratio": 0.0, "people_frame": False}
            frame["quality"] = public_quality(frame["_signature"], faces=faces)
    raw_count = len(frames)
    recovered_count = sum("recovered" in frame["reasons"] for frame in frames)
    blank_dropped = len(merged) - raw_count

    dedup_dropped = 0
    if not args.no_dedup:
        frames, dedup_dropped = deduplicate_frames(
            frames, cluster_hook=_ocr_cluster, scope=profile["dedup_scope"]
        )
    ocr_frames = ocr_state["frames"]
    ocr_status = ocr_state["status"]
    hard_cap = args.max_candidates is not None
    cap = args.max_candidates or profile["cap"]
    frames, cap_dropped, trimmed_reserved = select_with_budget(
        frames, chapters, cap, profile["per_target"],
        unplanned_floor=profile["unplanned_floor"], hard_cap=hard_cap,
    )
    budget = getattr(select_with_budget, "last", {"reserved": 0, "cap_effective": cap})
    if trimmed_reserved:
        print(f"[vsum] warning: --max-candidates {cap} dropped {trimmed_reserved} reserved frames", file=sys.stderr)

    part_dimensions = {
        part["path"]: probe_media(part["path"]) for part in parts
    }
    records: list[dict] = []
    for index, frame in enumerate(frames):
        reason = next((reason for reason in REASON_PRIORITY if reason in frame["reasons"]), "scene")
        final_path = candidate_dir / f"c_{index:04d}_t{frame['actual_t']:08.3f}_{reason}.jpg"
        Path(frame["path"]).rename(final_path)
        candidate_id = f"c_{index:04d}"
        part = part_for(parts, frame["actual_t"]) or parts[0]
        media = part_dimensions[part["path"]]
        width, height = _scaled_dimensions(media["width"], media["height"], args.resolution)
        records.append({
            "candidate_id": candidate_id,
            "frame_id": candidate_id,
            "requested_t": frame["requested_t"],
            "actual_t": frame["actual_t"],
            "t": frame["actual_t"],
            "timestamp_error": frame["timestamp_error"],
            "path": str(final_path),
            "width": width,
            "height": height,
            "chapter_id": frame.get("chapter_id"),
            "target_ids": sorted(frame.get("target_ids", set())),
            "seg_ids": sorted(frame.get("seg_ids", set())),
            "reasons": sorted(frame.get("reasons", set())),
            "quality": frame["quality"],
            "scene_score": round(float(frame.get("scene_score", 0.0)), 6),
            "part_mapping_confidence": frame.get("part_mapping_confidence", "unknown"),
            "family_id": frame.get("family_id"),
            "family_revisits": sorted(frame.get("family_revisits", [])),
        })
    shutil.rmtree(raw_dir, ignore_errors=True)
    (work / "dropped.json").write_text(json.dumps(DROP_LOG, indent=2) + "\n", encoding="utf-8")
    strips = generate_strips(work, chapters, records) if (args.strips and chapters) else []
    coverage = coverage_report(chapters, records)
    cost = cost_estimate(
        records, tier, profile,
        scene_seconds=scene_seconds, terminal_probes=len(terminal_probes), seeks=seeks,
        faces_status=faces_status, ocr_frames=ocr_frames,
        overlays=len(overlays), overlay_seconds=overlay_seconds,
    )
    strip_pixels = sum(strip["pixel_area"] for strip in strips)
    baseline_pixels = 60 * 512 * 288
    # Without strips every candidate is read individually — say so in the metric
    # instead of projecting a saving that will not happen.
    projected_individual_reads = min(len(strips), len(records)) if strips else len(records)
    projected_total_pixels = strip_pixels + projected_individual_reads * 512 * 288
    payload = {
        "schema_version": 2,
        "engine_version": ENGINE_VERSION,
        "tier": tier,
        "mode": mode_alias,
        "profile": profile,
        "profile_override": profile_override,
        "profile_sha256": profile_digest(profile),
        "overlays": overlays,
        "mask_fraction": round(mask_fraction(mask), 4),
        "parts": parts,
        "counts": {
            "scene": len(scene_points), "raw": raw_count,
            "blank_or_seek_dropped": blank_dropped, "recovered": recovered_count,
            "dedup_dropped": dedup_dropped,
            "cap_dropped": cap_dropped, "reserved_trimmed": trimmed_reserved,
            "cap": cap, "cap_effective": budget["cap_effective"], "reserved": budget["reserved"],
            "final": len(records),
        },
        "coverage": coverage,
        "terminal_probes": terminal_probes,
        "cost": cost,
        "triage": {
            "instructions": "Read temporal strips first; open individual 512px candidates only when selected or uncertain.",
            "strips": strips,
            "pixel_area": strip_pixels,
            "projected_individual_reads": projected_individual_reads,
            "projected_total_pixel_area": projected_total_pixels,
            "baseline_60x512_pixel_area": baseline_pixels,
            "strip_to_baseline_ratio": round(strip_pixels / baseline_pixels, 4) if baseline_pixels else 0,
            "projected_to_baseline_ratio": round(projected_total_pixels / baseline_pixels, 4) if baseline_pixels else 0,
        },
        "candidates": records,
    }
    (work / "candidates.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print()
    print("# candidate frames report")
    print()
    print(f"- **Tier:** {tier} (alias: --mode {mode_alias}) — scene pass: {scene_setting}")
    cap_note = f"pool {cap}"
    if budget["cap_effective"] > cap:
        cap_note += (
            f" lifted to {budget['cap_effective']}: {budget['reserved']} reserved target/coverage frames "
            f"+ {profile['unplanned_floor']} unplanned slots"
        )
    print(f"- **Candidates:** {len(records)} ({cap_note}; raw {raw_count}; dedup {dedup_dropped} [{profile['dedup_scope']} scope]; cap {cap_dropped})")
    if profile["pip_mask"] != "on":
        print("- **Overlay mask:** off (profile)")
    elif overlays:
        boxes = "; ".join(
            f"{o['kind']} at x={o['bbox'][0]:.2f} y={o['bbox'][1]:.2f} w={o['bbox'][2] - o['bbox'][0]:.2f} "
            f"h={o['bbox'][3] - o['bbox'][1]:.2f} (moves in {o['motion_fraction']:.0%} of pairs)"
            for o in overlays
        )
        print(f"- **Overlay mask:** {boxes} — {mask_fraction(mask):.1%} of every signature ignored for "
              f"dedup and the re-grab gate; written frames are untouched ({overlay_seconds:.1f}s)")
    else:
        print(f"- **Overlay mask:** none detected — no persistent picture-in-picture or bar ({overlay_seconds:.1f}s)")
    dims = ", ".join(f"{n}×{size}" for size, n in cost["frame_dimensions"].items()) or "-"
    print(
        f"- **Image tokens (estimate):** ≈{cost['image_tokens_estimate']:,} for one batched Read "
        f"({dims}; ≈{cost['image_tokens_per_candidate']} each, w×h/{IMAGE_TOKEN_DIVISOR}; other providers differ)"
    )
    cpu = cost["cpu"]
    refine_note = (
        f"grab refinement: {cpu['refine']} (≤{cpu['refine_max_decodes']} × ~3 s decodes)"
        if cpu["refine"] != "none" else "grab refinement: off"
    )
    print(
        f"- **CPU:** 1 {cpu['scene_pass']} scene pass over {format_time(scene_seconds)} of chapter windows · "
        f"{cpu['terminal_probes']} terminal probe{'s' if cpu['terminal_probes'] != 1 else ''} · "
        f"{cpu['seeks']} seeks + signatures · OCR: {ocr_status}"
        f"{f' ({ocr_frames} frames)' if ocr_frames else ''} · faces: {faces_status} · {refine_note}"
    )
    other = cost["other_tier"]
    print(
        f"- **Other tier:** `--tier {other['tier']}` pool {other['cap']} candidates "
        f"(≈{other['max_image_tokens']:,} image tokens before the reserved-frame lift; "
        f"it reserves {PROFILES[other['tier']]['per_target']} frames per target)"
    )
    if terminal_probes:
        flipped = sum(1 for probe in terminal_probes if probe["flipped"])
        print(
            f"- **Slide/diagram terminal probes:** {len(terminal_probes)} measured, "
            f"{flipped} ended at a screen flip, {len(terminal_probes) - flipped} at the window end"
        )
    print(f"- **Manifest:** `{work / 'candidates.json'}`")
    if strips:
        print(
            f"- **Temporal strips:** {len(strips)}; projected strip + selective-read pixel ratio "
            f"vs 60×512 baseline: {payload['triage']['projected_to_baseline_ratio']:.1%}"
        )
    unresolved = [row for row in coverage["chapters"] if row["status"] == "unresolved"]
    if unresolved:
        print("- **Unresolved visual chapters:** " + ", ".join(row["chapter_id"] for row in unresolved))
    if chapters:
        status_by_chapter = {row["chapter_id"]: row["status"] for row in coverage["chapters"]}
        print()
        print("## Per-chapter coverage")
        print()
        print("| chapter | window | candidates | status |")
        print("|---|---|---|---|")
        for chapter in chapters:
            n = sum(1 for record in records if record.get("chapter_id") == chapter["chapter_id"])
            targets = len(chapter.get("visual_targets", []))
            print(
                f"| {chapter['chapter_id']} | {format_time(chapter['start'])}-{format_time(chapter['end'])} "
                f"| {n} ({targets} target{'s' if targets != 1 else ''}) "
                f"| {status_by_chapter.get(chapter['chapter_id'], '-')} |"
            )
        print()
        print("A needs_frames chapter with only 1 candidate is a static stretch — if its point "
              "is visual, add a target inside its window before triage.")
    print()
    if strips:
        print("Read the temporal strips first, in order:")
        for strip in strips:
            print(f"- `{strip['path']}` → {', '.join(strip['candidate_ids'])}")
        print()
        print("Open an individual candidate only when selected or uncertain:")
    else:
        print("**Read ALL candidate paths below in a single message (parallel Read calls), "
              "then triage per the skill rubric. Select by `candidate_id` — never copy times.**")
        print()
    for record in records:
        extras = ""
        quality = record["quality"]
        faces = quality.get("faces")
        if isinstance(faces, dict):
            extras += f", faces={faces['count']}/{faces['area_ratio']:.2f}"
            if faces["people_frame"]:
                extras += " (people frame)"
        if quality.get("text_chars") is not None:
            extras += f", text={quality['text_chars']}"
        if record.get("family_id"):
            extras += f", family={record['family_id']}"
            if record.get("family_revisits"):
                extras += " (same picture also at " + ", ".join(
                    format_time(t) for t in record["family_revisits"][:4]
                ) + ")"
        print(
            f"- `{record['path']}` ({record['candidate_id']}, "
            f"actual_t={record['actual_t']:.3f} [{format_time(record['actual_t'])}], "
            f"chapter={record['chapter_id']}, targets={','.join(record['target_ids']) or '-'}{extras})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
