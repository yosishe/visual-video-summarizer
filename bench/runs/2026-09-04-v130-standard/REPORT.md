# Benchmark report — `2026-09-04-v130-standard`

| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ISb0nrlNoKQ | 22 | 50 | 86% | 14 | 59% | 100% | 7% | 100% | 79% | 64% | 59% | 45% | 1.33 | 572 |

## ISb0nrlNoKQ — missed essential visuals
- `s02` Board: 'Agents we tested' (OpenClaw, Manus, Claude Code, Perplexity) + 'The most useful agent' → **triage_rejected**
- `s06` Board: 'Enter Task → Computer' ×4 + 'Command Center for Agents that have access to a computer' → **not_in_pool**
- `s08` Board: OpenClaw shape — Computer with memory/skills/gateway + telegram/whatsapp/discord/slack + Skills/Integrations/Personality/Memory vs Tasks/Heartbeat/Webhook → **triage_rejected**
- `s09` Board: skills overview — many colored skill cards (supadata, etc.) → **triage_rejected**
- `s13` Board: Employee — 'That gets things done / Do things that surprise you / Make suggestions that are useful' + 'Specific Goals, Intent' → **not_in_pool**
- `s19` Board: YouTube AI Agent → Subs/Views/Conversions (goals) → **triage_rejected**
- `s26` Board: Focused Agent → Open rate/Subscribe/CTR→Revenue (email agent example) + 'Narrow Focus…Reviewable' list → **triage_rejected**
- `s27` Board: final list 'Narrow Focus / Duplicable / Sharable / Understandable / Reviewable / Faster loops / More autonomous' with the team chart → **triage_rejected**
- `s28` Markdown skill document open in the browser → **not_in_pool**

## ISb0nrlNoKQ — summary (en, 2010 words)
- coverage: 60%; hebrew ratio: 0%; niqqud: 0; bidi controls: 0; blocks opening with Latin: 31; dashes: 0

IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.
