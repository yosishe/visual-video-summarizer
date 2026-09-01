# Changelog

## 1.0.0 — 2026-09-01

First public release.

- Pipeline: transcript-first chaptering → hybrid frame-candidate union (scene ∪ cue-offset ∪ pins ∪ final ∪ safety grid ∪ chapter coverage) → free blank/duplicate filtering → single 512px triage pass → 1280px re-grab of selections → manifest-driven HTML.
- Truthful timestamps: scene detection is metadata-only; every frame is extracted by seeking to its own timestamp (label == content by construction).
- Near-duplicate audit over the final selections (identical pairs fail the run, similar pairs listed for review).
- Per-chapter coverage table + midpoint fill for starved chapters (`--chapters`).
- Lossless stripping of YouTube auto-sub rolling overlap (~halves transcript tokens).
- Security hardening for publication: no `.env` reads from project directories; own config home at `~/.config/summarize-video/.env` (legacy `~/.config/watch/.env` fallback); strict validation of selection names and crop expressions.
- Frame-engine internals adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT).
