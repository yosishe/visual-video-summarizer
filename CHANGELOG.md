# Changelog

## Unreleased — 1.4.0 (step 2: overlay mask + family dedup)

- **Overlay mask (`scripts/layout.py`).** One 1 fps 160×90 gray decode per video; per pixel, the fraction of one-second pairs in which it moved (|Δ| > 4); pixels above `max(0.12, median + 4·MAD)` → 3×3 close → connected components → `webcam` (1–20 % of the frame, aspect 0.8–2.4, ≥30 % filled, no side > 60 %) or `bar` (≤10 % tall, ≥80 % wide), kept only when present in both halves of the pairs (IoU ≥ 0.5). The mask blanks those boxes (mid-gray) in every signature — dedup, the re-grab gate, refinement, the hard-duplicate audit — and never touches a written frame. Measured on the release screencast: webcam found at x 0–0.15, y 0.69–1.0 (5.2 % of the signature), 7–9 s. `-skip_frame nokey` is deliberately not used: libdav1d ignores it for AV1 and emits duplicated frames that look still. Profile key `pip_mask`.
- **Family dedup across the video.** `deduplicate_frames(scope="family")` compares every frame with every other; a family keeps one representative per chapter that holds a protected frame (target / coverage / cue / pin), drops revisits elsewhere and records them as `family_revisits` on the keeper; `family_id` is reported per candidate and in the triage list ("same picture also at …"). The old chapter×target scope survives as `dedup_scope: chapter` for ablation. Fixes the 1.3.0 blind spot where a target frame and a scene frame of the same slide were never compared.
- **Measured (bench, ISb0nrlNoKQ, draft annotation of 22 essential visuals; intervals of two states corrected after inspecting frames):** residual near-duplicate pairs in the pool 4 → 0 (standard) and 4 → 1 (high); distinct pictures 47 → 50 / 72 → 75. Important Visual Recall standard 59 % → 86 % (PoR 1.33 → 1.64), high 82 % → 86 % (PoR 1.55 → 1.64); redundancy 7 % → 10 % / 10 % → 5 %; alignment 79 % → 88 % / 89 % → 83 %. Pool recall unchanged (86–91 %): the missed boards are unsampled whiteboard states between targets — the v1.5 visual-state engine's job. CPU +9 s per run.
- Tests: 10 new (overlay detection on synthetic frames and a synthetic video, masked signatures, family vs chapter scope); 62 total.

## Unreleased — 1.4.0 (step 1: benchmark + loss attribution)

