# The engine: transcript, candidates, tiers, cost

`workflow.py run` executes these scripts for you. This file explains what they do and which flags exist; read it when a report or a stop needs interpretation, not before every run.

## Transcript (no video download)

`transcript.py "<source>" --work "<work>" [--whisper groq|openai] [--no-whisper] [--langs "<yt-dlp pattern>"] [--wanted he,en] [--language xx]`

The caption track is chosen by provenance, not by name: a manual track in the video's own language, then a manual track in Hebrew/English, then the original-language auto-captions (`xx-orig`), then untranslated auto-captions. YouTube's machine-translated tracks are never used — the Hebrew comes from you. (YouTube keys Hebrew as `iw`; the report says `he`.) `--langs` forces a track and bypasses the ranking; the record then says truthfully whether that track is machine-translated. The report lists the creator's chapters when the video has them, prints the transcript as `seg_NNNN [MM:SS-MM:SS] text` (the `seg_id`s are the join keys for everything that follows), and a **health** line: caption coverage of the duration, largest uncaptioned gap, words per minute, repetition, reordered cues, skipped transcription chunks. Health warnings never stop the run; they tell you how much of the video the transcript actually represents.

`transcript.json` carries `status: ok | no_transcript`. Exit 6 = no usable transcript: the file is still written (with the reason under `source_detail.reason`) and every later stage refuses it. There is no frames-only path.

Cloud transcription is off unless the user explicitly chose `--whisper groq|openai` (audio is uploaded to that provider; keys come only from the environment or `~/.config/summarize-video/.env`; a stored key is not consent). `--no-whisper` always disables upload. A coding-agent subscription is not a transcription API key.

## Candidates (cheap, 512px)

`candidates.py "<source>" --work "<work>" --transcript … --chapters … --tier standard|high [--visual-content illustrated|none] [--allow-unresolved] [--sections S-E,…] [--allow-long] [--max-image-tokens N] [--max-candidates N] [--engine states|legacy] [--strips]`

What it does: refuses a missing, empty or failed transcript and an invalid `chapters.json` (exit 10) before touching the network → downloads the video once (≤720p; for videos over 20 minutes only the padded ranges of chapters that need frames, with exact cuts) → **one dense 2 fps scan of every `needs_frames` chapter** (160×90 gray, one ffmpeg pass) that yields the overlay mask and the **visual states**: runs of the same picture (a drifting whiteboard or a scrolling page is one state; a slide flip or a board wipe starts another), each with a mode (talk / static content / canvas / dynamic UI), a representative time (the last settled frame; the fullest frame of a build), the transcript segments it was on screen for, and a family id shared by revisits → one candidate per state, targets attached to the states that overlap their window (`action_result` → the first state after the action) → chapter-coverage midpoints only where no state exists → every frame is extracted by seeking to its own timestamp with the decoded `actual_t` recorded and drift-checked → blank/black filter with recovery for target frames → **overlay mask**: a persistent picture-in-picture (webcam) or bar is detected once per video and blanked in every signature (written frames are untouched) → **family dedup across the whole video** → cap (standard 48, high 64): targets and chapter coverage are reserved, the remaining slots are filled greedily by importance + novelty + time spread → `<work>/candidates/` + `candidates.json` with per-chapter and per-target coverage, the `inputs` block (source identity, transcript and chapters hashes, download cache key), a `states` block, a `cost` block and the contact sheets; `<work>/states.json` holds every state. `--engine legacy` restores the 1.3 scene-detection sampler for comparison.

| | `--tier standard` (default) | `--tier high` |
|---|---|---|
| scene pass | fixed threshold 0.15 | adaptive (median + 8·MAD, floor 0.04) |
| samples per target | 2–3 | 5–6, 3 alternatives kept |
| pool cap / unplanned floor | 48 / 12 | 64 / 16 |
| grab-time refinement | off | sharpest near-duplicate within ±1.5 s (`blurdetect`) |
| overlay (webcam/bar) mask | on (`pip_mask`) | on |
| dedup scope | whole video, family-based (`dedup_scope: family`) | same |
| face demotion | off | on when `cv2` is importable, else reported `unavailable` |
| OCR text density (ranking only) | off | on (ffmpeg `ocr` filter; tesseract) |
| image tokens, 16:9 source | 48 × 209 ≈ 10k nominal (measured 10.5k with the reserved-frame lift) | 64 × 209 ≈ 13.4k nominal (measured 15.9k) |

Measured on the 18-minute screencast used for every release (12 chapters, 24 targets, Apple Silicon): `standard` 50 candidates in 26 s ≈ 9.9k image tokens; `high` 76 candidates in 69 s ≈ 15k image tokens (OCR on 45 cluster frames), grab 29 s, PDF 3 s. Sharpness refinement moved 0/20 frames there — a crisp screen recording has nothing sharper to offer; the gate earns its keep on camera-recorded slides, whiteboards and transitions.

Use `high` when the user asks for it, when a target stays unresolved, when the screen changes within a second, or when an action result is ambiguous. `--mode light|advanced` remain as aliases of the two tiers. `--strips` additionally renders 256px temporal strips for a cheaper first look — off by default because slide text is not legible at that size.

The report's **cost line** states the tier, the image-token estimate from the candidates' real dimensions, the CPU passes, and the other tier's ceiling. Quote it to the user if they ask what a run costs.

**Token guards (hard, enforced by the script).** A video over 120 minutes stops with exit 8 — ask the user before re-running with `--allow-long` (and `--sections`). The image spend is budgeted before you look at anything: `--max-image-tokens` (default `SUMMARY_MAX_IMAGE_TOKENS` in `~/.config/summarize-video/.env`, else 12,000 `standard` / 20,000 `high`) fixes how many shortlist frames will be decoded, and the report's **Token budget** line says the plan. Exit 7 = even the contact sheets do not fit: tell the user the number and let them raise the budget or lower `--max-candidates`; never pass `--allow-over-budget` on your own.

**Coverage gate.** `unresolved` (a `needs_frames` chapter or a target with no candidate) is exit 9; `--allow-unresolved` exists for the benchmark only. `--visual-content none` is passed by the controller after an explicit no-visuals decision and turns an all-`needs_frames: false` chapters file into a recorded `no_visual_chapters` outcome instead of an error.

## Provenance (adapted work)

Frame-engine internals (scene detection, pts stamps, thumbnail dedup) are adapted from `bradautomates/claude-video` (MIT); the sharpness gate and "content gap" targeting follow `CZX2244/dsh-bilibili`; face demotion follows `ConflictHQ/PlanOpticon`.
