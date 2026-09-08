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
2. Explicitly authorized Whisper API fallback (--whisper groq|openai) on
   downloaded / local file audio, with the source language passed when known.
   Key presence alone never enables upload.

Emits stable segment records {seg_id, start, end, text} so frames can
reference exact transcript spans, plus a readable transcript.txt, the
transcript `language` and a `source_detail` record.

VTT parsing/dedup and download patterns adapted from
bradautomates/claude-video (MIT).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from gates import (  # noqa: E402
    ENGINE_VERSION,
    EXIT_SOURCE_UNAVAILABLE,
    health_status,
    health_summary,
    source_identity,
    transcript_health,
    canonical_source,
)
from hostenv import require_tools, run_text, utf8_stdio  # noqa: E402
from safety import atomic_write, sanitize_tool_output, ytdlp_command  # noqa: E402
from acquisition import (proxy_policy_failure,   # noqa: E402
    AcquisitionError,
    DISCOVERY_TIMEOUT,
    EXIT_ACQUISITION,
    EXIT_PARTIAL,
    MEDIA_TIMEOUT,
    canonical_hash,
    classify_tool_failure,
    file_lock,
    hash_file,
    http_failure,
    read_cache,
    retry_call,
    run_process,
    write_cache,
    record_event,
)
import whisper as whisper_module  # noqa: E402

CHUNK_FAILURES = whisper_module.CHUNK_FAILURES
DETECTED_LANGUAGE = whisper_module.DETECTED_LANGUAGE
load_api_key = whisper_module.load_api_key
transcribe_video = whisper_module.transcribe_video
configured_local_model = getattr(whisper_module, "configured_local_model", lambda: None)


class PartialTranscription(SystemExit):
    """Compatibility until the whisper worker's richer exception is integrated."""

    def __init__(self, segments: list[dict], failures: list[dict]):
        self.segments = segments
        self.failures = failures
        super().__init__("transcription completed with missing chunks")


PartialTranscription = getattr(whisper_module, "PartialTranscription", PartialTranscription)

TRANSCRIPT_SCHEMA = 2
# The tail of the last yt-dlp stderr, so a "Video unavailable" can be recorded
# (sanitised) in transcript.json instead of being lost in the console.
YTDLP_LAST: dict[str, str] = {"stderr": ""}


class SourceUnavailable(RuntimeError):
    """The source itself cannot be read (private, removed, blocked, unreadable file)."""

TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
EXIT_NO_TRANSCRIPT = 6
EXIT_TOO_LONG = 8
MAX_DURATION_SECONDS = 120 * 60
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
    return _parse_vtt_text(text)


def _parse_vtt_text(text: str) -> list[dict]:
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
            entry = next((dict(entry) for entry in entries
                          if isinstance(entry, dict) and entry.get("url") and entry.get("ext") == "vtt"), None)
            if entry is None:
                continue
            wanted_rank = wanted_norm.index(base) if base in wanted_norm else len(wanted_norm)
            rows.append({
                "key": key, "kind": kind, "language": base, "original": bool(is_orig_suffix or (orig and base == orig)),
                "translated": False, "score": score, "_rank": (score, wanted_rank, 0 if is_orig_suffix else 1, key),
                "_entry": entry,
            })
    rows.sort(key=lambda row: row["_rank"])
    for row in rows:
        row.pop("_rank", None)
    return rows


def _run_ytdlp(args: list[str]) -> int:
    require_tools("yt-dlp")
    proc = run_process(ytdlp_command(args), timeout=MEDIA_TIMEOUT if "-f" in args else DISCOVERY_TIMEOUT)
    captured = proc.stderr or ""
    if captured:
        safe = sanitize_tool_output(captured)
        if safe:
            sys.stderr.write(safe + "\n")
    YTDLP_LAST["stderr"] = captured[-4000:]
    return proc.returncode


def _public_track(track: dict | None) -> dict | None:
    return {key: value for key, value in (track or {}).items() if not key.startswith("_")} if track else None


def _metadata(info: dict, url: str) -> dict:
    return {
        "id": info.get("id"), "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"), "url": info.get("webpage_url") or url,
        "language": info.get("language"), "chapters": info.get("chapters") or [],
        "subtitles": info.get("subtitles") or {},
        "automatic_captions": info.get("automatic_captions") or {},
    }


