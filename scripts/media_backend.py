#!/usr/bin/env python3
"""FFmpeg/yt-dlp adapter with explicit source-time mapping and cache identity."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from frame_utils import (
    SIGNATURE_HEIGHT,
    SIGNATURE_PIXELS,
    SIGNATURE_WIDTH,
    finite_number,
    probe_media,
    signature_from_gray,
)


PTS_RE = re.compile(r"pts_time:([-+0-9.eE]+)")


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def merge_sections(sections: list[tuple[float, float]], duration: float, padding: float = 0.0) -> list[tuple[float, float]]:
    bounded = []
    for start, end in sections:
        left = max(0.0, finite_number(start) - padding)
        right = min(duration, finite_number(end) + padding) if duration > 0 else finite_number(end) + padding
        if right > left:
            bounded.append((left, right))
    bounded.sort()
    merged: list[list[float]] = []
    for start, end in bounded:
        if not merged or start > merged[-1][1] + 0.05:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def source_identity(source: str, sections: list[tuple[float, float]], exact: bool) -> dict:
    if is_url(source):
        identity: dict = {"kind": "url", "source": source}
    else:
        path = Path(source).expanduser().resolve()
        stat = path.stat()
        identity = {
            "kind": "file",
            "path": str(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return {**identity, "sections": sections, "exact": bool(exact), "schema": 3}


def cache_key(identity: dict) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _download_one(source: str, output_template: Path, section: tuple[float, float] | None) -> Path:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is required for URL sources")
    command = [
        executable,
        "--no-playlist",
        "--no-part",
        "-f", "bv*+ba/best",
        "--merge-output-format", "mp4",
        "-o", str(output_template),
    ]
    if section is not None:
        command.extend([
            "--download-sections", f"*{section[0]:.3f}-{section[1]:.3f}",
            "--force-keyframes-at-cuts",
        ])
    command.extend(["--", source])
    _run(command, "yt-dlp video acquisition")
    prefix = output_template.name.split("%", 1)[0]
    candidates = sorted(output_template.parent.glob(prefix + "*"))
    media = [path for path in candidates if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not media:
        raise RuntimeError(f"yt-dlp produced no media matching {output_template}")
    return media[0].resolve()


def prepare_media(
    source: str,
    work: Path,
    sections: list[tuple[float, float]],
    duration: float,
    exact: bool,
) -> tuple[list[dict], dict]:
    """Return source-mapped media parts. URL cache reuse is identity-gated."""
    download_dir = work / "download"
    download_dir.mkdir(parents=True, exist_ok=True)
    identity = source_identity(source, sections, exact)
    key = cache_key(identity)
    manifest_path = download_dir / "parts.json"
    if manifest_path.exists():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if isinstance(cached, dict) and cached.get("cache_key") == key:
            parts = cached.get("parts") or []
            if parts and all(Path(row.get("path", "")).exists() for row in parts):
                return parts, cached

    if not is_url(source):
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"media file not found: {path}")
        metadata = probe_media(path)
        parts = [{
            "part_id": "part_000",
            "path": str(path),
            "source_start": 0.0,
            "media_start": metadata["start_time"],
            "duration": metadata["duration"],
            "frame_duration": metadata["frame_duration"],
        }]
    else:
        partial = bool(sections) and duration >= 20 * 60
        selected_sections = sections if partial else []
        parts = []
        if not selected_sections:
            path = _download_one(source, download_dir / "video.%(ext)s", None)
            metadata = probe_media(path)
            parts.append({
                "part_id": "part_000", "path": str(path), "source_start": 0.0,
                "media_start": metadata["start_time"], "duration": metadata["duration"],
                "frame_duration": metadata["frame_duration"],
            })
        else:
            for index, section in enumerate(selected_sections):
                path = _download_one(source, download_dir / f"part_{index:03d}.%(ext)s", section)
                metadata = probe_media(path)
                requested_duration = section[1] - section[0]
                if exact and abs(metadata["duration"] - requested_duration) > max(0.4, metadata["frame_duration"] * 4):
                    raise RuntimeError(
                        f"section {section} has unreliable duration mapping: decoded {metadata['duration']:.3f}s"
                    )
                parts.append({
                    "part_id": f"part_{index:03d}", "path": str(path),
                    "source_start": section[0], "media_start": metadata["start_time"],
                    "duration": metadata["duration"], "frame_duration": metadata["frame_duration"],
                })

    manifest = {"schema_version": 3, "cache_key": key, "identity": identity, "parts": parts}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return parts, manifest


def load_parts(work: Path) -> list[dict]:
    path = work / "download" / "parts.json"
    if not path.exists():
        raise RuntimeError(f"missing media manifest: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 3:
        raise RuntimeError("media manifest is not an independent-engine schema v3 manifest")
    parts = payload.get("parts") or []
    if not parts:
        raise RuntimeError("media manifest has no parts")
    return parts


def part_for(parts: list[dict], timestamp: float) -> dict | None:
    point = finite_number(timestamp, -1.0)
    matches = [
        part for part in parts
        if finite_number(part.get("source_start")) <= point
        <= finite_number(part.get("source_start")) + finite_number(part.get("duration")) + 0.05
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda part: abs(
            point - (finite_number(part.get("source_start")) + finite_number(part.get("duration")) / 2)
        ),
    )


def _local_time(part: dict, source_time: float) -> float:
    return finite_number(part.get("media_start")) + source_time - finite_number(part.get("source_start"))


def nearest_frame_time(part: dict, source_time: float) -> float:
    """Ask ffprobe for decoded frame timestamps around the request, then choose the nearest."""
    local_request = max(0.0, _local_time(part, source_time))
    margin = max(0.5, finite_number(part.get("frame_duration"), 0.04) * 8)
    interval_start = max(0.0, local_request - margin)
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required")
    result = _run([
        executable, "-v", "error", "-select_streams", "v:0",
        "-read_intervals", f"{interval_start:.6f}%+{margin * 2 + 0.5:.6f}",
        "-show_entries", "frame=best_effort_timestamp_time",
        "-of", "json", str(Path(part["path"]).resolve()),
    ], "frame timestamp probe")
    payload = json.loads(result.stdout or "{}")
    decoded = [
        finite_number(row.get("best_effort_timestamp_time"), math.nan)
        for row in payload.get("frames", [])
    ]
    decoded = [value for value in decoded if math.isfinite(value)]
    if not decoded:
        raise RuntimeError(f"no decoded frame timestamps near {source_time:.3f}s")
    local_actual = min(decoded, key=lambda value: abs(value - local_request))
    return finite_number(part.get("source_start")) + local_actual - finite_number(part.get("media_start"))


def extract_frame(parts: list[dict], source_time: float, output: Path, width: int = 512) -> tuple[float, dict]:
    part = part_for(parts, source_time)
    if part is None:
        raise RuntimeError(f"timestamp {source_time:.3f}s is outside acquired media")
    local_request = max(0.0, _local_time(part, source_time))
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required")
    scale = f"scale=w='min({max(16, min(width, 1998))},iw)':h=-2:flags=lanczos"
    result = subprocess.run([
        executable, "-hide_banner", "-loglevel", "info", "-y", "-copyts",
        "-ss", f"{local_request:.9f}", "-i", str(Path(part["path"]).resolve()),
        "-frames:v", "1", "-vf", f"showinfo,{scale}", "-q:v", "2", str(output),
    ], capture_output=True, text=True)
    matches = PTS_RE.findall(result.stderr)
    if result.returncode != 0 or not matches:
        raise RuntimeError(f"frame extraction failed: {result.stderr.strip()}")
    media_actual = finite_number(matches[-1], math.nan)
    if not math.isfinite(media_actual):
        raise RuntimeError("frame extraction returned no finite decoded timestamp")
    actual_source = (
        finite_number(part.get("source_start"))
        + media_actual
        - finite_number(part.get("media_start"))
    )
    tolerance = max(0.1, finite_number(part.get("frame_duration"), 0.04) * 2.5)
    if abs(actual_source - source_time) > tolerance:
        raise RuntimeError(
            f"decoded timestamp drift: requested {source_time:.6f}s, actual {actual_source:.6f}s, "
            f"tolerance {tolerance:.6f}s"
        )
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"frame extraction produced no file: {output}")
    return round(actual_source, 6), part


def scan_window(parts: list[dict], start: float, end: float, rate: float) -> list[dict]:
    """Decode only tiny grayscale frames for a bounded source-time window."""
    if end <= start or rate <= 0:
        return []
    records: list[dict] = []
    frame_size = SIGNATURE_PIXELS
    for part in parts:
        part_start = finite_number(part.get("source_start"))
        part_end = part_start + finite_number(part.get("duration"))
        piece_start = max(start, part_start)
        piece_end = min(end, part_end)
        if piece_end <= piece_start:
            continue
        local_start = max(0.0, _local_time(part, piece_start))
        executable = shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("ffmpeg is required")
        result = subprocess.run([
            executable, "-hide_banner", "-loglevel", "error",
            "-ss", f"{local_start:.6f}", "-i", str(Path(part["path"]).resolve()),
            "-t", f"{piece_end - piece_start:.6f}",
            "-vf", f"fps={rate:.6f},scale={SIGNATURE_WIDTH}:{SIGNATURE_HEIGHT}:flags=area,format=gray",
            "-f", "rawvideo", "pipe:1",
        ], capture_output=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"low-resolution window scan failed: {detail}")
        count = len(result.stdout) // frame_size
        for index in range(count):
            pixels = result.stdout[index * frame_size:(index + 1) * frame_size]
            timestamp = min(piece_end - 0.0001, piece_start + index / rate)
            records.append({
                "t": round(timestamp, 6),
                "signature": signature_from_gray(pixels),
                "part_id": part.get("part_id"),
            })
    records.sort(key=lambda row: row["t"])
    return records


def make_strip(images: list[Path], output: Path, cell_width: int = 256) -> int:
    if not images:
        return 0
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required")
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, image in enumerate(images):
        inputs.extend(["-i", str(image.resolve())])
        label = f"v{index}"
        filters.append(f"[{index}:v]scale={cell_width}:-2:flags=area[{label}]")
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"hstack=inputs={len(images)}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([
        executable, "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(output),
    ], "temporal strip rendering")
    metadata = probe_media(output)
    return int(metadata["width"] * metadata["height"])
