# visual-video-summarizer

A Claude Code skill (`/summarize-video`) that turns a video — YouTube URL or local file — into a **detailed English HTML summary**: chapters synthesized from the transcript, with a handful of carefully selected, timestamp-aligned frames (slides, screens, demos) embedded next to the exact text they illustrate.

Transcript + frames, stitched by time. Not a frame dump.

## The idea

Most video-summarization pipelines extract frames first and think later. This one inverts the order so that **all text decisions happen before any image token is spent**:

```
transcript  →  chapters  →  cheap candidates (512px)  →  ONE visual triage  →  high-res re-grab  →  HTML
 (free-ish)    (text only)     (ffmpeg, no model)         (the only image spend)   (zero tokens)    (from a manifest)
```

Key design decisions:

- **Captions before download.** `yt-dlp --skip-download` fetches the transcript without touching the video. Whisper (Groq/OpenAI) is only a fallback.
- **Placement is arithmetic, not vision.** Chapters are defined from the transcript with time windows; a frame belongs to a chapter by its timestamp. The model is never asked "where does this image go?"
- **Hybrid candidate union.** Scene changes (threshold 0.15) ∪ cue grabs at **+0.5s/+1.5s** after "as you can see" moments (speakers talk *before* the screen changes) ∪ pinned timestamps ∪ final frame ∪ a safety frame in any >90s gap.
- **Free filtering before the model looks.** Blank/black frames and near-duplicates are dropped using 16×16 grayscale thumbnails in ffmpeg passes — dropped frames cost zero tokens. Cue/pin/final frames are never evicted by the cap.
- **Label == content, by construction.** Scene detection is a metadata-only pass that yields timestamps; every candidate frame is then extracted by seeking to its own timestamp (fast seek, verified frame-identical to an accurate output-side seek). No positional pairing of filter output with written files — the alignment that a timestamp-driven summary lives or dies on cannot drift.
- **Two resolution passes.** Candidates are analyzed once at 512px; only the ~20 selected frames are re-extracted at 1280px (+640px thumbs) for the HTML — without re-reading them.
- **A near-duplicate audit guards the final page.** After the high-res re-grab, all selections are compared pairwise (16×16 thumbs); two selections rendering the same picture fail the run with the offending pair named, instead of shipping the same frame twice under different captions.
- **Per-chapter coverage floor.** Pass `--chapters` and the candidate report includes a coverage table; chapters that need frames but sit in a static stretch get midpoint fill frames, so relevant moments aren't silently absent from the triage pool.
- **A manifest is the source of truth.** `manifest.json` records every frame's timestamp, chapter, transcript segment, role (evidence/illustration), and caption — so the page can be rebuilt or a frame swapped without re-analyzing the video.

## Install

Copy this directory to your Claude Code skills folder:

```bash
git clone https://github.com/yosishe/visual-video-summarizer ~/.claude/skills/summarize-video
```

Prerequisites: `ffmpeg`, `ffprobe`, `yt-dlp` (`brew install ffmpeg yt-dlp`). Keep `yt-dlp` fresh — YouTube 403s usually mean it's outdated.

Optional: a Whisper API key for videos without captions — `GROQ_API_KEY` (preferred) or `OPENAI_API_KEY`, in the environment or in `~/.config/watch/.env`.

## Use

In Claude Code:

```
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID
```

The output lands in `summary-<video-id>/` — `index.html`, `manifest.json`, and an `assets/` folder with the selected frames. Every figure caption links back to the exact second on YouTube.

The scripts also work standalone:

```bash
python3 scripts/transcript.py "<url-or-path>"                 # transcript, no video download
python3 scripts/candidates.py "<url>" --work DIR --cues 58,122 --pins 45,210
python3 scripts/grab.py --work DIR --spec selections.json --out-dir out/assets
```

## Credits

Frame-engine internals (scene detection via `select=gt(scene,T)` + `showinfo` pts_time stamps, 16×16 thumbnail dedup, even-sampling) are adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT); `scripts/whisper.py` is copied from it. MIT licensed — see [LICENSE](LICENSE).
