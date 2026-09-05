# Authoring `chapters.json` (Step 2 — text only, no images yet)

Write `<work>/chapters.json` from the transcript report (`<work>/reports/transcript.md` or `<work>/transcript.txt`). The controller validates it before anything is downloaded (`workflow.py run` or `validate chapters`): every rule marked **enforced** stops the run with exit 10 and names the chapter or target.

## Shape

Top-level array. Chapter windows are half-open `[start,end)`; only the last chapter owns the exact end.

```json
[{"chapter_id": "ch03", "title": "Export workflow", "start": 120.0, "end": 210.0,
  "needs_frames": true,
  "visual_targets": [
    {"target_id": "ch03_export_result", "kind": "action_result",
     "seg_ids": ["seg_0084", "seg_0085"], "action_seg_id": "seg_0084",
     "why": "the completed export dialog proves the narrated action"}
  ]}]
```

## Rules

- **Enforced:** `chapter_id` (unique), `start`, `end` (numbers, `end > start`, chronological, non-overlapping, last `end` ≤ video duration + 1 s), and `needs_frames` as a real JSON boolean on every chapter (`null`, `"false"` and `0` are rejected).
- **Enforced:** every `seg_ids` entry, `action_seg_id` and legacy `seg_id` must be a segment id from this transcript. An unknown id is an error, never a silent fallback to the whole chapter.
- **Enforced:** `kind` ∈ `state`, `action_result`, `diagram`, `slide`; `target_id` unique; `seg_ids` is an array (a bare string is rejected); a target without `seg_ids` needs `anchor_t` or `window` (edge case only).
- **Enforced under an illustrated request:** at least one chapter has `needs_frames: true`. If the video genuinely has no informative visual content (a static talking head, audio with a still image), do not fake a target: record the decision with `workflow.py decide no-visuals --work "<work>" --reason "<why>"` and the run continues in text-only mode with that reason printed in the delivery report.
- 5–12 chapters for a typical talk; each a coherent topic, not a fixed length (a warning outside 3–20).
- **`needs_frames: false` for pure-talk chapters** (intro banter, Q&A logistics, outro). Nothing is extracted there — the cheapest frame is the one you never pay for.
- A target is a moment the transcript says something is on screen: "as you can see", "look at this", "now I click", "notice the graph", a demo action being narrated. Skip rhetorical "look, the point is…". Be restrained — **≤2 targets per chapter** typically (a warning above 3). Kinds: `action_result` (search *after* the action segment — the result appears after the words), `state`, `diagram`, `slide` (search inside the referenced segments).
- **The five content gaps** — a stretch of transcript that is incomplete *without* the picture is where a target belongs, whether or not the speaker says "look":
  1. **Dangling reference** — "this one here", "that number", "the second option": the words point at something unnamed.
  2. **Conclusion without its data** — "so it's clearly faster", "the numbers speak for themselves": the claim is on screen, the transcript only asserts it.
  3. **Unspoken operation** — the speaker narrates an action ("I'll just set that up") but not what changed; the result is an `action_result` target.
  4. **Silent demo** — a stretch with sparse or filler speech inside a `needs_frames` chapter; check the coverage table for it and add a `state` target.
  5. **Visual comparison** — "before and after", "these two side by side", "notice the difference".
- `slide` / `diagram` targets land on the **measured** terminal build state: the engine scores scene changes across the target window and samples the last stable frame before the screen flips (boards are drawn while they are discussed). You still reference the segments where it is discussed.
- **Reference segments, never seconds.** `anchor_t` / `window` exist for a known edge case only. The engine derives every timestamp from `seg_ids`; you do not type `--cues` or `--pins` any more.
- The transcript report lists the creator's own chapters when YouTube has them — a prior, not a substitute.

## After extraction

`candidates.py` prints a **per-chapter coverage table**. A `needs_frames` chapter with only 1 candidate is a static stretch: if its point is visual, add a target inside its window and run again. `unresolved` (a `needs_frames` chapter or a target with no candidate) stops the run with **exit 9**: fix the chapter (a target inside the window, or `needs_frames: false` with a reason) or re-run with `--tier high`. Do not render around it. Changing `chapters.json` invalidates the candidate pool; the controller re-runs extraction, and a re-run is a new image spend — change it once, deliberately.
