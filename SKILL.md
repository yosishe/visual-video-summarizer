---
name: summarize-video
description: Turn a video (URL or local file) into a detailed English HTML summary - chapters synthesized from the transcript, with selected timestamp-aligned frames (slides, screens, demos) embedded next to the text they illustrate. Use when Yosi asks to summarize a video into HTML, "סכם לי את הסרטון ל-HTML", "make a visual summary of this talk", or types /summarize-video <url-or-path>.
argument-hint: "<video-url-or-path> [notes / focus]"
user-invocable: true
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# /summarize-video

Pipeline: **transcript → chapters → cheap candidates (512px) → one visual triage → high-res re-grab of the selected few → HTML built from a manifest.**

The ordering is the point: all text decisions happen before any image token is spent, placement is solved by time arithmetic (frame → chapter by timestamp window), and the only image spend is a single batched Read of candidates. Selected frames are re-extracted at 1280px for the HTML **without re-reading them** — you already saw them at 512px.

Frame-engine internals (scene detection, pts_time stamps, 16x16 dedup) are adapted from `bradautomates/claude-video` (MIT). Whisper keys are shared with the `/watch` skill's config (`~/.config/watch/.env`).

## Resolve SKILL_DIR

Set `SKILL_DIR` to the absolute path of the directory containing THIS SKILL.md (normally `~/.claude/skills/summarize-video`). Scripts live at `SKILL_DIR/scripts/`. Guard once:

```bash
SKILL_DIR="$HOME/.claude/skills/summarize-video"
[ -f "$SKILL_DIR/scripts/transcript.py" ] || { echo "scripts not found under $SKILL_DIR" >&2; exit 1; }
```

Prereqs: `ffmpeg`, `ffprobe`, `yt-dlp` (`brew install ffmpeg yt-dlp`). A Whisper key (`GROQ_API_KEY` preferred, or `OPENAI_API_KEY`, in env or `~/.config/watch/.env`) is only needed when the source has no captions.

## Step 1 — Transcript (no video download)

```bash
python3 "$SKILL_DIR/scripts/transcript.py" "<source>"
```

Options: `--langs "en.*"` (default) · `--whisper groq|openai` · `--no-whisper` · `--work DIR`.

The report prints the **work dir** (use it for every later step) and the full transcript as `seg_NNNN [MM:SS-MM:SS] text` lines. If no transcript is available, tell the user and offer frames-only (skip to Step 3 with no cues; captions/Whisper hints are in stderr).

## Step 2 — Chapterize (you, text only — no images yet)

Read the transcript and write `<work>/chapters.json`:

```json
[{"chapter_id": "ch01", "title": "Setup & Architecture", "start": 45, "end": 210,
  "needs_frames": true,
  "cues": [{"t": 58, "seg_id": "seg_0012", "why": "'as you can see' - points at diagram"}]}]
```

Rules:
- 5–12 chapters for a typical talk; each a coherent topic, not a fixed length.
- **`needs_frames: false` for pure-talk chapters** (intro banter, Q&A logistics, outro). No candidates will be generated there — that is the cheapest frame you'll never pay for.
- Cues = moments the speaker directs attention to the screen: "as you can see", "look at this", "now I click", "notice the graph", a demo action being narrated. Judgment call — skip rhetorical "look, the point is…". Be restrained: **≤2 cues per chapter** typically.
- For an action cue ("now I click…"), also note the **end** of that segment — the result appears on screen after the words. Pass segment-end times via `--pins`.

## Step 3 — Candidates (cheap, 512px)

Build the command from chapters.json — cue times to `--cues`, chapter starts + action segment-ends to `--pins`:

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "<source>" --work "<work>" \
  --cues 58,122,187 --pins 45,210,400,64.8
```

What it does: downloads the video once (≤720p) → union of scene-change frames (threshold 0.15) ∪ cue grabs at **+0.5s and +1.5s** after each cue ∪ pins ∪ final frame ∪ a safety frame in any >90s gap → blank/black filter + near-duplicate removal (16x16 thumbs) → cap 60, **cue/pin/final frames never evicted** → `<work>/candidates/` + `candidates.json`.

Other flags: `--max-candidates N` · `--scene-threshold F` · `--resolution W` · `--no-dedup`.

**Long videos (>20 min):** after Step 2, prefer partial download — pass only the ranges of chapters with `needs_frames: true`, padded by ~5s each side (keyframe snap makes section starts approximate):

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "$URL" --work "<work>" --sections 40-215,590-880 --cues ...
```

