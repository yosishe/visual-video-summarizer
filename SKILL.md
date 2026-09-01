---
name: summarize-video
version: "1.2.0"
description: Turn a video (YouTube URL or local file) into a detailed English HTML summary - transcript-driven chapters with timestamp-aligned, pixel-verified frames (slides, screens, demos) embedded next to the text they illustrate. Use when the user asks to summarize a video, wants a visual summary or HTML digest of a talk/lecture/screencast/demo, or types /summarize-video <url-or-path>.
argument-hint: "<video-url-or-path> [notes / focus]"
user-invocable: true
allowed-tools: Bash, Read, Write, AskUserQuestion
license: MIT
homepage: https://github.com/yosishe/visual-video-summarizer
repository: https://github.com/yosishe/visual-video-summarizer
author: yosishe
metadata:
  version: "1.2.0"
  homepage: https://github.com/yosishe/visual-video-summarizer
  repository: https://github.com/yosishe/visual-video-summarizer
  author: yosishe
---

# /summarize-video

Pipeline: **transcript → chapters + visual targets → candidates (512px) → one visual triage by candidate ID → pixel-verified re-grab → rendered HTML → one self-contained file.**

The ordering is the point: every text decision happens before an image token is spent; frame placement is derived from decoded timestamps and transcript segment IDs, never typed by hand; the only image spend is one batched Read of the candidates; the selected frames are re-decoded at 1280px and verified against what you saw, without re-reading them.

