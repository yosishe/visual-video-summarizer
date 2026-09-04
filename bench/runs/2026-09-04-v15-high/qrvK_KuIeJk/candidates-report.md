
# candidate frames report

- **Tier:** high (alias: --mode advanced) — states engine (66 states from a 2.0 fps scan)
- **Visual states:** 66 (A talk 6, B static 12, C canvas 0, D dynamic UI 48); 3 families, 1 builds; mode timeline per 20 s: `DDDBAAAADDDDDDDDDDD`; scan 9.6s — `states.json` in the work dir
- **Candidates:** 52 (pool 64; raw 64; dedup 4 [family scope]; cap 8)
- **Overlay mask:** none detected — no persistent picture-in-picture or bar (0.0s)
- **Image tokens (estimate):** ≈10,868 for one batched Read (52×512x288; 209 each, ⌈w/28⌉×⌈h/28⌉; other providers differ)
- **CPU:** 1 adaptive scene pass over 05:46 of chapter windows · 0 terminal probes · 64 seeks + signatures · OCR: on · faces: unavailable · grab refinement: sharpness (≤20 × ~3 s decodes)
- **Other tier:** `--tier standard` pool 48 candidates (≈10,032 image tokens before the reserved-frame lift; it reserves 2 frames per target)
- **Manifest:** `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates.json`

## Per-chapter coverage

| chapter | window | candidates | status |
|---|---|---|---|
| ch01 | 00:00-00:47 | 0 (0 targets) | not-required |
| ch02 | 00:47-01:42 | 0 (0 targets) | not-required |
| ch03 | 01:42-03:00 | 10 (1 target) | covered |
| ch04 | 03:00-04:17 | 5 (2 targets) | covered |
| ch05 | 04:17-06:11 | 0 (0 targets) | not-required |
| ch06 | 06:11-07:18 | 0 (0 targets) | not-required |
| ch07 | 07:18-08:17 | 12 (1 target) | covered |
| ch08 | 08:17-10:29 | 25 (2 targets) | covered |
| ch09 | 10:29-12:04 | 0 (0 targets) | not-required |
| ch10 | 12:04-13:12 | 0 (0 targets) | not-required |

A needs_frames chapter with only 1 candidate is a static stretch — if its point is visual, add a target inside its window before triage.

**Two-stage triage.** Stage 1 — Read ALL 4 contact sheets in one message (≈4,692 image tokens for the whole pool; reading every candidate individually would cost 10,868): for every tile decide keep/drop by its burned-in id, group the same picture into one family, and report each sheet's sentinel tile as blank (if you cannot find it, fall back to reading the candidates below individually). Stage 2 — `python3 "$SKILL_DIR/scripts/shortlist.py" --work "<work>" --ids <kept ids>` re-decodes the kept frames at 768px (verified against the candidates); Read those, then write selections.json by `candidate_id` — never copy times.

- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/sheets/sheet_00.jpg` → c_0000, c_0001, c_0002, c_0003, c_0004, c_0005, c_0006, c_0007, c_0008, c_0009, c_0010, c_0011, c_0012, c_0013, c_0014; sentinel `x_0029`
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/sheets/sheet_01.jpg` → c_0015, c_0016, c_0017, c_0018, c_0019, c_0020, c_0021, c_0022, c_0023, c_0024, c_0025, c_0026, c_0027, c_0028, c_0029; sentinel `x_0193`
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/sheets/sheet_02.jpg` → c_0030, c_0031, c_0032, c_0033, c_0034, c_0035, c_0036, c_0037, c_0038, c_0039, c_0040, c_0041, c_0042, c_0043, c_0044; sentinel `x_0219`
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/sheets/sheet_03.jpg` → c_0045, c_0046, c_0047, c_0048, c_0049, c_0050, c_0051; sentinel `x_0378`

