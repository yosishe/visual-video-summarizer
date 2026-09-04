# Benchmark report — `2026-09-04-v130-high`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 76 | 91% | 20 | 86% | 100% | 5% | 100% | 89% | 64% | 77% | 53% | 1.64 | 870 |

## ISb0nrlNoKQ — missed essential visuals
- `s02` Board: 'Agents we tested' (OpenClaw, Manus, Claude Code, Perplexity) + 'The most useful agent' → **not_in_pool**
- `s06` Board: 'Enter Task → Computer' ×4 + 'Command Center for Agents that have access to a computer' → **not_in_pool**
- `s13` Board: Employee — 'That gets things done / Do things that surprise you / Make suggestions that are useful' + 'Specific Goals, Intent' → **triage_rejected**

## ISb0nrlNoKQ — summary (en, 2010 words)
- coverage: 60%; hebrew ratio: 0%; niqqud: 0; bidi controls: 0; blocks opening with Latin: 31; dashes: 0

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
