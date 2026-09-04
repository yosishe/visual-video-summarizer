# Benchmark report — `2026-09-04-v15-high-p128`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7L9VP1E5CU4 | 19 | 128 | 89% | — | — | — | — | — | — | — | — | — | — | 1308 |
| BUTjcAjfMgY | 27 | 80 | 81% | — | — | — | — | — | — | — | — | — | — | 480 |
| aircAruvnKk | 24 | 108 | 100% | — | — | — | — | — | — | — | — | — | — | 1209 |
| f8_uF_IDV50 | 22 | 110 | 73% | — | — | — | — | — | — | — | — | — | — | 1486 |

## 7L9VP1E5CU4 — essential visuals absent from the pool
`s18`, `s32`

## BUTjcAjfMgY — essential visuals absent from the pool
`s25`, `s33`, `s36`, `s38`, `s47`

## f8_uF_IDV50 — essential visuals absent from the pool
`s02`, `s03`, `s08`, `s09`, `s17`, `s32`

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
