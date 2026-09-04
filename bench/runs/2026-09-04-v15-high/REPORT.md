# Benchmark report — `2026-09-04-v15-high`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7L9VP1E5CU4 | 19 | 64 | 68% | — | — | — | — | — | — | — | — | — | — | 654 |
| BUTjcAjfMgY | 27 | 73 | 78% | — | — | — | — | — | — | — | — | — | — | 438 |
| ISb0nrlNoKQ | 22 | 64 | 100% | 20 | 91% | 100% | 0% | 100% | 88% | 64% | 73% | 52% | 1.75 | 732 |
| aircAruvnKk | 24 | 64 | 96% | — | — | — | — | — | — | — | — | — | — | 717 |
| f8_uF_IDV50 | 22 | 64 | 68% | — | — | — | — | — | — | — | — | — | — | 865 |
| qrvK_KuIeJk | 4 | 52 | 75% | — | — | — | — | — | — | — | — | — | — | 823 |

## 7L9VP1E5CU4 — essential visuals absent from the pool
`s07`, `s10`, `s18`, `s32`, `s35`, `s36`

## BUTjcAjfMgY — essential visuals absent from the pool
`s25`, `s31`, `s33`, `s36`, `s38`, `s47`

## ISb0nrlNoKQ — missed essential visuals
- `s03` Perplexity Computer UI (Search→Computer mode, task running) → **triage_rejected**
- `s28` Markdown skill document open in the browser → **triage_rejected**

## aircAruvnKk — essential visuals absent from the pool
`s03`

## f8_uF_IDV50 — essential visuals absent from the pool
`s02`, `s03`, `s05`, `s08`, `s09`, `s17`, `s32`

## qrvK_KuIeJk — essential visuals absent from the pool
`s05`

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
