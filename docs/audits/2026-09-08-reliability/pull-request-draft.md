# Make acquisition failures explicit and resume local transcription

Draft only: no pull request has been published. Target repository: [yosishe/visual-video-summarizer](https://github.com/yosishe/visual-video-summarizer), base `main`, working branch `codex/visual-video-reliability`.

Caption download errors could be mistaken for absent captions and trigger unnecessary audio acquisition and transcription. Permanent provider failures also continued through later chunks. This change preserves failure categories, stops inappropriate fallbacks, limits eligible operations to three attempts, and retains work across interruption.

Validated source/track/options/hash caches reuse inventory, captions, media and completed chunks, with explicit shared-cache support and writer locks. An installed, explicitly configured multilingual whisper.cpp model enables local transcription; cloud upload remains an explicit provider choice. Actual encoded sizes are checked, uncertain uploads require an explicit retry decision, and missing chunks pause completion unless the user accepts a visibly partial delivery bound to those exact gaps.

The existing controller, original-frame selection, pixel checks, source references, Hebrew/English rendering and delivery gates remain. Controller JSON is compact, omitted options survive same-source reconfiguration, HTML can proceed with requested PDF outstanding, and Windows dependency setup has bounded feed-error recovery with executable checks. The skill and existing references document the implemented decisions and limitations.

Validation:

- Baseline: 285 tests passed. Redesign: 350 tests passed locally in 36.893 seconds, including actual PDF export, synthetic-media frame grounding, Hebrew/RTL and bundling.
- Independent review passed for the reviewed reliability scope after reproduced fixes; its focused run covered 91 tests.
- Deterministic benchmark: 13/13 decision assertions passed. Four-chunk authentication failure calls fell from four to one; caption 429 caused zero media/ASR fallback calls; uncertain uploads were not automatically replayed.
- Existing six-video scorer artifacts produce identical scores. This is not a new end-to-end video quality study.
- Python compilation, diff whitespace and clean-baseline patch application passed.
- New Linux/macOS/Windows CI has not run because automatic approval review blocked commit/publication. Real Hebrew/English model quality and live cloud behavior/billing remain unmeasured; no software/model download or cloud upload was performed.

Closes #16.

Merge remains with the repository owner.
