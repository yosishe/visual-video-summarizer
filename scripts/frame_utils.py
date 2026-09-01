#!/usr/bin/env python3
"""Dependency-free primitives for the visual evidence pipeline.

This module owns timeline rules, compact visual fingerprints, stable IDs, and
media metadata. Acquisition and ranking remain separate so every stage can be
tested without a model or network connection.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Iterable, Sequence


SIGNATURE_WIDTH = 64
SIGNATURE_HEIGHT = 36
SIGNATURE_PIXELS = SIGNATURE_WIDTH * SIGNATURE_HEIGHT


def finite_number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def format_time(seconds: float) -> str:
    total = max(0, int(round(finite_number(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, second = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{second:02d}" if hours else f"{minutes:02d}:{second:02d}"


def chapter_for_time(chapters: list[dict], timestamp: float) -> dict | None:
    """Resolve a timestamp using [start, end), with an inclusive final endpoint."""
    point = finite_number(timestamp, -1.0)
    for index, chapter in enumerate(chapters):
        start = finite_number(chapter.get("start"), -1.0)
        end = finite_number(chapter.get("end"), -1.0)
        if start <= point < end:
            return chapter
        if index == len(chapters) - 1 and math.isclose(point, end, abs_tol=0.05):
            return chapter
    return None


def validate_chapters(chapters: object, duration: float | None = None) -> list[dict]:
    if not isinstance(chapters, list) or not chapters:
        raise SystemExit("chapters.json must contain a non-empty array")
    seen_chapters: set[str] = set()
    seen_targets: set[str] = set()
    previous_end: float | None = None
    normalized: list[dict] = []
    for index, raw in enumerate(chapters):
        if not isinstance(raw, dict):
            raise SystemExit(f"chapter {index} must be an object")
        chapter_id = str(raw.get("chapter_id") or "").strip()
        start = finite_number(raw.get("start"), -1.0)
        end = finite_number(raw.get("end"), -1.0)
        if not chapter_id or chapter_id in seen_chapters:
            raise SystemExit(f"chapter {index} has a missing or duplicate chapter_id")
        if start < 0 or end <= start:
            raise SystemExit(f"{chapter_id}: invalid interval [{start}, {end})")
        if previous_end is not None and start < previous_end - 0.001:
            raise SystemExit(f"{chapter_id}: chapters overlap at {start:.3f}s")
        if duration and end > duration + 0.1:
            raise SystemExit(f"{chapter_id}: end {end:.3f}s exceeds media duration {duration:.3f}s")
        seen_chapters.add(chapter_id)
        previous_end = end
        targets: list[dict] = []
        for target_index, target in enumerate(raw.get("visual_targets") or []):
            if not isinstance(target, dict):
                raise SystemExit(f"{chapter_id}: visual target {target_index} must be an object")
            target_id = str(target.get("target_id") or "").strip()
            kind = str(target.get("kind") or "state")
            if not target_id or target_id in seen_targets:
                raise SystemExit(f"{chapter_id}: missing or duplicate target_id")
            if kind not in {"state", "action_result", "diagram", "slide"}:
                raise SystemExit(f"{target_id}: unsupported target kind {kind!r}")
            seen_targets.add(target_id)
            targets.append({**target, "target_id": target_id, "kind": kind})
        normalized.append({
            **raw,
            "chapter_id": chapter_id,
            "title": str(raw.get("title") or chapter_id),
            "start": start,
            "end": end,
            "needs_frames": bool(raw.get("needs_frames", bool(targets))),
            "visual_targets": targets,
        })
    return normalized


def validate_segments(payload: object) -> tuple[dict, list[dict]]:
    if not isinstance(payload, dict):
        raise SystemExit("transcript.json must be an object")
    rows = payload.get("segments")
    if not isinstance(rows, list):
        raise SystemExit("transcript.json requires segments[]")
    seen: set[str] = set()
    normalized: list[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"transcript segment {index} must be an object")
        seg_id = str(row.get("seg_id") or f"seg_{index:04d}")
        if seg_id in seen:
            raise SystemExit(f"duplicate transcript segment {seg_id}")
        start = finite_number(row.get("start"), -1.0)
        end = finite_number(row.get("end"), -1.0)
        if start < 0 or end < start:
            raise SystemExit(f"{seg_id}: invalid interval")
        seen.add(seg_id)
        normalized.append({**row, "seg_id": seg_id, "start": start, "end": end})
    return payload, normalized


def segment_ids_at(segments: Sequence[dict], timestamp: float, tolerance: float = 0.8) -> list[str]:
    point = finite_number(timestamp)
    direct = [
        str(row["seg_id"])
        for row in segments
        if finite_number(row.get("start")) <= point < finite_number(row.get("end"))
    ]
    if direct:
        return direct
    nearby = sorted(
        segments,
        key=lambda row: min(
            abs(point - finite_number(row.get("start"))),
            abs(point - finite_number(row.get("end"))),
        ),
    )
    if nearby:
        distance = min(
            abs(point - finite_number(nearby[0].get("start"))),
            abs(point - finite_number(nearby[0].get("end"))),
        )
        if distance <= tolerance:
            return [str(nearby[0]["seg_id"])]
    return []


def _edge_plane(pixels: bytes, width: int, height: int) -> bytes:
    edges = bytearray(width * height)
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            index = row + x
            horizontal = abs(pixels[index + 1] - pixels[index - 1])
            vertical = abs(pixels[index + width] - pixels[index - width])
            edges[index] = min(255, horizontal + vertical)
    return bytes(edges)


def signature_from_gray(pixels: bytes, width: int = SIGNATURE_WIDTH, height: int = SIGNATURE_HEIGHT) -> dict:
    if len(pixels) != width * height:
        raise ValueError(f"expected {width * height} grayscale bytes, received {len(pixels)}")
    edges = _edge_plane(pixels, width, height)
    mean = statistics.fmean(pixels) if pixels else 0.0
    contrast = statistics.pstdev(pixels) if len(pixels) > 1 else 0.0
    sharpness = statistics.fmean(edges) if edges else 0.0
    extreme = sum(value <= 4 or value >= 251 for value in pixels) / max(1, len(pixels))
    blank = contrast < 2.2 or (extreme > 0.995 and sharpness < 1.4)
    return {
        "pixels": pixels,
        "edges": edges,
        "digest": hashlib.sha256(pixels + edges).hexdigest(),
        "mean": round(mean, 4),
        "contrast": round(contrast, 4),
        "sharpness": round(sharpness, 4),
        "blank": blank,
    }


def visual_signature(path: Path | str) -> dict:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required")
    result = subprocess.run(
        [
            executable,
            "-hide_banner", "-loglevel", "error", "-i", str(Path(path).resolve()),
            "-frames:v", "1", "-vf",
            f"scale={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:flags=area,format=gray",
            "-f", "rawvideo", "pipe:1",
        ],
        capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) < SIGNATURE_PIXELS:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not fingerprint {path}: {detail}")
    return signature_from_gray(result.stdout[:SIGNATURE_PIXELS])


def _mean_absolute(first: bytes, second: bytes) -> float:
    if len(first) != len(second) or not first:
        return 255.0
    return sum(abs(a - b) for a, b in zip(first, second)) / len(first)


def _changed_ratio(first: bytes, second: bytes, threshold: int = 16) -> float:
    if len(first) != len(second) or not first:
        return 1.0
    return sum(abs(a - b) >= threshold for a, b in zip(first, second)) / len(first)


def _active_tiles(first: bytes, second: bytes, width: int = SIGNATURE_WIDTH, height: int = SIGNATURE_HEIGHT) -> float:
    """Preserve local edits that a global mean would hide."""
    tile_width, tile_height = 8, 6
    active = 0
    total = 0
    for top in range(0, height, tile_height):
        for left in range(0, width, tile_width):
            deltas: list[int] = []
            for y in range(top, min(top + tile_height, height)):
                base = y * width
                for x in range(left, min(left + tile_width, width)):
                    deltas.append(abs(first[base + x] - second[base + x]))
            total += 1
            if deltas and statistics.fmean(deltas) >= 10.0:
                active += 1
    return active / max(1, total)


def compare_signatures(first: dict, second: dict) -> dict:
    pixels_a = first.get("pixels", b"")
    pixels_b = second.get("pixels", b"")
    edges_a = first.get("edges", b"")
    edges_b = second.get("edges", b"")
    return {
        "luma_mad": round(_mean_absolute(pixels_a, pixels_b), 5),
        "edge_mad": round(_mean_absolute(edges_a, edges_b), 5),
        "changed_ratio": round(_changed_ratio(pixels_a, pixels_b), 6),
        "active_tile_ratio": round(_active_tiles(pixels_a, pixels_b), 6),
    }


def is_hard_duplicate(first: dict, second: dict) -> bool:
    if first.get("digest") and first.get("digest") == second.get("digest"):
        return True
    delta = compare_signatures(first, second)
    return delta["luma_mad"] < 0.35 and delta["changed_ratio"] < 0.0015


def is_near_duplicate(first: dict, second: dict) -> bool:
    delta = compare_signatures(first, second)
    return (
        delta["luma_mad"] < 2.6
        and delta["edge_mad"] < 5.0
        and delta["changed_ratio"] < 0.012
        and delta["active_tile_ratio"] < 0.025
    )


def quality_payload(signature: dict) -> dict:
    return {
        "mean_luma": signature["mean"],
        "contrast": signature["contrast"],
        "sharpness": signature["sharpness"],
        "blank": bool(signature["blank"]),
        "fingerprint": signature["digest"],
    }


def candidate_identifier(chapter_id: str, target_ids: Iterable[str], actual_t: float, digest: str) -> str:
    identity = json.dumps(
        [chapter_id, sorted(str(item) for item in target_ids), round(float(actual_t), 6), digest],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "cand_" + hashlib.sha256(identity).hexdigest()[:14]


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_media(path: Path | str) -> dict:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required")
    result = subprocess.run(
        [
            executable, "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(Path(path).resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    payload = json.loads(result.stdout or "{}")
    video = next((row for row in payload.get("streams", []) if row.get("codec_type") == "video"), {})
    rate_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        numerator, denominator = rate_text.split("/", 1)
        frame_rate = float(numerator) / max(float(denominator), 1.0)
    except (ValueError, ZeroDivisionError):
        frame_rate = 0.0
    duration = finite_number(payload.get("format", {}).get("duration"))
    if not duration:
        duration = finite_number(video.get("duration"))
    start_time = finite_number(
        video.get("start_time"), finite_number(payload.get("format", {}).get("start_time"))
    )
    return {
        "duration": duration,
        "start_time": start_time,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "frame_rate": frame_rate,
        "frame_duration": 1.0 / frame_rate if frame_rate > 0 else 0.04,
        "has_audio": any(row.get("codec_type") == "audio" for row in payload.get("streams", [])),
    }
