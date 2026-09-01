---
name: summarize-video
description: Turn a video URL or local file into a detailed English HTML summary with transcript-aligned, non-duplicate visual evidence. Use for visual video summaries, timestamped chapter notes, or /summarize-video requests where frames must match what is being discussed.
allowed-tools: Bash, Read, Write, AskUserQuestion
license: MIT
metadata:
  version: "1.1.0"
  homepage: https://github.com/yosishe/visual-video-summarizer
  repository: https://github.com/yosishe/visual-video-summarizer
  author: yosishe
---

# /summarize-video

Produce an English HTML summary whose frames are evidence, not decoration:

**captions → transcript chapters and visual targets → low-cost temporal strips → selected candidate IDs → verified re-grab → deterministic HTML**

Text decisions happen before video download. Frame placement is derived from the decoded timestamp and transcript intervals. The model never invents a frame timestamp, writes asset links by hand, or reads deliverable-resolution images.

## Setup

Set `SKILL_DIR` to the absolute directory containing this file. The scripts are direct siblings under `SKILL_DIR/scripts/`. Guard once:

```bash
SKILL_DIR="<absolute path of this skill directory>"
[ -f "$SKILL_DIR/scripts/transcript.py" ] || { echo "scripts not found under $SKILL_DIR" >&2; exit 1; }
```

The runtime requires `python3`, `ffmpeg`, `ffprobe`, and `yt-dlp`. A Groq or OpenAI Whisper key in the environment or `~/.config/summarize-video/.env` is needed only when captions are unavailable; `~/.config/watch/.env` remains a legacy fallback.

Detailed JSON contracts and compatibility flags are in [references/contracts.md](references/contracts.md). Read that reference when authoring `chapters.json`, `selections.json`, or `summary.json`.

## 1. Acquire the transcript first

```bash
python3 "$SKILL_DIR/scripts/transcript.py" "<source>" --work "<work>"
```

For URLs, this requests metadata and captions with `yt-dlp --skip-download`. Whisper is the fallback. Use `--langs` when the source captions are not English; the HTML language remains English.

Keep the returned `transcript.json`. Its stable `seg_id` values are the join keys for chapters, visual targets, summary blocks, and frames.

## 2. Create chapters and visual targets using text only

Write `<work>/chapters.json`. Use half-open chapter windows: `[start,end)`. The final chapter alone may own the exact video end.

```json
[
  {
    "chapter_id": "ch03",
    "title": "Export workflow",
    "start": 120.0,
    "end": 210.0,
    "needs_frames": true,
    "visual_targets": [
      {
        "target_id": "ch03_export_result",
        "kind": "action_result",
        "seg_ids": ["seg_0084", "seg_0085"],
        "action_seg_id": "seg_0084",
        "why": "The completed export dialog proves the narrated action"
      }
    ]
  }
]
```

Use `needs_frames: false` for pure talking-head, logistics, or outro chapters. Add a target only when a visual can preserve information: a slide, diagram, UI state, code, chart, or action result.

Target kinds:

- `action_result`: search after the action segment. Set `action_seg_id` when `seg_ids` also includes later explanation; otherwise the earliest referenced segment end is the anchor.
- `state`, `diagram`, or `slide`: search within the referenced segment interval.

Prefer segment IDs over handwritten seconds. `anchor_t` and an explicit `[start,end]` `window` are escape hatches for a known edge case.

## 3. Extract candidates

Light mode is the default and should be used first:

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "<source>" \
  --work "<work>" \
  --transcript "<work>/transcript.json" \
  --chapters "<work>/chapters.json" \
  --mode light
