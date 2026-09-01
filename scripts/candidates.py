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
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import (  # noqa: E402
    chapter_for_time,
    compare_signatures,
    format_time,
    is_near_duplicate,
    parse_time,
    probe_media,
    public_quality,
    segment_ids_for_time,
    visual_signature,
)

# Light reserves up to two frames per target plus one per chapter before any
# unplanned scene change gets a slot; 36 left too few for those, so a slide
# nobody predicted in chapters.json could be capped out. 48 keeps the pool
# bounded while leaving room for what the transcript did not foresee.
LIGHT_CAP = 48
ADVANCED_CAP = 60
LIGHT_SCENE_THRESHOLD = 0.15
ADVANCED_SCENE_FLOOR = 0.04
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


def scene_detect_advanced(part: dict, windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    selected: list[tuple[float, float]] = []
    for window in windows:
        overlap = _part_window(part, window)
        if overlap is None:
            continue
        start, end = overlap
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-copyts",
            "-ss", f"{_media_timestamp(part, start):.3f}",
            "-t", f"{end - start:.3f}", "-i", part["path"],
            "-vf", "select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
            "-an", "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"ffmpeg adaptive scene scoring failed: {result.stderr.strip()}")
        scores = [
            (_absolute_from_media_pts(part, float(match.group(1))), float(match.group(2)))
            for match in SCENE_SCORE_RE.finditer(result.stderr)
        ]
        if not scores:
            continue
        values = [score for _, score in scores]
        center = median(values)
        deviations = [abs(score - center) for score in values]
        adaptive = max(ADVANCED_SCENE_FLOOR, center + 8 * median(deviations))
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
        selected.extend((round(timestamp, 3), round(score, 6)) for timestamp, score in local_maxima)
    return selected


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


def target_sample_times(target: dict, mode: str) -> list[float]:
    start, end = target["window"]
    anchor = target["anchor_t"]
    if target["kind"] == "action_result":
        offsets = (0.20, 0.80, 1.60) if mode == "light" else (0.10, 0.35, 0.70, 1.20, 1.80, 2.40)
        return [min(end, max(start, anchor + offset)) for offset in offsets]
    if mode == "light":
        return [min(end, max(start, anchor)), min(end, max(start, anchor + 0.60))]
    span = max(0.05, end - start)
    return [start + span * fraction for fraction in (0.10, 0.30, 0.50, 0.70, 0.90)]


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


def drop_frame(frame: dict) -> None:
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


def deduplicate_frames(frames: list[dict]) -> tuple[list[dict], int]:
    clusters: list[list[dict]] = []
    for frame in sorted(frames, key=lambda item: item["actual_t"]):
        cluster = next(
            (
                row for row in clusters
                if _same_dedup_scope(row[0], frame)
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
    for cluster in clusters:
        representative = max(cluster, key=_frame_score)
        for member in cluster:
            if member is representative:
                continue
            for field in ("reasons", "target_ids", "target_kinds", "seg_ids"):
                representative[field] |= member[field]
            representative["target_anchors"].update(member["target_anchors"])
            drop_frame(member)
            dropped += 1
        kept.append(representative)
    return sorted(kept, key=lambda item: item["actual_t"]), dropped


def _recover_blank(
    frame: dict,
    parts: list[dict],
    raw_dir: Path,
    resolution: int,
    sequence: int,
    chapters: list[dict],
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
        signature = visual_signature(recovered["path"])
        recovered["_signature"] = signature
        recovered["quality"] = public_quality(signature)
        if not signature["blank"]:
            recovered["reasons"].add("recovered")
            recovered.pop("chapters", None)
            return recovered
        drop_frame(recovered)
    return None


def select_with_budget(
    frames: list[dict], chapters: list[dict], cap: int, per_target: int
) -> tuple[list[dict], int]:
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
    if len(selected) > cap:
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
            drop_frame(frame)
    return sorted(selected, key=lambda item: item["actual_t"]), len(frames) - len(selected)


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


def _write_empty_manifest(work: Path, mode: str, chapters: list[dict]) -> None:
    payload = {
        "schema_version": 2, "mode": mode, "parts": [],
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
    parser.add_argument("--mode", choices=("light", "advanced"), default="light")
    parser.add_argument("--cues", default=None, help="Legacy comma-separated cue timestamps")
    parser.add_argument("--pins", default=None, help="Legacy comma-separated pinned timestamps")
    parser.add_argument("--sections", default=None, help="Explicit comma-separated source ranges S-E")
    parser.add_argument("--scene-threshold", type=float, default=None)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--min-per-chapter", type=int, default=None)
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
        _write_empty_manifest(work, args.mode, chapters)
        print("[vsum] no visual chapters or legacy cues: no video downloaded", file=sys.stderr)
        return 0
    if args.source is None and not (work / "download" / "parts.json").exists():
        raise SystemExit("Video source is required because no cached parts exist")

    sections = parse_ranges(args.sections)
    if not sections and args.source and is_url(args.source) and chapters:
        sections = derive_sections(chapters, duration)
    parts = resolve_parts(args.source, work, sections, exact_sections=bool(sections))
    total_end = duration or max(float(part["source_start"]) + float(part["duration"]) for part in parts)

    raw_dir = work / "raw"
    candidate_dir = work / "candidates"
    for directory in (raw_dir, candidate_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    windows = visual_windows(chapters) if chapters else [
        (min(float(part["source_start"]) for part in parts), total_end)
    ]
    points: list[dict] = []
    scene_threshold = args.scene_threshold or LIGHT_SCENE_THRESHOLD
    scene_points: list[tuple[float, float]] = []
    for part in parts:
        if args.mode == "advanced":
            scene_points.extend(scene_detect_advanced(part, windows))
        else:
            scene_points.extend(scene_detect_light(part, windows, scene_threshold))
    for timestamp, score in scene_points:
        points.append(make_point(timestamp, "scene", chapters, segments, scene_score=score))

    for chapter in chapters:
        if not chapter["needs_frames"]:
            continue
        for target in chapter.get("visual_targets", []):
            for timestamp in target_sample_times(target, args.mode):
                points.append(make_point(timestamp, "target", chapters, segments, target=target))
        minimum = args.min_per_chapter if args.min_per_chapter is not None else (1 if args.mode == "light" else 2)
        span = chapter["end"] - chapter["start"]
        for fraction in ((0.5,) if minimum == 1 else (0.5, 0.25, 0.75))[:minimum]:
            points.append(make_point(chapter["start"] + span * fraction, "coverage", chapters, segments))
    for cue in legacy_cues:
        for offset in ((0.5, 1.5) if args.mode == "light" else (0.2, 0.5, 1.0, 1.5, 2.0)):
            points.append(make_point(cue + offset, "cue", chapters, segments))
    for pin in legacy_pins:
        points.append(make_point(pin, "pin", chapters, segments))
    if not chapters or (chapters and chapters[-1]["needs_frames"]):
        points.append(make_point(max(0.0, total_end - 0.5), "final", chapters, segments))

    merged = merge_points(points)
    frames: list[dict] = []
    for sequence, point in enumerate(merged):
        point["chapters"] = chapters
        frame = point_grab(parts, point, raw_dir, args.resolution, f"{sequence:04d}")
        if not frame:
            continue
        frame.pop("chapters", None)
        signature = visual_signature(frame["path"])
        frame["_signature"] = signature
        frame["quality"] = public_quality(signature)
        if signature["blank"]:
            recovered = _recover_blank(frame, parts, raw_dir, args.resolution, sequence, chapters)
            drop_frame(frame)
            if recovered:
                frames.append(recovered)
            continue
        frames.append(frame)
    raw_count = len(frames)
    recovered_count = sum("recovered" in frame["reasons"] for frame in frames)
    blank_dropped = len(merged) - raw_count

    dedup_dropped = 0
    if not args.no_dedup:
        frames, dedup_dropped = deduplicate_frames(frames)
    cap = args.max_candidates or (LIGHT_CAP if args.mode == "light" else ADVANCED_CAP)
    per_target = 2 if args.mode == "light" else 3
    frames, cap_dropped = select_with_budget(frames, chapters, cap, per_target)

    records: list[dict] = []
    for index, frame in enumerate(frames):
        reason = next((reason for reason in REASON_PRIORITY if reason in frame["reasons"]), "scene")
        final_path = candidate_dir / f"c_{index:04d}_t{frame['actual_t']:08.3f}_{reason}.jpg"
        Path(frame["path"]).rename(final_path)
        candidate_id = f"c_{index:04d}"
        records.append({
            "candidate_id": candidate_id,
            "frame_id": candidate_id,
            "requested_t": frame["requested_t"],
            "actual_t": frame["actual_t"],
            "t": frame["actual_t"],
            "timestamp_error": frame["timestamp_error"],
            "path": str(final_path),
            "chapter_id": frame.get("chapter_id"),
            "target_ids": sorted(frame.get("target_ids", set())),
            "seg_ids": sorted(frame.get("seg_ids", set())),
            "reasons": sorted(frame.get("reasons", set())),
            "quality": frame["quality"],
            "scene_score": round(float(frame.get("scene_score", 0.0)), 6),
            "part_mapping_confidence": frame.get("part_mapping_confidence", "unknown"),
        })
    shutil.rmtree(raw_dir, ignore_errors=True)
    strips = generate_strips(work, chapters, records) if (args.strips and chapters) else []
    coverage = coverage_report(chapters, records)
    strip_pixels = sum(strip["pixel_area"] for strip in strips)
    baseline_pixels = 60 * 512 * 288
    # Without strips every candidate is read individually — say so in the metric
    # instead of projecting a saving that will not happen.
    projected_individual_reads = min(len(strips), len(records)) if strips else len(records)
    projected_total_pixels = strip_pixels + projected_individual_reads * 512 * 288
    payload = {
        "schema_version": 2,
        "mode": args.mode,
        "parts": parts,
        "counts": {
            "scene": len(scene_points), "raw": raw_count,
            "blank_or_seek_dropped": blank_dropped, "recovered": recovered_count,
            "dedup_dropped": dedup_dropped,
            "cap_dropped": cap_dropped, "final": len(records),
        },
        "coverage": coverage,
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
    print(f"- **Mode:** {args.mode}")
    print(f"- **Candidates:** {len(records)} (raw {raw_count}; dedup {dedup_dropped}; cap {cap_dropped})")
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
        print(
            f"- `{record['path']}` ({record['candidate_id']}, "
            f"actual_t={record['actual_t']:.3f} [{format_time(record['actual_t'])}], "
            f"chapter={record['chapter_id']}, targets={','.join(record['target_ids']) or '-'})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
