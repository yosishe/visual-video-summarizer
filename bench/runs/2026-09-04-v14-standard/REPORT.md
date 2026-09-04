# Benchmark report — `2026-09-04-v14-standard`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 50 | 86% | 20 | 86% | 100% | 10% | 95% | 88% | 64% | 82% | 53% | 1.64 | 572 |

## ISb0nrlNoKQ — missed essential visuals
- `s06` Board: 'Enter Task → Computer' ×4 + 'Command Center for Agents that have access to a computer' → **not_in_pool**
- `s13` Board: Employee — 'That gets things done / Do things that surprise you / Make suggestions that are useful' + 'Specific Goals, Intent' → **not_in_pool**
- `s28` Markdown skill document open in the browser → **not_in_pool**

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
