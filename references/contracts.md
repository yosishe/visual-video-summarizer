# Data contracts

Use this reference when authoring or consuming pipeline JSON. Generated schema version 3 files are immutable stage outputs; rerun their producer instead of editing them.

## `transcript.json`

Required top-level fields: `schema_version`, `video`, and `segments`.

Each segment has unique `seg_id`, finite `start`, finite `end`, and non-empty `text`. `video` contains source metadata, duration, and whether the original source was a URL.

## `chapters.json`

Top-level ordered array. Chapters must not overlap and use half-open `[start,end)` ownership. Required fields:

- `chapter_id`: unique stable ID.
- `title`: English display title.
- `start`, `end`: source-media seconds.
- `needs_frames`: whether the chapter requires visual evidence.

Optional `visual_targets` entries:

- `target_id`: unique stable evidence ID.
- `kind`: `state`, `action_result`, `diagram`, or `slide`.
- `seg_ids`: transcript segments that establish or explain the pixels.
- `action_seg_id`: segment whose end starts an action-result search.
- `why`: concise evidence requirement.
- `anchor_t`: explicit source-time anchor.
- `window`: explicit absolute `[start,end]` search window, clamped to the chapter.

Legacy `cues` are converted to state targets and written back as normalized `visual_targets`. New work should author targets directly.

## `download/parts.json`

Generated cache and time-mapping manifest:

```json
{
  "schema_version": 3,
  "cache_key": "sha256",
  "identity": {
    "kind": "file-or-url",
    "sections": [[120.0, 130.0]],
    "exact": true,
    "schema": 3
  },
  "parts": [
    {
      "part_id": "part_000",
      "path": "/absolute/media.mp4",
      "source_start": 120.0,
      "media_start": 0.0,
      "duration": 10.0,
      "frame_duration": 0.033367
    }
  ]
}
```

Source URL/path, file size/mtime, requested ranges, exact-cut mode, or schema changes invalidate the key. Downstream stages reject older manifest shapes instead of guessing their time mapping.

## `candidates.json`

Generated fields include:

```json
{
  "schema_version": 3,
  "engine": "independent-visual-evidence-engine",
  "mode": "light",
  "chapters": [],
  "counts": {},
  "candidates": [
    {
      "candidate_id": "cand_6f91b38d1ab274",
      "requested_t": 133.4,
      "actual_t": 133.433367,
      "timestamp_error": 0.033367,
      "chapter_id": "ch03",
      "target_ids": ["ch03_export_result"],
      "target_kinds": ["action_result"],
      "seg_ids": ["seg_0084", "seg_0085"],
      "reasons": ["target", "recovered"],
      "quality": {
        "mean_luma": 142.1,
        "contrast": 48.2,
        "sharpness": 11.4,
        "blank": false,
        "fingerprint": "sha256"
      },
      "media_part_id": "part_000",
      "path": "/absolute/candidate.jpg"
    }
  ],
  "coverage": {"chapters": [], "targets": []},
  "triage": {}
}
```

`candidate_id` is content-addressed from chapter, target set, decoded time, and visual fingerprint. `coverage` values are `covered`, `not-required`, or `unresolved`.

`chapters` is the normalized runtime view, including legacy-cue conversion. The authored `chapters.json` is never overwritten; the renderer uses this normalized view for target enforcement.

The triage budget reports pixel area, not vendor tokens. `projected_individual_reads` includes every evidence group, even when it has no multi-frame strip. `candidate_only_ratio` is the cost of reading every final 512px candidate individually against the 60×512 baseline.

## `selections.json`

Model-authored top-level array. Maximum 20 rows globally and three per chapter. Every required target must be represented.

Required fields:

- `candidate_id`: must exist in `candidates.json`; timestamps are not accepted as identity.
- `name`: safe asset stem using ASCII letters, digits, `_`, or `-`.
- `chapter_id`: must equal decoded timestamp ownership.
- `role`: `evidence` or `illustration`.
- `caption`, `alt`: non-empty English text.
- `anchor_seg_ids`: must overlap candidate provenance and an adjacent summary block.

Optional `crop` is integer FFmpeg `w:h:x:y` syntax.

## `summary.json`

Requires non-empty English `overview` and exactly one chapter entry per `chapters.json` row. Every chapter has non-empty `blocks`; each block has English `text` and one or more valid `seg_ids`. Optional `key_points` is an English string array.

## Asset and output manifests

`grab.py` writes schema version 3 `assets/assets-manifest.json` with decoded time, source candidate, dimensions, SHA-256 hashes, failures, and hard-duplicate pairs.

`render.py` writes:

- `manifest.json`: video, chapters, prose, coverage, frame provenance, quality, and asset hashes.
- `index.html`: deterministic escaped English HTML that references validated local assets only.

The renderer refuses incomplete target selection, unresolved evidence, extraction failures, hard duplicates, cross-chapter timestamps, missing assets/hashes, missing alt/caption, unknown segments, non-overlapping provenance, or exceeded budgets.

## Compatibility

The candidate CLI still accepts `--cues`, `--pins`, `--sections`, `--resolution`, `--max-candidates`, `--scene-threshold`, and `--no-dedup`. `--scene-threshold` is accepted only to keep older automation from crashing; the independent engine uses per-window motion statistics rather than a global threshold.
