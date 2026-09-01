#!/usr/bin/env python3
"""Acquire timestamps and text before any video-frame download occurs."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import format_time, probe_media  # noqa: E402
from media_backend import is_url  # noqa: E402
from speech_to_text import load_api_key, transcribe_media  # noqa: E402


TIMING_RE = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+"
    r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]*>")
WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _seconds(hours: str | None, minutes: str, seconds: str, milliseconds: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _clean_caption(lines: list[str]) -> str:
    fragments = []
    for line in lines:
        value = html.unescape(TAG_RE.sub("", line)).replace("\u200b", " ").strip()
        if value:
            fragments.append(value)
    return " ".join(fragments).strip()


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_RE.finditer(text)]


def _remove_rolling_overlap(previous: str, current: str, minimum: int = 3) -> str:
    previous_tokens = _tokens(previous)
    current_matches = list(WORD_RE.finditer(current))
    current_tokens = [match.group(0).casefold() for match in current_matches]
    for width in range(min(len(previous_tokens), len(current_tokens)), minimum - 1, -1):
        if previous_tokens[-width:] == current_tokens[:width]:
            if width == len(current_matches):
                return ""
            return current[current_matches[width].start():].strip()
    return current


def compact_captions(rows: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for raw in sorted(rows, key=lambda row: (row["start"], row["end"])):
        row = {**raw, "text": str(raw.get("text") or "").strip()}
        if not row["text"]:
            continue
        if compact and _tokens(row["text"]) == _tokens(compact[-1]["text"]):
            compact[-1]["end"] = max(compact[-1]["end"], row["end"])
            continue
        if compact:
            shortened = _remove_rolling_overlap(compact[-1]["text"], row["text"])
            if not shortened:
                compact[-1]["end"] = max(compact[-1]["end"], row["end"])
                continue
            row["text"] = shortened
        compact.append(row)
    return compact


def parse_vtt(path: Path | str) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict] = []
    index = 0
    while index < len(lines):
        match = TIMING_RE.search(lines[index])
        if not match:
            index += 1
            continue
        groups = match.groups()
        start = _seconds(*groups[:4])
        end = _seconds(*groups[4:])
        index += 1
        content = []
        while index < len(lines) and lines[index].strip():
            content.append(lines[index])
            index += 1
        text = _clean_caption(content)
        if text and end >= start:
            rows.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        index += 1
    return compact_captions(rows)


def _yt_dlp() -> str:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is required for URL sources")
    return executable


def _run(command: list[str], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed: {result.stderr.strip()}")


def fetch_text_metadata(url: str, directory: Path, languages: str) -> tuple[Path | None, dict]:
    """Request metadata and captions only; this command never requests video."""
    directory.mkdir(parents=True, exist_ok=True)
    _run([
        _yt_dlp(), "--skip-download", "--no-playlist", "--write-info-json",
        "--write-subs", "--write-auto-subs", "--sub-langs", languages,
        "--sub-format", "vtt", "-o", str(directory / "source.%(ext)s"), "--", url,
    ], "caption acquisition")
    captions = sorted(directory.glob("source*.vtt"))
    preferred = [
        path for path in captions
        if any(marker in path.name.casefold() for marker in (".en.", ".en-us.", ".en-gb."))
    ]
    info_path = directory / "source.info.json"
    info = {}
    if info_path.is_file():
        raw = json.loads(info_path.read_text(encoding="utf-8"))
        info = {
            "id": raw.get("id"),
            "title": raw.get("title"),
            "uploader": raw.get("uploader") or raw.get("channel"),
            "duration": raw.get("duration"),
            "url": raw.get("webpage_url") or url,
        }
    return (preferred[0] if preferred else (captions[0] if captions else None)), info


def download_audio_source(url: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    _run([
        _yt_dlp(), "--no-playlist", "-f", "ba/bestaudio",
        "-o", str(directory / "speech-source.%(ext)s"), "--", url,
    ], "audio-only acquisition")
    candidates = [
        path for path in sorted(directory.glob("speech-source.*"))
        if path.suffix.lower() not in {".json", ".vtt", ".part"}
    ]
    if not candidates:
        raise RuntimeError("audio-only acquisition produced no media")
    return candidates[0]


def _records(rows: list[dict]) -> list[dict]:
    return [
        {
            "seg_id": f"seg_{index:04d}",
            "start": round(float(row["start"]), 3),
            "end": round(float(row["end"]), 3),
            "text": str(row["text"]).strip(),
        }
        for index, row in enumerate(rows)
        if str(row.get("text") or "").strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire a timestamped transcript before video frames")
    parser.add_argument("source")
    parser.add_argument("--work")
    parser.add_argument("--langs", default="en.*")
    parser.add_argument("--no-whisper", action="store_true", help="Disable speech-to-text fallback")
    parser.add_argument("--whisper", choices=["groq", "openai"], help="Choose speech backend")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="visual-summary-")
    )
    work.mkdir(parents=True, exist_ok=True)
    download_dir = work / "download"
    url_source = is_url(args.source)
    rows: list[dict] = []
    source_kind: str | None = None
    info: dict = {}

    if url_source:
        print("[vsum] requesting captions and metadata without video download", file=sys.stderr)
        try:
            caption_path, info = fetch_text_metadata(args.source, download_dir, args.langs)
            if caption_path:
                rows = parse_vtt(caption_path)
                source_kind = "captions"
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[vsum] caption acquisition unavailable: {exc}", file=sys.stderr)
        duration = float(info.get("duration") or 0)
    else:
        media = Path(args.source).expanduser().resolve()
        if not media.is_file():
            raise SystemExit(f"media file not found: {media}")
        metadata = probe_media(media)
        duration = metadata["duration"]
        info = {"id": media.stem, "title": media.name, "url": str(media)}
        if not metadata["has_audio"]:
            args.no_whisper = True

    if not rows and not args.no_whisper:
        backend, key = load_api_key(args.whisper)
        if backend and key:
            try:
                media = download_audio_source(args.source, download_dir) if url_source else Path(args.source)
                rows, backend_used = transcribe_media(media, work, backend, key)
                source_kind = f"speech-to-text:{backend_used}"
            except RuntimeError as exc:
                print(f"[vsum] speech-to-text fallback failed: {exc}", file=sys.stderr)
        else:
            print("[vsum] no captions and no configured speech-to-text key", file=sys.stderr)

    records = _records(rows)
    payload = {
        "schema_version": 3,
        "source": source_kind,
        "video": {
            "id": info.get("id") or "video",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "url": info.get("url") or args.source,
            "duration": duration,
            "is_url": url_source,
        },
        "segments": records,
    }
    transcript_path = work / "transcript.json"
    transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        f"{row['seg_id']} [{format_time(row['start'])}-{format_time(row['end'])}] {row['text']}"
        for row in records
    ]
    (work / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n# transcript report\n")
    print(f"- **Work dir:** `{work}`")
    print(f"- **Duration:** {format_time(duration)} ({duration:.3f}s)")
    print(f"- **Segments:** {len(records)}" + (f" via {source_kind}" if source_kind else ""))
    print(f"- **Manifest:** `{transcript_path}`")
    if not records:
        print("- **Status:** no transcript available; use visual-only chaptering or configure speech-to-text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
