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

## Baseline (1.3.0 release run, ISb0nrlNoKQ, draft annotation)

| profile | pool | pool recall | selected | IVR | redund. | align | uniform(pool) | random | PoR | tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|
| v130-standard | 50 | 91% | 14 | 64% | 0% | 79% | 55% | 44% | 1.46 | 572 |
| v130-high | 76 | 91% | 20 | 86% | 5% | 89% | 77% | 53% | 1.64 | 870 |

Two essential boards are absent from both pools (`s06` "Enter Task → Computer",
`s13` "Employee" list): unplanned whiteboard states between targets, exactly the
recall gap the visual-state engine (v1.5) is meant to close.
