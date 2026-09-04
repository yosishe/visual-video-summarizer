# bench/ — measuring the frame engine instead of believing it

The benchmark answers one question per engine change: **did essential visuals
survive, at what redundancy, placed next to the right text, at what cost** —
reported separately, next to uniform and random baselines at the same frame
budget. It exists because nothing in this field measures accuracy (crv's
7-video eyeball benchmark is the only one) and because the 1.3.0 release run
showed three of four new signals had no observable effect.

## Layout

```
bench/
  manifest.json            corpus: ids, category, language, --langs (YouTube keys Hebrew as `iw`)
  annotations/<id>.json    human ground truth (essential visuals as INTERVALS; see below)
  inputs/<id>/chapters.json  model-authored chapters, shared across profiles (same targets → fair engine comparison)
  profiles/<name>.json     tier + PROFILES override for one ablation
  storyboard.py            annotation sheets from YouTube's storyboard track (no video download)
  run.py                   deterministic stages per profile; imports existing runs
  score.py                 metrics → runs/<run>/scores.json + REPORT.md
  runs/<date>-<profile>/<id>/  candidates.json, dropped.json, selections.json, summary.json, manifest.json, cost.json
```

`sheets/` and the per-run `work/` dirs are git-ignored (media and thumbnails);
only ids, annotations, JSON artifacts and reports are committed.

## Annotating a video (15–30 min, no download)

```bash
python3 bench/storyboard.py <video_id>
```

writes `sheets/<id>/sheet_NN.jpg` — 4×3 tiles with the tile time burned in and
a red frame on every coarse-state boundary — and `annotations/<id>.draft.json`
with the coarse states pre-filled. Edit the draft: set every state's `class` to
`essential` (the summary is materially worse without it), `acceptable` (a fine
alternative for the same content), or `irrelevant`; adjust `start`/`end` to the
interval the visual is on screen; link build stages and reprises with
`duplicates_of`; list `spoken_seg_ids` (from the run's transcript) for the
segments that discuss it; add a `summary_checklist` of claims with
`must_tokens`. Rename to `<id>.json` and set `status` to `reviewed`.

## Running a profile

```bash
python3 bench/run.py prepare    --profile v130-standard         # transcripts; copies inputs/<id>/chapters.json when present
# author work/chapters.json in Claude Code for new videos, copy to inputs/<id>/
python3 bench/run.py candidates --profile v130-standard         # candidates.py with the profile; cost.json
# triage in Claude Code → work/selections.json (+ summary.json)
python3 bench/run.py finish     --profile v130-standard         # grab + render
python3 bench/score.py --run bench/runs/<date>-v130-standard
```

Ablations are profiles: `{"tier": "high", "override": {"scene_floor": 0.08}}`
becomes `candidates.py --tier high --profile-override '{"scene_floor": 0.08}'`;
the effective profile and its sha256 land in `candidates.json`.

## Metrics (score.py)

| metric | definition |
|---|---|
| pool recall | essential states with ≥1 *candidate* in `[start−1 s, end]` — the triage upper bound |
| Important Visual Recall (IVR) | the same over *selected* frames — the number to optimise |
| precision | selected frames inside an essential or acceptable state |
| redundancy rate | selected frames beyond the first per `duplicates_of` group |
| frame efficiency | essential hits ÷ selected |
| alignment accuracy | essential hits whose `anchor_seg_ids` intersect the state's `spoken_seg_ids` |
| baselines | uniform over time (needs_frames chapters), uniform over the pool, random (5 seeds) at the same budget; PoR = IVR ÷ random |
| cost | image tokens `Σ ⌈w/28⌉×⌈h/28⌉` per candidate read, per minute; wall-clock from cost.json |
| summary | checklist coverage; Hebrew hygiene counts (niqqud, bidi controls, blocks opening with Latin, dashes) |

Every missed essential visual is attributed to `not_in_pool`, `blank_dropped`,
`dedup_dropped`, `cap_dropped` (from `dropped.json`, engine ≥ 1.4) or
`triage_rejected` — that list is the input to the next engine iteration.

## Results so far (ISb0nrlNoKQ, draft annotation of 22 essential visuals)

| profile | pool | pool recall | selected | IVR | precision | redund. | align | uniform(pool) | random | PoR | tokens/min | CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v130-standard | 50 | 86% | 14 | 59% | 100% | 7% | 79% | 59% | 45% | 1.33 | 572 | 26 s |
| v130-high | 76 | 86% | 20 | 82% | 100% | 10% | 89% | 73% | 53% | 1.55 | 870 | 69 s |
| v14-standard (overlay mask + family dedup) | 50 | 86% | 20 | 82% | 100% | 15% | 94% | 77% | 52% | 1.58 | 572 | 39 s |
| v14-high | 76 | 91% | 20 | 82% | 100% | 10% | 88% | 73% | 52% | 1.58 | 870 | 82 s |
| **v15-standard** (visual states, greedy fill) | 48 | 91% | 20 | **86%** | 100% | **5%** | 94% | 77% | 52% | **1.67** | 549 | 27 s |
| **v15-high** | 64 | **100%** | 20 | **91%** | 100% | **0%** | 88% | 73% | 52% | **1.75** | 732 | 29 s |

(Annotation revision c: three interval boundaries were corrected after
inspecting extracted frames — `s02`, `s29`, `s25/s26` — so every row was
rescored against the same ground truth; earlier reports quoted slightly
different v1.3.0/v1.4 figures.)

Residual near-duplicate pairs inside the pool (masked predicate, v1.4): 4 → 0
(standard), 4 → 1 (high); distinct pictures 47 → 50 / 72 → 75.

v1.5 replaced the 300-seek scene pass with one 2 fps decode: 102 seeks instead
of 327, pool recall 86/91 % → 91/100 %, and the two whiteboard states no earlier
profile ever sampled (`s06` "Enter Task → Computer", `s13` "Employee") are now
in both pools and selected. The remaining misses are a per-chapter cap
(`s03`: chapter 2 holds four essential pictures and the renderer allows three)
and one board the annotation still places about 10 s off (`s28`).

The v14-high run also carries the first **Hebrew** summary (`--lang he`):
1,108 words, checklist coverage 100 % (bilingual `must_tokens`), 100 % of
segments cited, 85 % Hebrew letters, 0 niqqud, 0 bidi controls, 0 prose blocks
opening with Latin, 0 audit errors (after two rounds of fixes the audit forced:
boundary segments cited in the wrong chapter, counts the speaker never said);
render 4.5 s, PDF 2.2 MB via Chrome.

The v1.3.0 selections were made by the release-run session; the v1.4/v1.5
selections by this session under the same Step 4 rubric — triage variance is
part of what the numbers contain.