## Step 4 — Triage (the ONLY image spend)

**Read every candidate path in a single message** (parallel Read calls). Then select per chapter:

- **Content test:** keeps information — slide, code, diagram, chart, UI state, demo result. A frame of the presenter's face fails.
- **Placement test:** its timestamp falls inside the chapter, and the transcript at that moment actually discusses it.
- **Novelty test:** not a second copy of an already-selected frame. For a slide built up in stages (bullets appearing), pick the **last** frame of the run — the complete one.
- **Quota:** 1–3 per chapter, soft cap ~20 total. A chapter can end with zero. Extra frames only when they show a state the previous frame doesn't cover.
- Assign each selection `role`: `evidence` (proves a number/claim/action) or `illustration` (represents the chapter). Roles shape the caption: evidence captions state what the frame shows; illustration captions set the scene.

Write `<work>/selections.json`:

```json
[{"t": 133.4, "name": "ch03_export", "chapter_id": "ch03", "seg_id": "seg_0084",
  "role": "evidence", "caption": "The export button is visible as the presenter explains the next step.",
  "crop": "w:h:x:y (optional - only to blow up a small UI region)"}]
```

## Step 5 — Re-grab at deliverable quality (zero tokens)

```bash
python3 "$SKILL_DIR/scripts/grab.py" --work "<work>" --spec "<work>/selections.json" \
  --out-dir "summary-<video-id>/assets"
```

Produces `<name>-full.jpg` (1280px) + `<name>-thumb.jpg` (640px) per selection. Do **not** Read these.

## Step 6 — Write the summary

Create `summary-<video-id>/` in the cwd with:

1. **`manifest.json`** — the source of truth the HTML is built from: video info + per-frame `{frame_id, t, name, chapter_id, seg_ids, role, selection_reasons, caption}`. Copy `chapters.json` content in too. This is what lets you (or a later session) fix one frame or rebuild the page without re-analyzing the video.
2. **`index.html`** — English, `<html lang="en" dir="ltr">`. Per chapter: heading + time range, a **detailed** summary of what is said (synthesize, don't paste transcript; quote only lines that matter), and each selected frame as:

```html
<figure data-frame-id="ch03_export" data-time="133.4" data-segment-id="seg_0084" data-role="evidence">
  <a href="assets/ch03_export-full.jpg">
    <img src="assets/ch03_export-thumb.jpg" loading="lazy" alt="Export dialog with MP4 selected">
  </a>
  <figcaption><b>2:13</b> — The export button is visible as the presenter explains the next step.</figcaption>
</figure>
```

- Every frame sits next to the point it illustrates — never a detached gallery at the end.
- For YouTube sources, make each timestamp a link: `https://youtu.be/<id>?t=133`.
- Design the page properly (headings, readable measure, figures styled); it's a deliverable, not a dump.

## Step 7 — Verify & clean up

- `ls` the assets dir and check every `src`/`href` in the HTML resolves; open the page in the browser preview if the user wants to see it.
- Delete the expensive intermediates: `<work>/candidates/` and `<work>/download/`. Keep `<work>/transcript.json`, `chapters.json`, `selections.json` until the session ends (cheap, enable re-grabs); `summary-<id>/` is the product and stays.
- Follow-up questions about the same video: answer from context — do not re-run anything. A changed frame choice needs only Steps 4b→5→6 (edit selections.json, re-grab, re-render from the manifest).

## Token notes

- Candidates: ~30–60 frames at 512px ≈ 20–50k image tokens, once. Transcript: a few k.
- Never Read the `-full.jpg` outputs. Never bump `--resolution` above 512 for candidates; if on-screen text must be legible in the *deliverable*, that's what the 1280px grab (or a `crop`) is for.
- If the user asks about one specific moment afterwards, grab that single timestamp — don't re-run the candidate pass.

## Failure modes

- **Download fails** (login/region-locked): report yt-dlp's stderr plainly; don't retry in a loop.
- **No transcript** (no captions, no Whisper key): offer frames-only summary; chapterize by visual content instead, or ask for a key.
- **Section download missing a range**: candidates.py skips it with a stderr note — re-request with wider padding or fall back to a full download.
- **Very static video** (few scene changes): the safety grid + cues still give coverage; consider `--scene-threshold 0.08` on a re-run.
