# Changelog

## 1.1.0 — 2026-09-01

- Added transcript-linked `visual_targets` and bounded `light`/`advanced` extraction modes.
- Candidate records now preserve decoded `actual_t` values and selections use immutable `candidate_id` references instead of copied timestamps.
- Added target-aware multi-resolution deduplication, protected-cue recovery, adaptive local scene scoring, and fail-closed post-filter coverage.
- Added strip-first visual triage plus explicit image-area metrics; the cached real-video benchmark produced 36 candidates and a projected 33.3% image-read area versus the former 60-frame baseline, with no unresolved targets.
- Added verified full-resolution re-grab and deterministic `manifest.json`/`index.html` rendering with asset, chapter, segment, duplicate, timestamp-link, and alt-text checks.
- Added 12 Python standard-library and synthetic FFmpeg tests for chapter boundaries, local UI changes, action results, black transitions, cache invalidation, budgeting, coverage, and rendering.

## 1.0.0 — 2026-09-01

First public release.

- Pipeline: transcript-first chaptering → hybrid frame-candidate union (scene ∪ cue-offset ∪ pins ∪ final ∪ safety grid ∪ chapter coverage) → free blank/duplicate filtering → single 512px triage pass → 1280px re-grab of selections → manifest-driven HTML.
- Truthful timestamps: scene detection is metadata-only; every frame is extracted by seeking to its own timestamp (label == content by construction).
- Near-duplicate audit over the final selections (identical pairs fail the run, similar pairs listed for review).
- Per-chapter coverage table + midpoint fill for starved chapters (`--chapters`).
- Lossless stripping of YouTube auto-sub rolling overlap (~halves transcript tokens).
- Security hardening for publication: no `.env` reads from project directories; own config home at `~/.config/summarize-video/.env` (legacy `~/.config/watch/.env` fallback); strict validation of selection names and crop expressions.
- Frame-engine internals adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT).

## 1.1.0 — 2026-09-01

- New `scripts/bundle.py` + final pipeline step: the primary deliverable is now **one self-contained HTML file** (`summary-<id>.html`) with all images embedded as data URIs — opens with a double click, no server, no sidecar folder. The `summary-<id>/` directory remains the editable source; re-run bundle.py after changes.