Frame-engine internals (scene detection, pts stamps, thumbnail dedup) are adapted from `bradautomates/claude-video` (MIT). Whisper keys live in `~/.config/summarize-video/.env` (the `/watch` skill's `~/.config/watch/.env` is read as a legacy fallback).

## Resolve SKILL_DIR

Set `SKILL_DIR` to the absolute path of the directory containing THIS SKILL.md — your harness reported that path when this file was loaded (a plain clone lands at `~/.claude/skills/summarize-video`). The scripts are direct siblings at `SKILL_DIR/scripts/`. Guard once:

```bash
SKILL_DIR="<absolute path of the directory containing this SKILL.md>"
[ -f "$SKILL_DIR/scripts/transcript.py" ] || { echo "scripts not found under $SKILL_DIR" >&2; exit 1; }
```

Prereqs: `ffmpeg`, `ffprobe`, `yt-dlp` (`brew install ffmpeg yt-dlp`). A Whisper key (`GROQ_API_KEY` preferred, or `OPENAI_API_KEY`) is needed only when the source has no captions. JSON contracts for every file you author are in [references/contracts.md](references/contracts.md).

## Step 1 — Transcript (no video download)

```bash
python3 "$SKILL_DIR/scripts/transcript.py" "<source>" --work "<work>"
```

Options: `--langs "en.*"` (default) · `--whisper groq|openai` · `--no-whisper`. Omit `--work` to get a fresh temp dir; the report prints it — use it for every later step.

The report prints the transcript as `seg_NNNN [MM:SS-MM:SS] text`. Those `seg_id`s are the join keys for everything that follows. If no transcript is available, tell the user and offer a frames-only summary.

## Step 2 — Chapters and visual targets (you, text only — no images yet)

Write `<work>/chapters.json`. Chapter windows are half-open `[start,end)`; only the last chapter owns the exact end.

```json
[{"chapter_id": "ch03", "title": "Export workflow", "start": 120.0, "end": 210.0,
  "needs_frames": true,
  "visual_targets": [
    {"target_id": "ch03_export_result", "kind": "action_result",
     "seg_ids": ["seg_0084", "seg_0085"], "action_seg_id": "seg_0084",
     "why": "the completed export dialog proves the narrated action"}
  ]}]
```

Rules:
- 5–12 chapters for a typical talk; each a coherent topic, not a fixed length.
- **`needs_frames: false` for pure-talk chapters** (intro banter, Q&A logistics, outro). Nothing is extracted there — the cheapest frame is the one you never pay for.
- A target is a moment the transcript says something is on screen: "as you can see", "look at this", "now I click", "notice the graph", a demo action being narrated. Skip rhetorical "look, the point is…". Be restrained — **≤2 targets per chapter** typically. Kinds: `action_result` (search *after* the action segment — the result appears after the words), `state`, `diagram`, `slide` (search inside the referenced segments).
- **Reference segments, never seconds.** `anchor_t` / `window` exist for a known edge case only. The engine derives every timestamp from `seg_ids`; you do not type `--cues` or `--pins` any more.

## Step 3 — Candidates (cheap, 512px)

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "<source>" --work "<work>" \
  --transcript "<work>/transcript.json" --chapters "<work>/chapters.json" --mode light
```

What it does: downloads the video once (≤720p; for videos over 20 minutes only the padded ranges of chapters that need frames, with exact cuts) → **scene detection across every `needs_frames` chapter** (so a slide flip nobody predicted still reaches the pool) → dense samples around each target (`action_result` at +0.2/+0.8/+1.6s after the action) → chapter-coverage midpoints → every frame is extracted by seeking to its own timestamp with the decoded `actual_t` recorded and drift-checked → blank/black filter with recovery for target frames → duplicate clustering scoped to chapter + target, keeping the sharpest complete representative → cap (light 48, advanced 60), targets and chapter coverage never evicted → `<work>/candidates/` + `candidates.json` with per-chapter and per-target coverage.

The report includes a **per-chapter coverage table**. A `needs_frames` chapter showing 1 candidate is a static stretch: if its point is visual, add a target inside its window and re-run. `unresolved` is a failure, not a warning.

`--mode advanced` when a target stays unresolved, the screen changes within a second, or an action result is ambiguous: adaptive local scene thresholds, denser sampling, 3 alternatives per target. `--strips` additionally renders 256px temporal strips for a cheaper first look — off by default because slide text is not legible at that size; accuracy comes first.

## Step 4 — Triage (the ONLY image spend)

**Read every candidate path in a single message** (parallel Read calls). Then select per chapter:

- **Content test:** keeps information — slide, code, diagram, chart, UI state, demo result. A frame of the presenter's face fails.
- **Placement test:** its chapter (printed per candidate, derived from the decoded timestamp) is where the transcript discusses it.
- **Novelty test — one frame per board/scene:** when several candidates show the same whiteboard, slide, or screen at different build stages, select ONLY the most complete one (last of the run) — never one per stage, unless the transcript discusses an intermediate stage at length as its own point (then that stage earns its own frame). This applies across chapters too.
- **Quota:** 1–3 per chapter, at most 20 total. A chapter can end with zero. A long chapter with several distinct points deserves its 2–3 frames; do not starve it to hit a low number.
- Assign `role`: `evidence` (proves a number/claim/action) or `illustration` (represents the chapter). Roles shape the caption.

Write `<work>/selections.json` **by `candidate_id`** — never by timestamp (the ID resolves to the full-precision decoded time; copying a displayed time is how a rounded-down scene cut once embedded the wrong shot):

```json
[{"candidate_id": "c_0017", "name": "ch03_export_result", "chapter_id": "ch03",
  "role": "evidence",
  "caption": "The completed export dialog appears after the presenter confirms the action.",
  "alt": "Completed export dialog with the selected output settings",
  "anchor_seg_ids": ["seg_0084", "seg_0085"],
  "crop": "w:h:x:y (optional - only to blow up a small UI region)"}]
```

`anchor_seg_ids` are the transcript segments the frame illustrates; the renderer places the figure right after the prose block that cites them.

## Step 5 — Re-grab at deliverable quality (zero tokens)

```bash
python3 "$SKILL_DIR/scripts/grab.py" --work "<work>" --spec "<work>/selections.json" \
  --out-dir "summary-<video-id>/assets"
```

For each selection this re-decodes the source at the candidate's `actual_t`, **verifies the new pixels match the candidate you looked at**, then writes `<name>-full.jpg` (1280px) + `<name>-thumb.jpg` (640px) and `assets-manifest.json` with hashes. Do **not** Read these.

Exit 2 = an extraction failure or unsafe name/crop (fix it); exit 3 = two selections render the same picture (keep the more complete one, fix `selections.json`, re-run). Never work around the audit.

## Step 6 — Write the summary, then render

Write `<work>/summary.json` — the prose, with provenance. Every block cites the segments it synthesizes; a frame is inserted after the first block whose `seg_ids` overlap its `anchor_seg_ids`:

```json
{"overview": "One-sentence claim of the whole video.",
 "chapters": [{"chapter_id": "ch03", "title": "Export workflow",
   "blocks": [{"text": "Detailed synthesis of what is said…", "seg_ids": ["seg_0084", "seg_0085"]}],
   "key_points": ["optional bullet"]}]}
```

Write **detailed** English prose: synthesize, don't paste the transcript; quote only the lines that matter. Then render — never hand-write the HTML:

```bash
python3 "$SKILL_DIR/scripts/render.py" --work "<work>" --summary "<work>/summary.json" \
  --selections "<work>/selections.json" --assets-dir "summary-<video-id>/assets" \
  --out-dir "summary-<video-id>"
```

The renderer validates chapter ownership, segment provenance, budgets, coverage, duplicates and assets, then writes `manifest.json` (the source of truth), a designed `index.html` (TOC, claim box, chapter sections with timestamp links to YouTube, side-by-side figures), and — automatically — **`summary-<video-id>.html`: one self-contained file with every image embedded**. That single file is the deliverable the user opens and shares; no server is ever needed to view it. The directory stays as the editable source — change a caption or a frame, re-run render.

## Step 7 — Verify & clean up

- `grab.py` and `render.py` exited 0; the bundle line reports all images embedded. Send the user the single `summary-<video-id>.html`.
- Delete the expensive intermediates: `<work>/candidates/` and `<work>/download/`. Keep the JSON files (transcript, chapters, candidates, selections, summary) until the session ends — a changed frame needs only Steps 4→5→6 again.
- Follow-up questions about the same video: answer from context; do not re-run anything.

## Token notes

- Candidates: ~40–60 frames at 512px ≈ 25–50k image tokens, once. Transcript: a few thousand tokens.
- Never Read the `-full.jpg` outputs. Candidate resolution is capped at 512px; legibility in the *deliverable* comes from the 1280px re-grab (or a `crop`).

## Security & Permissions

**What this skill does:**
- Runs `yt-dlp` locally to fetch captions/metadata and download the video — network requests go only to the host the given URL points at (public data; no logins, no cookies, no posting)
- Runs `ffmpeg` / `ffprobe` locally to extract frames and, when Whisper is needed, a mono 16 kHz audio track
- Sends that extracted **audio only** to `api.groq.com` or `api.openai.com` — and only when the source has no captions AND the user has configured a Whisper API key (`--no-whisper` disables this entirely)
- Reads its own config at `~/.config/summarize-video/.env` (legacy fallback: `~/.config/watch/.env`) and the env vars `GROQ_API_KEY` / `OPENAI_API_KEY`
- Writes to a temp working directory and to `summary-<video-id>/` + `summary-<video-id>.html` in the current directory — nowhere else

**What this skill does NOT do:**
- Never uploads the video or frames to any API — the only outbound data is the audio clip for optional transcription
- Never reads `.env` files from the current directory or any project folder
- Never logs, prints, or stores API keys; each key is sent only to its own provider
- Never accesses accounts, browsers, or credentials; selection names and crop expressions are validated before they touch a path or an ffmpeg filter graph

Review the bundled scripts before first use — they are dependency-free Python.

## Failure modes

- **YouTube HTTP 403 / "PO Token" warnings**: yt-dlp is outdated — `brew upgrade yt-dlp` (or the platform equivalent) and retry once.
- **Download fails** (login/region-locked): report yt-dlp's stderr plainly; don't retry in a loop and don't use cookies without explicit authorization.
- **No transcript** (no captions, no Whisper key): offer a frames-only summary — chapterize by visual content, or ask for a key.
- **A required chapter/target is `unresolved`**: correct its segments or window, or re-run that chapter in `--mode advanced`; do not render around it.
- **Section download rejected** (exact cut failed): re-run without `--sections` for a full download.
- **grab.py exit 2/3**: fix the named extraction mismatch, unsafe crop, or duplicate selection.
