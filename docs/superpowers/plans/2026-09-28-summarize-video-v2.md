# summarize-video v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the skill so one `prepare`, one agent-written `notes.md` and one `render` turn a video link or file into a single self-contained illustrated HTML summary.

**Architecture:** Five small Python modules run as scripts from `scripts/` (sys.path[0]): `common` (tool checks, time labels), `source` (yt-dlp/ffprobe metadata and the media file), `transcript` (captions → local whisper → `transcript.md`), `frames` (1 fps 64×36 scan → settled screen states → full-size frames + 4×4 contact sheets), `render` (Markdown subset → checked HTML, PDF via Chrome). `vsum.py` is the CLI.

**Tech Stack:** Python ≥ 3.10 stdlib + Pillow ≥ 10.1; ffmpeg/ffprobe; yt-dlp; whisper-cli (whisper.cpp) only without captions; Chrome/Chromium/Edge only for PDF. Tests: `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-28-summarize-video-v2-design.md`

## Global Constraints

- Python ≥ 3.10; third-party Python: Pillow only.
- yt-dlp always runs as `yt-dlp --ignore-config --no-playlist …`; never cookies or login options.
- No network other than yt-dlp; no audio upload; nothing installed automatically.
- Every user-fixable failure raises `common.VsumError` with one sentence: what is missing and the next step.
- Run tests from the repo root: `python3 -m unittest discover -s tests -v`. Each test file inserts `scripts/` into `sys.path`.
- Work folder layout: `source.json`, `transcript.md`, `video.<ext>` (URLs), `frames/Fnn.jpg`, `frames.json`, `sheets/sheet-nn.jpg`, `notes.md` (agent), `summary.html`, `summary.pdf`.
- Hebrew is the default summary language; the page is `lang="he" dir="rtl"` when notes are Hebrew; CSS uses logical properties only.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Clear v1; captions → transcript

**Files:**
- Delete: everything from v1 except `LICENSE`, `.git*`, `docs/superpowers/`
- Create: `scripts/common.py`, `scripts/transcript.py` (captions part), `tests/test_transcript.py`, `.gitignore`

**Interfaces:**
- Produces: `common.VsumError`, `common.need(tool) -> str`, `common.run(cmd, what, binary=False) -> CompletedProcess`, `common.fmt_time(seconds) -> str`, `common.parse_time(label) -> int`;
  `transcript.parse_captions(text) -> list[tuple[float, float, str]]`, `transcript.rank_tracks(info) -> list[tuple[str, bool]]` (key, is_auto),
  `transcript.fetch_captions(url, info, work) -> tuple[list, dict] | None`, `transcript.caption_file(path) -> tuple[list, dict]`,
  `transcript.sidecar(media) -> Path | None`, `transcript.paragraphs(segments) -> list[tuple[float, str]]`,
  `transcript.describe(t: dict) -> str`, `transcript.markdown(meta, segments) -> str`, `transcript.base_lang(code) -> str | None`.
  Transcript dicts: `{"method": "captions", "track", "auto", "language"}` | `{"method": "whisper", "model", "language"}` | `{"method": "file", "file"}`.

- [ ] **Step 1: Remove v1**

```bash
git rm -r -q AGENT-START.txt CHANGELOG.md CHAT-GUIDE.he.md CHAT-PROMPT.txt NOTICE README.md SECURITY.md SKILL.md bench docs/audits docs/example-he.png references scripts tests
git mv .github/workflows/ci.yml .github/workflows/ci.yml  # kept, rewritten in Task 7
```

- [ ] **Step 2: Write the failing tests** — `tests/test_transcript.py`

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import fmt_time, parse_time  # noqa: E402
import transcript  # noqa: E402

ROLLING = """WEBVTT
Kind: captions
Language: en

00:00:00.080 --> 00:00:02.230 align:start position:0%
 
So<00:00:00.320><c> many</c><00:00:00.480><c> devs</c>

00:00:02.230 --> 00:00:02.240 align:start position:0%
So many devs
 

00:00:02.240 --> 00:00:04.070 align:start position:0%
So many devs
work<00:00:02.399><c> with</c><00:00:02.639><c> LLMs.</c>
"""

SRT = """1
00:00:01,000 --> 00:00:03,500
Hello &amp; welcome

2
00:00:03,500 --> 00:00:06,000
<i>to the</i> lecture
"""

OVERLAP = """WEBVTT

00:00:00.000 --> 00:00:02.000
we will talk about tokens today

00:00:02.000 --> 00:00:04.000
about tokens today and how they cost money
"""


def vtt(url="u"):
    return [{"ext": "vtt", "url": url}]


class Captions(unittest.TestCase):
    def test_youtube_rolling_captions_keep_each_phrase_once(self):
        self.assertEqual(transcript.parse_captions(ROLLING),
                         [(0.08, 2.23, "So many devs"), (2.24, 4.07, "work with LLMs.")])

    def test_srt_with_entities_and_tags(self):
        self.assertEqual(transcript.parse_captions(SRT),
                         [(1.0, 3.5, "Hello & welcome"), (3.5, 6.0, "to the lecture")])

    def test_overlapping_cues_are_stripped(self):
        self.assertEqual(transcript.parse_captions(OVERLAP)[1][2], "and how they cost money")

    def test_rank_prefers_original_and_drops_translations(self):
        info = {"language": "en-US", "subtitles": {"de": vtt()},
                "automatic_captions": {"en-orig": vtt(), "en": vtt(), "iw": vtt("u&tlang=iw"),
                                       "fr": [{"ext": "json3", "url": "u"}]}}
        self.assertEqual(transcript.rank_tracks(info), [("en-orig", True), ("en", True), ("de", False)])

    def test_manual_hebrew_beats_automatic_original(self):
        info = {"language": "en", "subtitles": {"iw": vtt()}, "automatic_captions": {"en-orig": vtt()}}
        self.assertEqual(transcript.rank_tracks(info), [("iw", False), ("en-orig", True)])

    def test_base_lang(self):
        self.assertEqual(transcript.base_lang("iw"), "he")
        self.assertEqual(transcript.base_lang("en-US"), "en")
        self.assertIsNone(transcript.base_lang(None))


class Paragraphs(unittest.TestCase):
    def test_break_near_30_seconds_at_sentence_end(self):
        segments = [(i * 10.0, i * 10.0 + 10, f"sentence {i}.") for i in range(7)]
        self.assertEqual(transcript.paragraphs(segments), [
            (0.0, "sentence 0. sentence 1. sentence 2."),
            (30.0, "sentence 3. sentence 4. sentence 5."),
            (60.0, "sentence 6.")])

    def test_without_punctuation_break_at_60_seconds(self):
        segments = [(i * 10.0, i * 10.0 + 10, f"words {i}") for i in range(8)]
        self.assertEqual([p[0] for p in transcript.paragraphs(segments)], [0.0, 60.0])

    def test_markdown_header_and_lines(self):
        meta = {"title": "Talk", "url": "https://youtu.be/x", "duration": 75,
                "chapters": [{"t": 0, "title": "Intro"}, {"t": 33, "title": "Tokens"}],
                "transcript": {"method": "captions", "track": "en-orig", "auto": True, "language": "en"}}
        text = transcript.markdown(meta, [(0.0, 5.0, "Hello there."), (40.0, 45.0, "Tokens cost money.")])
        self.assertIn("- Creator chapters: 00:00 Intro · 00:33 Tokens", text)
        self.assertIn("- Transcript: automatic captions, track en-orig", text)
        self.assertIn("- Words: 5", text)
        self.assertIn("[00:00] Hello there. Tokens cost money.", text)


