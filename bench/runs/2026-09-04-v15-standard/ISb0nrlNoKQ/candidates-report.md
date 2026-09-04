
# candidate frames report

- **Tier:** standard (alias: --mode light) — states engine (100 states from a 2.0 fps scan)
- **Visual states:** 100 (A talk 0, B static 14, C canvas 86, D dynamic UI 0); 10 families, 1 builds; mode timeline per 20 s: `CCCCCCCCCCCCCCCCCCCCCCCBBBBBCCCCCCCCBBBCCCCCCCCCCCBBB`; scan 13.0s — `states.json` in the work dir
- **Candidates:** 48 (pool 48; raw 102; dedup 16 [family scope]; cap 38)
- **Overlay mask:** webcam at x=0.00 y=0.69 w=0.15 h=0.31 (moves in 32% of pairs) — 5.2% of every signature ignored for dedup and the re-grab gate; written frames are untouched (0.0s)
- **Image tokens (estimate):** ≈10,032 for one batched Read (48×512x288; 209 each, ⌈w/28⌉×⌈h/28⌉; other providers differ)
- **CPU:** 1 fixed scene pass over 17:33 of chapter windows · 0 terminal probes · 102 seeks + signatures · OCR: off · faces: off · grab refinement: off
- **Other tier:** `--tier high` pool 64 candidates (≈13,376 image tokens before the reserved-frame lift; it reserves 3 frames per target)
- **Manifest:** `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates.json`

## Per-chapter coverage

| chapter | window | candidates | status |
|---|---|---|---|
| ch01 | 00:00-00:43 | 0 (0 targets) | not-required |
| ch02 | 00:43-02:34 | 6 (3 targets) | covered |
| ch03 | 02:34-03:47 | 2 (2 targets) | covered |
| ch04 | 03:47-05:07 | 3 (2 targets) | covered |
| ch05 | 05:07-06:09 | 5 (2 targets) | covered |
| ch06 | 06:09-07:32 | 4 (1 target) | covered |
| ch07 | 07:32-08:24 | 3 (2 targets) | covered |
| ch08 | 08:24-11:04 | 7 (3 targets) | covered |
| ch09 | 11:04-12:26 | 4 (1 target) | covered |
| ch10 | 12:26-14:08 | 5 (2 targets) | covered |
| ch11 | 14:08-16:44 | 6 (2 targets) | covered |
| ch12 | 16:44-18:16 | 3 (2 targets) | covered |

A needs_frames chapter with only 1 candidate is a static stretch — if its point is visual, add a target inside its window before triage.

**Read ALL candidate paths below in a single message (parallel Read calls), then triage per the skill rubric. Select by `candidate_id` — never copy times.**

- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0000_t0062.529_target.jpg` (c_0000, actual_t=62.529 [01:03], chapter=ch02, targets=ch02_perplexity_switch, state=s_0002/C, family=f_001)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0001_t0092.025_target.jpg` (c_0001, actual_t=92.025 [01:32], chapter=ch02, targets=ch02_perplexity_switch,ch02_side_panel, state=s_0004/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0002_t0107.507_target.jpg` (c_0002, actual_t=107.507 [01:48], chapter=ch02, targets=ch02_manus_computer, state=s_0005/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0003_t0113.513_target.jpg` (c_0003, actual_t=113.513 [01:54], chapter=ch02, targets=ch02_manus_computer, state=s_0007/C (first stage), family=f_002)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0004_t0132.532_state.jpg` (c_0004, actual_t=132.532 [02:13], chapter=ch02, targets=-, state=s_0008/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0005_t0141.508_state.jpg` (c_0005, actual_t=141.508 [02:22], chapter=ch02, targets=-, state=s_0009/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0006_t0197.030_target.jpg` (c_0006, actual_t=197.030 [03:17], chapter=ch03, targets=ch03_mac_minis,ch03_openclaw_shape, state=s_0011/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0007_t0226.526_state.jpg` (c_0007, actual_t=226.526 [03:47], chapter=ch03, targets=-, state=s_0013/C, family=f_003)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0008_t0260.026_target.jpg` (c_0008, actual_t=260.026 [04:20], chapter=ch04, targets=ch04_skills_overview, state=s_0018/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0009_t0262.529_target.jpg` (c_0009, actual_t=262.529 [04:23], chapter=ch04, targets=ch04_skills_overview, state=s_0019/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0010_t0302.002_target.jpg` (c_0010, actual_t=302.002 [05:02], chapter=ch04, targets=ch04_dependability_chart, state=s_0025/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0011_t0326.526_target.jpg` (c_0011, actual_t=326.526 [05:27], chapter=ch05, targets=ch05_sweet_spot, state=s_0027/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0012_t0337.003_target.jpg` (c_0012, actual_t=337.003 [05:37], chapter=ch05, targets=ch05_sweet_spot, state=s_0028/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0013_t0350.517_state.jpg` (c_0013, actual_t=350.517 [05:51], chapter=ch05, targets=-, state=s_0031/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0014_t0352.519_state.jpg` (c_0014, actual_t=352.519 [05:53], chapter=ch05, targets=-, state=s_0032/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0015_t0361.027_target.jpg` (c_0015, actual_t=361.027 [06:01], chapter=ch05, targets=ch05_manus_skills, state=s_0033/C, family=f_002 (same picture also at 07:18, 16:47, 16:50, 17:01))
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0016_t0393.026_target.jpg` (c_0016, actual_t=393.026 [06:33], chapter=ch06, targets=ch06_shear_tweet, state=s_0035/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0017_t0419.019_target.jpg` (c_0017, actual_t=419.019 [06:59], chapter=ch06, targets=ch06_shear_tweet, state=s_0036/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0018_t0425.525_state.jpg` (c_0018, actual_t=425.525 [07:06], chapter=ch06, targets=-, state=s_0037/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0019_t0431.031_state.jpg` (c_0019, actual_t=431.031 [07:11], chapter=ch06, targets=-, state=s_0038/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0020_t0458.024_target.jpg` (c_0020, actual_t=458.024 [07:38], chapter=ch07, targets=ch07_journal_bot_telegram, state=s_0043/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0021_t0467.033_target.jpg` (c_0021, actual_t=467.033 [07:47], chapter=ch07, targets=ch07_focused_agent_shape,ch07_journal_bot_telegram, state=s_0044/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0022_t0486.519_target.jpg` (c_0022, actual_t=486.519 [08:07], chapter=ch07, targets=ch07_focused_agent_shape, state=s_0045/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0023_t0513.013_target.jpg` (c_0023, actual_t=513.013 [08:33], chapter=ch08, targets=ch08_content_bot, state=s_0049/B)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0024_t0520.520_target.jpg` (c_0024, actual_t=520.520 [08:41], chapter=ch08, targets=ch08_content_bot,ch08_goals_skills, state=s_0050/B)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0025_t0536.503_target.jpg` (c_0025, actual_t=536.503 [08:57], chapter=ch08, targets=ch08_goals_skills, state=s_0053/B)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0026_t0620.520_state.jpg` (c_0026, actual_t=620.520 [10:21], chapter=ch08, targets=-, state=s_0057/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0027_t0627.527_state.jpg` (c_0027, actual_t=627.527 [10:28], chapter=ch08, targets=-, state=s_0058/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0028_t0643.510_target.jpg` (c_0028, actual_t=643.510 [10:44], chapter=ch08, targets=ch08_direct_path, state=s_0059/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0029_t0649.516_target.jpg` (c_0029, actual_t=649.516 [10:50], chapter=ch08, targets=ch08_direct_path, state=s_0060/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0030_t0689.522_state.jpg` (c_0030, actual_t=689.522 [11:30], chapter=ch09, targets=-, state=s_0064/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0031_t0701.000_state.jpg` (c_0031, actual_t=701.000 [11:41], chapter=ch09, targets=-, state=s_0065/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0032_t0709.008_target.jpg` (c_0032, actual_t=709.008 [11:49], chapter=ch09, targets=ch09_remix, state=s_0066/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0033_t0720.520_target.jpg` (c_0033, actual_t=720.520 [12:01], chapter=ch09, targets=ch09_remix, state=s_0067/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0034_t0755.021_target.jpg` (c_0034, actual_t=755.021 [12:35], chapter=ch10, targets=ch10_journal_agent, state=s_0069/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0035_t0762.528_target.jpg` (c_0035, actual_t=762.528 [12:43], chapter=ch10, targets=ch10_journal_agent,ch10_journal_informs, state=s_0070/C, family=f_008)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0036_t0799.532_target.jpg` (c_0036, actual_t=799.532 [13:20], chapter=ch10, targets=ch10_journal_informs, state=s_0072/B)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0037_t0802.502_target.jpg` (c_0037, actual_t=802.502 [13:23], chapter=ch10, targets=ch10_journal_informs, state=s_0073/B)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0038_t0845.011_state.jpg` (c_0038, actual_t=845.011 [14:05], chapter=ch10, targets=-, state=s_0075/C, family=f_009 (same picture also at 14:09))
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0039_t0879.011_target.jpg` (c_0039, actual_t=879.011 [14:39], chapter=ch11, targets=ch11_markdown_skills, state=s_0080/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0040_t0888.521_target.jpg` (c_0040, actual_t=888.521 [14:49], chapter=ch11, targets=ch11_markdown_skills, state=s_0081/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0041_t0953.519_target.jpg` (c_0041, actual_t=953.519 [15:54], chapter=ch11, targets=ch11_loops, state=s_0086/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0042_t0957.523_target.jpg` (c_0042, actual_t=957.523 [15:58], chapter=ch11, targets=ch11_loops, state=s_0087/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0043_t0984.517_state.jpg` (c_0043, actual_t=984.517 [16:25], chapter=ch11, targets=-, state=s_0090/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0044_t0999.532_state.jpg` (c_0044, actual_t=999.532 [16:40], chapter=ch11, targets=-, state=s_0092/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0045_t1005.505_target.jpg` (c_0045, actual_t=1005.505 [16:46], chapter=ch12, targets=ch12_team, state=s_0093/C, family=f_004 (same picture also at 07:22))
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0046_t1041.007_target.jpg` (c_0046, actual_t=1041.007 [17:21], chapter=ch12, targets=ch12_open_questions, state=s_0098/C)
- `/Users/26yos/.claude/skills/summarize-video/bench/runs/2026-09-04-v15-standard/ISb0nrlNoKQ/work/candidates/c_0047_t1095.027_target.jpg` (c_0047, actual_t=1095.027 [18:15], chapter=ch12, targets=ch12_open_questions, state=s_0099/B, family=f_010)
