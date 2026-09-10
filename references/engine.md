# The engine: transcript, candidates, tiers, cost

`workflow.py run` executes these scripts for you. This file explains what they do and which flags exist; read it when a report or a stop needs interpretation, not before every run.

## Transcript (no video download)

*Development only — `workflow.py run` passes these flags for you; a run started by hand has no `run.json` bindings and cannot pass `verify`:*

`transcript.py "<source>" --work "<work>" [--whisper local|groq|openai] [--local-model <path>] [--no-whisper] [--cache-dir <path>] [--langs <pattern>] [--wanted he,en] [--language xx]`

The controller is canonical; these direct commands are development interfaces. Its order is valid transcript/cache, then captions ranked by provenance: manual original language, manual Hebrew/English, original-language auto captions, then untranslated auto captions. YouTube uses `iw` for Hebrew; output normalizes it to `he`. `--langs` explicitly overrides ranking and records any machine translation truthfully. Caption text stays in transcript.txt/JSON; the default stage report is a concise index and health line.

Metadata is discovered once into an operation-owned directory. The selected VTT URL is fetched directly with bounded HTTP handling; there is no second downloader extraction for captions. A confirmed expired resource URL permits one recorded metadata refresh of the same track. Absent tracks permit ASR; 429, access, malformed replies and empty acquired captions stop. Failures and recovery are specified in [failures.md](failures.md).

Configured local transcription uses an installed `whisper-cli` and compatible multilingual GGML `.bin` model (`--local-model`, `LOCAL_WHISPER_MODEL`, or a registered model). `-l auto` is passed without a source-language hint; English-only `.en` models are rejected. Preflight detects files and CLI capabilities; actual model loading establishes compatibility. Never download software/models automatically. Cloud Groq/OpenAI is an explicit per-source choice even with stored credentials; `--no-whisper` disables every backend. Existing credentials are read only when that provider is selected.

`workflow.py configure-local --local-model <existing-model.bin>` validates existing local setup and atomically records a non-secret model path for future runs. Explicit per-run selection takes precedence over the environment, then the registration. Registration never reads the credential `.env` file. `VSUM_LOCAL_MODEL_CONFIG` selects an isolated registration file for tests or another local setup. A missing or invalid selected model is reported by preflight and fails when local transcription is needed; it does not prevent valid cached transcripts or captions from succeeding. No model is downloaded or silently substituted.

Transcription has at-most-ten-minute checkpoints with two-second look-back, actual encoded audio capped at 24,000,000 bytes and multipart at 25,000,000 bytes. ffmpeg transcodes and stat verifies; byte estimates alone never authorize an upload. Cache identity includes audio hash, range, engine/version, model, language and settings. Local model contents and binary/help identity are hashed. Valid chunks resume with zero ASR calls. Overlap reconciliation requires temporal and textual evidence of duplication and retains unmatched boundary speech. Failed ranges prevent completion; user-bound acceptance permits only visibly PARTIAL output.

Canonical source/track/options/content hashes bind caption inventories, VTT, parsed transcripts and media. Run-local acquisition cache is `<work>/.cache/acquisition`; chunk cache defaults to `<work>/.whisper-cache`. `--cache-dir` explicitly shares these namespaces. No leftover file or legacy unhashed media manifest is adopted simply because it exists. OS advisory locks prevent competing writers and release after crashes. Completed downloaded sections survive a later section failure. Cached evidence is a snapshot; to intentionally refresh changed captions, start with a fresh cache/work directory.

Frames prefer `bv[height<=720]/b[height<=720]`, so sufficient captions do not trigger a separate audio stream. Already acquired combined media is reused after source/hash/stream validation. All downloader operations use one process at a time, one fragment, no nested retries and abort-on-missing-fragment. The 120-minute guard runs before audio download/transcription and before frame processing; `--allow-long` requires the user's decision.

## Tool decision matrix