Candidates (for stage 2 and for the `spoken`/`text` provenance of captions):
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0000_t0107.507_state.jpg` (c_0000, actual_t=107.507 [01:48], chapter=ch03, targets=-, state=s_0000/D)
  spoken: "second most intelligent beings on the planet yeah Jeffrey Hinton told us the artificial intelligence he set in motion was an accident born of a failure in the"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0001_t0119.519_state.jpg` (c_0001, actual_t=119.519 [02:00], chapter=ch03, targets=-, state=s_0001/D)
  spoken: "artificial intelligence he set in motion was an accident born of a failure in the 1970s at the University of Edinburgh he dreamed of simulating a neural network…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0002_t0136.003_state.jpg` (c_0002, actual_t=136.003 [02:16], chapter=ch03, targets=-, state=s_0002/D)
  spoken: "on a computer simply as a tool for what he was really studying the human brain but back then almost no one thought software could mimic the brain his PhD adviso…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0003_t0147.514_state.jpg` (c_0003, actual_t=147.514 [02:28], chapter=ch03, targets=-, state=s_0003/D)
  spoken: "brain his PhD advisor told him to drop it before it ruined his career Hinton says he failed to figure out the human mind but the long Pursuit led to an artifici…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0004_t0152.519_state.jpg` (c_0004, actual_t=152.519 [02:33], chapter=ch03, targets=-, state=s_0004/D)
  spoken: "expected it took like 50 years before it worked well but in the end it did work well at what point did you you realize that you were right about neural"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0005_t0164.531_target.jpg` (c_0005, actual_t=164.531 [02:45], chapter=ch03, targets=ch03_turing_award, state=s_0008/B)
  spoken: "right in 2019 Hinton and collaborators Yan laon on the left and yosua Beno won"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0006_t0166.033_target.jpg` (c_0006, actual_t=166.033 [02:46], chapter=ch03, targets=ch03_turing_award, state=s_0009/B)
  spoken: "right in 2019 Hinton and collaborators Yan laon on the left and yosua Beno won"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0007_t0167.501_target.jpg` (c_0007, actual_t=167.501 [02:48], chapter=ch03, targets=ch03_turing_award, state=s_0010/B, family=f_001)
  spoken: "right in 2019 Hinton and collaborators Yan laon on the left and yosua Beno won the touring award the Nobel Prize of computing to understand how their work"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0008_t0175.509_state.jpg` (c_0008, actual_t=175.509 [02:56], chapter=ch03, targets=-, state=s_0014/B)
  spoken: "the touring award the Nobel Prize of computing to understand how their work on artificial neural networks helped machines learn to learn let us take you"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0009_t0177.010_state.jpg` (c_0009, actual_t=177.010 [02:57], chapter=ch03, targets=-, state=s_0015/B, family=f_002)
  spoken: "the touring award the Nobel Prize of computing to understand how their work on artificial neural networks helped machines learn to learn let us take you"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0010_t0180.013_target.jpg` (c_0010, actual_t=180.013 [03:00], chapter=ch04, targets=ch04_robots, state=s_0018/B, family=f_003 (same picture also at 03:00))
  spoken: "on artificial neural networks helped machines learn to learn let us take you to a a game look at that oh my goodness this is Google's AI lab in London which we …"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0011_t0212.512_target.jpg` (c_0011, actual_t=212.512 [03:33], chapter=ch04, targets=ch04_robots, state=s_0019/A)
  spoken: "to a a game look at that oh my goodness this is Google's AI lab in London which we first showed you this past April Jeffrey Hinton wasn't involved in this socce…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0012_t0223.023_target.jpg` (c_0012, actual_t=223.023 [03:43], chapter=ch04, targets=ch04_layers, state=s_0021/A)
  spoken: "own oh go in general here's how AI does it Henton and his collaborators created software in layers with each layer handling part of the problem that's the so-ca…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0013_t0234.000_target.jpg` (c_0013, actual_t=234.000 [03:54], chapter=ch04, targets=ch04_layers, state=s_0022/A)
  spoken: "Henton and his collaborators created software in layers with each layer handling part of the problem that's the so-called neural network but this is the key whe…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0014_t0252.018_target.jpg` (c_0014, actual_t=252.018 [04:12], chapter=ch04, targets=ch04_layers, state=s_0023/A)
  spoken: "key when for example the robot scores a message is sent back down through all of the layers that says that pathway was right likewise when an answer is wrong th…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0015_t0445.011_state.jpg` (c_0015, actual_t=445.011 [07:25], chapter=ch07, targets=-, state=s_0025/D)
  spoken: "not insects and that's where he had all the things about the family today at 75 Hinton recently retired after what he calls 10 happy years at Google now he's pr…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0016_t0449.516_state.jpg` (c_0016, actual_t=449.516 [07:30], chapter=ch07, targets=-, state=s_0026/D)
  spoken: "family today at 75 Hinton recently retired after what he calls 10 happy years at Google now he's professor ameritus at the University of Toronto"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0017_t0452.018_state.jpg` (c_0017, actual_t=452.018 [07:32], chapter=ch07, targets=-, state=s_0027/D)
  spoken: "years at Google now he's professor ameritus at the University of Toronto and he happened to mention he has more academic citations than his father some"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0018_t0454.020_state.jpg` (c_0018, actual_t=454.020 [07:34], chapter=ch07, targets=-, state=s_0028/D)
  spoken: "years at Google now he's professor ameritus at the University of Toronto and he happened to mention he has more academic citations than his father some"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0019_t0458.024_state.jpg` (c_0019, actual_t=458.024 [07:38], chapter=ch07, targets=-, state=s_0029/D)
  spoken: "and he happened to mention he has more academic citations than his father some of his research led to chatbots like Google's Bard which we met last spring"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0020_t0460.026_state.jpg` (c_0020, actual_t=460.026 [07:40], chapter=ch07, targets=-, state=s_0030/D)
  spoken: "and he happened to mention he has more academic citations than his father some of his research led to chatbots like Google's Bard which we met last spring"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0021_t0461.528_target.jpg` (c_0021, actual_t=461.528 [07:42], chapter=ch07, targets=ch07_bard_story, state=s_0031/D)
  spoken: "and he happened to mention he has more academic citations than his father some of his research led to chatbots like Google's Bard which we met last spring confo…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0022_t0467.033_target.jpg` (c_0022, actual_t=467.033 [07:47], chapter=ch07, targets=ch07_bard_story, state=s_0032/D)
  spoken: "of his research led to chatbots like Google's Bard which we met last spring confounding absolutely confounding we asked Bard to write a story from six words for…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0023_t0483.016_target.jpg` (c_0023, actual_t=483.016 [08:03], chapter=ch07, targets=ch07_bard_story, state=s_0035/D)
  spoken: "my wife but we never had a baby Bard created a deeply human tale of a man whose wife could not conceive and a stranger who accepted the shoes to heal"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0024_t0491.024_state.jpg` (c_0024, actual_t=491.024 [08:11], chapter=ch07, targets=-, state=s_0037/D)
  spoken: "my wife but we never had a baby Bard created a deeply human tale of a man whose wife could not conceive and a stranger who accepted the shoes to heal the pain a…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0025_t0495.028_state.jpg` (c_0025, actual_t=495.028 [08:15], chapter=ch07, targets=-, state=s_0038/D)
  spoken: "whose wife could not conceive and a stranger who accepted the shoes to heal the pain after her miscarriage I am rarely rarely speechless I don't know what to ma…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0026_t0496.529_state.jpg` (c_0026, actual_t=496.529 [08:17], chapter=ch07, targets=-, state=s_0039/D)
  spoken: "the pain after her miscarriage I am rarely rarely speechless I don't know what to make of this chatbots are said to be language models that just predict the nex…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0027_t0503.002_state.jpg` (c_0027, actual_t=503.002 [08:23], chapter=ch08, targets=-, state=s_0040/D)
  spoken: "the pain after her miscarriage I am rarely rarely speechless I don't know what to make of this chatbots are said to be language models that just predict the nex…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0028_t0509.009_state.jpg` (c_0028, actual_t=509.009 [08:29], chapter=ch08, targets=-, state=s_0041/D)
  spoken: "this chatbots are said to be language models that just predict the next most likely word based on probability you'll hear people saying things like they're just…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0029_t0511.511_state.jpg` (c_0029, actual_t=511.511 [08:32], chapter=ch08, targets=-, state=s_0042/D)
  spoken: "likely word based on probability you'll hear people saying things like they're just doing autocomplete they're just trying to predict the next word and they're …"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0030_t0513.013_state.jpg` (c_0030, actual_t=513.013 [08:33], chapter=ch08, targets=-, state=s_0043/D)
  spoken: "just doing autocomplete they're just trying to predict the next word and they're just using statistics well it's true they're just trying to predict the next wo…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0031_t0531.531_state.jpg` (c_0031, actual_t=531.531 [08:52], chapter=ch08, targets=-, state=s_0044/D)
  spoken: "they're just using statistics well it's true they're just trying to predict the next word but if you think about it to predict the next word you have to underst…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0032_t0539.005_state.jpg` (c_0032, actual_t=539.005 [08:59], chapter=ch08, targets=-, state=s_0045/D)
  spoken: "predicting the next word so they're not intelligent is crazy you have to be really intelligent to predict the next word really accurately to prove it Hinton sho…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0033_t0542.008_state.jpg` (c_0033, actual_t=542.008 [09:02], chapter=ch08, targets=-, state=s_0046/D)
  spoken: "Hinton showed us a test he devised for chat chat gp4 the chatbot from a company called open AI it was sort of reassuring to see a turing Award winner mistype an…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0034_t0544.511_state.jpg` (c_0034, actual_t=544.511 [09:05], chapter=ch08, targets=-, state=s_0047/D)
  spoken: "chat gp4 the chatbot from a company called open AI it was sort of reassuring to see a turing Award winner mistype and blame"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0035_t0546.012_state.jpg` (c_0035, actual_t=546.012 [09:06], chapter=ch08, targets=-, state=s_0048/D)
  spoken: "open AI it was sort of reassuring to see a turing Award winner mistype and blame"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0036_t0548.014_state.jpg` (c_0036, actual_t=548.014 [09:08], chapter=ch08, targets=-, state=s_0049/D)
  spoken: "open AI it was sort of reassuring to see a turing Award winner mistype and blame the computer oh damn this thing we're going to go back and start again that's o…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0037_t0556.022_state.jpg` (c_0037, actual_t=556.022 [09:16], chapter=ch08, targets=-, state=s_0050/D)
  spoken: "the computer oh damn this thing we're going to go back and start again that's okay hinton's test was a riddle about house painting an answer would demand"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0038_t0557.023_state.jpg` (c_0038, actual_t=557.023 [09:17], chapter=ch08, targets=-, state=s_0051/D)
  spoken: "okay hinton's test was a riddle about house painting an answer would demand"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0039_t0560.527_target.jpg` (c_0039, actual_t=560.527 [09:21], chapter=ch08, targets=ch08_prompt, state=s_0052/D)
  spoken: "okay hinton's test was a riddle about house painting an answer would demand reasoning and planning this is what he typed into chat"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0040_t0564.030_target.jpg` (c_0040, actual_t=564.030 [09:24], chapter=ch08, targets=ch08_prompt, state=s_0053/D)
  spoken: "okay hinton's test was a riddle about house painting an answer would demand reasoning and planning this is what he typed into chat gp4 the rooms in my house are…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0041_t0573.006_target.jpg` (c_0041, actual_t=573.006 [09:33], chapter=ch08, targets=ch08_prompt, state=s_0054/D, family=f_004)
  spoken: "reasoning and planning this is what he typed into chat gp4 the rooms in my house are painted white or blue or yellow and yellow paint Fades to White within a ye…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0042_t0581.014_state.jpg` (c_0042, actual_t=581.014 [09:41], chapter=ch08, targets=-, state=s_0056/D)
  spoken: "Fades to White within a year in 2 years time I'd like all the rooms to be white what should I do the answer began in one second gp4 advised the rooms painted in"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0043_t0585.018_target.jpg` (c_0043, actual_t=585.018 [09:45], chapter=ch08, targets=ch08_answer, state=s_0057/D)
  spoken: "what should I do the answer began in one second gp4 advised the rooms painted in blue need to be repainted the rooms painted in yellow don't need to be repainte…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0044_t0592.025_state.jpg` (c_0044, actual_t=592.025 [09:52], chapter=ch08, targets=-, state=s_0058/D)
  spoken: "blue need to be repainted the rooms painted in yellow don't need to be repainted because they would Fade to White before the deadline and oh I"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0045_t0597.030_state.jpg` (c_0045, actual_t=597.030 [09:57], chapter=ch08, targets=-, state=s_0059/D)
  spoken: "repainted because they would Fade to White before the deadline and oh I didn't even think of that it warned if you paint the yellow rooms white there's"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0046_t0601.000_state.jpg` (c_0046, actual_t=601.000 [10:01], chapter=ch08, targets=-, state=s_0060/D)
  spoken: "repainted because they would Fade to White before the deadline and oh I didn't even think of that it warned if you paint the yellow rooms white there's a risk t…"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0047_t0606.005_state.jpg` (c_0047, actual_t=606.005 [10:06], chapter=ch08, targets=-, state=s_0061/D)
  spoken: "didn't even think of that it warned if you paint the yellow rooms white there's a risk the color might be off when the yellow Fades besides it advised you'd be"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0048_t0611.511_state.jpg` (c_0048, actual_t=611.511 [10:12], chapter=ch08, targets=-, state=s_0062/D)
  spoken: "a risk the color might be off when the yellow Fades besides it advised you'd be wasting resources painting rooms that were going to Fade to White anyway you"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0049_t0613.012_state.jpg` (c_0049, actual_t=613.012 [10:13], chapter=ch08, targets=-, state=s_0063/D)
  spoken: "wasting resources painting rooms that were going to Fade to White anyway you believe that chat GPD 4 understands I believe it definitely"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0050_t0626.025_state.jpg` (c_0050, actual_t=626.025 [10:26], chapter=ch08, targets=-, state=s_0064/D)
  spoken: "wasting resources painting rooms that were going to Fade to White anyway you believe that chat GPD 4 understands I believe it definitely understands yes and in …"
- `<skill>/bench/runs/2026-09-04-v15-high/qrvK_KuIeJk/work/candidates/c_0051_t0628.528_state.jpg` (c_0051, actual_t=628.528 [10:29], chapter=ch08, targets=-, state=s_0065/D)
  spoken: "understands yes and in 5 years time I think in 5 years time it may well be able to reason better than us reasoning that he says is leading to ai's great"
