
# candidate frames report

- **Tier:** high (alias: --mode advanced) — states engine (132 states from a 2.0 fps scan)
- **Visual states:** 132 (A talk 0, B static 43, C canvas 0, D dynamic UI 89); 29 families, 9 builds; mode timeline per 20 s: `DDDDDDDDBBBBBDDDDDDDDDDDDDDDDBBBBBBBBBDDDDDDDDDDDDBBD`; scan 15.0s — `states.json` in the work dir
- **Candidates:** 108 (pool 128; raw 137; dedup 27 [family scope]; cap 2)
- **Overlay mask:** none detected — no persistent picture-in-picture or bar (0.0s)
- **Image tokens (estimate):** ≈22,572 for one batched Read (108×512x288; 209 each, ⌈w/28⌉×⌈h/28⌉; other providers differ)
- **Token budget:** 20,000 — planned ≈19,562 (sheets 9,706 + shortlist ≤22 × 448 at 768px); `shortlist.py` refuses more than 22 ids
- **CPU:** 1 adaptive scene pass over 17:09 of chapter windows · 0 terminal probes · 137 seeks + signatures · OCR: on (33 frames) · faces: unavailable · grab refinement: sharpness (≤20 × ~3 s decodes)
- **Other tier:** `--tier standard` pool 48 candidates (≈10,032 image tokens before the reserved-frame lift; it reserves 2 frames per target)
- **Manifest:** `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates.json`

## Per-chapter coverage

| chapter | window | candidates | status |
|---|---|---|---|
| ch01 | 00:00-01:07 | 7 (2 targets) | covered |
| ch02 | 01:07-02:43 | 14 (1 target) | covered |
| ch03 | 02:43-03:35 | 6 (2 targets) | covered |
| ch04 | 03:35-05:31 | 12 (3 targets) | covered |
| ch05 | 05:31-08:38 | 16 (4 targets) | covered |
| ch06 | 08:38-11:36 | 14 (4 targets) | covered |
| ch07 | 11:36-12:30 | 8 (2 targets) | covered |
| ch08 | 12:30-13:26 | 0 (0 targets) | not-required |
| ch09 | 13:26-15:16 | 9 (3 targets) | covered |
| ch10 | 15:16-16:28 | 13 (1 target) | covered |
| ch11 | 16:28-17:03 | 0 (0 targets) | not-required |
| ch12 | 17:03-18:40 | 9 (1 target) | covered |

A needs_frames chapter with only 1 candidate is a static stretch — if its point is visual, add a target inside its window before triage.

**Two-stage triage.** Stage 1 — Read ALL 8 contact sheets in one message (≈9,706 image tokens for the whole pool; reading every candidate individually would cost 22,572): for every tile decide keep/drop by its burned-in id, group the same picture into one family, and report each sheet's sentinel tile as blank (if you cannot find it, fall back to reading the candidates below individually). Stage 2 — `python3 "$SKILL_DIR/scripts/shortlist.py" --work "<work>" --ids <kept ids>` re-decodes the kept frames at 768px (verified against the candidates); Read those, then write selections.json by `candidate_id` — never copy times.

- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_00.jpg` → c_0000, c_0001, c_0002, c_0003, c_0004, c_0005, c_0006, c_0007, c_0008, c_0009, c_0010, c_0011, c_0012, c_0013, c_0014; sentinel `x_0029`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_01.jpg` → c_0015, c_0016, c_0017, c_0018, c_0019, c_0020, c_0021, c_0022, c_0023, c_0024, c_0025, c_0026, c_0027, c_0028, c_0029; sentinel `x_0193`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_02.jpg` → c_0030, c_0031, c_0032, c_0033, c_0034, c_0035, c_0036, c_0037, c_0038, c_0039, c_0040, c_0041, c_0042, c_0043, c_0044; sentinel `x_0219`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_03.jpg` → c_0045, c_0046, c_0047, c_0048, c_0049, c_0050, c_0051, c_0052, c_0053, c_0054, c_0055, c_0056, c_0057, c_0058, c_0059; sentinel `x_0378`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_04.jpg` → c_0060, c_0061, c_0062, c_0063, c_0064, c_0065, c_0066, c_0067, c_0068, c_0069, c_0070, c_0071, c_0072, c_0073, c_0074; sentinel `x_0456`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_05.jpg` → c_0075, c_0076, c_0077, c_0078, c_0079, c_0080, c_0081, c_0082, c_0083, c_0084, c_0085, c_0086, c_0087, c_0088, c_0089; sentinel `x_0517`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_06.jpg` → c_0090, c_0091, c_0092, c_0093, c_0094, c_0095, c_0096, c_0097, c_0098, c_0099, c_0100, c_0101, c_0102, c_0103, c_0104; sentinel `x_0674`
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/sheets/sheet_07.jpg` → c_0105, c_0106, c_0107; sentinel `x_0714`