| Input state / next need | Select | Prerequisites and relative cost | Do not select / stop |
|---|---|---|---|
| Valid complete run | inspect controller state | local validation; zero acquisition/ASR calls | no repeat download, model call or image read |
| Need text; usable cached track | validated cache | matching canonical source/options/hash | corrupt/unbound cache is a miss |
| Need text; no validated inventory | yt-dlp metadata | permitted source, supported local runtime/EJS; one tool attempt, hidden HTTP count unknown | no batch scraping, cloud ASR before discovery |
| Selected caption URL | bounded direct HTTP VTT | cached track identity; normally one HTTP GET | 429/access/schema failure cannot mean absent captions |
| No usable captions; local configured | ffmpeg + whisper.cpp | compatible installed model; local CPU/GPU latency, no audio upload | no automatic model installation/download |
| No captions; cloud explicitly chosen | selected Groq/OpenAI endpoint | user upload choice, key, actual size bound; provider charges/limits | no fallback across providers on auth/quota/errors |
| Illustrated chapters need frames | cached media, then bounded video acquisition + ffmpeg | grounded targets, duration/budget gates; one video stream or existing combined source | no audio download solely for frames |
| Candidate interpretation | host model: sheets then verified shortlist | exactly reported image set, two batched reads | no redundant full-frame image reads |
| Render verified summary | static HTML/bundle | audit/grounding/pixel/localization gates | no evidence-free diagram substitution |
| PDF requested | installed Chrome/Edge, then installed WeasyPrint | rendering gate; local process cost | HTML remains available if engines fail; PDF outstanding |

These are relative costs, not measured provider prices. Source restrictions are correlated across scrapers; another scraper is not an independent recovery path. The official captions download API requires video-edit permission and therefore cannot replace arbitrary-link extraction: [YouTube documentation](https://developers.google.com/youtube/v3/docs/captions/download). Retained yt-dlp extraction is upstream-sensitive and must be permitted for the user's source; public availability alone does not grant every form of automated access.


## Candidates (cheap, 512px)

*Development only — the controller passes these flags; `--allow-*` flags are never yours to add:*

`candidates.py "<source>" --work "<work>" --transcript … --chapters … --tier standard|high [--visual-content illustrated|none] [--decided-by init|model|user] [--allow-unresolved] [--sections S-E,…] [--allow-long] [--max-image-tokens N] [--max-candidates N] [--engine states|legacy] [--strips]`

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

**Coverage gate.** `unresolved` (a `needs_frames` chapter or a target with no candidate) is exit 9; `--allow-unresolved` exists for the benchmark only. `--visual-content none` is passed by the controller after an explicit no-visuals decision and turns an all-`needs_frames: false` chapters file into a recorded `no_visual_chapters` outcome instead of an error — after a **visual probe**: the same ≤720p download (cached under the same key as an illustrated run), one sparse whole-video state scan at `probe_fps` (1 fps), and a verdict on stillness: a picture that holds still for two consecutive samples is content, the dominant still picture (cover image, main shot) is the backdrop, and ≥ min(90 s, 20 % of the video) of still content beyond it *contradicts* the decision. A contradicted model decision is exit 10 with the spans and chapters (the manifest keeps the evidence); a user decision (`--decided-by user`) is recorded with a warning; `--decided-by init` (the user asked for text only) skips the probe. The probe measures stillness, not meaning: a presenter in front of a physical whiteboard never settles and reads as talk, a long still photo of a guest reads as content — `probe.json` makes each case auditable and `--by user` is the override. A chapter that needs frames under a no-visuals decision is refused before any download.

## Provenance (adapted work)

Frame-engine internals (scene detection, pts stamps, thumbnail dedup) are adapted from `bradautomates/claude-video` (MIT); the sharpness gate and "content gap" targeting follow `CZX2244/dsh-bilibili`; face demotion follows `ConflictHQ/PlanOpticon`.
