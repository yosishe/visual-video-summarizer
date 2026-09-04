# Changelog

## Unreleased

- `LICENSE` is now the MIT text only; third-party attributions (claude-video, Heebo, design references) moved to `NOTICE`.

- **Caption track selection bug.** `--sub-langs` is a regex in yt-dlp: the ranked key `en` also downloaded `en-de`, `en-en`, `en-hi`, … (machine translations of the manual track) and the fallback glob then picked `video.en-de.vtt` alphabetically, so a 3Blue1Brown video was transcribed from a translated track while `source_detail` reported it as the track chosen. The ranked key is now anchored (`^en$`) and the exact `video.<key>.vtt` is used; the explicit `--langs` path prefers the shortest track name. `bench/run.py prepare` no longer passes the manifest's `langs` pattern (which bypassed the ranking) — `langs_override` forces one when needed. Tests: 2 new (`FetchCaptionsTests`).
- **Benchmark widened from one video to six.** Chapters authored for the five other manifest videos (`bench/inputs/<id>/chapters.json`), storyboard-derived annotation drafts for all five (`bench/annotations/<id>.json`, status `draft`, pending Yosi's review), the v15-high pool run on each; results per category in `bench/README.md`.
- Integration tests are hermetic to the user's `SUMMARY_LANG` config (fixtures are English).
- **Token guards.** `candidates.py --max-image-tokens` (default `SUMMARY_MAX_IMAGE_TOKENS`, else 12,000 `standard` / 20,000 `high`) plans the spend before any read — sheets fixed, shortlist variable — writes it to `candidates.json` `token_budget`, prints a **Token budget** line, and stops with exit 7 when even the sheets do not fit (`--allow-over-budget` to proceed); `shortlist.py` enforces the budget's `shortlist_max`. Videos over 120 minutes stop with exit 8 unless `--allow-long`. `SKILL.md` gains the read rules that keep the spend bounded. Tests: 6 new (`test_token_budget.py`); 113 total.
- **Public-release hygiene.** LICENSE is the verbatim MIT text with third-party notices moved below it (so GitHub detects the license); committed benchmark artifacts no longer contain this machine's absolute paths (`bench/run.py` relativizes them to `<skill>/…` on copy); README gains a cost-and-guards section, an example screenshot (`docs/example-he.png`) and a benchmark section.

## 1.6.0 — 2026-09-04

One release for the vNext roadmap of the 2026-09-04 audit, built and measured in four stages on `feat/bench` (PR #7). Each stage was scored on the benchmark against the previous one before the next began.

### Two-stage triage: contact sheets + verified shortlist, caption provenance

- **`scripts/sheets.py`.** After `candidates.json` is written, the pool is tiled into 4×4 contact sheets (320 px tiles, chronological, the candidate id and time burned into an 18 px bar under every tile, one flat-gray **sentinel** tile with an `x_…` id per sheet at a random non-first position). A 4×4 sheet is 1280×792 → 1,334 visual tokens, 83 per tile against 209 for a 512 px candidate read alone. The report lists every sheet with its ids and sentinel; the model keeps/drops by burned-in id and must report the sentinel as blank (CollagePrompt / "VLMs are blind": grids beyond 4×4 lose accuracy and models mis-index cells). Profile keys `sheets`, `sheet_tiles`; PIL absent → the report falls back to individual reads.
- **`scripts/shortlist.py`.** Stage 2 re-decodes the kept ids (≤ 30) at 640 px (`standard`) / 768 px (`high`) and checks each against its 512 px candidate with the overlay-masked near-duplicate gate, so the legible frame the model reads is provably the picture grab will write. Profile key `shortlist_px`.
- **Caption provenance.** In `high`, the first 300 characters tesseract reads on a candidate are kept as `quality.ocr_text`; the report prints `spoken: "…"` (the aligned segments) and `ocr: "…"` under every candidate so `caption.shows` can quote UI strings and `audit_summary.py`'s caption check has something to ground them against. `SKILL.md` Step 4 is now the two-stage procedure; the Security section states the OCR text is kept in the work directory only.
- **Measured (ISb0nrlNoKQ, v15-high pool of 64):** 5 sheets ≈ 6,026 tokens + a 24-frame shortlist at 768 px ≈ 10,752 → 16,778 image tokens against 13,376 for reading all 64 candidates at 512 px — as the audit predicted, token-neutral to slightly dearer at 18 minutes (the pool is small), a 2–3× saving on hour-long pools, and the model reads its final picks at 768 px instead of 512. All five sentinels were identified; shortlist gate 24/24. Selections and scores are unchanged from v1.5.
- Tests: 3 new (`test_sheets.py`); 105 total.

### Visual-state engine

- **`scripts/states.py` — the video as pictures, not instants.** One 2 fps 160×90 gray decode per part (`showinfo` timestamps, ≈13 s for 18 min of AV1) yields the overlay mask *and* per-sample masked 64×36 signatures, ink and motion. Samples merge into runs — compared with the run's anchor (drift) and last frame (cuts) under per-mode thresholds; a canvas (whiteboard/typing) or dynamic-UI run may drift with its anchor, a static slide may not, so a cross-fade can never merge two slides — then into states clipped at chapter boundaries, each with a mode (A talk / B static / C canvas / D dynamic UI, from 20-s window statistics), a representative time (last settled frame; the fullest settled frame of a build), `alt_t` (first settled / max ink / last settled), a build record, a family id shared by revisits, the transcript segments it was on screen for (overlap ∪ a 3–4 s lead ∪ EN/HE cue phrases) and a bounded importance (targets 0.35, cues 0.15, YouTube's most-replayed heatmap 0.15, chapter need 0.10, mode prior 0.25). Written to `work/states.json`.
- **`candidates.py --engine states` (default; `legacy` keeps the 1.3 sampler for ablation).** One candidate per non-talk state (plus the first settled frame of a build whose target cites its start); targets attach to the states overlapping their window — `action_result` to the first state after the action — so `target_sample_times`, the terminal probe and the scene pass no longer run. Chapter coverage midpoints are only added where no state exists. Candidates carry `state_id`, `mode`, `importance`, `aligned_seg_ids`; the report prints the mode timeline and state counts.
- **Greedy fill.** Unplanned pool slots are filled by argmax of 0.5·importance + 0.3·novelty (distance to the nearest chosen picture) + 0.2·uniformity (time gap) instead of even spacing; the uniform pick is still computed and recorded as `baselines.uniform_fill`.
- **`render.py`** accepts anchors inside `aligned_seg_ids` (engine-derived lead/overlap), not only the target's own segments.
- **Measured (bench, ISb0nrlNoKQ, annotation rev. c, 22 essential visuals):** seeks 80/327 → 102/102; CPU standard 39 s → 27 s, high 82 s → 29 s; pool recall 86/91 % → **91/100 %**; IVR 82/82 % → **86/91 %**; redundancy 15/10 % → **5/0 %**; PoR 1.58 → **1.67/1.75**; image tokens/min 572/870 → 549/732 (no reserved-frame lift needed). The two whiteboard states every earlier profile missed are now in the pool and selected.
- Tests: 9 new (`test_states.py`); 102 total.

### Hebrew by default, RTL rendering, grounding audit, caption provenance

- **`--lang he|en`, Hebrew by default.** The summary, captions and page are written in Hebrew directly from the transcript (never a translation of an English draft); `--lang en` keeps the English path. Resolution: flag → `summary.json` `lang` → `SUMMARY_LANG` (env or `~/.config/summarize-video/.env`) → `en`. `SKILL.md` Step 6 carries the Hebrew writing rubric (structure, what to keep, what to drop, terminology, no niqqud, every sentence opens in Hebrew).
- **`summary.json` schema 3** — `lang`, `source_language`, `glossary`, blocks with `block_id` and `kind` (`prose` | `code` → `<pre dir="ltr">` | `quote` → `<blockquote dir="auto">`); `backticks` are the only inline markup and render as `<code dir="ltr">`.
- **Captions with provenance** — `caption` is `{shows, why, look_at}` (string still accepted), `novelty` (`new_state` | `build_stage` | `reprise`; a build stage must justify itself in `why`), optional `triage-rejections.json`. The renderer prints the "why" under every figure.
- **RTL rendering** — `<html lang dir>` from the document language, logical CSS throughout (`border-inline-start`, `padding-inline-start`, `max-inline-size` …), timestamps/ranges/code isolated with `<bdi dir="ltr">` / `dir="ltr"`, `<h1 dir="auto">` for the video title, UI strings in a localized `STRINGS` table, and two Heebo subsets (Regular/Bold, 18 KB WOFF each, SIL OFL — `scripts/fonts/`) embedded as data URIs so the single file renders identically offline. Manifest schema 3 (`lang`, `direction`, `translation_mode`, `transcript_source`, `audit`, `summary_sha256`, `selections_sha256`, `ensure_ascii=False`); asset files are re-hashed before rendering.
- **`scripts/audit_summary.py`** — deterministic grounding gate, run by `render.py` (exit 5 on errors): numbers, `backtick` identifiers and URLs must be in the cited segments (or the video's metadata); segments in order and inside their chapter (±5 s); no niqqud, no bidi control characters, Hebrew prose in Hebrew blocks; soft reviews for names found elsewhere in the transcript, dropped negations, uncited stretches over 60 s, captions whose terms are not in the frame's segments/OCR. On the first real Hebrew summary it caught 9 errors (chapter ownership of boundary segments, invented counts) before the first render.
- **Caption tracks by provenance** (`transcript.py`) — manual original → manual he/en → `xx-orig` ASR → untranslated ASR; machine-translated tracks (`tlang=`) are never used; YouTube's `iw` is reported as `he`; `transcript.json` carries `language`, `source_detail` and the creator's chapter list; yt-dlp's exit code is checked; exit 6 when no transcript (the frames-only promise is withdrawn). Whisper gets the language hint and reports the detected language. Geresh/gershayim are word-internal for overlap stripping.
- **Image tokens** — `⌈w/28⌉×⌈h/28⌉` (Claude vision docs) replaces `w×h/750`: 512×288 = 209, not 197; docs and the report updated.
- **Hygiene** — `bundle.py` hardened (asset paths cannot escape `assets/`, manifest required, atomic write, never over `index.html`); dead `safety` reason removed; `triage.instructions` no longer mentions strips when there are none; the provenance line finally renders (`transcript.source`).
- **Verified end to end** on the ISb0nrlNoKQ high run: 12-chapter Hebrew summary (1,076 words, 100 % of segments cited, 85 % Hebrew letters), 20 Hebrew captions, render 4.5 s, PDF via Chrome 2.2 MB — RTL layout, two-column TOC, isolated LTR runs, Heebo — 0 audit errors after two rounds of fixes.
- Tests: 31 new (RTL rendering, audit, track ranking, bundle); 93 total.

### Overlay mask + family dedup

- **Overlay mask (`scripts/layout.py`).** One 1 fps 160×90 gray decode per video; per pixel, the fraction of one-second pairs in which it moved (|Δ| > 4); pixels above `max(0.12, median + 4·MAD)` → 3×3 close → connected components → `webcam` (1–20 % of the frame, aspect 0.8–2.4, ≥30 % filled, no side > 60 %) or `bar` (≤10 % tall, ≥80 % wide), kept only when present in both halves of the pairs (IoU ≥ 0.5). The mask blanks those boxes (mid-gray) in every signature — dedup, the re-grab gate, refinement, the hard-duplicate audit — and never touches a written frame. Measured on the release screencast: webcam found at x 0–0.15, y 0.69–1.0 (5.2 % of the signature), 7–9 s. `-skip_frame nokey` is deliberately not used: libdav1d ignores it for AV1 and emits duplicated frames that look still. Profile key `pip_mask`.
- **Family dedup across the video.** `deduplicate_frames(scope="family")` compares every frame with every other; a family keeps one representative per chapter that holds a protected frame (target / coverage / cue / pin), drops revisits elsewhere and records them as `family_revisits` on the keeper; `family_id` is reported per candidate and in the triage list ("same picture also at …"). The old chapter×target scope survives as `dedup_scope: chapter` for ablation. Fixes the 1.3.0 blind spot where a target frame and a scene frame of the same slide were never compared.
- **Measured (bench, ISb0nrlNoKQ, draft annotation of 22 essential visuals; intervals of two states corrected after inspecting frames):** residual near-duplicate pairs in the pool 4 → 0 (standard) and 4 → 1 (high); distinct pictures 47 → 50 / 72 → 75. Important Visual Recall standard 59 % → 86 % (PoR 1.33 → 1.64), high 82 % → 86 % (PoR 1.55 → 1.64); redundancy 7 % → 10 % / 10 % → 5 %; alignment 79 % → 88 % / 89 % → 83 %. Pool recall unchanged (86–91 %): the missed boards are unsampled whiteboard states between targets — the v1.5 visual-state engine's job. CPU +9 s per run.
- Tests: 10 new (overlay detection on synthetic frames and a synthetic video, masked signatures, family vs chapter scope); 62 total.

### Benchmark + loss attribution

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