class Times(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(fmt_time(59.9), "00:59")
        self.assertEqual(fmt_time(3723), "1:02:03")
        self.assertEqual(parse_time("12:34"), 754)
        self.assertEqual(parse_time("1:02:03"), 3723)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run — expect failure** `python3 -m unittest tests.test_transcript -v` → `ModuleNotFoundError: No module named 'common'`

- [ ] **Step 4: Implement** `scripts/common.py`

```python
"""Helpers every stage shares: tool checks, running tools, time labels."""
import shutil
import subprocess

INSTALL = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "yt-dlp": "brew install yt-dlp",
    "whisper-cli": "brew install whisper-cpp",
}


class VsumError(Exception):
    """Something the user can fix; the message says what and how."""


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        hint = INSTALL.get(tool)
        raise VsumError(f"{tool} is not installed" + (f" (install: {hint})" if hint else "") + ", then run again.")
    return path


def run(cmd: list[str], what: str, binary: bool = False) -> subprocess.CompletedProcess:
    """Run a tool; a failure becomes a VsumError carrying the tool's own last error line."""
    need(cmd[0])
    if binary:
        proc = subprocess.run(cmd, capture_output=True)
        err = proc.stderr.decode("utf-8", "replace")
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        err = proc.stderr
    if proc.returncode != 0:
        lines = [line.strip() for line in (err or "").splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR")] or lines
        raise VsumError(f"{what} failed: {errors[-1] if errors else f'exit code {proc.returncode}'}")
    return proc


def fmt_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_time(label: str) -> int:
    total = 0
    for part in label.split(":"):
        total = total * 60 + int(part)
    return total
```

`scripts/transcript.py` (captions part; Task 2 appends whisper):

```python
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
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


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
```

`.gitignore`:

```
__pycache__/
*.py[cod]
.DS_Store
vsum-*/
```

- [ ] **Step 5: Run — expect pass** `python3 -m unittest tests.test_transcript -v` → all OK
- [ ] **Step 6: Commit** `git add -A && git commit -m "v2: clear v1; captions → transcript"`

---

### Task 2: Local whisper

**Files:** Modify `scripts/transcript.py` (append), `tests/test_transcript.py` (append class)

**Interfaces:**
- Produces: `transcript.whisper_model() -> str`, `transcript.parse_whisper(data) -> list`, `transcript.detected_language(output) -> str | None`,
  `transcript.transcribe(media: Path, work: Path, language: str | None, duration: float) -> tuple[list, dict]`.

- [ ] **Step 1: Failing tests** (append to `tests/test_transcript.py`, before the `__main__` guard)

```python
import os  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from common import VsumError  # noqa: E402


class Whisper(unittest.TestCase):
    def test_parse_json(self):
        data = {"transcription": [{"offsets": {"from": 0, "to": 2500}, "text": " שלום  עולם "},
                                  {"offsets": {"from": 2500, "to": 3000}, "text": " "}]}
        self.assertEqual(transcript.parse_whisper(data), [(0.0, 2.5, "שלום עולם")])

    def test_detected_language(self):
        self.assertEqual(transcript.detected_language("x auto-detected language: he (p = 0.99)"), "he")
        self.assertIsNone(transcript.detected_language("nothing here"))

    def test_model_from_environment(self):
        with tempfile.NamedTemporaryFile(suffix=".bin") as model, \
                mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": model.name}):
            self.assertEqual(transcript.whisper_model(), model.name)

    def test_model_from_v1_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp, "ggml.bin")
            model.write_bytes(b"x")
            reg = Path(tmp, "local-model.json")
            reg.write_text('{"model_path": "%s"}' % model)
            with mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": ""}), \
                    mock.patch.object(transcript, "_registration", return_value=reg):
                self.assertEqual(transcript.whisper_model(), str(model))

    def test_no_model_says_what_to_do(self):
        with mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": ""}), \
                mock.patch.object(transcript, "_registration", return_value=None):
            with self.assertRaisesRegex(VsumError, "VSUM_WHISPER_MODEL"):
                transcript.whisper_model()
```

- [ ] **Step 2: Run — expect failure** `python3 -m unittest tests.test_transcript -v` → `AttributeError: module 'transcript' has no attribute 'parse_whisper'`

- [ ] **Step 3: Implement** (append to `scripts/transcript.py`)

```python
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
    """Local whisper over the whole file. Without a known language, detect it on 30 s taken at 20 %
    of the video — a silent or musical intro misleads detection."""
    need("whisper-cli")
    model = whisper_model()
    wav = work / "audio.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        "audio extraction")
    lang = base_lang(language)
    if not lang:
        clip = work / "clip.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{duration * 0.2:.1f}", "-t", "30", "-i", str(wav),
             str(clip)], "audio clip")
        proc = run(["whisper-cli", "-m", model, "-f", str(clip), "-l", "auto", "-dl"], "language detection")
        clip.unlink(missing_ok=True)
        lang = detected_language(proc.stderr + proc.stdout) or "auto"
    out = work / "whisper"
    run(["whisper-cli", "-m", model, "-f", str(wav), "-l", lang, "-oj", "-of", str(out), "-np"],
        "local transcription")
    wav.unlink(missing_ok=True)
    segments = parse_whisper(json.loads(out.with_suffix(".json").read_text(encoding="utf-8")))
    if not segments:
        raise VsumError("local transcription found no speech in this video.")
    return segments, {"method": "whisper", "model": Path(model).name, "language": lang}
```

- [ ] **Step 4: Run — expect pass** `python3 -m unittest tests.test_transcript -v`
- [ ] **Step 5: Commit** `git commit -am "v2: local whisper fallback"`

---

### Task 3: Screen states → candidate moments

**Files:** Create `scripts/frames.py` (selection part), `tests/test_frames.py`

**Interfaces:**
- Produces: constants `W=64, H=36`; `frames.scan(video) -> list[Image]`, `frames.overlay_mask(samples) -> Image` ('L', 255 = compared),
  `frames.settled(samples, mask) -> list[tuple[int, int]]`, `frames.pick(samples, cap) -> list[{"t": float, "held": int}]`,
  `frames.default_cap(duration) -> int`.

- [ ] **Step 1: Failing tests** — `tests/test_frames.py`

```python
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image, ImageDraw  # noqa: E402

import frames  # noqa: E402
from frames import H, W  # noqa: E402


def slide(box, background=40):
    image = Image.new("L", (W, H), background)
    ImageDraw.Draw(image).rectangle(box, fill=215)
    return image


A = slide((4, 4, 30, 30))
B = slide((34, 4, 60, 30))
BLANK = Image.new("L", (W, H), 128)


def noise(rng, image=None, box=(0, 0, W, H)):
    image = (image or BLANK).copy()
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            image.putpixel((x, y), rng.randrange(256))
    return image


class Pick(unittest.TestCase):
    def test_one_frame_per_distinct_state_blank_and_revisit_dropped(self):
        samples = [A] * 10 + [BLANK] * 5 + [B] * 10 + [A] * 10
        self.assertEqual(frames.pick(samples, 32), [{"t": 9.0, "held": 10}, {"t": 24.0, "held": 10}])

    def test_cap_keeps_the_states_held_longest(self):
        samples = [A] * 10 + [B] * 6
        self.assertEqual(frames.pick(samples, 1), [{"t": 9.0, "held": 10}])

    def test_webcam_overlay_is_masked(self):
        rng = random.Random(0)
        webcam = (W - 16, H - 12, W, H)
        samples = [noise(rng, A, webcam) for _ in range(20)]
        mask = frames.overlay_mask(samples)
        self.assertEqual(mask.getpixel((W - 5, H - 5)), 0)
        self.assertEqual(mask.getpixel((5, 5)), 255)
        self.assertEqual(len(frames.pick(samples, 32)), 1)

    def test_long_stretch_without_a_settled_screen_gets_pictures(self):
        rng = random.Random(1)
        samples = [noise(rng) for _ in range(300)]
        picked = frames.pick(samples, 32)
        self.assertEqual(len(picked), 2)
        self.assertTrue(104 <= picked[0]["t"] <= 134 and 224 <= picked[1]["t"] <= 254, picked)

    def test_default_cap(self):
        self.assertEqual(frames.default_cap(600), 32)
        self.assertEqual(frames.default_cap(1800), 48)
        self.assertEqual(frames.default_cap(7200), 96)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect failure** `python3 -m unittest tests.test_frames -v` → `ModuleNotFoundError: No module named 'frames'`

- [ ] **Step 3: Implement** `scripts/frames.py`

```python
"""The pictures: every screen state that held still, saved full size, and contact sheets to pick from."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from common import fmt_time, run

W, H = 64, 36         # scan size; one sample per second
MOVING = 10           # a pixel moved between two samples when it changed by more than this
OVERLAY_SHARE = 0.30  # pixels moving in more than this share of pairs form an overlay (webcam, ticker)
OVERLAY_MAX = 0.35    # the overlay is masked only while it covers less than this share of the frame
STILL = 3.0           # mean change under this: the screen held still
MIN_HELD = 3          # seconds a state must hold to count
BLANK = 8.0           # pixel spread under this: a blank frame
SAME = 5.0            # mean change under this: the same picture as one already kept
GAP = 120             # seconds without a candidate before one is added


def scan(video: Path) -> list[Image.Image]:
    proc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps=1,scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"],
               "video scan", binary=True)
    size, data = W * H, proc.stdout
    return [Image.frombytes("L", (W, H), data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def overlay_mask(samples: list[Image.Image]) -> Image.Image:
    """255 where samples are compared; 0 over a small overlay that never stops moving."""
    everything = Image.new("L", (W, H), 255)
    if len(samples) < 3:
        return everything
    step = max(1, (len(samples) - 1) // 1000)
    pairs = range(0, len(samples) - 1, step)
    counts = [0] * (W * H)
    for p in pairs:
        moved = ImageChops.difference(samples[p], samples[p + 1]).point(lambda v: 255 if v > MOVING else 0)
        for i, value in enumerate(moved.tobytes()):
            if value:
                counts[i] += 1
    limit = OVERLAY_SHARE * len(pairs)
    hot = Image.new("L", (W, H))
    hot.putdata([255 if c > limit else 0 for c in counts])
    hot = hot.filter(ImageFilter.MaxFilter(3))
    share = sum(1 for v in hot.tobytes() if v) / (W * H)
    return everything if share == 0 or share > OVERLAY_MAX else ImageChops.invert(hot)


def difference(a: Image.Image, b: Image.Image, mask: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b), mask).mean[0]


def settled(samples: list[Image.Image], mask: Image.Image) -> list[tuple[int, int]]:
    """(first, last) second of every run in which the screen held still for MIN_HELD seconds."""
    runs, first = [], 0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or difference(samples[i - 1], samples[i], mask) >= STILL:
            if i - first >= MIN_HELD:
                runs.append((first, i - 1))
            first = i
    return runs


def pick(samples: list[Image.Image], cap: int) -> list[dict]:
    """Candidate moments in time order: the last second of each distinct settled state (the fully
    built slide), plus the calmest second of any stretch longer than GAP without one."""
    if not samples:
        return []
    mask = overlay_mask(samples)
    kept = []  # (second, seconds held)

    def novel(i: int) -> bool:
        return (ImageStat.Stat(samples[i], mask).stddev[0] >= BLANK
                and all(difference(samples[i], samples[k], mask) >= SAME for k, _ in kept))

    for first, last in settled(samples, mask):
        if novel(last):
            kept.append((last, last - first + 1))
    bounds = [-1] + sorted(k for k, _ in kept) + [len(samples)]
    for a, b in zip(bounds, bounds[1:]):
        for centre in range(a + GAP, b, GAP):
            window = range(max(a + 1, centre - 15), min(b, centre + 15))
            calmest = min(window, key=lambda i: difference(samples[i], samples[min(i + 1, len(samples) - 1)], mask))
            if novel(calmest):
                kept.append((calmest, 1))
    if len(kept) > cap:
        kept = sorted(kept, key=lambda k: -k[1])[:cap]
    return [{"t": float(t), "held": held} for t, held in sorted(kept)]


def default_cap(duration: float) -> int:
    """16 candidates per 10 minutes, between 32 and 96 (two to six contact sheets)."""
    return max(32, min(96, round(16 * duration / 600)))
```

- [ ] **Step 4: Run — expect pass** `python3 -m unittest tests.test_frames -v`
- [ ] **Step 5: Commit** `git add -A && git commit -m "v2: settled screen states as candidate frames"`

---

### Task 4: Source, full-size frames and contact sheets

**Files:** Create `scripts/source.py`, `tests/test_source.py`; modify `scripts/frames.py` (append), `tests/test_frames.py` (append)

**Interfaces:**
- Consumes: `common.run`, `common.need`, `common.VsumError`.
- Produces: `source.is_url(s)`, `source.youtube_id(url) -> str | None`, `source.slug(text) -> str`,
  `source.probe(source) -> tuple[meta: dict, info: dict]` (meta keys: kind url path id title duration uploader language chapters),
  `source.duration(path) -> float`, `source.has_video(path) -> bool`, `source.media(meta, work) -> Path`;
  `frames.extract(video, picks, folder) -> list[{"id", "t", "held"}]`, `frames.sheets(frames, folder, out) -> list[Path]`.

- [ ] **Step 1: Failing tests** — `tests/test_source.py`

```python
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import source  # noqa: E402
from common import VsumError  # noqa: E402


class Links(unittest.TestCase):
    def test_youtube_ids(self):
        for url in ("https://www.youtube.com/watch?v=aircAruvnKk&list=PLZ",
                    "https://youtu.be/aircAruvnKk?t=3", "https://www.youtube.com/shorts/aircAruvnKk",
                    "https://www.youtube.com/embed/aircAruvnKk", "https://m.youtube.com/watch?feature=x&v=aircAruvnKk"):
            self.assertEqual(source.youtube_id(url), "aircAruvnKk", url)
        self.assertIsNone(source.youtube_id("https://drive.google.com/file/d/abc/view"))

    def test_is_url_and_slug(self):
        self.assertTrue(source.is_url("https://x.com/v"))
        self.assertFalse(source.is_url("/tmp/talk.mp4"))
        self.assertEqual(source.slug("GMT2026 Recording (1)"), "GMT2026-Recording-1")

    def test_missing_file(self):
        with self.assertRaisesRegex(VsumError, "no such file"):
            source.probe("/no/such/talk.mp4")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class LocalFile(unittest.TestCase):
    def test_probe_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp, "My Talk.mp4")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=3",
                            str(video)], check=True)
            meta, info = source.probe(str(video))
            self.assertEqual((meta["kind"], meta["id"], meta["title"], info), ("file", "My-Talk", "My Talk", {}))
            self.assertAlmostEqual(meta["duration"], 3.0, delta=0.2)
            self.assertTrue(source.has_video(video))
            self.assertEqual(source.media(meta, Path(tmp)), video)


if __name__ == "__main__":
    unittest.main()
```

Append to `tests/test_frames.py` (before the `__main__` guard):

```python
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class Files(unittest.TestCase):
    def test_extract_and_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp, "v.mp4")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=s=640x360:d=20:r=10", str(video)], check=True)
            picks = [{"t": float(t), "held": 3} for t in range(0, 20)]
            made = frames.extract(video, picks, Path(tmp, "frames"))
            self.assertEqual([f["id"] for f in made[:2]], ["F01", "F02"])
            self.assertTrue(Path(tmp, "frames", "F20.jpg").is_file())
            with Image.open(Path(tmp, "frames", "F01.jpg")) as im:
                self.assertEqual(im.size, (640, 360))
            paths = frames.sheets(made, Path(tmp, "frames"), Path(tmp, "sheets"))
            self.assertEqual([p.name for p in paths], ["sheet-01.jpg", "sheet-02.jpg"])
            with Image.open(paths[0]) as first, Image.open(paths[1]) as second:
                self.assertEqual(first.size, (4 * 320, 4 * (180 + 26)))
                self.assertEqual(second.size, (4 * 320, 1 * (180 + 26)))
```

- [ ] **Step 2: Run — expect failure** `python3 -m unittest tests.test_source tests.test_frames -v` → `No module named 'source'` / `no attribute 'extract'`

- [ ] **Step 3: Implement** `scripts/source.py`

```python
"""Where the video comes from: its metadata, and the media file that frames and whisper read."""
import json
import re
from pathlib import Path

from common import VsumError, need, run

YTDLP = ["yt-dlp", "--ignore-config", "--no-playlist"]
YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})")


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def youtube_id(url: str | None) -> str | None:
    match = YOUTUBE_ID.search(url or "")
    return match.group(1) if match else None


def slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text).strip("-")[:60] or "video"


def probe(text: str) -> tuple[dict, dict]:
    """(meta, info): meta is saved as source.json; info is yt-dlp's full record ({} for a file)."""
    if is_url(text):
        need("yt-dlp")
        info = json.loads(run(YTDLP + ["-J", text], "reading the video page").stdout)
        url = info.get("webpage_url") or text
        vid = youtube_id(url)
        meta = {"kind": "youtube" if vid else "url", "url": url, "path": None,
                "id": vid or slug(str(info.get("id") or info.get("title") or "video")),
                "title": info.get("title") or url, "duration": float(info.get("duration") or 0),
                "uploader": info.get("uploader") or info.get("channel"), "language": info.get("language"),
                "chapters": [{"t": float(c.get("start_time") or 0), "title": c.get("title") or ""}
                             for c in info.get("chapters") or []]}
        return meta, info
    path = Path(text).expanduser().resolve()
    if not path.is_file():
        raise VsumError(f"no such file: {path}")
    return {"kind": "file", "url": None, "path": str(path), "id": slug(path.stem), "title": path.stem,
            "duration": duration(path), "uploader": None, "language": None, "chapters": []}, {}


def _ffprobe(path: Path, *args: str) -> dict:
    proc = run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)], "reading the media file")
    return json.loads(proc.stdout or "{}")