Candidates (for stage 2 and for the `spoken`/`text` provenance of captions):
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0000_t0003.500_state.jpg` (c_0000, actual_t=3.500 [00:04], chapter=ch01, targets=-, state=s_0000/D)
  spoken: "This is a 3."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0001_t0008.500_target.jpg` (c_0001, actual_t=8.500 [00:08], chapter=ch01, targets=ch01_pixel_three, state=s_0001/D)
  spoken: "This is a 3. It's sloppily written and rendered at an extremely low resolution of 28x28 pixels,"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0002_t0011.000_target.jpg` (c_0002, actual_t=11.000 [00:11], chapter=ch01, targets=ch01_pixel_three, state=s_0002/D)
  spoken: "This is a 3. It's sloppily written and rendered at an extremely low resolution of 28x28 pixels, but your brain has no trouble recognizing it as a 3."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0003_t0018.000_target.jpg` (c_0003, actual_t=18.000 [00:18], chapter=ch01, targets=ch01_many_threes,ch01_pixel_three, state=s_0003/D)
  spoken: "It's sloppily written and rendered at an extremely low resolution of 28x28 pixels, but your brain has no trouble recognizing it as a 3. And I want you to take a…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0004_t0039.000_target.jpg` (c_0004, actual_t=39.000 [00:39], chapter=ch01, targets=ch01_many_threes, state=s_0004/D)
  spoken: "And I want you to take a moment to appreciate how crazy it is that brains can do this so effortlessly. I mean, this, this and this are also recognizable as 3s, …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0005_t0049.000_state.jpg` (c_0005, actual_t=49.000 [00:49], chapter=ch01, targets=-, state=s_0005/D)
  spoken: "see this 3 are very different from the ones firing when you see this 3. But something in that crazy-smart visual cortex of yours resolves these as representing …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0006_t0066.500_state.jpg` (c_0006, actual_t=66.500 [01:06], chapter=ch01, targets=-, state=s_0006/D)
  spoken: "But if I told you, hey, sit down and write for me a program that takes in a grid of 28x28 pixels like this and outputs a single number between 0 and 10, telling…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0007_t0069.000_state.jpg` (c_0007, actual_t=69.000 [01:09], chapter=ch02, targets=-, state=s_0007/D)
  spoken: "telling you what it thinks the digit is, well the task goes from comically trivial to dauntingly difficult. Unless you've been living under a rock, I think I ha…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0008_t0073.000_state.jpg` (c_0008, actual_t=73.000 [01:13], chapter=ch02, targets=-, state=s_0008/D)
  spoken: "dauntingly difficult. Unless you've been living under a rock, I think I hardly need to motivate the relevance and importance of machine learning and neural netw…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0009_t0096.000_state.jpg` (c_0009, actual_t=96.000 [01:36], chapter=ch02, targets=-, state=s_0009/D)
  spoken: "and importance of machine learning and neural networks to the present and to the future. But what I want to do here is show you what a neural network actually i…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0010_t0100.000_target.jpg` (c_0010, actual_t=100.000 [01:40], chapter=ch02, targets=ch02_network_preview, state=s_0010/D)
  spoken: "or you hear about a neural network quote-unquote learning. This video is just going to be devoted to the structure component of that, and the following one is g…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0011_t0107.500_target.jpg` (c_0011, actual_t=107.500 [01:48], chapter=ch02, targets=ch02_network_preview, state=s_0011/D (first stage))
  spoken: "and the following one is going to tackle learning. What we're going to do is put together a neural network that can learn to recognize handwritten digits. This …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0012_t0124.500_target.jpg` (c_0012, actual_t=124.500 [02:04], chapter=ch02, targets=ch02_network_preview, state=s_0011/D)
  spoken: "and the following one is going to tackle learning. What we're going to do is put together a neural network that can learn to recognize handwritten digits. This …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0013_t0130.000_state.jpg` (c_0013, actual_t=130.000 [02:10], chapter=ch02, targets=-, state=s_0012/D)
  spoken: "does this and play with it on your own computer. There are many many variants of neural networks, and in recent years there's been sort of a boom in research to…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0014_t0132.000_state.jpg` (c_0014, actual_t=132.000 [02:12], chapter=ch02, targets=-, state=s_0013/D)
  spoken: "There are many many variants of neural networks, and in recent years there's been sort of a boom in research towards these variants, but in these two introducto…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0015_t0134.000_state.jpg` (c_0015, actual_t=134.000 [02:14], chapter=ch02, targets=-, state=s_0014/D)
  spoken: "and in recent years there's been sort of a boom in research towards these variants, but in these two introductory videos you and I are just going to look at the…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0016_t0137.500_state.jpg` (c_0016, actual_t=137.500 [02:18], chapter=ch02, targets=-, state=s_0015/D)
  spoken: "and in recent years there's been sort of a boom in research towards these variants, but in these two introductory videos you and I are just going to look at the…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0017_t0147.000_state.jpg` (c_0017, actual_t=147.000 [02:27], chapter=ch02, targets=-, state=s_0016/D)
  spoken: "but in these two introductory videos you and I are just going to look at the simplest plain vanilla form with no added frills. This is kind of a necessary prere…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0018_t0157.000_state.jpg` (c_0018, actual_t=157.000 [02:37], chapter=ch02, targets=-, state=s_0017/D)
  spoken: "modern variants, and trust me it still has plenty of complexity for us to wrap our minds around. But even in this simplest form it can learn to recognize handwr…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0019_t0159.500_state.jpg` (c_0019, actual_t=159.500 [02:40], chapter=ch02, targets=-, state=s_0018/D)
  spoken: "which is a pretty cool thing for a computer to be able to do. And at the same time you'll see how it does fall short of a couple hopes that we might have for it…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0020_t0162.500_state.jpg` (c_0020, actual_t=162.500 [02:42], chapter=ch02, targets=-, state=s_0019/B, family=f_001 (same picture also at 02:43))
  spoken: "And at the same time you'll see how it does fall short of a couple hopes that we might have for it."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0021_t0164.500_state.jpg` (c_0021, actual_t=164.500 [02:44], chapter=ch03, targets=-, state=s_0021/B)
  spoken: "short of a couple hopes that we might have for it. As the name suggests neural networks are inspired by the brain, but let's break that down."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0022_t0172.000_state.jpg` (c_0022, actual_t=172.000 [02:52], chapter=ch03, targets=-, state=s_0022/B)
  spoken: "short of a couple hopes that we might have for it. As the name suggests neural networks are inspired by the brain, but let's break that down. What are the neuro…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0023_t0183.000_state.jpg` (c_0023, actual_t=183.000 [03:03], chapter=ch03, targets=-, state=s_0023/B)
  spoken: "What are the neurons, and in what sense are they linked together? Right now when I say neuron all I want you to think about is a thing that holds a number, spec…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0024_t0184.500_target.jpg` (c_0024, actual_t=184.500 [03:04], chapter=ch03, targets=ch03_input_neurons, text=344, state=s_0024/B, family=f_002 (same picture also at 05:06))
  spoken: "It's really not more than that. For example the network starts with a bunch of neurons corresponding to" — ocr: "Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stream #0:0: Video: wrapped_avframe, yuvj420p(pc, bt470bg/…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0025_t0195.000_target.jpg` (c_0025, actual_t=195.000 [03:15], chapter=ch03, targets=ch03_input_neurons, text=378, state=s_0025/B, family=f_003 (same picture also at 05:08))
  spoken: "It's really not more than that. For example the network starts with a bunch of neurons corresponding to each of the 28x28 pixels of the input image, which is 78…" — ocr: "28 - 28 x 28 = 784 Etat ait al Peet tits 28 0 BH BH Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stream…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0026_t0214.000_target.jpg` (c_0026, actual_t=214.000 [03:34], chapter=ch03, targets=ch03_activation, state=s_0027/B)
  spoken: "Each one of these holds a number that represents the grayscale value of the corresponding pixel, ranging from 0 for black pixels up to 1 for white pixels. This …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0027_t0217.000_state.jpg` (c_0027, actual_t=217.000 [03:37], chapter=ch04, targets=-, state=s_0028/B, family=f_004 (same picture also at 03:16))
  spoken: "and the image you might have in mind here is that each neuron is lit up when its activation is a high number. So all of these 784 neurons make up the first laye…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0028_t0219.000_state.jpg` (c_0028, actual_t=219.000 [03:39], chapter=ch04, targets=-, state=s_0029/B)
  spoken: "So all of these 784 neurons make up the first layer of our network."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0029_t0227.500_target.jpg` (c_0029, actual_t=227.500 [03:48], chapter=ch04, targets=ch04_output_layer, state=s_0031/B (first stage))
  spoken: "Now jumping over to the last layer, this has 10 neurons, each representing one of the digits. The activation in these neurons, again some number that's between …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0030_t0238.500_target.jpg` (c_0030, actual_t=238.500 [03:58], chapter=ch04, targets=ch04_output_layer, text=360, state=s_0031/B, family=f_005)
  spoken: "Now jumping over to the last layer, this has 10 neurons, each representing one of the digits. The activation in these neurons, again some number that's between …" — ocr: "6 784) @ ee: ey 6 1-3 4 0 ie) 0 Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stream #0:0: Video: wrappe…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0031_t0247.000_target.jpg` (c_0031, actual_t=247.000 [04:07], chapter=ch04, targets=ch04_output_layer, text=376, state=s_0033/B, family=f_006)
  spoken: "represents how much the system thinks that a given image corresponds with a given digit. There's also a couple layers in between called the hidden layers, which…" — ocr: "|" layers eS |] we SS 2 Ol Ds fe Ze 8 Zw 0G ce Ze 8 0 Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stre…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0032_t0265.000_target.jpg` (c_0032, actual_t=265.000 [04:25], chapter=ch04, targets=ch04_hidden_layers, state=s_0035/B)
  spoken: "how on earth this process of recognizing digits is going to be handled. In this network I chose two hidden layers, each one with 16 neurons, and admittedly that…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0033_t0272.500_state.jpg` (c_0033, actual_t=272.500 [04:32], chapter=ch04, targets=-, state=s_0036/D)
  spoken: "To be honest I chose two layers based on how I want to motivate the structure in just a moment, and 16, well that was just a nice number to fit on the screen. I…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0034_t0291.500_state.jpg` (c_0034, actual_t=291.500 [04:52], chapter=ch04, targets=-, state=s_0039/D)
  spoken: "to exactly how those activations from one layer bring about activations in the next layer. It's meant to be loosely analogous to how in biological networks of n…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0035_t0304.500_state.jpg` (c_0035, actual_t=304.500 [05:04], chapter=ch04, targets=-, state=s_0042/D, family=f_007)
  spoken: "some groups of neurons firing cause certain others to fire. Now the network I'm showing here has already been trained to recognize digits, and let me show you w…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0036_t0323.500_target.jpg` (c_0036, actual_t=323.500 [05:24], chapter=ch04, targets=ch04_propagation, state=s_0046/D)
  spoken: "according to the brightness of each pixel in the image, that pattern of activations causes some very specific pattern in the next layer which causes some patter…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0037_t0325.000_target.jpg` (c_0037, actual_t=325.000 [05:25], chapter=ch04, targets=ch04_propagation, state=s_0047/D)
  spoken: "which finally gives some pattern in the output layer. And the brightest neuron of that output layer is the network's choice,"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0038_t0330.000_target.jpg` (c_0038, actual_t=330.000 [05:30], chapter=ch04, targets=ch04_propagation, state=s_0048/D)
  spoken: "which finally gives some pattern in the output layer. And the brightest neuron of that output layer is the network's choice, so to speak, for what digit this im…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0039_t0332.000_state.jpg` (c_0039, actual_t=332.000 [05:32], chapter=ch05, targets=-, state=s_0049/D)
  spoken: "so to speak, for what digit this image represents."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0040_t0334.000_state.jpg` (c_0040, actual_t=334.000 [05:34], chapter=ch05, targets=-, state=s_0050/D)
  spoken: "so to speak, for what digit this image represents. And before jumping into the math for how one layer influences the next,"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0041_t0338.000_state.jpg` (c_0041, actual_t=338.000 [05:38], chapter=ch05, targets=-, state=s_0051/D)
  spoken: "And before jumping into the math for how one layer influences the next, or how training works, let's just talk about why it's even reasonable"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0042_t0348.500_state.jpg` (c_0042, actual_t=348.500 [05:48], chapter=ch05, targets=-, state=s_0052/D)
  spoken: "And before jumping into the math for how one layer influences the next, or how training works, let's just talk about why it's even reasonable to expect a layere…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0043_t0371.000_target.jpg` (c_0043, actual_t=371.000 [06:11], chapter=ch05, targets=ch05_loop_neuron,ch05_subcomponents, state=s_0053/D)
  spoken: "What are we expecting here? What is the best hope for what those middle layers might be doing? Well, when you or I recognize digits, we piece together various c…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0044_t0375.500_target.jpg` (c_0044, actual_t=375.500 [06:16], chapter=ch05, targets=ch05_loop_neuron, text=420, state=s_0054/D (first stage), family=f_008)
  spoken: "Now in a perfect world, we might hope that each neuron in the second to last layer corresponds with one of these subcomponents, that anytime you feed in an imag…" — ocr: "Qe {2 R28 Qf - 2 ee Se eee) QE OCR 0 0 0 0 0 D3 ROLE 0 741: 0 DG Ds 0 0 20 0 D6 Qe OC 257 fe 6 8 Uh OC Ze e et Ze ol = O…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0045_t0382.000_target.jpg` (c_0045, actual_t=382.000 [06:22], chapter=ch05, targets=ch05_loop_neuron, text=432, state=s_0054/D, family=f_009)
  spoken: "Now in a perfect world, we might hope that each neuron in the second to last layer corresponds with one of these subcomponents, that anytime you feed in an imag…" — ocr: "0 Upper loop neuron...maybe... so. Swe 8 - CAN ee ie 1 Qe te OLN Ni 2 8 2 ODEN Ds : Re OLE ys 784) 5 Oe He 5s 0 0 0 OLE …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0046_t0413.000_state.jpg` (c_0046, actual_t=413.000 [06:53], chapter=ch05, targets=-, state=s_0056/D)
  spoken: "learning which combination of subcomponents corresponds to which digits. Of course, that just kicks the problem down the road, because how would you recognize t…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0047_t0421.000_state.jpg` (c_0047, actual_t=421.000 [07:01], chapter=ch05, targets=-, state=s_0057/D)
  spoken: "And I still haven't even talked about how one layer influences the next, but run with me on this one for a moment. Recognizing a loop can also break down into s…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0048_t0440.000_state.jpg` (c_0048, actual_t=440.000 [07:20], chapter=ch05, targets=-, state=s_0059/D)
  spoken: "is really just a long edge, or maybe you think of it as a certain pattern of several smaller edges. So maybe our hope is that each neuron in the second layer of…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0049_t0442.500_target.jpg` (c_0049, actual_t=442.500 [07:22], chapter=ch05, targets=ch05_edges_to_nine, text=355, state=s_0060/D, family=f_010)
  spoken: "So maybe our hope is that each neuron in the second layer of the network corresponds with the various relevant little edges." — ocr: "oh rim Ei ji ez Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stream #0:0: Video: wrapped_avframe, yuvj4…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0050_t0470.000_target.jpg` (c_0050, actual_t=470.000 [07:50], chapter=ch05, targets=ch05_edges_to_nine, state=s_0061/D)
  spoken: "the network corresponds with the various relevant little edges. Maybe when an image like this one comes in, it lights up all of the neurons associated with arou…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0051_t0480.500_state.jpg` (c_0051, actual_t=480.500 [08:00], chapter=ch05, targets=-, state=s_0063/D)
  spoken: "Moreover, you can imagine how being able to detect edges and patterns like this would be really useful for other image recognition tasks. And even beyond image …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0052_t0491.000_target.jpg` (c_0052, actual_t=491.000 [08:11], chapter=ch05, targets=ch05_speech, state=s_0064/D)
  spoken: "like this would be really useful for other image recognition tasks. And even beyond image recognition, there are all sorts of intelligent things you might want …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0053_t0501.000_target.jpg` (c_0053, actual_t=501.000 [08:21], chapter=ch05, targets=ch05_speech, state=s_0065/D)
  spoken: "Parsing speech, for example, involves taking raw audio and picking out distinct sounds, which combine to make certain syllables, which combine to form words, wh…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0054_t0511.000_state.jpg` (c_0054, actual_t=511.000 [08:31], chapter=ch05, targets=-, state=s_0066/D)
  spoken: "which combine to make up phrases and more abstract thoughts, etc. But getting back to how any of this actually works, picture yourself right now designing how e…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0055_t0520.000_state.jpg` (c_0055, actual_t=520.000 [08:40], chapter=ch06, targets=-, state=s_0068/D, family=f_011 (same picture also at 08:38))
  spoken: "The goal is to have some mechanism that could conceivably combine pixels into edges, or edges into patterns, or patterns into digits. And to zoom in on one very…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0056_t0531.500_state.jpg` (c_0056, actual_t=531.500 [08:52], chapter=ch06, targets=-, state=s_0070/D)
  spoken: "And to zoom in on one very specific example, let's say the hope is for one particular neuron in the second layer to pick up on whether or not the image has an e…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0057_t0536.000_state.jpg` (c_0057, actual_t=536.000 [08:56], chapter=ch06, targets=-, state=s_0071/D)
  spoken: "on whether or not the image has an edge in this region here. The question at hand is what parameters should the network have? What dials and knobs should you be…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0058_t0553.000_target.jpg` (c_0058, actual_t=553.000 [09:13], chapter=ch06, targets=ch06_weight_grid, state=s_0072/D (first stage))
  spoken: "or the pattern that several edges can make a loop, and other such things? Well, what we'll do is assign a weight to each one of the connections between our neur…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0059_t0569.500_target.jpg` (c_0059, actual_t=569.500 [09:30], chapter=ch06, targets=ch06_weight_grid, state=s_0072/D)
  spoken: "or the pattern that several edges can make a loop, and other such things? Well, what we'll do is assign a weight to each one of the connections between our neur…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0060_t0582.500_target.jpg` (c_0060, actual_t=582.500 [09:42], chapter=ch06, targets=ch06_weight_grid, state=s_0073/D)
  spoken: "I find it helpful to think of these weights as being organized into a little grid of their own, and I'm going to use green pixels to indicate positive weights, …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0061_t0600.500_target.jpg` (c_0061, actual_t=600.500 [10:00], chapter=ch06, targets=ch06_edge_weights, text=369, state=s_0076/B, family=f_014 (same picture also at 15:56))
  spoken: "except for some positive weights in this region that we care about, then taking the weighted sum of all the pixel values really just amounts to adding up the va…" — ocr: "Way + WrG2-+ W334 W1d4 e+ Un dn Q [> g 4 G/{ : / / / . 6 Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 S…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0062_t0607.500_target.jpg` (c_0062, actual_t=607.500 [10:08], chapter=ch06, targets=ch06_edge_weights, text=375, state=s_0077/B, family=f_013)
  spoken: "And if you really wanted to pick up on whether there's an edge here, what you might do is have some negative weights associated with the surrounding pixels. The…" — ocr: "1 61+ W224 W303 + Wid +--+ Wry oN oN Hee : 74): GZ 2 // / e . 0 Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0063_t0620.000_state.jpg` (c_0063, actual_t=620.000 [10:20], chapter=ch06, targets=-, state=s_0079/B)
  spoken: "are bright but the surrounding pixels are darker. When you compute a weighted sum like this, you might come out with any number, but for this network what we wa…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0064_t0627.000_state.jpg` (c_0064, actual_t=627.000 [10:27], chapter=ch06, targets=-, state=s_0080/B)
  spoken: "When you compute a weighted sum like this, you might come out with any number, but for this network what we want is for activations to be some value between 0 a…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0065_t0632.000_target.jpg` (c_0065, actual_t=632.000 [10:32], chapter=ch06, targets=ch06_sigmoid, state=s_0081/B)
  spoken: "So a common thing to do is to pump this weighted sum into some function that squishes the real number line into the range between 0 and 1. And a common function…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0066_t0649.000_target.jpg` (c_0066, actual_t=649.000 [10:49], chapter=ch06, targets=ch06_sigmoid, text=360, state=s_0082/B, family=f_015 (same picture also at 17:20))
  spoken: "that squishes the real number line into the range between 0 and 1. And a common function that does this is called the sigmoid function, also known as a logistic…" — ocr: "P Sigmoid 1 90) = 1 . - - - - 1 3 3 4 - Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 Stream #0:0: Video…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0067_t0659.000_state.jpg` (c_0067, actual_t=659.000 [10:59], chapter=ch06, targets=-, state=s_0083/B)
  spoken: "and it just steadily increases around the input 0. So the activation of the neuron here is basically a measure of how positive the relevant weighted sum is. But…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0068_t0695.000_target.jpg` (c_0068, actual_t=695.000 [11:35], chapter=ch06, targets=ch06_bias, text=443, state=s_0084/B, family=f_016 (same picture also at 11:58))
  spoken: "But maybe it's not that you want the neuron to light up when the weighted sum is bigger than 0. Maybe you only want it to be active when the sum is bigger than …" — ocr: "Sigmoid ee How positive is this? oO 7 ) + wag + wag +--+ + WndnET0) *bias? 9 6 0 Only activate meaningfully | | 784) : 7…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0069_t0696.500_state.jpg` (c_0069, actual_t=696.500 [11:36], chapter=ch07, targets=-, state=s_0086/B, family=f_012 (same picture also at 08:42, 11:36))
  spoken: "sum needs to be before the neuron starts getting meaningfully active. And that is just one neuron."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0070_t0701.000_state.jpg` (c_0070, actual_t=701.000 [11:41], chapter=ch07, targets=-, state=s_0087/B)
  spoken: "sum needs to be before the neuron starts getting meaningfully active. And that is just one neuron. Every other neuron in this layer is going to be connected to …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0071_t0706.500_state.jpg` (c_0071, actual_t=706.500 [11:46], chapter=ch07, targets=-, state=s_0088/B)
  spoken: "all 784 pixel neurons from the first layer, and each one of those 784 connections has its own weight associated with it."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0072_t0726.000_target.jpg` (c_0072, actual_t=726.000 [12:06], chapter=ch07, targets=ch07_784x16, text=394, state=s_0092/B, family=f_018)
  spoken: "on to the weighted sum before squishing it with the sigmoid. And that's a lot to think about! With this hidden layer of 16 neurons, that's a total of 784 times …" — ocr: "8 vei 7 Gm. 784x16 weights 66 . eit @ 7844 | BS one bias for each 6 Ge | 9 8 Output #0, null, to 'pipe:': Metadata: enco…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0073_t0732.000_target.jpg` (c_0073, actual_t=732.000 [12:12], chapter=ch07, targets=ch07_784x16, text=386, state=s_0093/B, family=f_017)
  spoken: "With this hidden layer of 16 neurons, that's a total of 784 times 16 weights, along with 16 biases. And all of that is just the connections from the first layer…" — ocr: "Qo 784x16 weights 16 biases ee 8: 0 5 =>) ge fe) ee 1. @ GeO 5 Za? 0 Output #0, null, to 'pipe:': Metadata: encoder : La…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0074_t0738.500_target.jpg` (c_0074, actual_t=738.500 [12:18], chapter=ch07, targets=ch07_13000, state=s_0094/B)
  spoken: "The connections between the other layers also have a bunch of weights and biases associated with them. All said and done, this network has almost exactly 13,000…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0075_t0742.500_target.jpg` (c_0075, actual_t=742.500 [12:22], chapter=ch07, targets=ch07_13000, state=s_0095/B)
  spoken: "a bunch of weights and biases associated with them. All said and done, this network has almost exactly 13,000 total weights and biases."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0076_t0745.500_target.jpg` (c_0076, actual_t=745.500 [12:26], chapter=ch07, targets=ch07_13000, text=403, state=s_0096/B, family=f_019 (same picture also at 15:54))
  spoken: "All said and done, this network has almost exactly 13,000 total weights and biases. 13,000 knobs and dials that can be tweaked and turned to make this network b…" — ocr: "% aa 784x16+16x 16 + 16x10 WR weights XY 5 Sd =: 16 +16 +10 : Jag 49) Gap YS Gye 6 eos) 9 285 5 Output #0, null, to 'pip…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0077_t0806.000_state.jpg` (c_0077, actual_t=806.000 [13:26], chapter=ch09, targets=-, state=s_0097/D)
  spoken: "digging into what the weights and biases are doing is a good way to challenge your assumptions and really expose the full space of possible solutions."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0078_t0821.000_target.jpg` (c_0078, actual_t=821.000 [13:41], chapter=ch09, targets=ch09_matrix_vector, state=s_0098/D)
  spoken: "your assumptions and really expose the full space of possible solutions. By the way, the actual function here is a little cumbersome to write down, don't you th…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0079_t0823.000_target.jpg` (c_0079, actual_t=823.000 [13:43], chapter=ch09, targets=ch09_matrix_vector, state=s_0099/D)
  spoken: "This is how you'd see it if you choose to read up more about neural networks. Organize all of the activations from one layer into a column as a vector."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0080_t0849.000_target.jpg` (c_0080, actual_t=849.000 [14:09], chapter=ch09, targets=ch09_matrix_vector, state=s_0100/D)
  spoken: "This is how you'd see it if you choose to read up more about neural networks. Organize all of the activations from one layer into a column as a vector. Then org…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0081_t0851.000_state.jpg` (c_0081, actual_t=851.000 [14:11], chapter=ch09, targets=-, state=s_0101/D)
  spoken: "terms in the matrix vector product of everything we have on the left here."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0082_t0857.000_state.jpg` (c_0082, actual_t=857.000 [14:17], chapter=ch09, targets=-, state=s_0102/D)
  spoken: "terms in the matrix vector product of everything we have on the left here. By the way, so much of machine learning just comes down to having a good"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0083_t0860.000_state.jpg` (c_0083, actual_t=860.000 [14:20], chapter=ch09, targets=-, state=s_0103/D)
  spoken: "By the way, so much of machine learning just comes down to having a good grasp of linear algebra, so for any of you who want a nice visual"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0084_t0868.000_state.jpg` (c_0084, actual_t=868.000 [14:28], chapter=ch09, targets=-, state=s_0104/D)
  spoken: "By the way, so much of machine learning just comes down to having a good grasp of linear algebra, so for any of you who want a nice visual understanding for mat…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0085_t0907.500_target.jpg` (c_0085, actual_t=907.500 [15:08], chapter=ch09, targets=ch09_bias_vector,ch09_compact, state=s_0105/D)
  spoken: "take a look at the series I did on linear algebra, especially chapter 3. Back to our expression, instead of talking about adding the bias to each one of these v…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0086_t0917.000_state.jpg` (c_0086, actual_t=917.000 [15:17], chapter=ch10, targets=-, state=s_0107/D, family=f_020 (same picture also at 15:16))
  spoken: "simpler and a lot faster, since many libraries optimize the heck out of matrix multiplication."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0087_t0923.000_state.jpg` (c_0087, actual_t=923.000 [15:23], chapter=ch10, targets=-, state=s_0108/D)
  spoken: "simpler and a lot faster, since many libraries optimize the heck out of matrix multiplication. Remember how earlier I said these neurons are simply things that …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0088_t0932.500_state.jpg` (c_0088, actual_t=932.500 [15:32], chapter=ch10, targets=-, state=s_0109/D)
  spoken: "Remember how earlier I said these neurons are simply things that hold numbers? Well of course the specific numbers that they hold depends on the image you feed …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0089_t0938.500_target.jpg` (c_0089, actual_t=938.500 [15:38], chapter=ch10, targets=ch10_function, state=s_0110/D)
  spoken: "so it's actually more accurate to think of each neuron as a function, one that takes in the outputs of all the neurons in the previous layer and spits out a num…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0090_t0941.500_target.jpg` (c_0090, actual_t=941.500 [15:42], chapter=ch10, targets=ch10_function, state=s_0111/D)
  spoken: "one that takes in the outputs of all the neurons in the previous layer and spits out a number between 0 and 1. Really the entire network is just a function, one…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0091_t0950.000_target.jpg` (c_0091, actual_t=950.000 [15:50], chapter=ch10, targets=ch10_function, text=384, state=s_0112/D, family=f_021)
  spoken: "Really the entire network is just a function, one that takes in 784 numbers as an input and spits out 10 numbers as an output. It's an absurdly complicated func…" — ocr: "Yo Network F(a0,-+-, 783) = |: 4 - ~ Law | Function Bont = me 50 8 Oe 9 0 ~ : - Output #0, null, to 'pipe:': Metadata: e…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0092_t0960.500_state.jpg` (c_0092, actual_t=960.500 [16:00], chapter=ch10, targets=-, state=s_0115/D)
  spoken: "in the forms of these weights and biases that pick up on certain patterns, and which involves iterating many matrix vector products and the sigmoid squishificat…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0093_t0972.500_state.jpg` (c_0093, actual_t=972.500 [16:12], chapter=ch10, targets=-, state=s_0117/D)
  spoken: "And in a way it's kind of reassuring that it looks complicated. I mean if it were any simpler, what hope would we have that it could take on the challenge of re…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0094_t0975.000_state.jpg` (c_0094, actual_t=975.000 [16:15], chapter=ch10, targets=-, state=s_0118/D)
  spoken: "I mean if it were any simpler, what hope would we have that it could take on the challenge of recognizing digits? And how does it take on that challenge? How do…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0095_t0977.500_state.jpg` (c_0095, actual_t=977.500 [16:18], chapter=ch10, targets=-, state=s_0119/D)
  spoken: "that it could take on the challenge of recognizing digits? And how does it take on that challenge? How does this network learn the appropriate weights and biase…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0096_t0980.500_state.jpg` (c_0096, actual_t=980.500 [16:20], chapter=ch10, targets=-, state=s_0120/D)
  spoken: "And how does it take on that challenge? How does this network learn the appropriate weights and biases just by looking at data? Well that's what I'll show in th…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0097_t0982.500_state.jpg` (c_0097, actual_t=982.500 [16:22], chapter=ch10, targets=-, state=s_0121/D)
  spoken: "How does this network learn the appropriate weights and biases just by looking at data? Well that's what I'll show in the next video, and I'll also dig a little"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0098_t0987.000_state.jpg` (c_0098, actual_t=987.000 [16:27], chapter=ch10, targets=-, state=s_0122/D)
  spoken: "How does this network learn the appropriate weights and biases just by looking at data? Well that's what I'll show in the next video, and I'll also dig a little…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0099_t1023.000_state.jpg` (c_0099, actual_t=1023.000 [17:03], chapter=ch12, targets=-, state=s_0123/D)
  spoken: "but I'm jumping back into it after this project, so patrons you can look out for updates there. To close things off here I have with me Lisha Li who did her PhD…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0100_t1035.000_state.jpg` (c_0100, actual_t=1035.000 [17:15], chapter=ch12, targets=-, state=s_0124/D)
  spoken: "so patrons you can look out for updates there. To close things off here I have with me Lisha Li who did her PhD work on the theoretical side of deep learning an…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0101_t1050.500_state.jpg` (c_0101, actual_t=1050.500 [17:30], chapter=ch12, targets=-, state=s_0126/D)
  spoken: "So Lisha one thing I think we should quickly bring up is this sigmoid function. As I understand it early networks use this to squish the relevant weighted sum i…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0102_t1064.500_target.jpg` (c_0102, actual_t=1064.500 [17:44], chapter=ch12, targets=ch12_relu, state=s_0127/D)
  spoken: "by this biological analogy of neurons either being inactive or active. Exactly. But relatively few modern networks actually use sigmoid anymore. Yeah. It's kind…"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0103_t1091.000_target.jpg` (c_0103, actual_t=1091.000 [18:11], chapter=ch12, targets=ch12_relu, state=s_0128/B)
  spoken: "Yes it's this kind of function where you're just taking a max of zero and a where a is given by what you were explaining in the video and what this was sort of …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0104_t1097.500_state.jpg` (c_0104, actual_t=1097.500 [18:18], chapter=ch12, targets=-, state=s_0129/B)
  spoken: "not then it would just not be activated so it'd be zero so it's kind of a simplification. Using sigmoids didn't help training or it was very difficult to train …"
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0105_t1104.000_state.jpg` (c_0105, actual_t=1104.000 [18:24], chapter=ch12, targets=-, state=s_0130/B)
  spoken: "train at some point and people just tried ReLU and it happened to work very well for these incredibly deep neural networks. All right thank you Lisha."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0106_t1117.500_state.jpg` (c_0106, actual_t=1117.500 [18:38], chapter=ch12, targets=-, state=s_0131/D)
  spoken: "to work very well for these incredibly deep neural networks. All right thank you Lisha."
- `<skill>/bench/runs/2026-09-04-v15-high-p128/aircAruvnKk/work/candidates/c_0107_t1119.500_final.jpg` (c_0107, actual_t=1119.500 [18:40], chapter=ch12, targets=-)
