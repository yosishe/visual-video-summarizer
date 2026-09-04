# Benchmark report — `2026-09-04-v15-high`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 64 | 100% | 20 | 91% | 100% | 0% | 100% | 88% | 64% | 73% | 52% | 1.75 | 732 |

## ISb0nrlNoKQ — missed essential visuals
- `s03` Perplexity Computer UI (Search→Computer mode, task running) → **triage_rejected**
- `s28` Markdown skill document open in the browser → **triage_rejected**

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