def duration(path: Path) -> float:
    return float(_ffprobe(path, "-show_entries", "format=duration").get("format", {}).get("duration") or 0)


def has_video(path: Path) -> bool:
    return bool(_ffprobe(path, "-select_streams", "v", "-show_entries", "stream=codec_type").get("streams"))


def media(meta: dict, work: Path) -> Path:
    """The file to read: the user's own, or one ≤720p download (H.264 preferred) with its audio."""
    if meta["kind"] == "file":
        return Path(meta["path"])

    def done() -> list[Path]:
        return [p for p in work.glob("video.*") if p.name.count(".") == 1]  # not video.f137.mp4 / .part

    if not done():
        run(YTDLP + ["-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b", "-S", "vcodec:h264,res:720,acodec:m4a",
                     "--merge-output-format", "mp4", "--no-progress", "-o", str(work / "video.%(ext)s"),
                     meta["url"]], "video download")
    files = done()
    if not files:
        raise VsumError("the video download finished without a file; run prepare again.")
    return files[0]
```

Append to `scripts/frames.py`:

```python
TILE, BAR, COLS, ROWS = (320, 180), 26, 4, 4


def extract(video: Path, picks: list[dict], folder: Path) -> list[dict]:
    """Full-size JPEGs F01.jpg… (at most 1280 px wide); returns [{id, t, held}]."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for pick_ in picks:
        fid = f"F{len(made) + 1:02d}"
        path = folder / f"{fid}.jpg"
        for t in (pick_["t"], max(0.0, pick_["t"] - 1)):  # a seek to the very end can land past the last frame
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                 "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(path)], f"frame {fid}")
            if path.is_file():
                made.append({"id": fid, "t": t, "held": pick_["held"]})
                break
    return made


def _font(size: int = 20):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def sheets(made: list[dict], folder: Path, out: Path) -> list[Path]:
    """4×4 grids of the candidates, 'F07 · 12:34' burned under each tile."""
    out.mkdir(parents=True, exist_ok=True)
    font, per, paths = _font(), COLS * ROWS, []
    for start in range(0, len(made), per):
        page = made[start:start + per]
        rows = -(-len(page) // COLS)
        sheet = Image.new("RGB", (COLS * TILE[0], rows * (TILE[1] + BAR)), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for n, frame in enumerate(page):
            x, y = (n % COLS) * TILE[0], (n // COLS) * (TILE[1] + BAR)
            with Image.open(folder / f"{frame['id']}.jpg") as image:
                tile = image.convert("RGB")
            tile.thumbnail(TILE)
            sheet.paste(tile, (x + (TILE[0] - tile.width) // 2, y + (TILE[1] - tile.height) // 2))
            draw.rectangle([x, y + TILE[1], x + TILE[0] - 1, y + TILE[1] + BAR - 1], fill=(0, 0, 0))
            draw.text((x + 8, y + TILE[1] + 2), f"{frame['id']} · {fmt_time(frame['t'])}", fill=(255, 255, 255), font=font)
        path = out / f"sheet-{start // per + 1:02d}.jpg"
        sheet.save(path, quality=85)
        paths.append(path)
    return paths
```

- [ ] **Step 4: Run — expect pass** `python3 -m unittest tests.test_source tests.test_frames -v`
- [ ] **Step 5: Commit** `git add -A && git commit -m "v2: source probe/download, full-size frames, contact sheets"`

---

### Task 5: notes.md → HTML

**Files:** Create `scripts/render.py`, `scripts/fonts/FrankRuhlLibre-hebrew.woff2` (Hebrew subset of the OFL font), `scripts/fonts/OFL.txt`, `tests/test_render.py`

**Interfaces:**
- Consumes: `common.fmt_time`, `common.parse_time`; `frames.json` rows `{id, t, held}`; `source.json` meta.
- Produces: `render.parse(text) -> list[dict]` (kinds title h2 h3 p ul ol quote code figure; key `line`),
  `render.inline(text, link) -> str`, `render.check(blocks, times, duration) -> (errors, warnings)`,
  `render.build(notes, meta, frames, folder) -> (html, errors, warnings)`, `render.pdf(page, out) -> bool`.

- [ ] **Step 1: Copy the font and its licence**

```bash
cp "<a local copy of FrankRuhlLibre.woff2: the Hebrew subset served by Google Fonts>" scripts/fonts/FrankRuhlLibre-hebrew.woff2
```

`scripts/fonts/OFL.txt`: the SIL OFL 1.1 text (from v1's `scripts/fonts/OFL.txt`), first line
`Copyright 2015 The Frank Ruhl Libre Project Authors (https://github.com/fontef/frankruhllibre)`.

- [ ] **Step 2: Failing tests** — `tests/test_render.py`

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image  # noqa: E402

import render  # noqa: E402

YT = {"kind": "youtube", "id": "abcdefghijk", "url": "https://www.youtube.com/watch?v=abcdefghijk",
      "title": "Original title", "duration": 600, "uploader": "Speaker",
      "transcript": {"method": "captions", "track": "en-orig", "auto": True, "language": "en"}}
NOTES = """# איך טוקנים עובדים

