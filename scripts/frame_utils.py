#!/usr/bin/env python3
"""Shared timestamp, chapter and visual-signature helpers.

The module deliberately depends only on Python's standard library and the
ffmpeg/ffprobe binaries already required by the skill.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
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
# blurdetect reports an edge-width estimate per frame: higher = blurrier.
BLUR_BLOCK_PCT = 80
# Face demotion (adapted from ConflictHQ/PlanOpticon): a webcam-sized face or
# two faces means a "people frame" — the presenter, not the content.
FACE_AREA_RATIO = 0.03
FACE_MIN_COUNT = 2
OCR_LANG_RE = re.compile(r"^[a-z_]+(\+[a-z_]+)*$")
OCR_CHAR_RE = re.compile(r"[A-Za-z0-9א-ת]")
METADATA_PTS_RE = re.compile(r"pts_time:([-0-9.]+)")


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


SIGNATURE_FILTER = (
    f"scale={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray"
)


def _empty_signature() -> dict:
    return {
        "pixels": b"", "edges": b"", "digest": "", "mean": 0.0,
        "contrast": 0.0, "sharpness": 0.0, "blank": False,
    }


def visual_signature(path: str | Path) -> dict:
    """Return a compact luma+edge signature and quality metrics."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", SIGNATURE_FILTER, "-frames:v", "1", "-f", "rawvideo", "-",
        ],
        capture_output=True,
    )
    pixels = result.stdout
    expected = SIGNATURE_WIDTH * SIGNATURE_HEIGHT
    if result.returncode != 0 or len(pixels) != expected:
        return _empty_signature()
    return signature_from_pixels(pixels)


def signature_from_pixels(pixels: bytes) -> dict:
    """Build the signature dict from one decoded 64×36 gray frame."""
    expected = SIGNATURE_WIDTH * SIGNATURE_HEIGHT
    if len(pixels) != expected:
        return _empty_signature()
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


def public_quality(
    signature: dict,
    *,
    faces: dict | str | None = None,
    text_chars: int | None = None,
) -> dict:
    quality = {
        "mean_luma": signature.get("mean", 0.0),
        "contrast": signature.get("contrast", 0.0),
        "sharpness": signature.get("sharpness", 0.0),
        "blank": bool(signature.get("blank", False)),
        "fingerprint": signature.get("digest", ""),
    }
    if faces is not None:
        quality["faces"] = faces
    if text_chars is not None:
        quality["text_chars"] = int(text_chars)
    return quality


# --- metadata series (blurdetect / scene_score) -------------------------------

def parse_metadata_series(stderr: str, key: str) -> list[tuple[float, float]]:
    """Parse `metadata=print` output into (pts_time, value) pairs for one key.

    ffmpeg prints a `frame:N pts:… pts_time:T` line followed by one
    `lavfi.<key>=<value>` line per frame; the pair is joined positionally.
    """
    series: list[tuple[float, float]] = []
    # `nan` is a legal value (blurdetect on an edge-free frame) and must keep
    # its slot: rows are joined positionally with the decoded frames.
    key_re = re.compile(r"lavfi\." + re.escape(key) + r"=(\S+)")
    current: float | None = None
    for line in stderr.splitlines():
        pts = METADATA_PTS_RE.search(line)
        if pts:
            try:
                current = float(pts.group(1))
            except ValueError:
                current = None
            continue
        match = key_re.search(line)
        if match and current is not None:
            try:
                value = float(match.group(1))
            except ValueError:
                value = float("nan")
            series.append((current, value))
            current = None
    return series


def blur_signature_series(
    path: str | Path, media_start: float, duration: float
) -> list[dict]:
    """One ffmpeg pass over [media_start, media_start+duration]: per frame, the
    blurdetect edge-width (stderr) and the 64×36 gray signature (stdout).

    Returned `t` values are media pts; callers map them to absolute time.
    """
    if duration <= 0:
        return []
    vf = (
        f"blurdetect=block_pct={BLUR_BLOCK_PCT},metadata=print:key=lavfi.blur,"
        f"{SIGNATURE_FILTER}"
    )
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-copyts",
            "-ss", f"{media_start:.3f}", "-t", f"{duration:.3f}", "-i", str(path),
            "-vf", vf, "-an", "-f", "rawvideo", "-",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    blur = parse_metadata_series(result.stderr.decode("utf-8", "replace"), "blur")
    frame_bytes = SIGNATURE_WIDTH * SIGNATURE_HEIGHT
    frames = len(result.stdout) // frame_bytes
    count = min(frames, len(blur))
    series: list[dict] = []
    for index in range(count):
        chunk = result.stdout[index * frame_bytes:(index + 1) * frame_bytes]
        series.append({
            "t": blur[index][0],
            "blur": blur[index][1],
            "signature": signature_from_pixels(chunk),
        })
    return series


