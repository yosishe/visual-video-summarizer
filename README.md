# visual-video-summarizer

**A Claude Code skill that turns a video into a page worth reading.** Give `/summarize-video` a YouTube URL or a local file and it produces a detailed English HTML summary: chapters synthesized from the transcript, with ~15–20 carefully chosen, timestamp-aligned frames (slides, screens, demos) embedded next to the exact sentences they illustrate — each caption linking back to that second of the video.

Built for talks, lectures, screencasts, and product demos. Transcript + frames, stitched by time. Not a frame dump.

## Quick start

```bash
brew install ffmpeg yt-dlp
git clone https://github.com/yosishe/visual-video-summarizer ~/.claude/skills/summarize-video
```

Then, in any Claude Code session:

```
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID
```

Output lands in `summary-<video-id>/` — a styled `index.html`, an `assets/` folder with the selected frames (1280px + thumbnails), and a `manifest.json` recording every frame's timestamp, chapter, transcript segment, and selection reason.

## How it works

Most video-summarization pipelines extract frames first and think later. This one inverts the order so that **all text decisions happen before any image token is spent**:

```
transcript  →  chapters  →  cheap candidates (512px)  →  ONE visual triage  →  high-res re-grab  →  HTML
 (free-ish)    (text only)     (ffmpeg, no model)         (the only image spend)   (zero tokens)    (from a manifest)
```

Key design decisions:

- **Captions before download.** `yt-dlp --skip-download` fetches the transcript without touching the video. Whisper (Groq/OpenAI) is only a fallback, and rolling caption overlap is stripped losslessly (~halves transcript tokens).
- **Placement is arithmetic, not vision.** Chapters are defined from the transcript with time windows; a frame belongs to a chapter by its timestamp. The model is never asked "where does this image go?"
- **Hybrid candidate union.** Scene changes (threshold 0.15) ∪ cue grabs at **+0.5s/+1.5s** after "as you can see" moments (speakers talk *before* the screen changes) ∪ pinned timestamps ∪ final frame ∪ a safety frame in any >90s gap ∪ per-chapter coverage fill.
- **Label == content, by construction.** Scene detection is a metadata-only pass that yields timestamps; every candidate frame is then extracted by seeking to its own timestamp. The alignment a timestamp-driven summary lives or dies on cannot drift.
- **Free filtering before the model looks.** Blank/black frames and near-duplicates are dropped using 16×16 grayscale thumbnails — dropped frames cost zero tokens. Cue/pin/coverage frames are never evicted by the cap.
- **Two resolution passes.** Candidates are analyzed once at 512px; only the selected frames are re-extracted at 1280px (+640px thumbs) for the HTML — without re-reading them.
- **A near-duplicate audit guards the final page.** After the high-res re-grab, all selections are compared pairwise; two selections rendering the same picture fail the run with the offending pair named, instead of shipping the same frame twice under different captions.
- **A manifest is the source of truth.** The page can be rebuilt, or one frame swapped, without re-analyzing the video.

## Token economics

For an 18-minute talk: transcript ≈ a few thousand tokens, one batched read of ~60 candidate frames at 512px ≈ 30–50k image tokens — and that's it. The 1280px deliverable frames are never read by the model. Total model cost is dominated by a single, bounded triage pass regardless of video length.

## Requirements

| | |
|---|---|
| `ffmpeg` / `ffprobe` | frame extraction, audio, thumbnails |
| `yt-dlp` | captions + video download (keep it updated — see Troubleshooting) |
| Python 3.10+ | bundled scripts, stdlib only — nothing to `pip install` |
| Whisper API key | **optional**, only for videos with no captions |

## Configuration (optional)

Only needed for videos without captions. Create `~/.config/summarize-video/.env` (chmod 600):

```
GROQ_API_KEY=...      # preferred - cheaper, faster (console.groq.com/keys)
OPENAI_API_KEY=...    # fallback (platform.openai.com/api-keys)
```

Environment variables with the same names also work. Users of the [claude-video](https://github.com/bradautomates/claude-video) `/watch` skill don't need to configure anything — `~/.config/watch/.env` is read as a legacy fallback.

## Script flags (standalone use)

The scripts also run outside Claude Code:

```bash
python3 scripts/transcript.py "<url-or-path>"        # transcript only, no video download
python3 scripts/candidates.py "<url>" --work DIR --chapters chapters.json --cues 58,122 --pins 45,210
python3 scripts/grab.py --work DIR --spec selections.json --out-dir out/assets
```

Useful flags: `--langs "he.*,en.*"` (caption languages) · `--sections 40-215,590-880` (partial download for long videos) · `--scene-threshold` · `--max-candidates` · `--no-whisper` · `--resolution`.

## Security & privacy

**Network:** `yt-dlp` talks only to the host of the URL you provide (public data — no logins, no cookies). If the video has no captions *and* you configured a Whisper key, the extracted **audio only** (mono 16 kHz mp3) is sent to `api.groq.com` or `api.openai.com`. The video itself and the frames never leave your machine. `--no-whisper` disables all transcription uploads.

**Reads:** the source file/URL, and this skill's own config (`~/.config/summarize-video/.env`, legacy `~/.config/watch/.env`). It deliberately does **not** read `.env` files from your project directories.

**Writes:** a temp working directory, and `summary-<video-id>/` in the directory where you run it. Nothing else.

**Keys:** never logged or written to any output; the Groq key goes only to Groq, the OpenAI key only to OpenAI.

The scripts are short, dependency-free Python — review them before first use.

## Troubleshooting

- **YouTube HTTP 403 / "PO Token" warnings** — your `yt-dlp` is outdated (YouTube rotates client requirements): `brew upgrade yt-dlp` and retry.
- **"No transcript available"** — the source has no captions and no Whisper key is configured; the skill can still produce a frames-only summary, or add a key (see Configuration).
- **Grab step exits with "Duplicate selections found"** — working as intended: two chosen frames render the same picture; keep the more complete one and re-run.

## Credits

Frame-engine internals (ffmpeg scene detection via `select=gt(scene,T)` + `showinfo` pts stamps, 16×16 thumbnail dedup, even-sampling) are adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT); `scripts/whisper.py` is copied from it. MIT licensed — see [LICENSE](LICENSE).