> הטענה המרכזית: טוקן אינו מילה.
>
> פסקה שנייה בתקציר.

## עיקרי הדברים
- נקודה ראשונה עם `tiktoken`
  שממשיכה בשורה שנייה.

## [02:15] מה זה טוקן
טקסט עם **הדגשה** ו-<script>alert(1)</script> וקישור https://example.com/a?b=1&c=2.
![F01](התרשים המלא של `Split`)

```bash
echo [99:99]
```
"""


def frame_dir(tmp):
    folder = Path(tmp, "frames")
    folder.mkdir()
    Image.new("RGB", (32, 18), (200, 30, 30)).save(folder / "F01.jpg")
    return folder


class Parse(unittest.TestCase):
    def test_blocks(self):
        kinds = [(b["kind"], b["line"]) for b in render.parse(NOTES)]
        self.assertEqual(kinds, [("title", 1), ("quote", 3), ("h2", 7), ("ul", 8), ("h2", 11), ("p", 12),
                                 ("figure", 13), ("code", 15)])
        blocks = render.parse(NOTES)
        self.assertEqual(blocks[3]["items"], ["נקודה ראשונה עם `tiktoken` שממשיכה בשורה שנייה."])
        self.assertEqual(blocks[6]["id"], "F01")

    def test_short_frame_ids_are_normalised(self):
        self.assertEqual(render.parse("![F7](x)")[0]["id"], "F07")


class Inline(unittest.TestCase):
    def test_escaping_code_bold(self):
        out = render.inline("a <b> **c** `x<y`", None)
        self.assertEqual(out, 'a &lt;b&gt; <strong>c</strong> <code dir="ltr">x&lt;y</code>')

    def test_timestamps(self):
        link = lambda s: f"https://www.youtube.com/watch?v=abcdefghijk&t={s}s"
        self.assertIn('href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=754s"', render.inline("[12:34]", link))
        self.assertEqual(render.inline("[1:02:03]", None), '<span class="ts" dir="ltr">1:02:03</span>')

    def test_url_keeps_trailing_period_outside(self):
        out = render.inline("see https://x.com/a.", None)
        self.assertEqual(out, 'see <a href="https://x.com/a" dir="ltr">https://x.com/a</a>.')


class Check(unittest.TestCase):
    def test_problems_have_line_numbers(self):
        blocks = render.parse("# t\n\n## [20:00] late\n![F09](x)\n![F01]()\n![F01](again)\nשָׁלוֹם\n")
        errors, warnings = render.check(blocks, {"F01": 10.0}, 600)
        self.assertIn("line 3: [20:00] is after the end of the video (10:00)", errors)
        self.assertIn("line 4: F09 is not one of the frames (see frames.json)", errors)
        self.assertIn("line 5: F01 needs a caption: ![F01](what it shows and why it is here)", errors)
        self.assertIn("line 6: F01 is already used on line 5", warnings)
        self.assertTrue(any("niqqud" in w for w in warnings))

    def test_chapters_required(self):
        errors, _ = render.check(render.parse("# only a title\n\ntext"), {}, 60)
        self.assertEqual(errors, ["line 1: no chapters; start each with a `## ` heading"])


class Build(unittest.TestCase):
    def test_hebrew_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, errors, _ = render.build(NOTES, YT, [{"id": "F01", "t": 135.0, "held": 4}], frame_dir(tmp))
        self.assertEqual(errors, [])
        self.assertIn('<html lang="he" dir="rtl">', page)
        self.assertIn("data:image/jpeg;base64,", page)
        self.assertIn('<nav class="toc"', page)
        self.assertIn('class="brief"', page)
        self.assertIn("&amp;t=135s", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("echo [99:99]", page)

    def test_english_page_and_bidi_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, errors, warnings = render.build("# Tokens‏\n\n## Intro\nText.\n", dict(YT, kind="url"), [],
                                                  frame_dir(tmp))
        self.assertEqual(errors, [])
        self.assertIn('<html lang="en" dir="ltr">', page)
        self.assertNotIn("‏", page)
        self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run — expect failure** `python3 -m unittest tests.test_render -v` → `No module named 'render'`

- [ ] **Step 4: Implement** `scripts/render.py`

```python
"""notes.md → one self-contained HTML page; a PDF through Chrome, Chromium or Edge when asked."""
import base64
import datetime
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import fmt_time, parse_time

FONT = Path(__file__).resolve().parent / "fonts" / "FrankRuhlLibre-hebrew.woff2"
FIGURE = re.compile(r"^!\[F(\d+)\]\((.*)\)$")
TIME = re.compile(r"\[(\d{1,2}(?::\d{2}){1,2})\]")
LEAD_TIME = re.compile(r"^\[(\d{1,2}(?::\d{2}){1,2})\]\s*")
CODE = re.compile(r"(`[^`\n]+`)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
URL = re.compile(r"https?://[^\s<>\"'\]]+")
HEBREW = re.compile(r"[֐-׿]")
LATIN = re.compile(r"[A-Za-z]")
NIQQUD = re.compile(r"[֑-ׇֽֿׁׂׅׄ]")
BIDI = re.compile(r"[‎‏‪-‮⁦-⁩]")
LISTS = {"ul": re.compile(r"^[-*]\s+"), "ol": re.compile(r"^\d+[.)]\s+"), "quote": re.compile(r"^>\s?")}
BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge"]

STRINGS = {
    "he": {"toc": "תוכן העניינים", "watch": "לצפייה בסרטון", "by": "מאת", "length": "אורך",
           "transcript": "מקור התמליל", "manual": "כתוביות ידניות", "auto": "כתוביות אוטומטיות",
           "whisper": "תמלול מקומי", "file": "קובץ כתוביות",
           "footer": "הופק בתאריך {date} באמצעות הכלי <bdi>summarize-video</bdi>, מתוך התמליל והפריימים של הסרטון."},
    "en": {"toc": "Contents", "watch": "Watch the video", "by": "by", "length": "Length",
           "transcript": "Transcript", "manual": "manual captions", "auto": "automatic captions",
           "whisper": "local transcription", "file": "subtitle file",
           "footer": "Written from the video's transcript and frames with summarize-video on {date}."},
}

CSS = """
:root{--ink:#1c1d1f;--muted:#5d6068;--paper:#fbfbf9;--rule:#e4e3de;--wash:#f0efeb;--accent:#b3302a;
--measure:41rem;--wide:60rem}
*{box-sizing:border-box}
html{background:var(--paper);color:var(--ink);-webkit-text-size-adjust:100%}
body{margin:0;font:1.125rem/1.9 "Frank Ruhl Libre","Iowan Old Style","Palatino Linotype",Georgia,serif;
font-kerning:normal;text-rendering:optimizeLegibility}
.masthead,main,footer{max-inline-size:var(--measure);margin-inline:auto;padding-inline:1.25rem}
.masthead{padding-block:4.5rem 2rem;border-block-end:1px solid var(--rule)}
h1{font-size:clamp(2rem,5vw,2.75rem);line-height:1.2;font-weight:800;margin:0 0 1.25rem}
.meta{margin:0;color:var(--muted);font-size:.95rem;line-height:1.8}
.meta a{color:var(--accent)}
main{padding-block:1rem 2rem}
.brief{margin:2.5rem 0 0;padding-inline-start:1.25rem;border-inline-start:3px solid var(--accent);
font-size:1.25rem;line-height:1.85}
.brief p{margin:0 0 .8rem}
.toc{margin-block:3rem 1rem;font-size:1rem;line-height:1.6}
.toc-title{margin:0 0 .6rem;font-weight:700;color:var(--muted)}
.toc ol{margin:0;padding-inline-start:1.4rem;columns:2 17rem;column-gap:2.5rem}
.toc li{break-inside:avoid;margin-block-end:.35rem}
.toc a{color:inherit;text-decoration:none}
.toc a:hover{text-decoration:underline}
.toc .t{margin-inline-start:.45rem;color:var(--muted);font:.78rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h2{font-size:1.8rem;line-height:1.35;font-weight:800;margin:4.5rem 0 1rem}
h3{font-size:1.3rem;line-height:1.4;font-weight:700;margin:2.5rem 0 .6rem}
p{margin:0 0 1.15rem}
ul,ol{margin:0 0 1.25rem;padding-inline-start:1.4rem}
li{margin-block:.4rem}
blockquote{margin:1.6rem 0;padding-inline-start:1.1rem;border-inline-start:2px solid var(--rule);color:#35383d}
code{font:.84em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--wash);padding:.1em .35em;
border-radius:3px;unicode-bidi:isolate}
pre{direction:ltr;text-align:start;background:var(--wash);padding:1rem 1.2rem;border-radius:4px;overflow-x:auto;
font:.88rem/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre code{background:none;padding:0;font-size:inherit}
.ts{font:600 .76rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--accent);text-decoration:none;
border:1px solid currentColor;border-radius:3px;padding:.2em .38em;vertical-align:.15em;unicode-bidi:isolate;
white-space:nowrap}
a.ts:hover{background:var(--accent);color:var(--paper)}
h2 .ts{margin-inline-end:.6rem;vertical-align:.35em}
figure{margin-block:2.4rem;margin-inline:calc(50% - min(var(--wide),100vw - 2.5rem) / 2)}
figure img{display:block;inline-size:100%;block-size:auto;border-radius:4px;background:var(--wash);cursor:zoom-in}
figcaption{max-inline-size:var(--measure);margin:.75rem auto 0;padding-inline:1.25rem;color:var(--muted);
font-size:.95rem;line-height:1.75}
figcaption .ts{margin-inline-end:.5rem}
footer{padding-block:2.5rem 4rem;color:var(--muted);font-size:.9rem;border-block-start:1px solid var(--rule)}
#zoom{position:fixed;inset:0;display:grid;place-items:center;padding:2vmin;background:rgb(18 19 21/.92);
cursor:zoom-out;z-index:9}
#zoom[hidden]{display:none}
#zoom img{max-inline-size:100%;max-block-size:100%;object-fit:contain}
@page{size:A4;margin:16mm 15mm}
@media print{html{background:#fff}body{font-size:11pt;line-height:1.75}.masthead{padding-block:0 1rem}
h2{break-after:avoid;margin-top:2.2rem}figure{break-inside:avoid;margin-inline:0}figcaption{padding-inline:0}
a{color:inherit}#zoom{display:none}}
"""

SCRIPT = """const z=document.getElementById('zoom');
document.addEventListener('click',e=>{if(e.target.matches('figure img')){z.firstElementChild.src=e.target.src;z.hidden=false}
else if(!z.hidden){z.hidden=true}});
document.addEventListener('keydown',e=>{if(e.key==='Escape')z.hidden=true});"""


def _block_start(line: str) -> bool:
    return line.startswith(("#", "```")) or bool(FIGURE.match(line)) or any(p.match(line) for p in LISTS.values())


def parse(text: str) -> list[dict]:
    """Blocks of the Markdown subset, each with the line it starts on."""
    lines = text.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        line, number = lines[i].strip(), i + 1
        if not line:
            i += 1
        elif line.startswith("```"):
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append({"kind": "code", "text": "\n".join(body), "line": number})
            i += 1
        elif FIGURE.match(line):
            num, caption = FIGURE.match(line).groups()
            blocks.append({"kind": "figure", "id": f"F{int(num):02d}", "text": caption.strip(), "line": number})
            i += 1
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            blocks.append({"kind": {1: "title", 2: "h2"}.get(level, "h3"), "text": line[level:].strip(), "line": number})
            i += 1
        else:
            kind = next((k for k, p in LISTS.items() if p.match(line)), None)
            if kind:
                items = []
                while i < len(lines) and LISTS[kind].match(lines[i].strip()):
                    items.append(LISTS[kind].sub("", lines[i].strip(), count=1))
                    i += 1
                    while (i < len(lines) and lines[i][:1] in (" ", "\t") and lines[i].strip()
                           and not _block_start(lines[i].strip())):
                        items[-1] += " " + lines[i].strip()
                        i += 1
                blocks.append({"kind": kind, "items": items, "line": number})
            else:
                body, i = [line], i + 1
                while i < len(lines) and lines[i].strip() and not _block_start(lines[i].strip()):
                    body.append(lines[i].strip())
                    i += 1
                blocks.append({"kind": "p", "text": " ".join(body), "line": number})
    return blocks


def stamp(label: str, link) -> str:
    target = link(parse_time(label)) if link else None
    if target:
        return f'<a class="ts" href="{html.escape(target)}" dir="ltr">{label}</a>'
    return f'<span class="ts" dir="ltr">{label}</span>'


def _url(match) -> str:
    url = match.group(0).rstrip(".,;:!?")
    return f'<a href="{url}" dir="ltr">{url}</a>{match.group(0)[len(url):]}'


def inline(text: str, link) -> str:
    """Escaped text with `code`, **bold**, bare URLs and [mm:ss] links to the source."""
    out = []
    for part in CODE.split(text):
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            out.append(f'<code dir="ltr">{html.escape(part[1:-1])}</code>')
            continue
        part = html.escape(part, quote=False)
        part = BOLD.sub(r"<strong>\1</strong>", part)
        part = URL.sub(_url, part)
        out.append(TIME.sub(lambda m: stamp(m.group(1), link), part))
    return "".join(out)


def check(blocks: list[dict], times: dict, duration: float) -> tuple[list[str], list[str]]:
    errors, warnings, used = [], [], {}
    if not any(b["kind"] == "h2" for b in blocks):
        errors.append("line 1: no chapters; start each with a `## ` heading")
    for b in blocks:
        n = b["line"]
        if b["kind"] == "code":
            continue
        if b["kind"] == "figure":
            if b["id"] not in times:
                errors.append(f"line {n}: {b['id']} is not one of the frames (see frames.json)")
            if not b["text"]:
                errors.append(f"line {n}: {b['id']} needs a caption: ![{b['id']}](what it shows and why it is here)")
            if b["id"] in used:
                warnings.append(f"line {n}: {b['id']} is already used on line {used[b['id']]}")
            used.setdefault(b["id"], n)
        for text in b.get("items") or [b.get("text", "")]:
            for m in TIME.finditer(text):
                if duration and parse_time(m.group(1)) > duration + 5:
                    errors.append(f"line {n}: [{m.group(1)}] is after the end of the video ({fmt_time(duration)})")
            if NIQQUD.search(text):
                warnings.append(f"line {n}: niqqud; write Hebrew without vowel points")
    return errors, warnings


def _source_link(meta: dict):
    if meta.get("kind") == "youtube" and meta.get("id"):
        return lambda seconds: f"https://www.youtube.com/watch?v={meta['id']}&t={seconds}s"
    return None


def _meta_line(meta: dict, s: dict) -> str:
    bits = []
    if meta.get("url"):
        bits.append(f'<a href="{html.escape(meta["url"])}">{s["watch"]}</a>')
    bits.append(f'<bdi dir="auto">{html.escape(meta.get("title") or "")}</bdi>')
    if meta.get("uploader"):
        bits.append(f'{s["by"]} <bdi dir="auto">{html.escape(meta["uploader"])}</bdi>')
    if meta.get("duration"):
        bits.append(f'{s["length"]} <bdi dir="ltr">{fmt_time(meta["duration"])}</bdi>')
    t = meta.get("transcript") or {}
    how = {"captions": s["auto"] if t.get("auto") else s["manual"], "whisper": s["whisper"],
           "file": s["file"]}.get(t.get("method"))
    if how:
        bits.append(f'{s["transcript"]}: {how}')
    return " · ".join(bits)


def _body(blocks: list[dict], times: dict, folder: Path, link) -> tuple[list[str], list[tuple]]:
    parts, toc, toc_at = [], [], None
    for b in blocks:
        kind = b["kind"]
        if kind == "h2":
            toc_at = len(parts) if toc_at is None else toc_at
            lead = LEAD_TIME.match(b["text"])
            name = b["text"][lead.end():] if lead else b["text"]
            toc.append((len(toc) + 1, name, lead.group(1) if lead else None))
            chip = stamp(lead.group(1), link) if lead else ""
            parts.append(f'<h2 id="s{len(toc)}">{chip}{inline(name, link)}</h2>')
        elif kind == "h3":
            parts.append(f"<h3>{inline(b['text'], link)}</h3>")
        elif kind == "p":
            parts.append(f"<p>{inline(b['text'], link)}</p>")
        elif kind in ("ul", "ol"):
            parts.append(f"<{kind}>" + "".join(f"<li>{inline(x, link)}</li>" for x in b["items"]) + f"</{kind}>")
        elif kind == "quote":
            paras, current = [], []
            for item in b["items"] + [""]:
                if item:
                    current.append(item)
                elif current:
                    paras.append(" ".join(current))
                    current = []
            brief = ' class="brief"' if not toc and not any('class="brief"' in p for p in parts) else ""
            parts.append(f"<blockquote{brief}>" + "".join(f"<p>{inline(p, link)}</p>" for p in paras) + "</blockquote>")
        elif kind == "code":
            parts.append(f'<pre dir="ltr"><code>{html.escape(b["text"])}</code></pre>')
        elif kind == "figure":
            data = base64.b64encode((folder / f"{b['id']}.jpg").read_bytes()).decode()
            alt = html.escape(re.sub(r"[`*]", "", b["text"]))
            parts.append(f'<figure><img src="data:image/jpeg;base64,{data}" alt="{alt}"><figcaption>'
                         f'{stamp(fmt_time(times[b["id"]]), link)}{inline(b["text"], link)}</figcaption></figure>')
    if toc:
        parts.insert(toc_at, toc)  # placeholder, filled by build()
    return parts, toc


def build(notes: str, meta: dict, frames: list[dict], folder: Path) -> tuple[str, list[str], list[str]]:
    """(html, errors, warnings); html is empty while there are errors."""
    warnings = []
    if BIDI.search(notes):
        notes = BIDI.sub("", notes)
        warnings.append("removed invisible direction marks; the page isolates directions itself")
    blocks = parse(notes)
    times = {f["id"]: f["t"] for f in frames}
    errors, found = check(blocks, times, meta.get("duration") or 0)
    warnings += found
    missing = sorted({b["id"] for b in blocks if b["kind"] == "figure" and b["id"] in times
                      and not (folder / f"{b['id']}.jpg").is_file()})
    errors += [f"{fid}.jpg is missing from {folder}; run prepare again" for fid in missing]
    if errors:
        return "", errors, warnings
    hebrew, latin = len(HEBREW.findall(notes)), len(LATIN.findall(notes))
    lang = "he" if hebrew > 0.3 * (hebrew + latin) else "en"
    s, link = STRINGS[lang], _source_link(meta)
    title = next((b["text"] for b in blocks if b["kind"] == "title"), meta.get("title") or "")
    parts, toc = _body([b for b in blocks if b["kind"] != "title"], times, folder, link)
    if toc:
        items = "".join(f'<li><a href="#s{i}">{inline(name, None)}</a>'
                        + (f'<span class="t" dir="ltr">{t}</span>' if t else "") + "</li>" for i, name, t in toc)
        nav = f'<nav class="toc" aria-label="{s["toc"]}"><p class="toc-title">{s["toc"]}</p><ol>{items}</ol></nav>'
        parts = [nav if isinstance(p, list) else p for p in parts]
    today = datetime.date.today()
    date = today.strftime("%d.%m.%Y") if lang == "he" else today.isoformat()
    font = ""
    if FONT.is_file():
        data = base64.b64encode(FONT.read_bytes()).decode()
        font = ('@font-face{font-family:"Frank Ruhl Libre";font-weight:300 900;font-display:swap;'
                f'src:url(data:font/woff2;base64,{data}) format("woff2");'
                "unicode-range:U+0590-05FF,U+200C-2010,U+20AA,U+25CC,U+FB1D-FB4F}")
    direction = "rtl" if lang == "he" else "ltr"
    return (f'<!doctype html>\n<html lang="{lang}" dir="{direction}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; '
            'font-src data:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'">\n'
            f"<title>{html.escape(title)}</title>\n<style>{font}{CSS}</style>\n</head>\n<body>\n"
            f'<header class="masthead"><h1 dir="auto">{inline(title, None)}</h1>'
            f'<p class="meta">{_meta_line(meta, s)}</p></header>\n<main>\n' + "\n".join(parts)
            + f'\n</main>\n<footer>{s["footer"].format(date=date)}</footer>\n'
            f'<div id="zoom" hidden><img alt=""></div>\n<script>{SCRIPT}</script>\n</body>\n</html>\n'), [], warnings


def browser() -> str | None:
    for name in BROWSERS:
        path = name if Path(name).is_file() else shutil.which(name)
        if path:
            return path
    return None


def pdf(page: Path, out: Path) -> bool:
    chrome = browser()
    if not chrome:
        return False
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                        "--no-pdf-header-footer", f"--print-to-pdf={out}", page.resolve().as_uri()],
                       capture_output=True, timeout=300)
    return out.is_file() and out.stat().st_size > 0
```

- [ ] **Step 5: Run — expect pass** `python3 -m unittest tests.test_render -v`
- [ ] **Step 6: Commit** `git add -A && git commit -m "v2: notes.md → self-contained HTML, PDF via Chrome"`

---

### Task 6: The CLI and an end-to-end run on a synthetic talk

**Files:** Create `scripts/vsum.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `python3 scripts/vsum.py prepare <source> [--out] [--captions] [--lang-audio] [--max-frames] [--no-frames] [--fresh]`
  and `python3 scripts/vsum.py render <work> [--pdf]`; exit 0 on success, 1 with `Stopped: …` or `notes.md line N: …`.

- [ ] **Step 1: Failing test** — `tests/test_pipeline.py`

```python
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

VSUM = Path(__file__).resolve().parents[1] / "scripts" / "vsum.py"
CAPTIONS = """WEBVTT

00:00:00.000 --> 00:00:08.000
Welcome, this is the first slide.

00:00:12.000 --> 00:00:20.000
The second slide shows the result.

00:00:26.000 --> 00:00:34.000
The last slide sums it up.
"""


def talk(folder: Path) -> Path:
    """34 s: slide A 8 s, blank 4 s, slide B 8 s, A again 6 s, slide C 8 s — plus a sidecar VTT."""
    specs = [("a", 8), ("blank", 4), ("b", 8), ("a", 6), ("c", 8)]
    boxes = {"a": [(20, 20, 150, 160)], "b": [(170, 20, 300, 160)], "c": [(20, 20, 150, 80), (170, 100, 300, 160)]}
    for name in ("a", "b", "c", "blank"):
        image = Image.new("RGB", (320, 180), (128, 128, 128) if name == "blank" else (30, 34, 40))
        for box in boxes.get(name, []):
            ImageDraw.Draw(image).rectangle(box, fill=(240, 240, 240))
        image.save(folder / f"{name}.png")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for name, seconds in specs:
        cmd += ["-loop", "1", "-t", str(seconds), "-i", str(folder / f"{name}.png")]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(specs))) + f"concat=n={len(specs)}:v=1:a=0,format=yuv420p",
            "-r", "25", str(folder / "talk.mp4")]
    subprocess.run(cmd, check=True)
    (folder / "talk.vtt").write_text(CAPTIONS, encoding="utf-8")
    return folder / "talk.mp4"


def vsum(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VSUM), *args], capture_output=True, text=True, encoding="utf-8")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class Pipeline(unittest.TestCase):
    def test_prepare_then_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            video, work = talk(Path(tmp)), Path(tmp, "work")
            done = vsum("prepare", str(video), "--out", str(work))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertIn("notes.md", done.stdout)
            self.assertIn("[00:00] Welcome, this is the first slide.", (work / "transcript.md").read_text())
            picked = json.loads((work / "frames.json").read_text())
            self.assertEqual(len(picked), 3, picked)
            self.assertTrue((work / "sheets" / "sheet-01.jpg").is_file())
            self.assertEqual(json.loads((work / "source.json").read_text())["transcript"]["method"], "file")

            (work / "notes.md").write_text(
                "# סיכום\n\n> תקציר קצר.\n\n## [00:00] שקף ראשון\nהסבר.\n![F01](השקף הראשון)\n\n"
                "## [00:12] שקף שני\nעוד הסבר.\n![F02](השקף השני)\n", encoding="utf-8")
            done = vsum("render", str(work))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            page = (work / "summary.html").read_text(encoding="utf-8")
            self.assertIn('dir="rtl"', page)
            self.assertEqual(page.count("data:image/jpeg;base64,"), 2)

            (work / "notes.md").write_text("# x\n\n## פרק\n![F99](לא קיים)\n", encoding="utf-8")
            done = vsum("render", str(work))
            self.assertEqual(done.returncode, 1)
            self.assertIn("notes.md line 4: F99 is not one of the frames", done.stdout)

            again = vsum("prepare", str(video), "--out", str(work))
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("reusing", again.stdout)

    def test_missing_file_stops_with_one_line(self):
        done = vsum("prepare", "/no/such/talk.mp4")
        self.assertEqual(done.returncode, 1)
        self.assertIn("Stopped: no such file", done.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — expect failure** `python3 -m unittest tests.test_pipeline -v` → return code 2, `can't open file … vsum.py`

- [ ] **Step 3: Implement** `scripts/vsum.py`

```python
#!/usr/bin/env python3
"""summarize-video: a video link or file → illustrated study notes. Run `prepare`, write notes.md, run `render`."""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frames  # noqa: E402
import render  # noqa: E402
import source  # noqa: E402
import transcript  # noqa: E402
from common import VsumError, fmt_time  # noqa: E402

SCRIPT = Path(__file__).resolve()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _transcript(args, meta: dict, info: dict, work: Path) -> tuple[list, dict]:
    if args.captions:
        return transcript.caption_file(Path(args.captions).expanduser())
    if meta["kind"] == "file":
        side = transcript.sidecar(Path(meta["path"]))
        if side:
            return transcript.caption_file(side)
    else:
        found = transcript.fetch_captions(meta["url"], info, work)
        if found:
            return found
    print("No captions: transcribing locally with whisper (about a tenth of the video's length)…", flush=True)
    return transcript.transcribe(source.media(meta, work), work, args.lang_audio or meta.get("language"),
                                 meta["duration"])


def prepare(args) -> int:
    meta, info = source.probe(args.source)
    work = Path(args.out or f"vsum-{meta['id']}").resolve()
    if args.fresh:
        for name in ("transcript.md", "frames.json", "source.json"):
            (work / name).unlink(missing_ok=True)
        for name in ("frames", "sheets", "captions"):
            shutil.rmtree(work / name, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    print(f"Work folder: {work}", flush=True)
    saved = _load(work / "source.json") or {}
    if (work / "transcript.md").is_file() and saved.get("transcript"):
        meta["transcript"] = saved["transcript"]
        meta["duration"] = meta["duration"] or saved.get("duration") or 0
        print("Transcript: reusing transcript.md")
    else:
        segments, meta["transcript"] = _transcript(args, meta, info, work)
        (work / "transcript.md").write_text(transcript.markdown(meta, segments), encoding="utf-8")
        print(f"Transcript: {transcript.describe(meta['transcript'])}", flush=True)
    picked = _load(work / "frames.json")
    if picked is not None:
        print(f"Frames: reusing {len(picked)} candidates")
    elif args.no_frames:
        picked = []
    else:
        video = source.media(meta, work)
        meta["duration"] = meta["duration"] or source.duration(video)
        if source.has_video(video):
            print("Scanning the video for screens that held still…", flush=True)
            cap = args.max_frames or frames.default_cap(meta["duration"])
            picked = frames.extract(video, frames.pick(frames.scan(video), cap), work / "frames")
            frames.sheets(picked, work / "frames", work / "sheets")
        else:
            picked = []
            print("No picture track: the summary will have no frames.")
        _save(work / "frames.json", picked)
    _save(work / "source.json", meta)
    sheets = sorted((work / "sheets").glob("sheet-*.jpg"))
    words = next((line.split(": ")[1] for line in (work / "transcript.md").read_text(encoding="utf-8").splitlines()
                  if line.startswith("- Words: ")), "?")
    look = (f"2. Look at every contact sheet in one message: {', '.join(str(p) for p in sheets)}\n"
            f"   (open a single {work / 'frames'}/Fnn.jpg only to read small text)" if sheets
            else "2. There are no frames to look at.")
    print(f"\nReady: \"{meta['title']}\" ({fmt_time(meta['duration'])}), {transcript.describe(meta['transcript'])}, "
          f"{len(picked)} candidate frames.\n\nNext:\n1. Read {work / 'transcript.md'} ({words} words) to the end.\n"
          f"{look}\n3. Write {work / 'notes.md'} (format and rules: SKILL.md).\n"
          f"4. Run: python3 \"{SCRIPT}\" render \"{work}\"")
    return 0


def render_cmd(args) -> int:
    work = Path(args.work).resolve()
    meta = _load(work / "source.json")
    if not meta:
        raise VsumError(f"{work} is not a prepared folder; run prepare first.")
    notes = work / "notes.md"
    if not notes.is_file():
        raise VsumError(f"write {notes} first (format: SKILL.md), then render again.")
    page, errors, warnings = render.build(notes.read_text(encoding="utf-8"), meta,
                                          _load(work / "frames.json") or [], work / "frames")
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"notes.md {error}")
        return 1
    out = work / "summary.html"
    out.write_text(page, encoding="utf-8")
    print(f"Summary: {out}")
    if args.pdf:
        pdf = work / "summary.pdf"
        print(f"PDF: {pdf}" if render.pdf(out, pdf) else "PDF: no Chrome, Chromium or Edge found; the HTML is complete.")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="vsum", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="transcript, candidate frames and contact sheets")
    p.add_argument("source", help="a YouTube or other video URL, or a local video or audio file")
    p.add_argument("--out", help="work folder (default: ./vsum-<id>)")
    p.add_argument("--captions", help="a .vtt or .srt file to use as the transcript")
    p.add_argument("--lang-audio", help="spoken language for whisper, e.g. he or en (default: detected)")
    p.add_argument("--max-frames", type=int, help="most candidate frames (default: 16 per 10 minutes, 32 to 96)")
    p.add_argument("--no-frames", action="store_true", help="text only: skip the video download and frames")
    p.add_argument("--fresh", action="store_true", help="redo every step instead of reusing earlier results")
    r = sub.add_parser("render", help="notes.md → summary.html")
    r.add_argument("work", help="the folder prepare created")
    r.add_argument("--pdf", action="store_true", help="also print summary.pdf with Chrome, Chromium or Edge")
    args = parser.parse_args(argv)
    try:
        return prepare(args) if args.command == "prepare" else render_cmd(args)
    except VsumError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — expect pass** `python3 -m unittest discover -s tests -v` (whole suite)
- [ ] **Step 5: Commit** `git add -A && git commit -m "v2: vsum CLI (prepare / render) with end-to-end test"`

---

### Task 7: Skill, docs, CI

**Files:** Create `SKILL.md`, `README.md`, `AGENT-START.txt`, `SECURITY.md`, `NOTICE`, `CHANGELOG.md`; rewrite `.github/workflows/ci.yml`

- [ ] **Step 1: `SKILL.md`** — frontmatter (`name: summarize-video`, description with Hebrew/English triggers, `license: MIT`, `metadata.version: "2.0.0"`) and the three steps: prepare (what it does, options, stop messages), read + write `notes.md` (format block, depth "detailed" default and "short"/"full" on request, picture rules, keep/drop, Hebrew style rules carried over from v1's `references/summary.md`, grounding and untrusted-content rule), render (errors → fix → rerun; delivery message).
- [ ] **Step 2: `README.md`** — what it is, screenshot `docs/example-he.png` (from the real run in Task 8), Hebrew quick start in `<div dir="rtl">`, install (`git clone … ~/.claude/skills/summarize-video`; Codex `~/.agents/skills/summarize-video`), requirements (`brew install ffmpeg yt-dlp`, Pillow, optional `whisper-cpp` + model, Chrome for PDF), how it works, privacy summary, development (`python3 -m unittest discover -s tests -v`).
- [ ] **Step 3: `AGENT-START.txt`** — a short Hebrew message a non-technical user pastes into Claude Code/Codex with a video link.
- [ ] **Step 4: `SECURITY.md`, `NOTICE` (Frank Ruhl Libre OFL, v1 lineage), `CHANGELOG.md` (2.0.0 on top of the v1 history, restored from `main`).**
- [ ] **Step 5: CI** — one job, `ubuntu-latest` and `macos-latest`, Python 3.10 and 3.12, ffmpeg installed, `pip install Pillow`, `python -m unittest discover -s tests -v`; actions pinned to v1's commit ids.
- [ ] **Step 6: Commit** `git add -A && git commit -m "v2: skill instructions, docs, CI"`

---

### Task 8: Real runs and tuning

- [ ] **Step 1:** `python3 scripts/vsum.py prepare "https://www.youtube.com/watch?v=nKSk_TiR8YA" --out <scratch>/run-tokens` → read transcript + sheets → write a detailed Hebrew `notes.md` per SKILL.md → `render --pdf` → view in a browser at desktop and phone width; fix what looks wrong.
- [ ] **Step 2:** `python3 scripts/vsum.py prepare "https://www.youtube.com/watch?v=tuY2ChJIx48" --out <scratch>/run-beyond` (no captions → whisper) → check transcript quality and timing → full summary as in Step 1.
- [ ] **Step 3:** Tune `frames.py` constants only if the sheets show missed or duplicated screens; rerun the tests.
- [ ] **Step 4:** Save a screenshot of the Hebrew page as `docs/example-he.png`; commit.
