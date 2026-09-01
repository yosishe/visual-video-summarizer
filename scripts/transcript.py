#!/usr/bin/env python3
"""Transcript acquisition for /summarize-video.

Order of preference (cheapest first):
1. Native captions via `yt-dlp --skip-download` (no video download at all).
2. Whisper API fallback (Groq preferred, OpenAI fallback) on audio-only
   download / local file audio. Keys are shared with the /watch skill's
   config at ~/.config/watch/.env.

Emits stable segment records {seg_id, start, end, text} so frames can
reference exact transcript spans, plus a readable transcript.txt.

VTT parsing/dedup and download patterns adapted from
bradautomates/claude-video (MIT).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from whisper import load_api_key, transcribe_video  # noqa: E402

TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue
        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1
        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = TAG_RE.sub("", lines[i]).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1
        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1
    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def _pick_subtitle(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    preferred = [
        c for c in candidates
        if any(marker in c.name for marker in (".en.", ".en-US.", ".en-GB.", ".en-orig."))
    ]
    return preferred[0] if preferred else candidates[0]


def _read_info(info_path: Path, url: str) -> dict:
    info: dict = {"url": url}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {
                "id": raw.get("id"),
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception as exc:
            print(f"[vsum] info.json parse failed: {exc}", file=sys.stderr)
    return info


def fetch_captions(url: str, out_dir: Path, langs: str) -> dict:
    """Fetch metadata + best VTT captions WITHOUT downloading the video."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", langs,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", str(out_dir / "video.%(ext)s"),
        "--",
        url,
    ]
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    subtitle = _pick_subtitle(out_dir)
    return {
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": _read_info(out_dir / "video.info.json", url),
    }


def download_audio(url: str, out_dir: Path) -> Path:
    """Audio-only download for the Whisper fallback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "ba/bestaudio",
        "--no-playlist",
        "--ignore-errors",
        "-o", str(out_dir / "audio_src.%(ext)s"),
        "--",
        url,
    ]
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    for candidate in sorted(out_dir.glob("audio_src.*")):
        if candidate.suffix.lower() not in (".json", ".vtt"):
            return candidate
    raise SystemExit(f"yt-dlp did not produce an audio file in {out_dir}")


def probe(path: str) -> dict:
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="transcript",
        description="Fetch a timestamped transcript (captions first, Whisper fallback).",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument("--work", default=None, help="Working directory (default: new tmp dir)")
    ap.add_argument("--langs", default="en.*", help="yt-dlp --sub-langs pattern (default en.*)")
    ap.add_argument("--no-whisper", action="store_true", help="Disable Whisper fallback")
    ap.add_argument("--whisper", choices=["groq", "openai"], default=None,
                    help="Force a Whisper backend")
    args = ap.parse_args()

    work = Path(args.work).expanduser().resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="vsum-"))
    dl_dir = work / "download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    print(f"[vsum] working dir: {work}", file=sys.stderr)

    segments: list[dict] = []
    source_kind: str | None = None
    info: dict = {}
    url_source = is_url(args.source)

    if url_source:
        print("[vsum] fetching metadata/captions via yt-dlp (no video download)…", file=sys.stderr)
        fetched = fetch_captions(args.source, dl_dir, args.langs)
        info = fetched["info"]
        if fetched["subtitle_path"]:
            try:
                segments = parse_vtt(fetched["subtitle_path"])
                source_kind = "captions"
            except Exception as exc:
                print(f"[vsum] subtitle parse failed: {exc}", file=sys.stderr)
        duration = float(info.get("duration") or 0)
    else:
        local = Path(args.source).expanduser().resolve()
        if not local.exists():
            raise SystemExit(f"File not found: {local}")
        meta = probe(str(local))
        duration = meta["duration"]
        info = {"id": local.stem, "title": local.name, "url": str(local)}
        if not meta["has_audio"]:
            print("[vsum] no audio stream — no transcript possible", file=sys.stderr)
            args.no_whisper = True

    if not segments and not args.no_whisper:
        backend, api_key = load_api_key(args.whisper)
        if backend and api_key:
            media = download_audio(args.source, dl_dir) if url_source else Path(args.source).expanduser().resolve()
            try:
                segments, used = transcribe_video(
                    str(media), work / "audio.mp3", backend=backend, api_key=api_key)
                source_kind = f"whisper ({used})"
            except SystemExit as exc:
                print(f"[vsum] whisper fallback failed: {exc}", file=sys.stderr)
        else:
            print(
                "[vsum] no captions and no Whisper API key (GROQ_API_KEY / OPENAI_API_KEY in "
                "env or ~/.config/watch/.env) — transcript unavailable",
                file=sys.stderr,
            )

    records = [
        {
            "seg_id": f"seg_{i:04d}",
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        for i, seg in enumerate(segments)
    ]

    payload = {
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
    (work / "transcript.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    txt_lines = [
        f"{r['seg_id']} [{format_time(r['start'])}-{format_time(r['end'])}] {r['text']}"
        for r in records
    ]
    (work / "transcript.txt").write_text("\n".join(txt_lines), encoding="utf-8")

    # --- report ---
    print()
    print("# transcript report")
    print()
    print(f"- **Work dir:** `{work}`")
    print(f"- **Source:** {args.source}")
    if info.get("title"):
        print(f"- **Title:** {info['title']}")
    print(f"- **Duration:** {format_time(duration)} ({duration:.1f}s)")
    print(f"- **Video id:** {payload['video']['id']}")
    if records:
        print(f"- **Segments:** {len(records)} (via {source_kind})")
        print(f"- **Files:** `{work / 'transcript.json'}`, `{work / 'transcript.txt'}`")
        print()
        print("## Transcript")
        print()
        print("```")
        print("\n".join(txt_lines))
        print("```")
    else:
        print("- **Transcript:** none available — chapterize from visuals only, or fix Whisper setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
