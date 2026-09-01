# Changelog

## 2.0.0 — 2026-09-01

Independent private implementation delivered as a parentless branch root.

- Replaced the complete transcript, media, frame-planning, ranking, verification, and rendering runtime.
- Added transcript-first caption acquisition and a streaming standard-library speech fallback.
- Added bounded 64×36 grayscale evidence scans with per-window robust motion thresholds.
- Added transition-to-stability selection for action results and progressive states.
- Added tile-aware visual comparison, semantic duplicate clustering, representative ranking, and content-addressed candidate IDs.
- Added schema version 3 source-time/cache manifests and full-precision decoded timestamp validation.
- Added fail-closed target selection, verified re-decode, asset hashes, duplicate checks, and deterministic English HTML.
- Added conservative image-read accounting that includes singleton evidence groups.
- Expanded the standard-library/synthetic-FFmpeg suite to 20 behavioral and integration tests.
- Cached 18:15 validation video: 24 candidates, seven strips, zero unresolved chapters/targets, and 34.2% projected image-read area versus the 60-frame baseline.
