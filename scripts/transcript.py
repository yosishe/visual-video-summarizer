"""The words of the video: captions when the source has them, local whisper otherwise."""
import html
import json
import os
import re
from pathlib import Path

from common import VsumError, fmt_time, need, run

YTDLP = ["yt-dlp", "--ignore-config", "--no-playlist"]
CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")
INLINE_TIME = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
TAG = re.compile(r"<[^>]*>")
WORD = re.compile(r"[\w'׳״]+")  # geresh and gershayim are word-internal in Hebrew
LEGACY = {"iw": "he", "in": "id", "jw": "jv"}
SENTENCE_END = (".", "?", "!", "…", ":")


def base_lang(code: str | None) -> str | None:
    if not code:
        return None
    base = str(code).split("-")[0].lower()
    return LEGACY.get(base, base)


def _seconds(h, m, s, ms) -> float:
    return (int(h or 0) * 3_600_000 + int(m) * 60_000 + int(s) * 1000 + int(ms)) / 1000


def parse_captions(text: str) -> list[tuple[float, float, str]]:
    """VTT or SRT → [(start, end, text)], YouTube's rolling auto-captions reduced to one copy."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues, i = [], 0
    while i < len(lines):
        match = CUE.search(lines[i])
        i += 1
        if not match:
            continue
        body = []
        while i < len(lines) and lines[i] != "":
            body.append(lines[i])
            i += 1
        g = match.groups()
        cues.append((_seconds(*g[:4]), _seconds(*g[4:]), body))
    rolling = any(INLINE_TIME.search(line) for _, _, body in cues for line in body)
    segments = []
    for start, end, body in cues:
        if rolling and len(body) > 1:
            body = body[1:]  # auto-captions repeat the previous line first
        words = " ".join(html.unescape(TAG.sub("", line)) for line in body).split()
        if words:
            segments.append((start, end, " ".join(words)))
    return _dedupe(segments)


def _strip_overlap(previous: str, text: str, min_words: int = 3) -> str:
    """Drop the head of `text` that repeats the tail of `previous` (≥ 3 words, case- and punctuation-blind)."""
    before = [w.casefold() for w in WORD.findall(previous)]
    matches = list(WORD.finditer(text))
    words = [m.group(0).casefold() for m in matches]
    for k in range(min(len(before), len(words)), min_words - 1, -1):
        if before[-k:] == words[:k]:
            return "" if k == len(matches) else text[matches[k].start():].strip()
    return text


def _dedupe(segments):
    out = []
    for start, end, text in segments:
        if out:
            p_start, _, p_text = out[-1]
            if text == p_text or text.startswith(p_text + " "):
                out[-1] = (p_start, end, text)
                continue
            text = _strip_overlap(p_text, text)
            if not text:
                out[-1] = (p_start, end, p_text)
                continue
        out.append((start, end, text))
    return out


def rank_tracks(info: dict) -> list[tuple[str, bool]]:
    """Caption tracks worth trying, best first, as (key, is_auto). Machine translations never.

    manual original · manual he/en · automatic original · automatic he/en · other manual · other automatic
    """
    original = base_lang(info.get("language"))
    rows = []
    for auto, table in ((False, info.get("subtitles") or {}), (True, info.get("automatic_captions") or {})):
        for key, formats in table.items():
            if key == "live_chat" or not isinstance(formats, list):
                continue
            if any("tlang=" in str(f.get("url", "")) for f in formats):
                continue
            if not any(f.get("ext") == "vtt" for f in formats):
                continue
            orig_suffix = key.endswith("-orig")
            lang = base_lang(key[:-5] if orig_suffix else key)
            is_original = orig_suffix or lang == original
            preferred = lang in ("he", "en")
            if auto:
                rank = 2 if is_original else 3 if preferred else 5
            else:
                rank = 0 if is_original else 1 if preferred else 4
            rows.append((rank, not orig_suffix, key, auto))
    rows.sort()
    return [(key, auto) for _, _, key, auto in rows]


def fetch_captions(url: str, info: dict, work: Path) -> tuple[list, dict] | None:
    """The best caption track that downloads and parses, or None."""
    folder = work / "captions"
    for key, auto in rank_tracks(info)[:3]:
        for old in folder.glob("*.vtt"):
            old.unlink()
        try:
            run(YTDLP + ["--skip-download", "--write-auto-subs" if auto else "--write-subs",
                         "--sub-langs", f"^{re.escape(key)}$", "--sub-format", "vtt",
                         "-o", str(folder / "subs.%(ext)s"), url], "caption download")
        except VsumError:
            continue
        for path in folder.glob("*.vtt"):
            segments = parse_captions(path.read_text(encoding="utf-8", errors="replace"))
            if segments:
                lang = base_lang(key[:-5] if key.endswith("-orig") else key)
                return segments, {"method": "captions", "track": key, "auto": auto, "language": lang}
    return None


def caption_file(path: Path) -> tuple[list, dict]:
    if not path.is_file():
        raise VsumError(f"no such subtitle file: {path}")
    segments = parse_captions(path.read_text(encoding="utf-8", errors="replace"))
    if not segments:
        raise VsumError(f"no captions could be read from {path}")
    return segments, {"method": "file", "file": path.name}


def sidecar(media: Path) -> Path | None:
    for ext in (".vtt", ".srt"):
        if media.with_suffix(ext).is_file():
            return media.with_suffix(ext)
    return None


def paragraphs(segments, target: float = 30.0, limit: float = 60.0) -> list[tuple[float, str]]:
    """Cues merged into ~30 s paragraphs, broken at a sentence end when there is one."""
    out, words, start = [], [], None
    for seg_start, seg_end, text in segments:
        start = seg_start if start is None else start
        words.append(text)
        span = seg_end - start
        if span >= limit or (span >= target and text.endswith(SENTENCE_END)):
            out.append((start, " ".join(words)))
            words, start = [], None
    if words:
        out.append((start, " ".join(words)))
    return out


def describe(t: dict) -> str:
    if t["method"] == "captions":
        return f"{'automatic' if t['auto'] else 'manual'} captions, track {t['track']}"
    if t["method"] == "whisper":
        return f"local whisper ({t['model']}), language {t['language']}"
    return f"subtitle file {t['file']}"


def markdown(meta: dict, segments) -> str:
    """transcript.md: a short header, then a `[mm:ss] text` paragraph about every 30 s."""
    lines = [f"# {meta['title']}", "",
             f"- Source: {meta.get('url') or meta.get('path')}",
             f"- Duration: {fmt_time(meta['duration'])}",
             f"- Transcript: {describe(meta['transcript'])}",
             f"- Words: {sum(len(text.split()) for _, _, text in segments)}"]
    if meta.get("chapters"):
        lines.append("- Creator chapters: " + " · ".join(f"{fmt_time(c['t'])} {c['title']}" for c in meta["chapters"]))
    lines.append("")
    for start, text in paragraphs(segments):
        lines += [f"[{fmt_time(start)}] {text}", ""]
    return "\n".join(lines)
