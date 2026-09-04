#!/usr/bin/env python3
"""Transcript acquisition for /summarize-video.

Order of preference (cheapest first):
1. Native captions via `yt-dlp --skip-download` (no video download at all),
   choosing the track by *provenance*, not by filename: a manual track in the
   video's own language, then a manual track in a wanted language, then the
   original-language ASR track (`<lang>-orig`), then any untranslated ASR
   track. Machine-translated tracks (their URL carries `tlang=`) are never
   used — the summary's language is produced by the model, not by YouTube MT.
   YouTube keys Hebrew as `iw`; it is reported as `he`.
2. Whisper API fallback (Groq preferred, OpenAI fallback) on audio-only
   download / local file audio, with the source language passed when known.

Emits stable segment records {seg_id, start, end, text} so frames can
reference exact transcript spans, plus a readable transcript.txt, the
transcript `language` and a `source_detail` record.

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

from whisper import DETECTED_LANGUAGE, load_api_key, transcribe_video  # noqa: E402

TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
EXIT_NO_TRANSCRIPT = 6
DEFAULT_WANTED = ("he", "en")
LEGACY_LANG_CODES = {"iw": "he", "ji": "yi", "in": "id"}


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


# Geresh/gershayim (׳ ״) are word-internal in Hebrew: צ׳אט, ת״א.
WORD_RE = re.compile(r"[\w'׳״]+")


def _strip_overlap(prev_text: str, text: str, min_words: int = 3) -> str:
    """YouTube auto-subs interleave rolling halves: segment N+1 opens with the
    tail of segment N ("...hundreds of different AI agent" / "hundreds of
    different AI agent workflows, mostly..."). Strip the longest word-level
    overlap (>= min_words) so every phrase appears once and the transcript
    reads linearly — roughly halving its token cost.

    Words are compared case-folded and without punctuation ("Claw," == "claw")
    because the two halves are often re-punctuated; the surviving text is
    sliced from the original string so its own punctuation is kept.
    """
    prev_tokens = [m.group(0).casefold() for m in WORD_RE.finditer(prev_text)]
    matches = list(WORD_RE.finditer(text))
    tokens = [m.group(0).casefold() for m in matches]
    for k in range(min(len(prev_tokens), len(tokens)), min_words - 1, -1):
        if prev_tokens[-k:] == tokens[:k]:
            if k == len(matches):
                return ""
            return text[matches[k].start():].strip()
    return text


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
        if out:
            stripped = _strip_overlap(out[-1]["text"], seg["text"])
            if not stripped:
                out[-1]["end"] = seg["end"]
                continue
            if stripped != seg["text"]:
                seg = {**seg, "text": stripped}
        out.append(seg)
    return out


# --- caption track selection ------------------------------------------------

def normalize_lang(code: str | None) -> str | None:
    """BCP-47-ish base language: `en-US` → `en`, YouTube's legacy `iw` → `he`."""
    if not code:
        return None
    base = str(code).split("-")[0].lower()
    return LEGACY_LANG_CODES.get(base, base)


def _is_translated(entries: list[dict]) -> bool:
    return any("tlang=" in str(entry.get("url") or "") for entry in entries or [])


def rank_caption_tracks(info: dict, wanted: tuple[str, ...] = DEFAULT_WANTED) -> list[dict]:
    """Order the video's caption tracks by trustworthiness.

    0 manual, original language · 1 manual, a wanted language (a human
    translation) · 2 ASR original (`xx-orig`, or `xx` == original language)
    · 3 ASR in a wanted language, untranslated · 4 any other manual · 5 any
    other untranslated ASR. Translated tracks (`tlang=`) are dropped.
    """
    orig = normalize_lang(info.get("language"))
    wanted_norm = [normalize_lang(w) for w in wanted]
    rows: list[dict] = []
    for kind, table in (("manual", info.get("subtitles") or {}), ("auto", info.get("automatic_captions") or {})):
        for key, entries in table.items():
            if not isinstance(entries, list) or _is_translated(entries):
                continue
            key = str(key)
            is_orig_suffix = key.endswith("-orig")
            base = normalize_lang(key[:-5] if is_orig_suffix else key)
            if kind == "manual":
                if orig and base == orig:
                    score = 0
                elif base in wanted_norm:
                    score = 1
                else:
                    score = 4
            else:
                if is_orig_suffix or (orig and base == orig):
                    score = 2
                elif base in wanted_norm:
                    score = 3
                else:
                    score = 5
            wanted_rank = wanted_norm.index(base) if base in wanted_norm else len(wanted_norm)
            rows.append({
                "key": key, "kind": kind, "language": base, "original": bool(is_orig_suffix or (orig and base == orig)),
                "translated": False, "score": score, "_rank": (score, wanted_rank, 0 if is_orig_suffix else 1, key),
            })
    rows.sort(key=lambda row: row["_rank"])
    for row in rows:
        row.pop("_rank", None)
    return rows


def _run_ytdlp(args: list[str]) -> int:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")
    proc = subprocess.run(["yt-dlp", *args], stdout=sys.stderr, stderr=sys.stderr)
    return proc.returncode


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
                "language": raw.get("language"),
                "chapters": raw.get("chapters") or [],
                "_raw": raw,
            }
        except Exception as exc:
            print(f"[vsum] info.json parse failed: {exc}", file=sys.stderr)
    return info


