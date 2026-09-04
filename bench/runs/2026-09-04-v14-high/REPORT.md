# Benchmark report — `2026-09-04-v14-high`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 76 | 91% | 20 | 86% | 100% | 5% | 100% | 83% | 64% | 73% | 53% | 1.64 | 870 |

## ISb0nrlNoKQ — missed essential visuals
- `s02` Board: 'Agents we tested' (OpenClaw, Manus, Claude Code, Perplexity) + 'The most useful agent' → **cap_dropped**
- `s06` Board: 'Enter Task → Computer' ×4 + 'Command Center for Agents that have access to a computer' → **dedup_dropped**
- `s28` Markdown skill document open in the browser → **triage_rejected**

## ISb0nrlNoKQ — summary (he, 1108 words)
- coverage: 100%; hebrew ratio: 85%; niqqud: 0; bidi controls: 0; blocks opening with Latin: 0; dashes: 0

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
