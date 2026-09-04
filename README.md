# visual-video-summarizer

**A Claude Code skill that turns a video into a page worth reading.** Give `/summarize-video` a YouTube URL or a local file and it produces a detailed illustrated HTML summary — **in Hebrew (right-to-left) by default, or English with `--lang en`**: chapters synthesized from the transcript, with ~15–20 carefully chosen, pixel-verified frames (slides, screens, demos) embedded next to the exact sentences they illustrate — each caption saying what the picture shows and why it is there, and linking back to that second of the video.

Built for talks, lectures, screencasts, and product demos. Transcript + frames, stitched by time. Not a frame dump.

## Quick start

```bash
brew install ffmpeg yt-dlp
git clone https://github.com/yosishe/visual-video-summarizer ~/.claude/skills/summarize-video
```

Then, in any Claude Code session:

```
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID --tier high --pdf
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID --lang en
```

## Hebrew, done properly

The Hebrew summary is written directly from the transcript by the model (never machine-translated, never a translation of an English draft), under a written rubric: keep every number, tool name, example and reasoning chain; drop greetings, sponsors and filler; Hebrew for terms the industry uses in Hebrew, English for the rest (`skill`, `prompt`), product names untransliterated; every sentence opens in Hebrew. Before anything is rendered, `scripts/audit_summary.py` checks the summary against the transcript: every number, `backtick` identifier and URL must appear in the segments the block cites; segments must be in order and inside their chapter; no niqqud, no bidi control characters. Names that auto-captions misspell ("Open Claw") are matched fuzzily and reported for review rather than failed.

The page itself follows the W3C bidi guidance: `dir="rtl"` on `<html>`, logical CSS, timestamps, ranges, code and English terms isolated left-to-right, the video title in its own direction, and a subset of the Heebo typeface (SIL OFL) embedded so the single file renders identically offline. `--pdf` prints it with Chrome headless (or WeasyPrint) — both implement the Unicode bidi algorithm for HTML text.

YouTube caption tracks are chosen by provenance (manual original → manual Hebrew/English → original-language auto-captions → untranslated auto-captions); YouTube's machine-translated tracks are never used as a source. A Hebrew-language video is summarized in Hebrew without translation, under the same rubric.

The deliverable is **one self-contained file** — `summary-<video-id>.html`, with every image embedded as a data URI: open it with a double click, mail it, drop it in a chat. No server involved. `--pdf` adds `summary-<video-id>.pdf` (printed by Chrome headless or WeasyPrint). Alongside it, `summary-<video-id>/` holds the editable source: `index.html`, an `assets/` folder (1280px frames + thumbnails), and `manifest.json` recording every frame's decoded timestamp, chapter, transcript segments, role, quality and asset hashes — change a caption or a frame and re-run `render.py` to regenerate both.

## Two tiers

| | `--tier standard` (default) | `--tier high` |
|---|---|---|
| scene pass | fixed threshold | adaptive (median + 8·MAD) |
| samples per target | 2–3 | 5–6, 3 alternatives kept |
| candidate pool | 48 nominal (≈ 10k image tokens; measured 10.5k) | 64 nominal (≈ 13.4k; measured 15.9k) |
| overlay mask + family dedup | on | on |
| grab-time refinement | — | sharpest frame within ±1.5 s that is still the triaged picture (`blurdetect`), re-verified |
| face demotion | — | when `opencv-python-headless` is installed (optional) |
| OCR text density | — | ffmpeg `ocr` filter as a slide-completeness ranking signal (never a text source) |

Both tiers measure where a slide or board stops being built up (a scene-score probe per `slide`/`diagram` target) instead of assuming the end of the sentence, and both print an honest cost line: image tokens from the candidates' real dimensions, CPU passes, and the other tier's ceiling.

## How it works

Most video-summarization pipelines extract frames first and think later. This one inverts the order so that **all text decisions happen before any image token is spent**:

```
transcript → chapters + visual targets → candidates (512px) → ONE triage by candidate ID → verified re-grab → render → bundle
 (free-ish)      (text only)               (ffmpeg, no model)     (the only image spend)     (zero tokens)   (manifest → HTML → one file)
```

Key design decisions:

- **Captions before download.** `yt-dlp --skip-download` fetches the transcript without touching the video. Whisper (Groq/OpenAI) is only a fallback, and the rolling overlap of auto-captions is stripped losslessly (~halves transcript tokens).
- **Timestamps come from the transcript, not from the model.** Chapters carry `visual_targets` that reference transcript segment IDs ("as you can see…", "now I click…"); the engine derives the search windows and sample times. Nobody types seconds.
- **Placement is arithmetic, not vision.** A frame belongs to a chapter by its decoded timestamp and sits next to the prose block that cites its segments. The model is never asked "where does this image go?"
- **Recall and precision both.** Scene detection scans every chapter that needs frames (so an unflagged slide flip still reaches the pool), while targets add dense sampling where the transcript predicts a visual — `action_result` targets sample *after* the narrated action, because speakers talk before the screen changes.
- **Label == content, by construction.** Every candidate is extracted by seeking to its own timestamp; the decoded `actual_t` is recorded and seek drift is rejected.
- **Free filtering before the model looks.** Blank/transition frames are dropped (protected frames are recovered past the transition). A persistent webcam picture-in-picture or tab/subtitle bar is detected once per video (pure Python over one low-resolution decode) and masked in every signature, so the presenter moving in the corner does not make identical slides look different. Near-duplicates are then clustered across the whole video into *families* — one representative per chapter that needs the picture, revisits dropped and listed — and only the sharpest, most complete representative survives. Dropped frames cost zero tokens; every drop is logged with its reason in `dropped.json`.
- **Selection by immutable ID, then pixel verification.** `selections.json` names candidate IDs; `grab.py` re-decodes the source at the recorded time and refuses to write an asset whose pixels don't match the candidate the model looked at. Two selections that render the same picture fail the run.
- **Sharper, never different.** In `--tier high`, grab looks ±1.5 s around the triaged frame for the sharpest frame that is *still a near-duplicate of it* — the same predicate as the verification gate — and verifies the new pixels again. The written time and the triaged time are both recorded; captions use the written one.
- **Targets come from content gaps.** The chaptering step looks for the five stretches of transcript that are incomplete without the picture — a dangling reference, a conclusion without its data, an unspoken operation, a silent demo, a visual comparison — not only for "as you can see".
- **Deterministic rendering from a manifest.** `render.py` validates provenance, budgets and coverage, then produces the page and the single-file bundle. The page can be rebuilt, or one frame swapped, without re-analyzing the video.

## Token economics

For an 18-minute talk: transcript ≈ a few thousand tokens, one batched read of the candidate pool (48 / 64 nominal) at 512px ≈ 10k–16k image tokens (`⌈w/28⌉×⌈h/28⌉` = 209 per 16:9 frame; 4:3 sources ~25% more) — and that's it. The 1280px deliverable frames are never read by the model. Total model cost is dominated by a single, bounded triage pass regardless of video length; the report prints the exact estimate for the run.

## Requirements

| | |
|---|---|
| `ffmpeg` / `ffprobe` | frame extraction, audio, thumbnails, `blurdetect`; the `ocr` filter (tesseract) for `--tier high`'s text signal |
| `yt-dlp` | captions + video download (keep it updated — see Troubleshooting) |
| Python 3.10+ | bundled scripts, stdlib only — nothing to `pip install` |
| Whisper API key | **optional**, only for videos with no captions |
| Google Chrome or WeasyPrint | **optional**, only for `--pdf` |
| `opencv-python-headless` | **optional**, enables face demotion in `--tier high`; absent → reported `unavailable` |

## Configuration (optional)

Only needed for videos without captions. Create `~/.config/summarize-video/.env` (chmod 600):

```
GROQ_API_KEY=...      # preferred - cheaper, faster (console.groq.com/keys)
OPENAI_API_KEY=...    # fallback (platform.openai.com/api-keys)
```

