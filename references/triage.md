# Triage and `selections.json` (Step 4 — the ONLY image spend)

Two reads, both listed in `<work>/reports/candidates.md` (the candidates report) and both recorded by the repository:

## 1. Contact sheets

Read ALL contact sheets in one message (4×4 tiles of 320px with the candidate id and time burned under each tile — a whole 64-frame pool is ≈ 5–6k image tokens instead of ≈ 13k). Decide per tile by its **burned-in id** (never by position — models miscount grid cells): keep / drop, and which tiles are the same picture. Every sheet carries one **sentinel** tile (flat gray, id `x_…`); report it as blank. If you cannot find a sheet's sentinel, do not trust your ids for that sheet — read its candidates individually instead. The report says which font the ids were burned with; when it says `default` (no TrueType font on this machine), read any tile whose id is not legible individually.

## 2. Shortlist (verified, recorded)

```
python "<SKILL_DIR>/scripts/workflow.py" shortlist --work "<work>" --ids c_0003,c_0011,...
```

(≤ 30 ids; ≈ 1.5 × the frames you expect to select.) This re-decodes the kept frames at 640px (`standard`) / 768px (`high`), pixel-verifies each against its candidate, prints their paths (`<work>/reports/shortlist.md`) and writes a **receipt** into `candidates.json` — the proof that triage happened. An id that is not a candidate is exit 10; a frame that fails the gate is listed under "Not written" (exit 2): drop it. **Read the written frames in a single message**, then select per chapter:

- **Content test:** keeps information — slide, code, diagram, chart, UI state, demo result. A frame of the presenter's face fails (in `high`, candidates flagged `(people frame)` in the report were already demoted in ranking; still apply the test).
- **Placement test:** its chapter (printed per candidate, derived from the decoded timestamp) is where the transcript discusses it.
- **Novelty test — one frame per board/scene:** when several candidates show the same whiteboard, slide, or screen at different build stages, select ONLY the most complete one (last of the run) — never one per stage, unless the transcript discusses an intermediate stage at length as its own point (then that stage earns its own frame). This applies across chapters too.
- **Quota:** 1–3 per chapter, at most 20 total (enforced). A chapter can end with zero. A long chapter with several distinct points deserves its 2–3 frames; do not starve it to hit a low number.
- Assign `role`: `evidence` (proves a number/claim/action) or `illustration` (represents the chapter). Roles shape the caption.
- The report marks candidates that are the same picture (`family=f_003`, "same picture also at 07:14"): pick at most one per family unless two chapters each need it.

## `selections.json`

Write `<work>/selections.json` **by `candidate_id`** — never by timestamp (the ID resolves to the full-precision decoded time; copying a displayed time is how a rounded-down scene cut once embedded the wrong shot). The caption is an object that says what is shown and **why the picture is here**; `novelty` makes the novelty test auditable:

```json
[{"candidate_id": "c_0017", "name": "ch03_export_result", "chapter_id": "ch03",
  "role": "evidence", "novelty": "new_state",
  "caption": {"shows": "דיאלוג הייצוא שהושלם, עם ההגדרות `PDF` ו-`A4` שנבחרו.",
              "why": "התוצאה של הפעולה שהמרצה מתאר; מוכיחה שהייצוא הצליח.",
              "look_at": "השורה `Export complete` בתחתית."},
  "alt": "דיאלוג ייצוא שהושלם עם הגדרות הפלט שנבחרו",
  "anchor_seg_ids": ["seg_0084", "seg_0085"],
  "crop": "w:h:x:y (optional - only to blow up a small UI region)"}]
```

- **Enforced** (`validate selections`, exit 10): known and unique `candidate_id`; `chapter_id` equal to the candidate's; `name` of letters, digits, `_`, `-`; `role`; `novelty` ∈ `new_state` (default) | `build_stage` (needs `caption.why`) | `reprise`; non-empty `caption.shows` (Hebrew in a Hebrew document); `alt` ≤ 160 characters; non-empty `anchor_seg_ids` that overlap the candidate's own segments (`seg_ids ∪ aligned_seg_ids`); `crop` as `w:h:x:y`; ≤ 3 per chapter, ≤ 20 total. A selection that was not in the verified shortlist is a warning (you chose it from a 320px tile).
- `caption.shows`: what is in the picture, one sentence. On-screen UI strings, menu names, commands and numbers go in `backticks`, in English exactly as they appear (the renderer isolates them LTR). `caption.why`: why the picture is here and not just text — "proves the number", "the final state of the board", "the result of the described action". `caption.look_at` (optional): the small detail a reader would miss. `alt` ≤ 125 characters ideally, visual content only, no "image of". In a Hebrew document all three open in Hebrew.
- `anchor_seg_ids` are the transcript segments the frame illustrates; the renderer places the figure right after the prose block that cites them.
- Optionally write `<work>/triage-rejections.json` — `[{"candidate_id": "c_0004", "reason": "people_frame|duplicate_of:c_0002|no_information|wrong_chapter|build_stage"}]` — you already looked; it costs nothing and it is what makes a missed visual explainable later.

## What happens next (zero tokens)

`workflow.py run` re-decodes every selection at 1280px (`grab.py`), **verifies the new pixels match the candidate you looked at**, writes `<name>-full.jpg` + `<name>-thumb.jpg` and `assets-manifest.json` bound to this selection set and this candidate pool. Do **not** Read these. In `high` (or `--refine sharpness`) grab also looks ±1.5 s around the triaged frame, inside its chapter, for the sharpest frame that is still a near-duplicate, and re-verifies; a failed refinement falls back to the triaged frame. Exit 2 = an extraction failure or unsafe name/crop (fix it); exit 3 = two selections render the same picture (keep the more complete one, fix `selections.json`, run again). Changing a caption later does not require a re-grab; changing a candidate id, name or crop does (the controller handles both).