def _validate_inventory(raw: object) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip():
        raise AcquisitionError("malformed_response", "Downloader metadata has no valid source id")
    duration = raw.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) \
            or not math.isfinite(float(duration)) or float(duration) <= 0:
        raise AcquisitionError("malformed_response", "Downloader metadata has an invalid duration")
    for name in ("subtitles", "automatic_captions"):
        table = raw.get(name)
        if not isinstance(table, dict):
            raise AcquisitionError("malformed_response", f"Downloader metadata has an invalid {name} table")
        for key, entries in table.items():
            if not isinstance(key, str) or not isinstance(entries, list):
                raise AcquisitionError("malformed_response", f"Downloader metadata has a malformed {name} track")
            for entry in entries:
                if not isinstance(entry, dict) or ("url" in entry and not isinstance(entry["url"], str)) \
                        or ("ext" in entry and not isinstance(entry["ext"], str)):
                    raise AcquisitionError("malformed_response", f"Downloader metadata has a malformed {name} entry")
    return raw


def _discover_info(url: str, out_dir: Path, cache_dir: Path, *, refresh: bool = False) -> dict:
    """Discover once into an operation-owned directory and cache the exact inventory."""
    source = canonical_source(url)
    key = canonical_hash({"kind": "caption_inventory", "source": source})
    cache_path = cache_dir / "caption_inventory" / f"{key}.json"
    lock = cache_path.with_suffix(".lock")
    with file_lock(lock):
        if not refresh:
            cached = read_cache(cache_path, key)
            if isinstance(cached, dict):
                record_event(out_dir, "caption_discovery", "yt-dlp", cache_hit=True)
                return _validate_inventory(cached)

        def attempt() -> dict:
            with tempfile.TemporaryDirectory(prefix="caption-discovery-", dir=out_dir) as tmp:
                owned = Path(tmp)
                rc = _run_ytdlp(["--skip-download", "--no-playlist", "-o", str(owned / "video.%(ext)s"),
                                 "--write-info-json", "--", url])
                exact = owned / "video.info.json"
                if rc != 0:
                    error = classify_tool_failure(YTDLP_LAST["stderr"], rc)
                    detail = sanitize_tool_output(YTDLP_LAST["stderr"])
                    if error.category == "source_unavailable" and detail:
                        error = AcquisitionError(error.category, f"yt-dlp could not fetch metadata: {detail}")
                    raise error
                if not exact.is_file() or exact.is_symlink():
                    raise AcquisitionError("malformed_response", "Downloader returned no operation-owned metadata")
                try:
                    raw = json.loads(exact.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError) as exc:
                    raise AcquisitionError("malformed_response", "Downloader returned malformed metadata") from exc
                return _validate_inventory(raw)

        raw = retry_call("caption_discovery", attempt, provider="yt-dlp", work=out_dir)
        write_cache(cache_path, key, raw)
        return raw


