# Benchmark report — `2026-09-04-v15-standard`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 48 | 91% | 20 | 86% | 100% | 5% | 100% | 94% | 64% | 77% | 52% | 1.67 | 549 |

## ISb0nrlNoKQ — missed essential visuals
- `s02` Board: 'Agents we tested' (OpenClaw, Manus, Claude Code, Perplexity) + 'The most useful agent' → **cap_dropped**
- `s26` Board: Focused Agent → Open rate/Subscribe/CTR→Revenue (email agent example) + 'Narrow Focus…Reviewable' list → **cap_dropped**
- `s28` Markdown skill document open in the browser → **triage_rejected**

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