def fetch_captions(url: str, out_dir: Path, langs: str | None, wanted: tuple[str, ...] = DEFAULT_WANTED) -> dict:
    """Fetch metadata, choose a caption track by provenance, fetch that track
    only. `langs` (a yt-dlp --sub-langs pattern) bypasses the ranking."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base_args = ["--skip-download", "--no-playlist", "-o", str(out_dir / "video.%(ext)s")]
    rc = _run_ytdlp(base_args + ["--write-info-json", "--", url])
    info_path = out_dir / "video.info.json"
    if rc != 0 and not info_path.exists():
        raise SystemExit(f"yt-dlp could not fetch metadata for {url} (exit {rc}); see the messages above")
    info = _read_info(info_path, url)
    raw = info.pop("_raw", {}) if isinstance(info.get("_raw"), dict) else {}
    tracks = rank_caption_tracks(raw, wanted) if raw else []

    chosen: dict | None = None
    subtitle: Path | None = None
    if langs:
        rc = _run_ytdlp(base_args + ["--write-subs", "--write-auto-subs", "--sub-langs", langs,
                                     "--sub-format", "vtt", "--convert-subs", "vtt", "--ignore-errors", "--", url])
        candidates = sorted(out_dir.glob("video*.vtt"))
        subtitle = candidates[0] if candidates else None
        if subtitle:
            key = subtitle.name[len("video."):-len(".vtt")]
            chosen = next((t for t in tracks if t["key"] == key), {"key": key, "kind": "unknown",
                                                                     "language": normalize_lang(key), "original": None})
    elif tracks:
        chosen = tracks[0]
        rc = _run_ytdlp(base_args + ["--write-subs", "--write-auto-subs", "--sub-langs", chosen["key"],
                                     "--sub-format", "vtt", "--convert-subs", "vtt", "--ignore-errors", "--", url])
        expected = out_dir / f"video.{chosen['key']}.vtt"
        if expected.exists():
            subtitle = expected
        else:
            candidates = sorted(out_dir.glob("video*.vtt"))
            subtitle = candidates[0] if candidates else None
    return {
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info,
        "track": chosen,
        "tracks_considered": len(tracks),
        "rejected_translated": sum(
            1 for table in (raw.get("subtitles") or {}, raw.get("automatic_captions") or {})
            for entries in table.values() if isinstance(entries, list) and _is_translated(entries)
        ) if raw else 0,
    }


def download_audio(url: str, out_dir: Path) -> Path:
    """Audio-only download for the Whisper fallback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run_ytdlp(["-N", "8", "-f", "ba/bestaudio", "--no-playlist", "--ignore-errors",
                "-o", str(out_dir / "audio_src.%(ext)s"), "--", url])
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
    ap.add_argument("--langs", default=None,
                    help="yt-dlp --sub-langs pattern (bypasses provenance ranking; default: rank tracks, "
                         "prefer manual original, then he/en; never machine-translated)")
    ap.add_argument("--wanted", default=",".join(DEFAULT_WANTED),
                    help="Comma-separated languages acceptable besides the original (default he,en)")
    ap.add_argument("--language", default=None, help="Source language hint for Whisper (ISO 639-1)")
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
    source_detail: dict = {}
    language: str | None = None
    info: dict = {}
    url_source = is_url(args.source)
    wanted = tuple(w.strip() for w in args.wanted.split(",") if w.strip()) or DEFAULT_WANTED

    if url_source:
        print("[vsum] fetching metadata/captions via yt-dlp (no video download)…", file=sys.stderr)
        fetched = fetch_captions(args.source, dl_dir, args.langs, wanted)
        info = fetched["info"]
        track = fetched.get("track")
        if fetched["subtitle_path"]:
            try:
                segments = parse_vtt(fetched["subtitle_path"])
                source_kind = "captions"
                language = (track or {}).get("language") or normalize_lang(info.get("language"))
                source_detail = {
                    "kind": "captions", "track": (track or {}).get("key"),
                    "manual": (track or {}).get("kind") == "manual",
                    "original": (track or {}).get("original"), "translated": False,
                    "youtube_language": info.get("language"),
                    "tracks_considered": fetched["tracks_considered"],
                    "rejected_translated": fetched["rejected_translated"],
                }
                print(f"[vsum] captions: {source_detail['track']} ({'manual' if source_detail['manual'] else 'auto'}"
                      f"{', original' if source_detail['original'] else ''}); "
                      f"{fetched['rejected_translated']} machine-translated tracks ignored", file=sys.stderr)
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
            hint = args.language or normalize_lang(info.get("language"))
            try:
                segments, used = transcribe_video(
                    str(media), work / "audio.mp3", backend=backend, api_key=api_key, language=hint)
                source_kind = f"whisper ({used})"
                language = normalize_lang(DETECTED_LANGUAGE["value"]) or hint
                source_detail = {"kind": "whisper", "backend": used, "language_hint": hint,
                                 "detected": DETECTED_LANGUAGE["value"], "translated": False}
            except SystemExit as exc:
                print(f"[vsum] whisper fallback failed: {exc}", file=sys.stderr)
        else:
            print(
                "[vsum] no captions and no Whisper API key (GROQ_API_KEY / OPENAI_API_KEY in "
                "env or ~/.config/summarize-video/.env) — transcript unavailable",
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
        "source_detail": source_detail,
        "language": language,
        "video": {
            "id": info.get("id") or "video",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "url": info.get("url") or args.source,
            "duration": duration,
            "is_url": url_source,
            "language": info.get("language"),
            "chapters": info.get("chapters") or [],
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
    if language:
        print(f"- **Language:** {language}" + (f" (track `{source_detail.get('track')}`, "
                                              f"{'manual' if source_detail.get('manual') else 'auto'})"
                                              if source_detail.get("track") else ""))
    if info.get("chapters"):
        print(f"- **Creator chapters:** {len(info['chapters'])} — " + "; ".join(
            f"{format_time(float(c.get('start_time') or 0))} {c.get('title')}" for c in info["chapters"][:12]
        ) + (" …" if len(info["chapters"]) > 12 else ""))
    if records:
        print(f"- **Segments:** {len(records)} (via {source_kind})")
        print(f"- **Files:** `{work / 'transcript.json'}`, `{work / 'transcript.txt'}`")
        print()
        print("## Transcript")
        print()
        print("```")
        print("\n".join(txt_lines))
        print("```")
        return 0
    print("- **Transcript:** none available — no usable captions and no Whisper key. "
          "Add GROQ_API_KEY / OPENAI_API_KEY to ~/.config/summarize-video/.env, or pass --langs for a specific track.")
    return EXIT_NO_TRANSCRIPT


if __name__ == "__main__":
    raise SystemExit(main())