def _fetch_caption_url(url: str) -> bytes:
    """Fetch only the selected inventory URL, with a finite deadline and size cap."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AcquisitionError("malformed_response", "Caption inventory URL must be HTTP(S) without credentials")
    request = Request(url, headers={"User-Agent": "visual-video-summarizer/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code in (403, 410):
            expiry = (parse_qs(urlparse(url).query).get("expire") or [None])[0]
            try:
                if expiry is not None and float(expiry) <= time.time():
                    raise AcquisitionError("expired_resource", "Selected caption resource URL expired") from exc
            except ValueError:
                pass
        raise http_failure(exc.code, exc.headers) from exc
    except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
        denied = proxy_policy_failure(exc)
        if denied is not None:
            raise denied from exc
        raise AcquisitionError("temporary_network", "Caption request failed temporarily", retryable=True) from exc
    if len(data) > 20 * 1024 * 1024:
        raise AcquisitionError("invalid_input", "Caption response exceeded the 20 MB limit")
    return data


def _decode_vtt(content: bytes) -> str:
    if not content:
        raise AcquisitionError("empty_response", "Caption URL returned an empty response")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcquisitionError("malformed_response", "Caption response was not valid UTF-8") from exc
    if not text.lstrip().startswith("WEBVTT"):
        raise AcquisitionError("malformed_response", "Caption response was not WebVTT")
    return text


def _forced_tracks(raw: dict, pattern: str) -> list[dict]:
    """Resolve a forced yt-dlp-style regex against the finite discovered inventory."""
    include, exclude = [], []
    try:
        for token in (part.strip() for part in pattern.split(",")):
            if not token:
                continue
            target = exclude if token.startswith("-") else include
            expression = token[1:] if token.startswith("-") else token
            target.append(re.compile(".*" if expression == "all" else expression))
    except re.error as exc:
        raise AcquisitionError("invalid_input", f"Invalid --langs pattern: {exc}") from exc

    def matches(key: str) -> bool:
        return bool(include) and any(item.search(key) for item in include) \
            and not any(item.search(key) for item in exclude)
    ranked = rank_caption_tracks(raw, DEFAULT_WANTED)
    by_key = {row["key"]: row for row in ranked}
    rows: list[dict] = []
    orig = normalize_lang(raw.get("language"))
    for kind, table in (("manual", raw.get("subtitles") or {}),
                        ("auto", raw.get("automatic_captions") or {})):
        for key, entries in table.items():
            if not matches(str(key)) or not isinstance(entries, list):
                continue
            entry = next((dict(row) for row in entries if isinstance(row, dict) and row.get("url")
                          and row.get("ext") == "vtt"), None)
            if not entry:
                continue
            base = normalize_lang(str(key)[:-5] if str(key).endswith("-orig") else str(key))
            translated = _is_translated(entries)
            rows.append({**by_key.get(str(key), {}), "key": str(key), "kind": kind, "language": base,
                         "original": bool(not translated and (str(key).endswith("-orig") or (orig and base == orig))),
                         "translated": translated, "_entry": entry})
    return sorted(rows, key=lambda row: (len(row["key"]), row["key"]))


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


def fetch_captions(url: str, out_dir: Path, langs: str | None, wanted: tuple[str, ...] = DEFAULT_WANTED,
                   *, cache_dir: Path | None = None, allow_long: bool = False) -> dict:
    """Fetch metadata, choose a caption track by provenance, fetch that track
    only. `langs` (a yt-dlp --sub-langs pattern) bypasses the ranking."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir or out_dir / ".cache").expanduser().resolve()
    raw = _discover_info(url, out_dir, cache_dir)
    info = _metadata(raw, url)
    if float(info.get("duration") or 0) > MAX_DURATION_SECONDS and not allow_long:
        return {"too_long": True, "info": info, "subtitle_path": None, "segments": [], "track": None,
                "tracks_considered": 0, "rejected_translated": 0}
    tracks = _forced_tracks(raw, langs) if langs else rank_caption_tracks(raw, wanted)
    chosen = tracks[0] if tracks else None
    if chosen and chosen.get("translated"):
        print(f"[vsum] warning: --langs selected `{chosen['key']}`, a machine-translated track", file=sys.stderr)

    subtitle: Path | None = None
    segments: list[dict] = []
    refreshed = False
    cache_hit = False
    caption_sha = None
    if chosen:
        source = canonical_source(url)
        selection = {"source": source, "langs": langs, "wanted": list(wanted),
                     "track": _public_track(chosen)}
        selection_key = canonical_hash(selection)
        caption_path = cache_dir / "captions" / f"{selection_key}.vtt"
        receipt = caption_path.with_suffix(".json")

        def validated_caption(content: bytes) -> tuple[str, list[dict]]:
            text = _decode_vtt(content)
            parsed = _parse_vtt_text(text)
            if not parsed:
                raise AcquisitionError("empty_response", "Caption track contained no usable speech cues")
            return text, parsed

        def obtain(track: dict) -> bytes:
            entry = track.get("_entry") or {}
            track_url = entry.get("url")
            if not track_url:
                raise AcquisitionError("malformed_response", "Selected caption track has no URL")
            return retry_call("caption_fetch", lambda: _fetch_caption_url(str(track_url)),
                              provider="youtube-captions", work=out_dir)

        with file_lock(caption_path.with_suffix(".lock")):
            cached = read_cache(receipt, selection_key)
            if isinstance(cached, dict) and caption_path.is_file() and not caption_path.is_symlink() \
                    and hash_file(caption_path) == cached.get("file_sha256"):
                content = caption_path.read_bytes()
                try:
                    caption_text, parsed_segments = validated_caption(content)
                except AcquisitionError:
                    # Earlier Windows writes could be hash-bound but contain
                    # CRCRLF and no usable cues. Reacquire under the normal policy.
                    record_event(out_dir, "caption_fetch", "youtube-captions", category="invalid_cache")
                else:
                    cache_hit = True
                    record_event(out_dir, "caption_fetch", "youtube-captions", cache_hit=True)
            if not cache_hit:
                try:
                    content = obtain(chosen)
                except AcquisitionError as exc:
                    if exc.category != "expired_resource":
                        raise
                    refresh_key = canonical_hash([source, (chosen.get("_entry") or {}).get("url")])
                    refresh_receipt = cache_dir / "caption_refresh" / f"{refresh_key}.json"
                    if read_cache(refresh_receipt, refresh_key) is not None:
                        raise AcquisitionError("expired_resource", "This expired resource already used its metadata refresh")
                    write_cache(refresh_receipt, refresh_key, {"attempted": True})
                    record_event(out_dir, "caption_discovery", "yt-dlp", category="metadata_refresh")
                    refreshed = True
                    raw = _discover_info(url, out_dir, cache_dir, refresh=True)
                    info = _metadata(raw, url)
                    tracks = _forced_tracks(raw, langs) if langs else rank_caption_tracks(raw, wanted)
                    chosen = next((track for track in tracks if track.get("key") == chosen.get("key")
                                   and track.get("kind") == chosen.get("kind")), None)
                    if not chosen:
                        raise AcquisitionError("malformed_response", "Caption inventory changed during signed URL refresh")
                    content = obtain(chosen)
                caption_text, parsed_segments = validated_caption(content)
                caption_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(caption_path, caption_text)
                write_cache(receipt, selection_key, {"file_sha256": hash_file(caption_path), "selection": selection})
        caption_sha = hashlib.sha256(caption_text.encode("utf-8")).hexdigest()
        subtitle = out_dir / "selected-caption.vtt"
        atomic_write(subtitle, caption_text)
        transcript_key = canonical_hash({**selection, "caption_sha256": caption_sha, "parser": TRANSCRIPT_SCHEMA})
        transcript_path = cache_dir / "transcripts" / f"{transcript_key}.json"
        with file_lock(transcript_path.with_suffix(".lock")):
            cached_segments = read_cache(transcript_path, transcript_key)
            if isinstance(cached_segments, dict) and isinstance(cached_segments.get("segments"), list) \
                    and cached_segments["segments"]:
                segments = cached_segments["segments"]
            else:
                segments = parsed_segments
                write_cache(transcript_path, transcript_key, {"segments": segments})
    return {
        "subtitle_path": str(subtitle) if subtitle else None,
        "segments": segments,
        "info": info,
        "track": _public_track(chosen),
        "tracks_considered": len(tracks),
        "rejected_translated": sum(
            1 for table in (raw.get("subtitles") or {}, raw.get("automatic_captions") or {})
            for entries in table.values() if isinstance(entries, list) and _is_translated(entries)
        ) if raw else 0,
        "inventory_refreshed": refreshed,
        "cache_hit": cache_hit,
        "caption_sha256": caption_sha,
    }