```

Light mode searches only visual target/chapter windows, keeps up to two alternatives per target, and caps the pool at 36 candidates. For videos over 20 minutes it derives padded visual sections after chapterization instead of downloading irrelevant ranges.

Use `--mode advanced` when the light report has an unresolved target/chapter, when the screen changes within a second, or when a narrated action result is ambiguous. Advanced mode uses rolling local scene-score thresholds, denser target samples, blank-transition recovery, and up to three alternatives per target with a 60-candidate cap. All partial downloads use exact cuts so their source-time mapping remains trustworthy. Advanced adds CPU work, not model context by default.

The candidate manifest records `requested_t`, decoded `actual_t`, seek error, chapter, targets, segments, quality, and mapping confidence. A seek beyond the allowed source-frame tolerance is rejected. Post-filter coverage is `covered`, `not-required`, or `unresolved`; never treat `unresolved` as success.

`--cues` and `--pins` remain available only for legacy runs. New runs should use `visual_targets`.

## 4. Spend image tokens selectively

Read the temporal-strip paths printed by `candidates.py` first. Each strip is ordered left-to-right and its candidate IDs are listed beside it. Open an individual 512px candidate only when it is selected or the strip is too small to verify text/detail.

Selection rules:

- The actual timestamp must belong to the declared chapter and the target/segment must discuss what is visible.
- Prefer the stable, complete post-action state over a cursor movement, fade, loading screen, or incomplete build.
- Select one frame per visual evidence unit. Add a second or third frame in a chapter only when it proves a different state or point.
- A chapter may contain zero frames when `needs_frames` is false. Keep no more than 20 frames globally and no more than three per chapter.

Write `<work>/selections.json` using immutable `candidate_id` values, never raw timestamps:

Do not copy or round the displayed time. Scene timestamps can sit exactly on a cut, and rounding down by one source frame can return the previous shot. `grab.py` resolves the full-precision `actual_t` from the candidate manifest.

```json
[
  {
    "candidate_id": "c_0017",
    "name": "ch03_export_result",
    "chapter_id": "ch03",
    "role": "evidence",
    "caption": "The completed export dialog appears after the presenter confirms the action.",
    "alt": "Completed export dialog with the selected output settings",
    "anchor_seg_ids": ["seg_0084", "seg_0085"]
  }
]
```

Use `crop` only when the relevant UI is illegible in the full candidate. It must use integer `w:h:x:y` syntax and is applied to both deliverable variants.

## 5. Re-grab and verify selected frames

```bash
mkdir -p "summary-<video-id>/assets"
python3 "$SKILL_DIR/scripts/grab.py" \
  --work "<work>" \
  --spec "<work>/selections.json" \
  --out-dir "summary-<video-id>/assets"
```

The script resolves each candidate ID, decodes its recorded `actual_t`, verifies the new visual signature against the 512px candidate, then creates 1280px full images and 640px thumbnails. It rejects unsafe names/crops and fails on extraction drift or hard duplicate selections. Do not read the generated full images.

## 6. Write summary evidence, then render

Write `<work>/summary.json`. Each English prose block must list the transcript segments it synthesizes. A frame is inserted immediately after the first block whose `seg_ids` overlap its `anchor_seg_ids`.

```json
{
  "overview": "A practical walkthrough of the export workflow and its tradeoffs.",
  "chapters": [
    {
      "chapter_id": "ch03",
      "title": "Export workflow",
      "blocks": [
        {
          "text": "The presenter configures the export and confirms the completed result.",
          "seg_ids": ["seg_0084", "seg_0085"]
        }
      ],
      "key_points": ["The final state is shown after the click, not while the menu is moving."]
    }
  ]
}
```

Render rather than hand-writing HTML:

```bash
python3 "$SKILL_DIR/scripts/render.py" \
  --work "<work>" \
  --summary "<work>/summary.json" \
  --selections "<work>/selections.json" \
  --assets-dir "summary-<video-id>/assets" \
  --out-dir "summary-<video-id>"
```

The renderer validates chapter ownership, segment provenance, budgets, duplicate/extraction reports, required coverage, asset existence, captions, alt text, and summary-block placement. It writes English `<html lang="en" dir="ltr">`, `manifest.json`, and `index.html`, with timestamp links for YouTube sources.

## 7. Final checks and cleanup

- Confirm `grab.py` and `render.py` return zero and no required coverage row is `unresolved`.
- Open `index.html` for visual QA and check that every frame actually supports its adjacent prose.
- Preserve the summary directory. Candidate and downloaded media are disposable after acceptance; retain transcript, chapters, selections, and summary JSON until follow-up edits are complete.
- Answer follow-ups from the preserved manifests. Re-run only the narrow stage that changed.

The `triage` section in `candidates.json` reports provider-neutral pixel area instead of pretending that every model prices images identically. Use `projected_to_baseline_ratio` to compare the strip-first workflow with the former 60×512 individual-frame baseline.

## Security and permissions

The skill runs `yt-dlp`, `ffmpeg`, and `ffprobe` locally. Network acquisition is limited to the provided public URL. If captions are absent and a configured Whisper key is available, only the extracted audio is sent to `api.groq.com` or `api.openai.com`; `--no-whisper` disables that fallback.

The skill reads its own config at `~/.config/summarize-video/.env` with legacy fallback to `~/.config/watch/.env`. It never reads project `.env` files, uploads video/frames, prints keys, accesses browsers/accounts, or posts data. It writes only its temporary work directory and the requested `summary-<video-id>/` output.

## Failure modes

- **YouTube HTTP 403 or PO-token warning:** update `yt-dlp` and retry once.
- **Download fails because of login/region controls:** report stderr; do not loop or use cookies without explicit authorization.
- **No transcript:** offer frames-only mode or ask the user whether to configure Whisper.
- **Required coverage is unresolved:** rerun that target in advanced mode or correct its segment/window; do not render incomplete HTML.
- **Grab exits 2 or 3:** fix the named extraction mismatch, unsafe crop, or duplicate selection; do not bypass the audit.
