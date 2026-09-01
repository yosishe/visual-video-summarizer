# Changelog

## 1.2.0 — 2026-09-01

Integration of the transcript-aligned engine (PR #2) with the single-file deliverable, plus recall and precision fixes found in review.

- **Transcript-linked visual targets.** `chapters.json` carries `visual_targets` referencing `seg_ids`; the engine derives every timestamp from the transcript — the model no longer types `--cues`/`--pins` (kept as legacy flags).
- **Selections by `candidate_id`.** `selections.json` references immutable candidate IDs; `grab.py` resolves the full-precision decoded `actual_t` and **verifies the re-decoded pixels match the candidate** before writing assets. Copying a displayed timestamp — the cause of a rounded-down scene cut embedding the previous shot — is no longer possible.
- **Recall kept.** Scene detection scans every `needs_frames` chapter in full (not only the predicted target windows), so unflagged slide flips still reach the pool; light cap raised 36 → 48 so reserved target frames don't crowd them out.
- **Deterministic rendering.** `render.py` validates provenance, budgets, coverage, duplicates and assets, then renders `manifest.json` + a designed `index.html` (TOC, claim box, chapter timestamp links, side-by-side figures) and bundles the **single self-contained `summary-<id>.html`** automatically.
- **Report:** per-chapter coverage table with counts and status; strips are opt-in (`--strips`) since 256px tiles are not legible for slide text.
- **Media cache identity** (`parts.json` cache key on source/sections/exact-cut); exact-cut sections (`--force-keyframes-at-cuts`) with decoded-duration validation.
- **Transcript overlap strip** compares words case-folded and punctuation-free (lossless, ~halves auto-caption tokens).
- **Blank detection** treats any near-uniform frame as blank (not only black/white) and recovers protected frames past transitions.
- Frontmatter restored for slash-command invocation (`user-invocable`, `argument-hint`, `version`) alongside spec `metadata`.
- Tests: 12 engine/integration tests (PR #2) + recall, transcript-overlap and blank-detection tests.

## 1.1.0 — 2026-09-01

- New `scripts/bundle.py` + final pipeline step: the primary deliverable is **one self-contained HTML file** (`summary-<id>.html`) with all images embedded as data URIs — opens with a double click, no server, no sidecar folder.
- PR #2 (Codex): transcript-linked `visual_targets`, light/advanced modes, decoded `actual_t` with seek-drift rejection, target-aware dedup with protected-cue recovery, verified full-resolution re-grab, deterministic `manifest.json`/`index.html` rendering, 12 stdlib + synthetic-ffmpeg tests.

## 1.0.1 — 2026-09-01

- Candidate filenames and the report carry full-precision timestamps; triage must copy them verbatim (scene timestamps sit exactly on cuts).

## 1.0.0 — 2026-09-01

First public release.

- Pipeline: transcript-first chaptering → hybrid frame-candidate union (scene ∪ cue-offset ∪ pins ∪ final ∪ safety grid ∪ chapter coverage) → free blank/duplicate filtering → single 512px triage pass → 1280px re-grab of selections → manifest-driven HTML.
- Truthful timestamps: scene detection is metadata-only; every frame is extracted by seeking to its own timestamp (label == content by construction).
- Near-duplicate audit over the final selections (identical pairs fail the run, similar pairs listed for review).
- Per-chapter coverage table + midpoint fill for starved chapters (`--chapters`).
- Lossless stripping of YouTube auto-sub rolling overlap (~halves transcript tokens).
- Security hardening for publication: no `.env` reads from project directories; own config home at `~/.config/summarize-video/.env` (legacy `~/.config/watch/.env` fallback); strict validation of selection names and crop expressions.
- Frame-engine internals adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT).