def choose_refined_frame(
    candidate_signature: dict,
    series: list[dict],
    t0: float,
    *,
    min_gain: float = 0.10,
) -> dict:
    """Pick the sharpest frame that is still the picture the model triaged.

    Eligibility is exactly the predicate grab.py already trusts —
    `is_near_duplicate` against the triaged candidate — restricted to the
    contiguous run of eligible frames around `t0`. Within that run the lowest
    blurdetect value wins, but only if it beats the baseline by `min_gain`
    (hysteresis: never move for noise). Pure function; `series` rows carry
    absolute `t`, `blur`, `signature`.
    """
    total = len(series)
    if not series:
        return {"t": t0, "applied": False, "reason": "empty-series", "eligible": 0, "total": 0}
    index0 = min(range(total), key=lambda index: abs(series[index]["t"] - t0))
    eligible = [
        math.isfinite(row["blur"]) and row["blur"] > 0
        and is_near_duplicate(candidate_signature, row["signature"])
        for row in series
    ]
    baseline = series[index0]
    if not eligible[index0]:
        return {
            "t": t0, "applied": False, "reason": "anchor-not-duplicate",
            "blur_before": baseline["blur"], "blur_after": baseline["blur"],
            "eligible": 0, "total": total,
        }
    lo = index0
    while lo > 0 and eligible[lo - 1]:
        lo -= 1
    hi = index0
    while hi + 1 < total and eligible[hi + 1]:
        hi += 1
    run = series[lo:hi + 1]
    best = min(run, key=lambda row: (row["blur"], abs(row["t"] - t0)))
    applied = best is not baseline and best["blur"] <= baseline["blur"] * (1.0 - min_gain)
    chosen = best if applied else baseline
    return {
        "t": round(chosen["t"], 3),
        "applied": applied,
        "blur_before": round(baseline["blur"], 4),
        "blur_after": round(chosen["blur"], 4),
        "delta_s": round(chosen["t"] - t0, 3),
        "eligible": len(run),
        "total": total,
    }


# --- optional signals for the high tier ---------------------------------------

def faces_available() -> bool:
    """True when OpenCV can be imported (the face signal is optional)."""
    try:
        import cv2  # type: ignore  # noqa: F401 — optional, lazy
    except Exception:
        return False
    return True


def detect_faces(path: str | Path) -> dict | None:
    """Haar-cascade face count and area ratio via OpenCV, when it is importable.

    Never a hard dependency: returns None ("unavailable") without cv2. Faces
    smaller than 7% of the frame height are sidebar thumbnails, not webcams.
    """
    try:
        import cv2  # type: ignore  # noqa: WPS433 — optional, lazy
    except Exception:  # ImportError or a broken install
        return None
    image = cv2.imread(str(path))
    if image is None:
        return None
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    minimum = max(24, int(0.07 * height))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(minimum, minimum))
    rows = list(faces) if len(faces) else []
    area = sum(int(w) * int(h) for (_, _, w, h) in rows)
    ratio = area / float(width * height) if width and height else 0.0
    return {
        "count": len(rows),
        "area_ratio": round(ratio, 4),
        "people_frame": ratio >= FACE_AREA_RATIO or len(rows) >= FACE_MIN_COUNT,
    }


def ocr_text_density(path: str | Path, lang: str = "eng+heb") -> int | None:
    """Count alphanumeric characters ffmpeg's `ocr` filter (tesseract) reads.

    A ranking signal for "how complete is this slide" — never a text source.
    Returns None when the filter or its language data is unavailable.
    """
    if not OCR_LANG_RE.fullmatch(lang):
        raise ValueError(f"bad OCR language spec: {lang!r}")
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-frames:v", "1", "-vf", f"ocr=language={lang},metadata=print:key=lavfi.ocr.text",
            "-an", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    marker = "lavfi.ocr.text="
    start = result.stderr.find(marker)
    if start < 0:
        return None
    text = result.stderr[start + len(marker):]
    # tesseract output may span lines; it ends at the next filter log line.
    stop = text.find("[Parsed_")
    if stop >= 0:
        text = text[:stop]
    return len(OCR_CHAR_RE.findall(text))
