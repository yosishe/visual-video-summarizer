"""The words of the video: captions when the source has them, local whisper otherwise."""
import html
import json
import math
import os
import re
from pathlib import Path

import source
from common import VsumError, fmt_time, need, run

YTDLP = ["yt-dlp", "--ignore-config", "--no-playlist"]
CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")
INLINE_TIME = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
TAG = re.compile(r"<[^>]*>")
WORD = re.compile(r"[\w'׳״]+")  # geresh and gershayim are word-internal in Hebrew
LEGACY = {"iw": "he", "in": "id", "jw": "jv"}
SENTENCE_END = re.compile(r"[.?!…:](\s|$)")  # auto-caption cues often end a sentence mid-cue
CHUNK = 600  # seconds of audio per whisper run


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
        while i < len(lines) and lines[i] != "" and not CUE.search(lines[i]):  # blank line or next cue ends it
            body.append(lines[i])
            i += 1
        if i < len(lines) and CUE.search(lines[i]) and body and body[-1].strip().isdigit():
            body.pop()  # the next SRT cue's number, when separator lines hold spaces
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
    auto_keys = (info.get("automatic_captions") or {}).keys()
    original = base_lang(info.get("language")) or next(
        (base_lang(key[:-5]) for key in auto_keys if key.endswith("-orig")), None)
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
        if span >= limit or (span >= target and SENTENCE_END.search(text)):
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


DETECTED = re.compile(r"auto-detected language:\s*([a-z]{2,3})")


def _registration() -> Path | None:
    """v1 stored the local model path here; reuse it."""
    try:
        return Path.home() / ".config" / "summarize-video" / "local-model.json"
    except RuntimeError:  # no home directory
        return None


def whisper_model() -> str:
    choices = [os.environ.get("VSUM_WHISPER_MODEL")]
    registration = _registration()
    if registration and registration.is_file():
        try:
            choices.append(json.loads(registration.read_text(encoding="utf-8")).get("model_path"))
        except (OSError, ValueError, AttributeError):
            pass
    for choice in choices:
        if choice and Path(choice).expanduser().is_file():
            return str(Path(choice).expanduser())
    raise VsumError(
        "the video has no captions and no local whisper model is set. Download a multilingual ggml model "
        "(e.g. ggml-large-v3-turbo.bin from https://huggingface.co/ggerganov/whisper.cpp), set "
        "VSUM_WHISPER_MODEL to its path and run again, or pass --captions with a subtitle file.")


def parse_whisper(data: dict) -> list[tuple[float, float, str]]:
    segments = []
    for item in data.get("transcription", []):
        words = str(item.get("text", "")).split()
        if words:
            offsets = item.get("offsets", {})
            segments.append((offsets.get("from", 0) / 1000, offsets.get("to", 0) / 1000, " ".join(words)))
    return segments


def detected_language(output: str) -> str | None:
    match = DETECTED.search(output)
    return base_lang(match.group(1)) if match else None


def transcribe(media: Path, work: Path, language: str | None, duration: float) -> tuple[list, dict]:
    """Local whisper in CHUNK-second parts, each saved as it finishes: a run stopped by an agent's
    command time limit resumes at the first unfinished part. Without a known language, detect it on
    30 s taken at 20 % of the video, since a silent or musical intro misleads detection."""
    need("whisper-cli")
    model = whisper_model()
    wav = work / "audio.wav"
    if not wav.is_file():
        partial = work / "audio.partial.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(partial)],
            "audio extraction")
        partial.replace(wav)
    duration = duration or source.duration(wav)
    lang = base_lang(language)
    if not lang:
        clip = work / "clip.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{duration * 0.2:.1f}", "-t", "30", "-i", str(wav),
             str(clip)], "audio clip")
        proc = run(["whisper-cli", "-m", model, "-f", str(clip), "-l", "auto", "-dl"], "language detection")
        clip.unlink(missing_ok=True)
        lang = detected_language(proc.stderr + proc.stdout) or "auto"
    parts = work / f"whisper-{Path(model).stem}-{lang}"
    parts.mkdir(exist_ok=True)
    segments = []
    for start in range(0, max(1, math.ceil(duration)), CHUNK):
        done = parts / f"{start:06d}.json"
        if not done.is_file():
            print(f"Transcribing {fmt_time(start)} to {fmt_time(min(start + CHUNK, duration))} "
                  f"of {fmt_time(duration)}…", flush=True)
            clip = parts / "clip.wav"
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(CHUNK), "-i", str(wav),
                 str(clip)], "audio clip")
            run(["whisper-cli", "-m", model, "-f", str(clip), "-l", lang, "-oj", "-of", str(parts / "part"), "-np"],
                "local transcription")
            (parts / "part.json").replace(done)
            clip.unlink(missing_ok=True)
        segments += [(s + start, e + start, text)
                     for s, e, text in parse_whisper(json.loads(done.read_text(encoding="utf-8")))]
    if not segments:
        raise VsumError("local transcription found no speech in this video.")
    wav.unlink(missing_ok=True)
    return segments, {"method": "whisper", "model": Path(model).name, "language": lang}