Environment variables with the same names also work. Users of the [claude-video](https://github.com/bradautomates/claude-video) `/watch` skill don't need to configure anything — `~/.config/watch/.env` is read as a legacy fallback.

## Standalone use

The scripts also run outside Claude Code; the JSON contracts are in [`references/contracts.md`](references/contracts.md):

```bash
python3 scripts/transcript.py "<url-or-path>" --work WORK          # transcript, no video download
# author WORK/chapters.json (chapters + visual_targets by seg_id)
python3 scripts/candidates.py "<url>" --work WORK --transcript WORK/transcript.json --chapters WORK/chapters.json --tier standard
# author WORK/selections.json (by candidate_id)
python3 scripts/grab.py --work WORK --spec WORK/selections.json --out-dir summary-ID/assets
# author WORK/summary.json (prose blocks citing seg_ids)
python3 scripts/render.py --work WORK --summary WORK/summary.json --selections WORK/selections.json --assets-dir summary-ID/assets --out-dir summary-ID --pdf
```

Useful flags: `--langs "he.*,en.*"` (caption languages) · `--tier high` (see the table above; `--mode light|advanced` are aliases of the two tiers) · `--refine sharpness|none` on `grab.py` (override the tier's default) · `--pdf` on `render.py` · `--strips` (256px temporal strips for a cheaper first look; off by default — slide text isn't legible at that size) · `--sections 40-215,590-880` (explicit ranges; long videos derive them automatically) · `--max-candidates` (a hard ceiling) · `--no-whisper`.

Tests (stdlib only, synthesize their own ffmpeg fixtures):

```bash
python3 -m unittest discover -s tests -v
```

## Security & privacy

**Network:** `yt-dlp` talks only to the host of the URL you provide (public data — no logins, no cookies). If the video has no captions *and* you configured a Whisper key, the extracted **audio only** (mono 16 kHz mp3) is sent to `api.groq.com` or `api.openai.com`. The video itself and the frames never leave your machine. `--no-whisper` disables all transcription uploads.

**Reads:** the source file/URL, and this skill's own config (`~/.config/summarize-video/.env`, legacy `~/.config/watch/.env`). It deliberately does **not** read `.env` files from your project directories.

**Writes:** a temp working directory, and `summary-<video-id>/` + `summary-<video-id>.html` (+ `.pdf` with `--pdf`) in the directory where you run it. Nothing else.

**Keys:** never logged or written to any output; the Groq key goes only to Groq, the OpenAI key only to OpenAI. Selection names and crop expressions are validated before they enter a path or an ffmpeg filter graph.

**Local-only signals:** `--tier high` runs ffmpeg's `ocr` filter (tesseract) on candidate frames and keeps only a character count per frame — the recognized text is never stored or shown; OpenCV is imported only if already installed. `--pdf` launches a local headless Chrome (or WeasyPrint) on the generated file.

The scripts are dependency-free Python — review them before first use.

## Troubleshooting

- **YouTube HTTP 403 / "PO Token" warnings** — your `yt-dlp` is outdated (YouTube rotates client requirements): `brew upgrade yt-dlp` and retry.
- **"No transcript available"** — the source has no captions and no Whisper key is configured; the skill can still produce a frames-only summary, or add a key (see Configuration).
- **A chapter or target is `unresolved`** — correct its segments/window or re-run it in `--tier high`; the renderer refuses to build a page with missing required evidence.
- **`grab.py` exits 2 or 3** — an extraction mismatch, unsafe name/crop, or two selections rendering the same picture; fix the named selection, don't bypass the audit.
- **`render.py --pdf` exits 4** — no PDF engine: install Google Chrome or `weasyprint`; the HTML is still produced.
- **`faces: unavailable` / `OCR: unavailable`** in a `high` report — an optional signal is missing on this machine (no OpenCV / no tesseract language data); the run is still valid.

## Contributors

- **yosishe** — project owner and maintainer.
- **OpenAI Codex** — transcript-aligned target engine, candidate-ID selection with verified re-grab, deterministic renderer, and the test suite (PR #2).
- **Claude Code** — original pipeline, review and integration, single-file bundle, recall/precision fixes.

## Credits

Frame-engine internals (ffmpeg scene detection via `select=gt(scene,T)` + `showinfo` pts stamps, thumbnail dedup, even-sampling) are adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT); `scripts/whisper.py` is copied from it. The transcript-aligned target engine, verified re-grab and renderer were developed in this repository (PR #2). The `blurdetect` sharpness gate and the "content gap" targeting rubric follow [CZX2244/dsh-bilibili](https://github.com/CZX2244/dsh-bilibili); people-frame demotion follows [ConflictHQ/PlanOpticon](https://github.com/ConflictHQ/PlanOpticon) (MIT). MIT licensed — see [LICENSE](LICENSE).
