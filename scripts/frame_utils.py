#!/usr/bin/env python3
"""Shared timestamp, chapter and visual-signature helpers.

The module deliberately depends only on Python's standard library and the
ffmpeg/ffprobe binaries already required by the skill.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

SIGNATURE_WIDTH = 64
SIGNATURE_HEIGHT = 36
PIXEL_CHANGE_THRESHOLD = 12
HARD_DUP_LUMA = 1.5
HARD_DUP_EDGE = 2.0
HARD_DUP_CHANGED = 0.006
NEAR_DUP_LUMA = 3.0
NEAR_DUP_EDGE = 4.0
NEAR_DUP_CHANGED = 0.025


def parse_time(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise ValueError(f"Cannot parse time value: {value!r}")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def probe_media(path: str | Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    return {
        "duration": _number(fmt.get("duration"), _number(video.get("duration"), 0.0)),
        "start_time": _number(fmt.get("start_time"), _number(video.get("start_time"), 0.0)),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "frame_duration": (1.0 / fps) if fps > 0 else 0.04,
    }


def _parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def chapter_for_time(chapters: list[dict], timestamp: float) -> dict | None:
    """Return the chapter owning timestamp using [start,end) intervals.

    Only the final chapter includes its end. This removes boundary ambiguity
    while still allowing a final-frame candidate at the video duration.
    """
    for index, chapter in enumerate(chapters):
        start = float(chapter["start"])
        end = float(chapter["end"])
        if start <= timestamp < end:
            return chapter
        if index == len(chapters) - 1 and math.isclose(timestamp, end, abs_tol=0.05):
            return chapter
    return None


def segment_ids_for_time(segments: list[dict], timestamp: float, tolerance: float = 1.0) -> list[str]:
    direct = [
        str(seg["seg_id"])
        for seg in segments
        if float(seg["start"]) <= timestamp < float(seg["end"])
    ]
    if direct:
        return direct
    nearby = sorted(
        segments,
        key=lambda seg: min(
            abs(timestamp - float(seg["start"])),
            abs(timestamp - float(seg["end"])),
        ),
    )
    if nearby:
        distance = min(
            abs(timestamp - float(nearby[0]["start"])),
            abs(timestamp - float(nearby[0]["end"])),
        )
        if distance <= tolerance:
            return [str(nearby[0]["seg_id"])]
    return []


def visual_signature(path: str | Path) -> dict:
    """Return a compact luma+edge signature and quality metrics."""
    vf = (
        f"scale={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray"
    )
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-",
        ],
        capture_output=True,
    )
    pixels = result.stdout
    expected = SIGNATURE_WIDTH * SIGNATURE_HEIGHT
    if result.returncode != 0 or len(pixels) != expected:
        return {
            "pixels": b"", "edges": b"", "digest": "", "mean": 0.0,
            "contrast": 0.0, "sharpness": 0.0, "blank": False,
        }
    values = list(pixels)
    mean = sum(values) / expected
    variance = sum((value - mean) ** 2 for value in values) / expected
    contrast = variance ** 0.5
    edges = _edge_map(values, SIGNATURE_WIDTH, SIGNATURE_HEIGHT)
    sharpness = sum(edges) / len(edges) if edges else 0.0
    # A frame carries no information when it is (near-)uniform at any luma —
    # not only black or white — or when almost every pixel is clipped and no
    # edges survive (a white flash, a fade). Uniform mid-gray transitions used
    # to pass the old black/white-only rule.
    extreme = sum(value <= 4 or value >= 251 for value in values) / expected
    blank = contrast < 2.2 or (extreme > 0.995 and sharpness < 1.4)
    return {
        "pixels": pixels,
        "edges": bytes(min(255, round(edge)) for edge in edges),
        "digest": hashlib.sha256(pixels).hexdigest(),
        "mean": round(mean, 3),
        "contrast": round(contrast, 3),
        "sharpness": round(sharpness, 3),
        "blank": blank,
    }


def _edge_map(values: list[int], width: int, height: int) -> list[float]:
    edges: list[float] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            right = values[index + 1] if x + 1 < width else values[index]
            below = values[index + width] if y + 1 < height else values[index]
            edges.append((abs(values[index] - right) + abs(values[index] - below)) / 2)
    return edges


def compare_signatures(first: dict, second: dict) -> dict:
    a = first.get("pixels", b"")
    b = second.get("pixels", b"")
    ae = first.get("edges", b"")
    be = second.get("edges", b"")
    if not a or not b or len(a) != len(b):
        return {"luma_mad": float("inf"), "edge_mad": float("inf"), "changed_ratio": 1.0}
    deltas = [abs(x - y) for x, y in zip(a, b)]
    edge_delta = (
        sum(abs(x - y) for x, y in zip(ae, be)) / len(ae)
        if ae and be and len(ae) == len(be)
        else float("inf")
    )
    return {
        "luma_mad": sum(deltas) / len(deltas),
        "edge_mad": edge_delta,
        "changed_ratio": sum(delta > PIXEL_CHANGE_THRESHOLD for delta in deltas) / len(deltas),
    }


def is_near_duplicate(first: dict, second: dict) -> bool:
    delta = compare_signatures(first, second)
    return (
        delta["luma_mad"] <= NEAR_DUP_LUMA
        and delta["edge_mad"] <= NEAR_DUP_EDGE
        and delta["changed_ratio"] <= NEAR_DUP_CHANGED
    )


def is_hard_duplicate(first: dict, second: dict) -> bool:
    delta = compare_signatures(first, second)
    return (
        delta["luma_mad"] <= HARD_DUP_LUMA
        and delta["edge_mad"] <= HARD_DUP_EDGE
        and delta["changed_ratio"] <= HARD_DUP_CHANGED
    )


def public_quality(signature: dict) -> dict:
    return {
        "mean_luma": signature.get("mean", 0.0),
        "contrast": signature.get("contrast", 0.0),
        "sharpness": signature.get("sharpness", 0.0),
        "blank": bool(signature.get("blank", False)),
        "fingerprint": signature.get("digest", ""),
    }
