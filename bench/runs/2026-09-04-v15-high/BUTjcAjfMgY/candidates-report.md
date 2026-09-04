
# candidate frames report

- **Tier:** high (alias: --mode advanced) — states engine (90 states from a 2.0 fps scan)
- **Visual states:** 90 (A talk 0, B static 89, C canvas 0, D dynamic UI 1); 20 families, 11 builds; mode timeline per 20 s: `BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBD`; scan 22.9s — `states.json` in the work dir
- **Candidates:** 73 (pool 64 lifted to 73: 57 reserved target/coverage frames + 16 unplanned slots; raw 98; dedup 17 [family scope]; cap 8)
- **Overlay mask:** none detected — no persistent picture-in-picture or bar (0.0s)
- **Image tokens (estimate):** ≈15,257 for one batched Read (73×512x288; 209 each, ⌈w/28⌉×⌈h/28⌉; other providers differ)
- **CPU:** 1 adaptive scene pass over 33:18 of chapter windows · 0 terminal probes · 98 seeks + signatures · OCR: on (28 frames) · faces: unavailable · grab refinement: sharpness (≤20 × ~3 s decodes)
- **Other tier:** `--tier standard` pool 48 candidates (≈10,032 image tokens before the reserved-frame lift; it reserves 2 frames per target)
- **Manifest:** `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates.json`

## Per-chapter coverage

| chapter | window | candidates | status |
|---|---|---|---|
| ch01 | 00:00-00:42 | 0 (0 targets) | not-required |
| ch02 | 00:42-01:50 | 4 (2 targets) | covered |
| ch03 | 01:50-02:44 | 3 (1 target) | covered |
| ch04 | 02:44-03:35 | 3 (1 target) | covered |
| ch05 | 03:35-04:25 | 3 (1 target) | covered |
| ch06 | 04:25-08:17 | 8 (4 targets) | covered |
| ch07 | 08:17-10:00 | 4 (2 targets) | covered |
| ch08 | 10:00-10:50 | 0 (0 targets) | not-required |
| ch09 | 10:50-12:07 | 2 (1 target) | covered |
| ch10 | 12:07-13:56 | 6 (3 targets) | covered |
| ch11 | 13:56-15:29 | 6 (2 targets) | covered |
| ch12 | 15:29-20:29 | 5 (4 targets) | covered |
| ch13 | 20:29-21:58 | 1 (1 target) | covered |
| ch14 | 21:58-23:28 | 3 (1 target) | covered |
| ch15 | 23:28-25:20 | 1 (1 target) | covered |
| ch16 | 25:20-29:15 | 7 (3 targets) | covered |
| ch17 | 29:15-30:32 | 3 (1 target) | covered |
| ch18 | 30:32-33:36 | 8 (4 targets) | covered |
| ch19 | 33:36-34:50 | 6 (1 target) | covered |

A needs_frames chapter with only 1 candidate is a static stretch — if its point is visual, add a target inside its window before triage.

**Two-stage triage.** Stage 1 — Read ALL 5 contact sheets in one message (≈6,670 image tokens for the whole pool; reading every candidate individually would cost 15,257): for every tile decide keep/drop by its burned-in id, group the same picture into one family, and report each sheet's sentinel tile as blank (if you cannot find it, fall back to reading the candidates below individually). Stage 2 — `python3 "$SKILL_DIR/scripts/shortlist.py" --work "<work>" --ids <kept ids>` re-decodes the kept frames at 768px (verified against the candidates); Read those, then write selections.json by `candidate_id` — never copy times.

- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/sheets/sheet_00.jpg` → c_0000, c_0001, c_0002, c_0003, c_0004, c_0005, c_0006, c_0007, c_0008, c_0009, c_0010, c_0011, c_0012, c_0013, c_0014; sentinel `x_0029`
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/sheets/sheet_01.jpg` → c_0015, c_0016, c_0017, c_0018, c_0019, c_0020, c_0021, c_0022, c_0023, c_0024, c_0025, c_0026, c_0027, c_0028, c_0029; sentinel `x_0193`
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/sheets/sheet_02.jpg` → c_0030, c_0031, c_0032, c_0033, c_0034, c_0035, c_0036, c_0037, c_0038, c_0039, c_0040, c_0041, c_0042, c_0043, c_0044; sentinel `x_0219`
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/sheets/sheet_03.jpg` → c_0045, c_0046, c_0047, c_0048, c_0049, c_0050, c_0051, c_0052, c_0053, c_0054, c_0055, c_0056, c_0057, c_0058, c_0059; sentinel `x_0378`
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/sheets/sheet_04.jpg` → c_0060, c_0061, c_0062, c_0063, c_0064, c_0065, c_0066, c_0067, c_0068, c_0069, c_0070, c_0071, c_0072; sentinel `x_0456`

Candidates (for stage 2 and for the `spoken`/`text` provenance of captions):
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0000_t0059.005_target.jpg` (c_0000, actual_t=59.005 [00:59], chapter=ch02, targets=ch02_world_model, text=406, state=s_0001/B, family=f_001)
  spoken: "Intelligence requires understanding how the world works. But of course, the world is a big and complicated place. So in order to understand it, you need to deve…" — ocr: "Intelligence Requires understanding how the world works. SEEN roms 2 wa By Output #0, null, to 'pipe:': Metadata: encode…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0001_t0067.514_target.jpg` (c_0001, actual_t=67.514 [01:08], chapter=ch02, targets=ch02_rain_prediction,ch02_world_model, state=s_0003/B)
  spoken: "reality that we live in into something you can fit into your head. And put simply, a model is something that allows you to make predictions. For example, if"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0002_t0072.002_target.jpg` (c_0002, actual_t=72.002 [01:12], chapter=ch02, targets=ch02_rain_prediction, state=s_0004/B)
  spoken: "simply, a model is something that allows you to make predictions. For example, if you were to look out the window and were to see dark clouds like this, your"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0003_t0109.005_target.jpg` (c_0003, actual_t=109.005 [01:49], chapter=ch02, targets=ch02_rain_prediction, text=426, state=s_0005/B, family=f_002)
  spoken: "you were to look out the window and were to see dark clouds like this, your mental model of the world and understanding that dark clouds typically mean it's goi…" — ocr: "Model Something that lets you make predictions - - - --+ It's going to rain Input Data (Menta Mode! Prediction Output #0…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0004_t0113.009_state.jpg` (c_0004, actual_t=113.009 [01:53], chapter=ch03, targets=-, state=s_0007/B)
  spoken: "turns out, computers can learn models of the world in very much the same way. Here I'll talk about three different ways computers can develop these models"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0005_t0138.501_state.jpg` (c_0005, actual_t=138.501 [02:19], chapter=ch03, targets=-, state=s_0008/B)
  spoken: "Here I'll talk about three different ways computers can develop these models of the world. So here when I say the word learning, what I mean by that is getting …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0006_t0163.510_target.jpg` (c_0006, actual_t=163.510 [02:44], chapter=ch03, targets=ch03_nested_diagram, text=418, state=s_0009/B, family=f_003 (same picture also at 02:47))
  spoken: "making decisions and then translating those instructions into computer code. Here I'm going to focus on three different ways we can get computers to do things w…" — ocr: "3 Ways Computers Can Learn Getting computers to do things without explicit instructions Output #0, null, to 'pipe:': Met…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0007_t0174.004_state.jpg` (c_0007, actual_t=174.004 [02:54], chapter=ch04, targets=-, state=s_0011/B)
  spoken: "deep learning in modern applications. Starting with way number one, machine learning allows computers to learn tasks directly from data. And so machine"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0008_t0198.011_target.jpg` (c_0008, actual_t=198.011 [03:18], chapter=ch04, targets=ch04_two_phases, state=s_0012/B)
  spoken: "learning allows computers to learn tasks directly from data. And so machine learning consists of two key phases. The first phase is called training. And this in…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0009_t0214.511_target.jpg` (c_0009, actual_t=214.511 [03:35], chapter=ch04, targets=ch04_two_phases, text=486, state=s_0013/B, family=f_004 (same picture also at 03:36))
  spoken: "machine learning model, we can use it to make predictions. So this brings us to phase two which is called inference where we give our machine learning model new…" — ocr: "Way 1: Machine Learning (ML) Allows computers to learn tasks directly from data Training (Phase 1) > - - fe] Taling ata …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0010_t0226.005_state.jpg` (c_0010, actual_t=226.005 [03:46], chapter=ch05, targets=-, state=s_0015/B)
  spoken: "problems. Let's dive into a bit more detail on how each of these two phases works. So I'll actually start with inference which is phase two because it's a littl…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0011_t0227.507_target.jpg` (c_0011, actual_t=227.507 [03:48], chapter=ch05, targets=ch05_linear_model, state=s_0016/B (first stage))
  spoken: "it's a little easier to understand. Inference involves using a model to make predictions. For example, suppose we have this simple linear model for predicting t…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0012_t0264.511_target.jpg` (c_0012, actual_t=264.511 [04:25], chapter=ch05, targets=ch05_linear_model, text=474, state=s_0016/B, family=f_005 (same picture also at 04:26))
  spoken: "it's a little easier to understand. Inference involves using a model to make predictions. For example, suppose we have this simple linear model for predicting t…" — ocr: "Inference (Phase 2) Using a model to make predictions Input 1 J=mxt+b 5 2 Parameters .9 = Tomorrow's high temp (predicti…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0013_t0291.004_target.jpg` (c_0013, actual_t=291.004 [04:51], chapter=ch06, targets=ch06_loss_equation, state=s_0019/B (first stage))
  spoken: "world data. So the first step in doing this is quantifying the discrepancy between our model's predictions and reality. One way we can do this is through this e…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0014_t0334.013_target.jpg` (c_0014, actual_t=334.013 [05:34], chapter=ch06, targets=ch06_loss_equation, state=s_0019/B)
  spoken: "world data. So the first step in doing this is quantifying the discrepancy between our model's predictions and reality. One way we can do this is through this e…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0015_t0335.515_target.jpg` (c_0015, actual_t=335.515 [05:36], chapter=ch06, targets=ch06_goal, state=s_0020/B (first stage))
  spoken: "examples. Instead of just one input x, we have n inputs x1, x2 all the way to xn. So here are our input values. And then we have our two parameter values m and …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0016_t0377.507_target.jpg` (c_0016, actual_t=377.507 [06:18], chapter=ch06, targets=ch06_goal, state=s_0020/B)
  spoken: "examples. Instead of just one input x, we have n inputs x1, x2 all the way to xn. So here are our input values. And then we have our two parameter values m and …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0017_t0397.510_target.jpg` (c_0017, actual_t=397.510 [06:38], chapter=ch06, targets=ch06_goal, state=s_0021/B)
  spoken: "we've quantified this discrepancy, the goal of training is to find the parameter values m and b that corresponds to the smallest possible loss function. While w…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0018_t0454.017_target.jpg` (c_0018, actual_t=454.017 [07:34], chapter=ch06, targets=ch06_matrix_form, state=s_0022/B)
  spoken: "that we can solve this problem in a closed form using math. The way that looks is we're just going to rewrite some terms. So we'll define this matrix X which wi…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0019_t0492.505_target.jpg` (c_0019, actual_t=492.505 [08:13], chapter=ch06, targets=ch06_optimal_theta, state=s_0023/B)
  spoken: "setting it equal to zero, and then solving for our parameter values. The way that looks is as follows. The gradient behaves a lot like a derivative which you pr…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0020_t0496.509_target.jpg` (c_0020, actual_t=496.509 [08:17], chapter=ch06, targets=ch06_optimal_theta, text=501, state=s_0024/B, family=f_006)
  spoken: "will be our optimal parameter values. And so if we assume that this matrix here is invertible then this becomes our optimal parameter values for theta." — ocr: "Training (Phase 1) Fit a model's predictions to reality -- n tet, x=|% ||. o=[f). and y=] % 1 te Loss Function: L(@) = l…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0021_t0523.002_target.jpg` (c_0021, actual_t=523.002 [08:43], chapter=ch07, targets=ch07_key_point, text=501, state=s_0025/B, family=f_006)
  spoken: "here is invertible then this becomes our optimal parameter values for theta. Okay. So what just happened? We just went through all this math and we computed thi…" — ocr: "Training (Phase 1) Fit a model's predictions to reality -- n ue, x=]% 1). o= fF). ana y= |? 5 a Loss Function: LO) = lly…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0022_t0546.009_target.jpg` (c_0022, actual_t=546.009 [09:06], chapter=ch07, targets=ch07_key_point, state=s_0026/B)
  spoken: "collect. Y is a set of target data that we collect and we can combine them to find our optimal parameter values. So put another way, the key point of machine le…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0023_t0549.512_state.jpg` (c_0023, actual_t=549.512 [09:10], chapter=ch07, targets=-, state=s_0027/B)
  spoken: "common thread you'll see throughout all machine learning models. And just to list a few other popular machine learning techniques you might come"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0024_t0599.512_target.jpg` (c_0024, actual_t=599.512 [10:00], chapter=ch07, targets=ch07_techniques_table, state=s_0028/B)
  spoken: "common thread you'll see throughout all machine learning models. And just to list a few other popular machine learning techniques you might come across. There's…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0025_t0660.515_state.jpg` (c_0025, actual_t=660.515 [11:01], chapter=ch09, targets=-, state=s_0029/B)
  spoken: "feature engineering began to wne around 2020 with the rise of deep learning. Deep learning is a specific type of machine learning which involves training neural…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0026_t0726.014_target.jpg` (c_0026, actual_t=726.014 [12:06], chapter=ch09, targets=ch09_cat_features, text=544, state=s_0030/B, family=f_007)
  spoken: "neural networks which can learn optimal features for a specific task all on their own. A cartoon diagram of this which CHBT helped me put together is let's say …" — ocr: "Way 2: Deep Learning (DL) Neural networks that learn optimal features (on their own) Deep Neural Nets Learn Features [ay…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0027_t0727.515_target.jpg` (c_0027, actual_t=727.515 [12:08], chapter=ch10, targets=ch10_neuron_steps, state=s_0032/B (first stage))
  spoken: "labels, you can train a neural network to do basically anything. The key technique in deep learning are neural networks, which are a series of operations that c…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0028_t0758.012_target.jpg` (c_0028, actual_t=758.012 [12:38], chapter=ch10, targets=ch10_neuron_formula,ch10_neuron_steps, text=427, state=s_0032/B, family=f_008)
  spoken: "labels, you can train a neural network to do basically anything. The key technique in deep learning are neural networks, which are a series of operations that c…" — ocr: "Neural Networks (NN) Aseries of operations that can approximate (practically) any function Neuron + : ye -@-@ - Output #…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0029_t0805.009_target.jpg` (c_0029, actual_t=805.009 [13:25], chapter=ch10, targets=ch10_neuron_formula, state=s_0034/B)
  spoken: "adding bias, and passing through an activation is fundamentally what a neuron is. Writing this out mathematically, Xi are the inputs to the neuron. W, I and B a…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0030_t0818.506_target.jpg` (c_0030, actual_t=818.506 [13:39], chapter=ch10, targets=ch10_layers_networks, state=s_0035/B)
  spoken: "nonlinearities that allow the network to be able to approximate practically any function. The way we can build up networks from neurons is we can take these inp…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0031_t0822.009_target.jpg` (c_0031, actual_t=822.009 [13:42], chapter=ch10, targets=ch10_layers_networks, state=s_0036/B)
  spoken: "everything we saw in the previous slide to this single circle. But we can combine multiple neurons together to form so-called layers. And then we can"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0032_t0833.504_target.jpg` (c_0032, actual_t=833.504 [13:54], chapter=ch10, targets=ch10_layers_networks, state=s_0037/B)
  spoken: "combine multiple neurons together to form so-called layers. And then we can combine multiple layers together to form entire neural networks. So this is how we t…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0033_t0840.011_state.jpg` (c_0033, actual_t=840.011 [14:00], chapter=ch11, targets=-, state=s_0039/B, family=f_009 (same picture also at 13:56))
  spoken: "then build layers into networks. But of course, neural networks in practice are much more sophisticated than the example I showed on the previous slide. So,"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0034_t0852.006_state.jpg` (c_0034, actual_t=852.006 [14:12], chapter=ch11, targets=-, state=s_0040/B)
  spoken: "much more sophisticated than the example I showed on the previous slide. So, while that vanilla neuron we saw in the previous slide is widely used, there's also…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0035_t0876.514_target.jpg` (c_0035, actual_t=876.514 [14:37], chapter=ch11, targets=ch11_activations, state=s_0041/B)
  spoken: "also another one called a long short-term memory neuron or LSTM, which is also pretty common when working with sequenced data. Also, there are a wide range of a…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0036_t0903.007_target.jpg` (c_0036, actual_t=903.007 [15:03], chapter=ch11, targets=ch11_activations, state=s_0042/B)
  spoken: "basically translates any inputs to a probability distribution. There are also several different types of layers that you'll commonly see in the wild. So before …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0037_t0928.015_target.jpg` (c_0037, actual_t=928.015 [15:28], chapter=ch11, targets=ch11_architectures, state=s_0043/B)
  spoken: "today. There also pooling layers, normalization layers and dropout layers. Finally, there are a zoo of different network architectures. Before we just saw a sim…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0038_t0928.516_target.jpg` (c_0038, actual_t=928.516 [15:29], chapter=ch11, targets=ch11_architectures, text=406, state=s_0044/B, family=f_010)
  spoken: "processing and the architecture behind large language models. So that's the" — ocr: "Training Neural Nets Searching for the parameters with the smallest loss Output #0, null, to 'pipe:': Metadata: encoder …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0039_t0955.510_target.jpg` (c_0039, actual_t=955.510 [15:56], chapter=ch12, targets=ch12_landscape, text=406, state=s_0045/B, family=f_010)
  spoken: "processing and the architecture behind large language models. So that's the anatomy of neural networks. But how do we actually train these objects on our data? …" — ocr: "Training Neural Nets Searching for the parameters with the smallest loss Output #0, null, to 'pipe:': Metadata: encoder …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0040_t1090.511_target.jpg` (c_0040, actual_t=1090.511 [18:11], chapter=ch12, targets=ch12_gradient_steps,ch12_landscape, state=s_0046/B)
  spoken: "significantly more complicated than the one we saw for the linear model. Just to show you a cartoon diagram of this, the loss function of a neural network will …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0041_t1134.005_target.jpg` (c_0041, actual_t=1134.005 [18:54], chapter=ch12, targets=ch12_gd_equation, state=s_0048/B)
  spoken: "algorithm for updating model parameters to minimize the loss. The equation for this is shown here where theta i are the old parameters of our model. This will b…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0042_t1170.508_target.jpg` (c_0042, actual_t=1170.508 [19:31], chapter=ch12, targets=ch12_gd_equation, state=s_0049/B)
  spoken: "are two key components of how we update our model parameters. The first is the gradient which we just talked about in the previous slide. So the gradient is tel…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0043_t1227.014_target.jpg` (c_0043, actual_t=1227.014 [20:27], chapter=ch12, targets=ch12_optimizers, state=s_0051/B)
  spoken: "there. But in practice, it's rare people use gradient descent because can have convergence issues. The data you use in computing your loss function is the full …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0044_t1315.502_target.jpg` (c_0044, actual_t=1315.502 [21:56], chapter=ch13, targets=ch13_table, state=s_0054/B)
  spoken: "other optimization strategy to develop the best final model. Some of the most common hyperparameters you'll see are epoch. So basically the number of epochs is …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0045_t1370.507_state.jpg` (c_0045, actual_t=1370.507 [22:51], chapter=ch14, targets=-, state=s_0056/B, family=f_012 (same picture also at 21:58))
  spoken: "overfitting and making your models more robust. So far, in talking about machine learning and deep learning, we've focused on models that learn by example. In o…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0046_t1383.003_state.jpg` (c_0046, actual_t=1383.003 [23:03], chapter=ch14, targets=-, state=s_0057/B)
  spoken: "and error. So far we've talked about supervised learning where you have a human who curates these examples. So here's our training data set with a set of inputs…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0047_t1405.009_target.jpg` (c_0047, actual_t=1405.009 [23:25], chapter=ch14, targets=ch14_supervised_vs_rl, state=s_0058/B)
  spoken: "machine learning model. However, with reinforcement learning, the strategy is a bit different. We have a model that can interact directly with reality or some a…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0048_t1516.003_target.jpg` (c_0048, actual_t=1516.003 [25:16], chapter=ch15, targets=ch15_elo_chart, state=s_0061/B)
  spoken: "own ways of solving problems and potentially surpass even expert human performance. A real world example of this is Alph Go, which is a deep learning model that…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0049_t1520.507_target.jpg` (c_0049, actual_t=1520.507 [25:21], chapter=ch16, targets=ch16_objective, text=390, state=s_0063/B (first stage), family=f_014 (same picture also at 25:20))
  spoken: "grandmaster performance. Going one layer deeper into how reinforcement learning works. The basic idea here is to update model parameters to maximize some reward…" — ocr: "How does it work? Update parameters to maximize rewards Output #0, null, to 'pipe:': Metadata: encoder : Lavf61.7.100 St…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0050_t1574.511_target.jpg` (c_0050, actual_t=1574.511 [26:15], chapter=ch16, targets=ch16_objective, state=s_0063/B)
  spoken: "grandmaster performance. Going one layer deeper into how reinforcement learning works. The basic idea here is to update model parameters to maximize some reward…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0051_t1581.001_target.jpg` (c_0051, actual_t=1581.001 [26:21], chapter=ch16, targets=ch16_objective, state=s_0064/B)
  spoken: "let me walk through it one piece at a time. So J is our objective. This is the reward that we're trying to maximize. Log of pi theta is the model's output. And …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0052_t1720.507_target.jpg` (c_0052, actual_t=1720.507 [28:41], chapter=ch16, targets=ch16_rollout, state=s_0066/B)
  spoken: "would be from when the car starts moving to when it reaches its destination or crashes. And then a bit more details here. So again, we already said that pi thet…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0053_t1723.010_state.jpg` (c_0053, actual_t=1723.010 [28:43], chapter=ch16, targets=-, state=s_0067/B)
  spoken: "parameters in very much the same way we did in deep learning. Namely we have this parameter update rule where we have theta k which are the old parameters,"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0054_t1753.507_target.jpg` (c_0054, actual_t=1753.507 [29:14], chapter=ch16, targets=ch16_update_rule, state=s_0069/B)
  spoken: "this parameter update rule where we have theta k which are the old parameters, theta ki which are the new parameters and then we have this update rule. So again…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0055_t1754.508_target.jpg` (c_0055, actual_t=1754.508 [29:15], chapter=ch16, targets=ch16_update_rule, text=410, state=s_0070/B, family=f_015 (same picture also at 29:30))
  spoken: "sign to give us this gradient ascent update rule. So everything I just" — ocr: "More RL Techniques Variants of REINFORCE to improve stability and efficiency Output #0, null, to 'pipe:': Metadata: enco…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0056_t1812.516_target.jpg` (c_0056, actual_t=1812.516 [30:13], chapter=ch17, targets=ch17_rl_table, text=567, state=s_0072/B, family=f_016)
  spoken: "techniques. However, that came out in 1992 and since then there have been a lot of improvements and new approaches introduced. So here's a summary table of some…" — ocr: "More RL Techniques Variants of REINFORCE to improve stability and efficiency einronce Mots aro Petey erent using at JO) …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0057_t1815.502_target.jpg` (c_0057, actual_t=1815.502 [30:16], chapter=ch17, targets=ch17_rl_table, state=s_0073/B)
  spoken: "there's this group relative policy optimization approach developed by DeepSeek and this is what they used in DeepSseek R1 and it basically combines"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0058_t1831.501_state.jpg` (c_0058, actual_t=1831.501 [30:32], chapter=ch17, targets=-, state=s_0075/B, family=f_017 (same picture also at 31:04))
  spoken: "they batch different examples into a group when computing this objective and updating the model parameters. For most of this video, I've talked about"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0059_t1865.502_target.jpg` (c_0059, actual_t=1865.502 [31:06], chapter=ch18, targets=ch18_quantity, state=s_0077/B (first stage))
  spoken: "you're fitting a model to bad data, you're going to have a bad model. That's why I wanted to finish this talk to review what makes good data. And so there reall…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0060_t1913.517_target.jpg` (c_0060, actual_t=1913.517 [31:54], chapter=ch18, targets=ch18_quantity, text=408, state=s_0077/B, family=f_018)
  spoken: "you're fitting a model to bad data, you're going to have a bad model. That's why I wanted to finish this talk to review what makes good data. And so there reall…" — ocr: "What makes good data? Property 1: Quantity > ( or Ota Less Data "Prone oovefting 1 ia .. | Output #0, null, to 'pipe:': …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0061_t1930.016_target.jpg` (c_0061, actual_t=1930.016 [32:10], chapter=ch18, targets=ch18_accuracy, text=401, state=s_0080/B (first stage), family=f_019)
  spoken: "represents reality. So there are two key aspects of this. The first is accuracy, which is the correctness of your data. For example, if your data set says someo…" — ocr: "What makes good data? Property 2: Quality Accuracy Conectness of data Output #0, null, to 'pipe:': Metadata: encoder : L…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0062_t1953.006_target.jpg` (c_0062, actual_t=1953.006 [32:33], chapter=ch18, targets=ch18_accuracy, state=s_0080/B)
  spoken: "represents reality. So there are two key aspects of this. The first is accuracy, which is the correctness of your data. For example, if your data set says someo…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0063_t1995.015_target.jpg` (c_0063, actual_t=1995.015 [33:15], chapter=ch18, targets=ch18_diversity, state=s_0081/B)
  spoken: "year when in fact they make $12,000 a month. That's another example of bad inaccurate data. The other aspect is diversity, which is basically the representative…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0064_t2005.008_target.jpg` (c_0064, actual_t=2005.008 [33:25], chapter=ch18, targets=ch18_summary, state=s_0082/B)
  spoken: "scenarios you want to use your final model in. Just to summarize, good data comes down to quantity and quality. So, we want more data over less data. We want hi…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0065_t2010.513_target.jpg` (c_0065, actual_t=2010.513 [33:31], chapter=ch18, targets=ch18_summary, state=s_0083/B)
  spoken: "comes down to quantity and quality. So, we want more data over less data. We want highquality data over lowquality data. And we want less highquality data over …"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0066_t2015.502_target.jpg` (c_0066, actual_t=2015.502 [33:36], chapter=ch18, targets=ch18_summary, state=s_0084/B)
  spoken: "want highquality data over lowquality data. And we want less highquality data over more lowquality data. All right, so I covered a ton of information in this vi…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0067_t2021.508_state.jpg` (c_0067, actual_t=2021.508 [33:42], chapter=ch19, targets=-, state=s_0085/B)
  spoken: "over more lowquality data. All right, so I covered a ton of information in this video, so I just wanted to recap with a few key takeaways. First is that solving…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0068_t2029.015_state.jpg` (c_0068, actual_t=2029.015 [33:49], chapter=ch19, targets=-, state=s_0086/B)
  spoken: "problems requires an accurate model of the world. And machine learning gives us a way to align models to reality using data and math. A specific type of"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0069_t2037.507_state.jpg` (c_0069, actual_t=2037.507 [33:58], chapter=ch19, targets=-, state=s_0087/B)
  spoken: "a way to align models to reality using data and math. A specific type of machine learning is deep learning, which involves using neural networks to learn useful…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0070_t2039.009_target.jpg` (c_0070, actual_t=2039.009 [33:59], chapter=ch19, targets=ch19_takeaways, state=s_0088/B (first stage))
  spoken: "useful features and mappings from raw data. And a lot of times deep learning is combined with reinforcement learning which allows computers to learn by interact…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0071_t2083.503_target.jpg` (c_0071, actual_t=2083.503 [34:44], chapter=ch19, targets=ch19_takeaways, state=s_0088/B)
  spoken: "useful features and mappings from raw data. And a lot of times deep learning is combined with reinforcement learning which allows computers to learn by interact…"
- `<skill>/bench/runs/2026-09-04-v15-high/BUTjcAjfMgY/work/candidates/c_0072_t2089.509_state.jpg` (c_0072, actual_t=2089.509 [34:50], chapter=ch19, targets=-, state=s_0089/D)
  spoken: "topics you want me to cover, please let me know in the comments below. And as always, thank you so much for your time and thanks for watching."