def _manifest_media(url: str, out_dir: Path) -> Path | None:
    """Reuse one full media part only when its source, hash and audio stream validate."""
    manifest = out_dir / "parts.json"
    try:
        if manifest.is_symlink():
            return None
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        identity = payload.get("identity") or {}
        if canonical_source(identity.get("source")) != canonical_source(url):
            return None
        if identity.get("sections") or bool(identity.get("exact_sections")):
            return None
        parts = payload.get("parts") or []
        if len(parts) != 1 or not isinstance(parts[0], dict):
            return None
        row = parts[0]
        path = Path(row["path"]).expanduser().resolve()
        expected = row.get("sha256") or row.get("file_sha256")
        if not expected or path.is_symlink() or not path.is_file() or hash_file(path) != expected:
            return None
        if not probe(str(path))["has_audio"]:
            return None
        return path
    except (OSError, ValueError, TypeError, KeyError, AttributeError, SourceUnavailable):
        return None


def download_audio(url: str, out_dir: Path, *, cache_dir: Path | None = None) -> Path:
    """Audio-only download for the Whisper fallback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    reusable = _manifest_media(url, out_dir)
    if reusable is not None:
        return reusable
    cache_dir = Path(cache_dir or out_dir / ".cache").expanduser().resolve()
    key = canonical_hash({"kind": "audio", "source": canonical_source(url), "format": "ba/b"})
    receipt_path = cache_dir / "audio" / f"{key}.json"
    media_path = cache_dir / "audio" / f"{key}.media"
    with file_lock(receipt_path.with_suffix(".lock")):
        receipt = read_cache(receipt_path, key)
        if isinstance(receipt, dict) and media_path.is_file() and not media_path.is_symlink() \
                and hash_file(media_path) == receipt.get("file_sha256"):
            return media_path

        def attempt() -> Path:
            with tempfile.TemporaryDirectory(prefix="audio-download-", dir=out_dir) as tmp:
                owned = Path(tmp)
                rc = _run_ytdlp(["-f", "ba/b", "--no-playlist",
                                 "-o", str(owned / "audio_src.%(ext)s"), "--", url])
                if rc != 0:
                    raise classify_tool_failure(YTDLP_LAST["stderr"], rc)
                candidates = [path for path in owned.iterdir() if path.is_file() and not path.is_symlink()
                              and path.name.startswith("audio_src.")]
                if len(candidates) != 1 or candidates[0].stat().st_size == 0:
                    raise AcquisitionError("malformed_response", "Downloader did not produce exactly one audio file")
                media_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(candidates[0], media_path)
                return media_path

        retry_call("audio_download", attempt, provider="yt-dlp", work=out_dir)
        write_cache(receipt_path, key, {"file_sha256": hash_file(media_path), "source": canonical_source(url)})
        return media_path


def probe(path: str) -> dict:
    require_tools("ffprobe")
    result = run_process(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
                         timeout=DISCOVERY_TIMEOUT)
    if result.returncode != 0:
        raise SourceUnavailable(f"ffprobe could not read the recording (exit {result.returncode}): "
                                f"{sanitize_tool_output(result.stderr) or 'no message'}")
    try:
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams", [])
        fmt = data.get("format", {})
        duration = float(fmt.get("duration") or 0)
        if not isinstance(streams, list) or not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid ffprobe fields")
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceUnavailable("ffprobe returned malformed media metadata") from exc
    return {
        "duration": duration,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="transcript",
        description="Fetch captions; upload audio only with an explicit --whisper provider.",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument("--work", default=None, help="Working directory (default: new tmp dir)")
    ap.add_argument("--langs", default=None,
                    help="yt-dlp --sub-langs pattern (bypasses provenance ranking; default: rank tracks, "
                         "prefer manual original, then he/en; never machine-translated)")
    ap.add_argument("--wanted", default=None,
                    help="Comma-separated languages acceptable besides the original (default he,en)")
    ap.add_argument("--language", default=None, help="Source language hint for Whisper (ISO 639-1)")
    ap.add_argument("--no-whisper", action="store_true", help="Disable Whisper fallback")
    ap.add_argument("--whisper", choices=["groq", "openai", "local"], default=None,
                    help="Transcribe with local Whisper, or explicitly allow audio upload to a cloud provider")
    ap.add_argument("--local-model", default=None, help="Local whisper.cpp model path (with --whisper local)")
    ap.add_argument("--cache-dir", default=None, help="Acquisition cache (default: run-local)")
    ap.add_argument("--retry-uncertain", action="store_true",
                    help="Explicitly allow retry of an uncertain transcription outcome")
    ap.add_argument("--allow-long", action="store_true",
                    help=f"Allow sources longer than {MAX_DURATION_SECONDS // 60} minutes")
    args = ap.parse_args()
    utf8_stdio()

    selected_model = Path(args.local_model).expanduser().resolve() if args.local_model else None

    work = Path(args.work).expanduser().resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="vsum-"))
    dl_dir = work / "download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    transcript_guard = file_lock(work / ".transcript.lock")
    transcript_guard.__enter__()

    def finish(code: int) -> int:
        transcript_guard.__exit__(None, None, None)
        return code

    cache_dir = (Path(args.cache_dir).expanduser().resolve() if args.cache_dir
                 else work / ".cache" / "acquisition")
    print(f"[vsum] working dir: {work}", file=sys.stderr)

    segments: list[dict] = []
    source_kind: str | None = None
    source_detail: dict = {}
    language: str | None = None
    info: dict = {}
    failure_reason: str | None = None
    unavailable_reason: str | None = None
    ytdlp_exit: int | None = None
    fetched: dict = {}
    acquisition_failure: AcquisitionError | None = None
    partial_failures: list[dict] = []
    partial = False
    too_long = False
    url_source = is_url(args.source)
    wanted = (tuple(w.strip() for w in args.wanted.split(",") if w.strip()) or DEFAULT_WANTED) if args.wanted \
        else DEFAULT_WANTED

    if url_source:
        print("[vsum] fetching metadata/captions via yt-dlp (no video download)…", file=sys.stderr)
        try:
            fetched = fetch_captions(args.source, dl_dir, args.langs, wanted,
                                     cache_dir=cache_dir, allow_long=args.allow_long)
        except AcquisitionError as exc:
            acquisition_failure = exc
            info = {"url": args.source}
            failure_reason = exc.message
            if exc.category == "source_unavailable":
                unavailable_reason = exc.message
        else:
            info = fetched["info"]
        if fetched.get("too_long"):
            too_long = True
            failure_reason = (f"video duration exceeds the {MAX_DURATION_SECONDS // 60}-minute guard; "
                              "use --allow-long to proceed")
        track = fetched.get("track")
        if acquisition_failure:
            print(f"[vsum] acquisition failed: {acquisition_failure.message}", file=sys.stderr)
        elif fetched.get("unavailable"):
            unavailable_reason = str(fetched["reason"])
            ytdlp_exit = fetched.get("exit")
            failure_reason = unavailable_reason
            print(f"[vsum] source unavailable: {unavailable_reason}", file=sys.stderr)
        elif fetched.get("segments"):
            segments = fetched["segments"]
            source_kind = "captions"
            language = (track or {}).get("language") or normalize_lang(info.get("language"))
            source_detail = {
                "kind": "captions", "track": (track or {}).get("key"),
                "manual": (track or {}).get("kind") == "manual",
                "original": (track or {}).get("original"),
                "translated": bool((track or {}).get("translated", False)),
                "youtube_language": info.get("language"),
                "tracks_considered": fetched["tracks_considered"],
                "rejected_translated": fetched["rejected_translated"],
                "caption_sha256": fetched.get("caption_sha256"),
                "inventory_refreshed": bool(fetched.get("inventory_refreshed")),
            }
            print(f"[vsum] captions: {source_detail['track']} ({'manual' if source_detail['manual'] else 'auto'}"
                  f"{', original' if source_detail['original'] else ''}); "
                  f"{fetched['rejected_translated']} machine-translated tracks ignored", file=sys.stderr)
        else:
            failure_reason = "no eligible caption track was found in the downloader inventory"
        duration = float(info.get("duration") or 0)
    else:
        local = Path(args.source).expanduser().resolve()
        info = {"id": local.stem, "title": local.name, "url": str(local)}
        duration = 0.0
        if not local.exists():
            unavailable_reason = f"file not found: {local}"
            failure_reason = unavailable_reason
            print(f"[vsum] source unavailable: {unavailable_reason}", file=sys.stderr)
        else:
            try:
                meta = probe(str(local))
            except AcquisitionError as exc:
                acquisition_failure = exc
                failure_reason = exc.message
                print(f"[vsum] acquisition failed: {exc.message}", file=sys.stderr)
            except SourceUnavailable as exc:
                unavailable_reason = str(exc)
                failure_reason = unavailable_reason
                print(f"[vsum] source unavailable: {unavailable_reason}", file=sys.stderr)
            else:
                duration = meta["duration"]
                failure_reason = "a local recording has no captions"
                if duration > MAX_DURATION_SECONDS and not args.allow_long:
                    too_long = True
                    failure_reason = (f"video duration exceeds the {MAX_DURATION_SECONDS // 60}-minute guard; "
                                      "use --allow-long to proceed")
                if not meta["has_audio"]:
                    print("[vsum] no audio stream — no transcript possible", file=sys.stderr)
                    failure_reason = "the recording has no audio stream"
                    args.no_whisper = True

    # A configured local model is considered only after captions and their
    # caches are exhausted, so a stale model path cannot break caption success.
    if not segments and not unavailable_reason and not acquisition_failure and not too_long \
            and not args.no_whisper and args.whisper is None:
        configured = selected_model or configured_local_model()
        if configured is not None:
            selected_model = Path(configured).expanduser().resolve()
            args.whisper = "local"

    whisper_report: dict | None = None
    if unavailable_reason or acquisition_failure or too_long:
        pass  # an unreadable source is never uploaded anywhere
    elif not segments and not args.no_whisper and args.whisper:
        selected_model = selected_model or (configured_local_model() if args.whisper == "local" else None)
        if selected_model is not None:
            selected_model = Path(selected_model).expanduser().resolve()
        backend, api_key = (("local", None) if args.whisper == "local" else load_api_key(args.whisper))
        if backend and (api_key or backend == "local"):
            try:
                if backend == "local" and (selected_model is None or selected_model.is_symlink()
                                           or not selected_model.is_file()):
                    raise AcquisitionError("dependency", "Configured local Whisper model is unavailable")
                media = (download_audio(args.source, dl_dir, cache_dir=cache_dir)
                         if url_source else Path(args.source).expanduser().resolve())
            except AcquisitionError as exc:
                acquisition_failure = exc
                media = None
            hint = args.language or normalize_lang(info.get("language"))
            try:
                if media is None:
                    raise acquisition_failure  # type: ignore[misc]
                transcribe_kwargs = {"backend": backend, "api_key": api_key, "language": hint}
                if selected_model is not None:
                    transcribe_kwargs["local_model"] = selected_model
                if args.cache_dir:
                    transcribe_kwargs["cache_dir"] = cache_dir
                if args.retry_uncertain:
                    transcribe_kwargs["retry_uncertain"] = True
                if args.allow_long:
                    transcribe_kwargs["allow_long"] = True
                segments, used = transcribe_video(str(media), work / "audio.mp3", **transcribe_kwargs)
                source_kind = f"whisper ({used})"
                language = normalize_lang(DETECTED_LANGUAGE["value"]) or hint
                source_detail = {"kind": "whisper", "backend": used, "language_hint": hint,
                                 "detected": DETECTED_LANGUAGE["value"], "translated": False,
                                 "local_model": str(selected_model) if selected_model else None}
                failure_reason = None
            except PartialTranscription as exc:
                segments = list(exc.segments)
                partial_failures = list(exc.failures)
                partial = True
                source_kind = f"whisper ({backend})"
                language = normalize_lang(DETECTED_LANGUAGE["value"]) or hint
                source_detail = {"kind": "whisper", "backend": backend, "language_hint": hint,
                                 "detected": DETECTED_LANGUAGE["value"], "translated": False,
                                 "local_model": str(selected_model) if selected_model else None}
                failure_reason = "transcription completed with missing chunks"
            except AcquisitionError as exc:
                acquisition_failure = exc
                failure_reason = exc.message
                print(f"[vsum] transcription acquisition failed: {exc.message}", file=sys.stderr)
            except SystemExit as exc:
                failure_reason = f"{args.whisper} transcription failed: {exc}"
                print(f"[vsum] whisper fallback failed: {exc}", file=sys.stderr)
            whisper_report = {"backend": args.whisper, "chunks_failed": len(CHUNK_FAILURES),
                              "failed_chunks": list(CHUNK_FAILURES)}
            if CHUNK_FAILURES:
                print(f"[vsum] warning: {len(CHUNK_FAILURES)} audio chunk(s) were skipped; "
                      "the transcript has gaps (see transcript.json health)", file=sys.stderr)
        else:
            failure_reason = (f"{failure_reason or 'no captions'}; --whisper {args.whisper} was selected "
                              "but no API key is available")
            print(
                "[vsum] no captions and no Whisper API key (GROQ_API_KEY / OPENAI_API_KEY in "
                "env or ~/.config/summarize-video/.env) — transcript unavailable",
                file=sys.stderr,
            )

    if unavailable_reason or acquisition_failure or too_long:
        pass  # the reason is the source, not the transcription policy
    elif not segments and not args.whisper and not args.no_whisper:
        failure_reason = f"{failure_reason or 'no captions'}; local transcription is not configured; cloud transcription was not authorized"
        print("[vsum] local transcription can use installed whisper-cli and an explicitly configured multilingual model "
              "(--whisper local --local-model <path>). Missing software/model downloads require approval. "
              "Cloud upload is a separate explicit --whisper groq|openai choice; a stored key is not consent.",
              file=sys.stderr)
    elif not segments and args.no_whisper:
        failure_reason = f"{failure_reason or 'no captions'}; --no-whisper disables transcription"

    # Segment ids are the join keys of everything downstream: they must follow
    # time order. Captions occasionally arrive out of order; sort and say so.
    reordered = any(segments[i]["start"] > segments[i + 1]["start"] for i in range(len(segments) - 1))
    if reordered:
        segments = sorted(segments, key=lambda seg: (float(seg["start"]), float(seg["end"])))
    records = [
        {
            "seg_id": f"seg_{i:04d}",
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        for i, seg in enumerate(segments)
    ]
    health = transcript_health(records, duration, source=source_kind, source_detail=source_detail,
                               language=language, video_language=info.get("language"))
    if reordered:
        health["warnings"].append("caption cues were re-sorted into time order")
        health["flags"].append("reordered")
    if whisper_report and whisper_report["chunks_failed"]:
        health["warnings"].append(f"{whisper_report['chunks_failed']} transcription chunk(s) failed and were skipped")
        health["flags"].append("chunks_failed")
    if whisper_report:
        health["whisper"] = whisper_report
    health["status"] = health_status(health["flags"])
    if not records:
        source_detail = {
            "kind": "none", "reason": failure_reason or "no usable transcript",
            "tracks_considered": fetched.get("tracks_considered", 0) if fetched else 0,
            "rejected_translated": fetched.get("rejected_translated", 0) if fetched else 0,
            "whisper_selected": args.whisper, "whisper_disabled": bool(args.no_whisper),
        }
        if unavailable_reason:
            source_detail["yt_dlp_exit"] = ytdlp_exit
    try:
        identity = source_identity(args.source)
    except OSError:
        identity = str(args.source)

    payload = {
        "schema_version": TRANSCRIPT_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "status": ("partial" if partial else "ok" if records else "too_long" if too_long else
                   "source_unavailable" if unavailable_reason else
                   "acquisition_failed" if acquisition_failure else "no_transcript"),
        "generated_at": _now(),
        "source": source_kind,
        "source_detail": source_detail,
        "source_identity": identity,
        # The request this transcript answers: a later run with other options is
        # a stale transcript, not an adopted one (gates.validate_transcript).
        "inputs": {"whisper": args.whisper, "no_whisper": bool(args.no_whisper),
                   "langs": args.langs, "wanted": args.wanted,
                   "local_model": str(selected_model) if selected_model else None},
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
        "health": health,
        "segments": records,
    }
    if acquisition_failure:
        payload["acquisition_error"] = acquisition_failure.as_dict()
    if partial:
        payload["failed_chunks"] = partial_failures
        payload["partial_fingerprint"] = canonical_hash({
            "source_identity": payload["source_identity"], "inputs": payload["inputs"],
            "segments": payload["segments"], "failed_chunks": payload["failed_chunks"],
        })
    atomic_write(work / "transcript.json", json.dumps(payload, indent=2, ensure_ascii=False))
    txt_lines = [
        f"{r['seg_id']} [{format_time(r['start'])}-{format_time(r['end'])}] {r['text']}"
        for r in records
    ]
    atomic_write(work / "transcript.txt", "\n".join(txt_lines))
    for warning in health["warnings"]:
        print(f"[vsum] transcript health: {warning}", file=sys.stderr)

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
        print(f"- **Health:** {health_summary(health)} · largest gap {health['largest_gap_s']:.0f} s · "
              f"{health['words']} words")
        print(f"- **Files:** `{work / 'transcript.json'}`, `{work / 'transcript.txt'}`")
        if partial:
            print(f"- **Partial:** {len(partial_failures)} chunk range(s) failed; full retained transcript is in the files above")
            return finish(EXIT_PARTIAL)
        return finish(0)
    if too_long:
        print(f"- **Too long:** source exceeds {MAX_DURATION_SECONDS // 60} minutes; use --allow-long to proceed.")
        return finish(EXIT_TOO_LONG)
    if acquisition_failure and not unavailable_reason:
        print(f"- **Acquisition failed:** {acquisition_failure.message} (`{acquisition_failure.category}`).")
        return finish(acquisition_failure.exit_code)
    if unavailable_reason:
        print(f"- **Source unavailable:** {unavailable_reason}. The video is private, removed, region-locked, "
              "blocked, or the file cannot be read. Report this to the user in plain words with one practical step "
              "(another public link, or a local recording); do not retry in a loop and do not use cookies, logins "
              "or another downloader. `transcript.json` records `status: source_unavailable`; every later stage "
              "refuses it.")
        return finish(EXIT_SOURCE_UNAVAILABLE)
    print(f"- **Transcript:** none available — {source_detail.get('reason')}. "
          + ("Transcription is disabled by --no-whisper. " if args.no_whisper else
             "Use installed local transcription with --whisper local --local-model <path>; request approval for "
             "missing local setup. Cloud --whisper groq|openai is a separate explicit upload choice. ") +
          "`transcript.json` records `status: no_transcript`; every later stage refuses it.")
    return finish(EXIT_NO_TRANSCRIPT)


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