- **`bench/`** — corpus manifest (6 videos: mixed screencast, slides lecture, VS Code demo, 60 Minutes interview, 3Blue1Brown animation, Hebrew Git tutorial), `storyboard.py` (annotation sheets from YouTube's storyboard track, zero download), `run.py` (deterministic stages per profile, import of existing runs), `score.py` (Important Visual Recall, pool recall, precision, redundancy, frame efficiency, alignment accuracy, image tokens by ⌈w/28⌉×⌈h/28⌉, uniform/random baselines and PoR, missed-visual attribution, summary checklist + Hebrew hygiene counts). Baseline recorded for the 1.3.0 release run on `ISb0nrlNoKQ`: standard IVR 64 % / high 86 % against a draft annotation of 22 essential visuals.
- **`dropped.json`** — `candidates.py` now records every discarded raw frame with its reason (`blank` / `dedup` / `cap`) and the keeper's time, so a missed visual is attributed to the stage that lost it.
- **`--profile-override`** — JSON merged over the tier's `PROFILES` entry (known keys only); `candidates.json` carries `profile_override` and `profile_sha256` so ablation runs group by effective profile.

## 1.3.0 — 2026-09-04

Two tiers, measured slide terminals, a sharpness gate that keeps the pixel-verification guarantee, PDF export, and three hygiene fixes — after a survey of the neighbouring projects (dsh-bilibili, PlanOpticon, keyframe-blogger, Video-Analyzer).

- **`--tier standard | high`.** All tier-dependent numbers live in one `PROFILES` table (`candidates.py`); `--mode light|advanced` stay as aliases and `candidates.json` keeps its `mode` key. `high`: adaptive scene scoring, 5–6 samples per target with 3 alternatives, a 64-frame pool with a 16-slot unplanned floor, and the three signals below. Chosen by argument only — the skill never asks.
- **Measured terminal frame for `slide`/`diagram` targets (both tiers).** A cheap 192px scene-score probe over the target window finds where the build-up ends: build steps are walked forward, the first screen flip stops the walk (`stable_terminal_from_scores`), and the frame 0.2 s before it replaces the old "end of the sentence minus 0.25 s" assumption.
- **Sharpness refinement at grab time (`high`, or `grab.py --refine sharpness`).** One `blurdetect` + signature pass over ±1.5 s picks the sharpest frame that is *still a near-duplicate of the triaged candidate* (the verification gate's own predicate), inside the chapter; the refined frame is re-decoded and gated again, and falls back to the triaged frame on any failure. Assets and `manifest.json` now carry `triaged_t` alongside the written `actual_t`; the renderer validates and captions the **written** time.
- **Face demotion (`high`, optional).** Haar-cascade people-frame detection via OpenCV when it is importable; `-25` in ranking, below the target bonus. Never a dependency — reported `faces: unavailable` otherwise.
- **OCR text density (`high`).** ffmpeg's `ocr` filter (tesseract, `eng+heb`) yields a per-frame character count used only to rank build states of a slide (up to +15). The 2026-09-01 decision — the model is the only OCR for triage — stands for `standard`; the text itself is never stored.
- **Five content gaps** added to Step 2 as target triggers: dangling reference, conclusion without its data, unspoken operation, silent demo, visual comparison.
- **`render.py --pdf`** prints the single-file HTML to `summary-<id>.pdf` via Chrome headless (WeasyPrint fallback) with new print CSS (A4, figures never split); exit 4 when no engine exists.
- **Honest cost line.** `candidates.json["cost"]` and the report: image tokens from each candidate's real dimensions (w×h/750), CPU passes, and the other tier's ceiling. The old "25–50k" claim is replaced by the formula (≈ 9.5k / 12.6k at the caps for 16:9).
- **Re-grab seeks half a frame early.** `actual_t` is a pts rounded to 3 decimals; seeking to it exactly landed on the *next* frame whenever the rounding went up. On the release screencast one of 14 standard-tier assets carried a timestamp one frame late (harmless on a static slide), and in `high` one mid-pan scene frame was refused by the verification gate outright (exit 2). After the fix: 0 of 14 late, 20/20 in `high`.
- Fixes: `--max-candidates` is a hard ceiling (the report warns when reserved frames are trimmed); `--scene-threshold` is honoured in the adaptive pass as its floor instead of being silently ignored; an unsafe selection name is now a listed failure → exit 2, as the docs always claimed; blurdetect `nan` rows keep their slot so metadata and decoded frames stay aligned; the report states the effective pool when reserved frames lift it above the nominal cap.
- Behaviour change for the `--mode advanced` alias: cap 60 → 64 and grab-time refinement on by default.
- Measured on the 18-minute release screencast (12 chapters, 24 targets): `standard` 50 candidates / 26 s / ≈9.9k image tokens; `high` 76 / 69 s / ≈15k (OCR on 45 cluster frames, faces unavailable without cv2); grab 29 s; PDF 2.2 MB via Chrome in 3 s. Refinement moved 0/20 frames — nothing sharper exists in a crisp screen recording; it is for camera footage and transitions.
- Tests: 22 new (profiles, terminal probe, refinement primitives, dedup cluster hook, signals, cost, print CSS, PDF engine fallback, high-tier integration with a blurred fixture, gate fallback, exit codes, PDF output) — 43 total.

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
