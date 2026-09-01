---
name: summarize-video
description: Create an English HTML video summary whose frames are transcript-linked evidence. Use for /summarize-video, visual lecture summaries, UI walkthroughs, diagrams, slides, or timestamped summaries that must minimize redundant image reads.
allowed-tools: Bash, Read, Write, AskUserQuestion
license: Proprietary
metadata:
  version: "2.0.0"
  repository: https://github.com/yosishe/visual-video-summarizer
  author: yosishe
---

# /summarize-video

Build a source-linked English HTML summary with an independent local evidence engine:

**transcript → evidence targets → tiny grayscale scans → stable candidate IDs → selective image review → verified assets → deterministic HTML**

The engine is implemented in this skill. It does not call, install, import, or execute another video skill. Its runtime tools are Python 3, FFmpeg/FFprobe, and yt-dlp.

Read [references/contracts.md](references/contracts.md) when writing JSON inputs. Read [references/architecture.md](references/architecture.md) only when debugging time mapping, ranking, cache behavior, or image-budget metrics.

## 1. Acquire text before video

```bash
SKILL_DIR="<absolute path to this skill>"
python3 "$SKILL_DIR/scripts/transcript.py" "<source>" --work "<work>"
```

For a URL, this stage asks yt-dlp for metadata and captions with `--skip-download`; it does not request video. If captions are absent, the optional speech fallback uses only an audio file and a key from the environment or `~/.config/summarize-video/.env`. Use `--no-whisper` to prohibit that network call.

Keep `transcript.json`. Its stable `seg_id` values are the join keys for evidence planning, prose, frames, and final HTML.

## 2. Define transcript-linked visual evidence

Write `<work>/chapters.json`. Chapter intervals are half-open `[start,end)`; only the last chapter may own the exact media end.

```json
[
  {
    "chapter_id": "ch03",
    "title": "Export result",
    "start": 120.0,
    "end": 210.0,
    "needs_frames": true,
    "visual_targets": [
      {
        "target_id": "ch03_export_result",
        "kind": "action_result",
        "seg_ids": ["seg_0084", "seg_0085"],
        "action_seg_id": "seg_0084",
        "why": "The completed dialog proves the narrated action"
      }
    ]
  }
]
```

Use a target only when the pixels preserve information: a UI state, action result, code, chart, diagram, or slide. Mark pure speech, logistics, and outro chapters with `needs_frames: false`.

Target kinds:

- `action_result`: find the first stable, non-blank state after the action transition.
- `state`: preserve a specific visible configuration.
- `diagram`: preserve a complete diagram rather than an intermediate build.
- `slide`: preserve a legible, stable slide.

Prefer `seg_ids`. Use `anchor_t` or an explicit absolute `window` only when transcript timing cannot describe the evidence moment.

## 3. Generate bounded candidates

Start with light mode:

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "<source>" \
  --work "<work>" \
  --transcript "<work>/transcript.json" \
  --chapters "<work>/chapters.json" \
  --mode light
```

Light mode decodes tiny 64×36 grayscale samples only inside evidence windows, keeps at most two alternatives per target, and caps model-readable candidates at 36. It does not scan pure-speech chapters.

Use `--mode advanced` only for an unresolved target, a sub-second UI change, a progressive diagram, or an ambiguous action result. Advanced increases scan density inside the same bounded windows, keeps at most three alternatives per target, and caps the result at 60. It adds local decode work, not automatic model image reads.

The engine:

- finds local motion relative to each evidence window rather than using a global scene threshold;
- waits through transitions and blank/loading states before choosing an action result;
- preserves small local UI/code changes with tile-aware visual comparison;
- clusters duplicates within the same semantic target and selects the strongest representative;
- assigns content-addressed `candidate_id` values from chapter, target, decoded time, and pixels;
- validates coverage again after extraction, deduplication, and budgeting;
- records `requested_t`, decoded `actual_t`, source mapping, quality, transcript provenance, and cache identity.

Any required `unresolved` row is a failure. Correct its target or rerun only that difficult window in advanced mode.

## 4. Review strips before individual images

Read the temporal strips printed by `candidates.py`. They are ordered left-to-right and list their candidate IDs. Open a 512px candidate only if it is selected or the strip is insufficient to verify detail.

The reported budget is provider-neutral pixel area. It conservatively counts one individual verification read per evidence group, including singleton groups. Do not convert it into a token-price claim without the selected model's current image-pricing rules.

Select one frame per evidence target. Add another frame in the same chapter only when it proves a distinct state. Maximums: 20 globally and three per chapter.

Write `<work>/selections.json` with candidate IDs, never copied timestamps:

```json
[
  {
    "candidate_id": "cand_6f91b38d1ab274",
    "name": "ch03_export_result",
    "chapter_id": "ch03",
    "role": "evidence",
    "caption": "The completed export dialog appears after confirmation.",
    "alt": "Completed export dialog with output settings",
    "anchor_seg_ids": ["seg_0084", "seg_0085"]
  }
]
```

Use `crop` only when necessary for legibility. Its syntax is integer `w:h:x:y`; the same crop is applied to both asset variants.

## 5. Re-decode and verify assets

```bash
mkdir -p "summary-<video-id>/assets"
python3 "$SKILL_DIR/scripts/grab.py" \
  --work "<work>" \
  --spec "<work>/selections.json" \
  --out-dir "summary-<video-id>/assets"
```

This stage resolves full-precision `actual_t` from the candidate manifest, decodes again from source media, compares pixels against the selected candidate, validates crop/name syntax, writes full and thumbnail hashes, and rejects hard duplicate selections.

## 6. Bind prose to evidence and render

Write `<work>/summary.json`. Every English prose block must cite the transcript segments it synthesizes:

```json
{
  "overview": "A practical walkthrough of the export workflow.",
  "chapters": [
    {
      "chapter_id": "ch03",
      "blocks": [
        {
          "text": "The presenter confirms the export and shows its completed state.",
          "seg_ids": ["seg_0084", "seg_0085"]
        }
      ]
    }
  ]
}
```

```bash
python3 "$SKILL_DIR/scripts/render.py" \
  --work "<work>" \
  --summary "<work>/summary.json" \
  --selections "<work>/selections.json" \
  --assets-dir "summary-<video-id>/assets" \
  --out-dir "summary-<video-id>"
```

The renderer fails closed when a required target is unresolved or omitted, a timestamp belongs to another chapter, provenance does not overlap the prose, a budget is exceeded, a hard duplicate exists, an extraction failed, or an asset/hash/alt/caption is missing. It writes `manifest.json` and escaped English `<html lang="en" dir="ltr">`.

## 7. Acceptance

- `candidates.py`, `grab.py`, and `render.py` exit zero.
- Every required chapter and target is covered and selected exactly where its evidence is discussed.
- No HTML frame is a hard duplicate or transition/blank substitute.
- Candidate IDs are unchanged when identical inputs and pixels are rerun.
- Open `index.html` and visually confirm every frame supports its adjacent prose.
- Preserve transcript, chapters, selections, summary, and manifests for follow-up edits; downloaded media and unused candidates are disposable after acceptance.

## Security boundary

The skill reads no project `.env`, browser state, cookies, or accounts. It never uploads video or frames. It writes only the requested work and output directories. Network activity is limited to the supplied public URL and, only when explicitly available and not disabled, an audio-only speech request to the chosen backend. Login-, cookie-, region-, or DRM-protected media requires separate user authorization; do not bypass those controls.
