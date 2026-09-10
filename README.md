# Visual Video Summarizer

[![CI](https://github.com/yosishe/visual-video-summarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/yosishe/visual-video-summarizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Reliable acquisition and local transcription

The [engineering audit and validation package](docs/audits/2026-09-08-reliability/README.md) records the redesign decisions, benchmark measurements and remaining validation limits.

The controller now reuses validated captions/media/chunks, preserves failure categories, and applies at most three eligible attempts with a 60-second total wait budget. Cached completed runs make no acquisition or transcription calls. Read [the decision and failure matrices](references/failures.md) before diagnosing a provider block; 403 is not proof of an outdated downloader.

For an already installed whisper.cpp and compatible multilingual model:

```text
python scripts/workflow.py init "<YouTube URL or local recording>" --work "<work>" --whisper local --local-model "<ggml-model.bin>"
python scripts/workflow.py run --work "<work>" --json
```

`LOCAL_WHISPER_MODEL` can configure local fallback. Captions are preferred when available. Software/model downloads require approval; cloud audio upload still requires the explicit `--whisper groq|openai` choice. `--no-whisper` disables all ASR. `--cache-dir` is an explicit shared-cache option; default caches stay inside the run.

`run --json` returns one compact routing result while full reports remain on disk. `init --force` for the same source preserves omitted language/PDF/transcription options. Missing chunks pause delivery; explicit acceptance permits only visibly PARTIAL output. Missing PDF support permits HTML with PDF still outstanding.

## ⁧סיכום עם תמונות מקישור ליוטיוב — מתחילים כאן⁩

⁧יש לכם **⁦Codex⁩, ⁦Claude Code⁩ או ⁦Antigravity⁩** עם אפשרות להריץ כלים במחשב? אפשר לתת לסוכן להכין ולהפעיל את הסקיל עבורכם. **אין צורך בשרת שאתם מארחים, בכתיבת קוד או בהעלאת קובץ הסרטון.** הסוכן מוריד את החומר מקישור ליוטיוב ומעבד אותו בסביבתכם, בכפוף לגישה לסרטון ולכלים הדרושים.⁩

1. ⁧פותחים אחד מהסוכנים האלה בתיקיית עבודה שבחרתם, עם אפשרות להרצת כלים מקומית.⁩
2. ⁧מעתיקים את [הודעת ההתחלה](AGENT-START.txt), מדביקים בשיחה ומוסיפים קישור **לסרטון ביוטיוב**.⁩
3. ⁧אם חסרה תוכנה, הסוכן יסביר מה צריך להתקין ויבקש אישור. אחרי ההכנה הוא ימשיך מאותו קישור וייצור סיכום עם תמונות מקוריות כקובץ שאפשר לפתוח.⁩

⁧[מדריך קצר לכל שלושת הסוכנים ופתרון תקלות](CHAT-GUIDE.he.md) · [הודעת התחלה מוכנה](AGENT-START.txt)⁩

⁧**הגעתם דרך צ׳אט רגיל של ⁦ChatGPT⁩ או ⁦Gemini⁩?** [ההוראות לצ׳אט](CHAT-PROMPT.txt) יעזרו להעביר את הבקשה לסוכן המתאים, עם אותו קישור. קישור למאגר או שם המוצר לבדם אינם מקנים גישה להורדה ולחילוץ תמונות.⁩

**Use your own agent:** paste [AGENT-START.txt](AGENT-START.txt) into Codex, Claude Code, or Antigravity in a local task, then add a YouTube URL. The agent checks its tools, requests approval for missing dependencies, and follows the full frame-verification pipeline. No maintainer-hosted service or extra transcription API key is needed for accessible captioned videos. Your agent account and its usage limits still apply. CI runs the controller end to end on Linux, macOS and Windows with a real ffmpeg and a fake downloader; the pipeline has not been validated with every host agent.

**For an assistant receiving this repository link:** for a summarization request, ask only for the YouTube URL if it is missing. If you have shell, file, network and image-review capabilities, read [SKILL.md](SKILL.md) and use its setup route; a missing executable is a setup issue, not a reason to abandon the request. Obtain approval before installing or updating software. If this session lacks those capabilities, use [CHAT-PROMPT.txt](CHAT-PROMPT.txt) to provide a copy-ready handoff to the user's own agent. Preserve their video URL and requested output. Do not silently substitute a transcript-only summary or demand a video upload. Repository review, editing and installation requests keep their own scope.

## Full illustrated reports with a local agent

**Turn lectures, tutorials, and demos into clear takeaways and illustrated study notes—with timestamps back to the source.**

Give `/summarize-video` a YouTube URL or a local recording. It creates a concise opening summary, main points and takeaways, then detailed chapters with original video frames beside the explanations they support. Open the result as a single HTML file; add PDF when you need a printable copy. Hebrew with RTL layout is the default; use `--lang en` for English.

Built for learners who need to understand an argument **and** see the slide, code, diagram, or demonstration behind it. Best with clear speech and useful on-screen material. It needs a transcript; a silent video or inaccessible source cannot produce a grounded lecture summary.

[Get started](#get-started) · [See the output](#what-you-get-with-the-local-engine) · [Data and permissions](SECURITY.md) · [Benchmarks](bench/README.md) · [Report a vulnerability privately](https://github.com/yosishe/visual-video-summarizer/security/advisories/new)

## What you get with the local engine

- **A brief to read first:** a short synthesis, essential points, and supported takeaways. Usually 150–250 words, shorter when appropriate, with source timestamps.
- **Detailed illustrated chapters:** explanations from the transcript, chapter key points, and selected original frames with captions explaining their relevance.
- **Traceable evidence:** text cites transcript segments; frame timestamps come from decoded video; selected frames are checked against the pixels the model reviewed.
- **Portable output:** `summary-ID.html` embeds its images and font. `--pdf` adds `summary-ID.pdf`. The editable `summary-ID/` directory keeps `index.html`, `assets/`, and the evidence manifest.

The screenshot below shows the illustrated chapter layout from an earlier Hebrew screencast run. Current output also includes the opening brief described above.

![Hebrew study notes with chapter links, original video frames and timestamped captions](docs/example-he.png)

The brief preserves the speaker's reasoning, exceptions, and qualifications. It is written after the detailed chapters and checked against its cited segments. The audit catches specific grounding errors; it cannot prove that every explanation is correct or complete.

## Get started

The extraction engine is an **agent skill**: the scripts handle media and evidence checks; your agent reads the images and writes the summary. The easiest route is the [copy-ready starter](AGENT-START.txt). It can run from a reviewed copy in a task folder without registering a permanent skill. The [Hebrew guide](CHAT-GUIDE.he.md) explains the user-facing steps.

### 1. Review and install

Read [SKILL.md](SKILL.md) and [SECURITY.md](SECURITY.md) before running; the [scripts](scripts/) are reviewable and each prints `--help`. For a one-off task, clone into a fresh folder under your chosen workspace. This downloads source without registering a skill or starting a service; it does not install dependencies. Do not overwrite an existing copy.

```bash
git clone https://github.com/yosishe/visual-video-summarizer.git visual-video-summarizer
cd visual-video-summarizer
git rev-parse HEAD
```

For reproducible installations, review a particular commit in GitHub and check out that full commit ID with `git checkout --detach <reviewed-commit>`. Update only after reviewing the incoming changes; the skill never updates itself. See [update and removal guidance](SECURITY.md#updates-and-removal).

**Optional, for repeated use:** after explicit installation approval, place the complete reviewed repository in the appropriate skill folder below. Keep `SKILL.md`, `scripts/`, `references/`, assets and their relative paths together; copying `SKILL.md` alone is insufficient. Choose one scope, check for a pre-existing skill, and do not overwrite or silently replace it.

| Host | Project skill folder | Personal skill folder | How to request a summary |
|---|---|---|---|
| [Codex](https://developers.openai.com/codex/skills) | `.agents/skills/summarize-video/` | `~/.agents/skills/summarize-video/` | Ask to use `summarize-video`; CLI/IDE also support `$summarize-video` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `.claude/skills/summarize-video/` | `~/.claude/skills/summarize-video/` | `/summarize-video` followed by the URL |
| [Antigravity](https://antigravity.google/docs/skills/) | `.agents/skills/summarize-video/` | `~/.gemini/config/skills/summarize-video/` | Ask to use `summarize-video` for the URL |

Paths and invocation conventions checked against the linked official documentation on 2026-09-05. Resolve `~` using the host's actual user directory. Every command is a single Python line (written as `python` below; use `python3` on macOS/Linux where `python` is absent); no shell-specific syntax is required. The fast test job runs on Linux, macOS and Windows; the media job runs the full suite on Linux; a portability job runs the controller end to end with a real ffmpeg and a fake `yt-dlp` on macOS and Windows. This project has not verified the complete pipeline with every host agent. Skill discovery and terminal/network/image access are separate checks. For one-off use, ask the agent to read `SKILL.md` from the actual clone path instead of assuming a slash command exists.

### 2. Check your tools

Required: **Python 3.10+, ffmpeg/ffprobe, and a current yt-dlp** for URLs. Local recordings do not need yt-dlp. The core Python scripts use the standard library; optional enhancements are listed below.

```bash
python scripts/doctor.py
# Machine-readable readiness, including an installed PDF engine:
python scripts/doctor.py --pdf --json
```

The doctor checks installed executable versions and config-file metadata. It does not install packages, read keys, download a video, or upload audio. A missing tool is printed with a platform-appropriate install hint (also under `hint` in `--json`). A successful check confirms prerequisites, not that a particular remote video is accessible.

If dependencies are missing, the agent identifies the missing tools and proposes the hinted setup command. Approve that concrete installation once; the agent can then run it, rerun the doctor, and continue with the same video URL. Do not bypass host permissions or install optional tools by default. You can also install the tools yourself, for example:

```bash
brew install python ffmpeg yt-dlp                              # macOS (Homebrew)
winget install Python.Python.3.12 Gyan.FFmpeg yt-dlp.yt-dlp    # Windows (or the choco/scoop equivalents)
```

On Linux, use your distribution's packages for Python and ffmpeg and the [official yt-dlp installation instructions](https://github.com/yt-dlp/yt-dlp#installation). YouTube extraction may also need the supported JavaScript runtime/components described by yt-dlp. This skill disables remote component downloads and ambient yt-dlp configuration; install necessary components explicitly.

### 3. Make your first summary

In any of the three agents, provide the YouTube URL and ask it to follow the local `SKILL.md` to create an illustrated Hebrew summary. The agent runs `scripts/workflow.py` in a loop, authors the three files it is asked for, and reports completion only after `workflow.py verify` passes. In Claude Code with the skill registered, you can also use:

```text
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID --lang en
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID --lang he --tier high --pdf
```

Use a short, captioned lecture first. No Groq/OpenAI transcription key is needed when captions are available. The agent writes the summary into the current task directory and reports the HTML path. Opening the bundled HTML requires no server or PDF engine.

**Before using private material:** the agent's model provider processes the transcript and the images the agent reads under that provider's settings. Local frame extraction does not make a hosted agent offline. Downloading a URL contacts the source platform and its delivery infrastructure. Audio transcription upload is **off by default**, even when a key exists. [Full data-flow and permission details](SECURITY.md#data-flow).

### Videos without captions and local recordings

Some videos have no downloadable captions. Local transcription can handle them without a provider audio upload when `whisper-cli` and a compatible multilingual GGML model are installed. If the doctor says the model is unconfigured, first reuse an existing compatible model from your local setup; that status does not mean you need to download another model. Register its path once:

```bash
python scripts/workflow.py configure-local --local-model "<existing-model.bin>"
```

Subsequent default runs use that registration when captions are absent. The registration stores only a local path in `~/.config/summarize-video/local-model.json`, separately from credentials. `--no-whisper` still disables transcription; a named cloud provider remains a separate explicit choice. For one run, select a model directly:

```text
python scripts/workflow.py init "<YouTube URL>" --work "<work>" --whisper local --local-model "<existing-model.bin>"
```

If local setup is missing, the agent should explain that option first and request approval for the exact software/model download. A subscription to Codex or Claude does not include a local ASR model or a separate transcription API key. Cloud transcription remains an optional provider choice:

```text
/summarize-video /absolute/path/to/lecture.mp4 --lang en --whisper groq
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID --whisper openai
```

Only choose one after accepting that provider's audio processing and any charges. Configure its matching `GROQ_API_KEY` or `OPENAI_API_KEY` in your environment or in `~/.config/summarize-video/.env` (owner-only permissions: `chmod 600` on macOS/Linux; on Windows keep it under your user profile and restrict it to your account). Do not paste keys into the agent conversation. The skill does not read another skill's credentials or a project's `.env`.

`--no-whisper` disables this fallback and takes precedence if both flags are supplied. Without captions or authorized transcription, the pipeline stops with exit 6; it does not invent a frames-only transcript. Existing installations that relied on automatic upload must now select a provider explicitly.

For Claude Code on the web or another managed cloud environment, check its supplied source-network policy before installing dependencies. A policy denial requires an operator-approved environment change or a permitted local session with the same URL and request. Installing tools, switching downloaders, or choosing an ASR provider cannot repair that network denial. A passing doctor only establishes local tool readiness; its version and skill path identify the copy actually being run.

## How it works

```text
transcript → chapters and visual targets → contact sheets → verified shortlist
           → selected original frames → detailed prose and opening brief
           → evidence audit → HTML and optional PDF
```

A deterministic controller, `scripts/workflow.py`, drives this sequence: `init` records the request, `run` executes every stage it can and stops at the next file only the agent can write (chapters, selections, summary), `verify` proves which stages completed. Each stage refuses a missing, empty, invalid or stale input — a transcript that failed, a chapter citing segments that do not exist, a candidate pool cut from an older transcript, assets grabbed for a different selection — so an incomplete run cannot be reported as a finished one. A video without informative visuals is delivered only after an explicit, recorded no-visuals decision, never by a quiet zero-frame render. `<work>/run.json` and `verify.json` say what actually happened. Details: [references/contracts.md](references/contracts.md#reliability-contract-17).

Text planning happens before image review. Targets reference transcript segment IDs; the engine derives search windows, decoded timestamps, and chapter placement. Scene detection also scans chapters for useful visuals the transcript did not predict. Blank and duplicate candidates are filtered before image review. The model reads the contact sheets once and the verified shortlist once; the selected output frames are re-decoded and checked against those candidates.

The brief adds a quick way into the existing detailed summary. It does not replace chapters or increase their coverage score. Old summaries without a `brief` remain valid. See [the JSON contracts](references/contracts.md) for schemas and [SKILL.md](SKILL.md) for the full workflow.

### Hebrew and English

The model writes directly from the transcript in the requested language. Manual/original caption tracks are preferred, with untranslated automatic captions used when needed; automatic captions can contain errors. The audit checks numbers, identifiers, URLs, cited segment ranges and Hebrew text hygiene, with review notices for some uncertain matches.

The page uses logical CSS and `dir="rtl"` for Hebrew, isolates timestamps and English/code runs, and embeds the Heebo subset under the SIL OFL. PDF export uses an already installed Chrome or WeasyPrint. Neither the skill nor the exporter installs a PDF engine.

### Quality, cost, and optional tools

| Choice | Behavior |
|---|---|
| `--tier standard` | Default, smaller candidate pool and image budget |
| `--tier high` | Denser sampling, adaptive scene threshold, verified sharpness refinement; more CPU and a larger image budget |
| Image budget | Default 12,000 tokens for standard / 20,000 for high; contact sheets and shortlist are sized before reading |
| Long videos | Over 120 minutes requires explicit `--allow-long`; `--sections` can limit extraction |
| `Pillow` | Optional faster box filtering; required for the test suite |
| `opencv-python-headless` | Optional face demotion in high tier |
| ffmpeg OCR support | Optional slide-ranking signal; candidate metadata may retain an OCR excerpt, not just a count |
| Chrome or WeasyPrint | Optional, for PDF export only |

Image token estimates use the project's documented Claude sizing formula; other providers and models can differ. Text/model charges and cloud transcription charges are separate. Budgets limit this workflow's planned image reads, not every action a host agent could take. Reproducible measurements, draft annotation status, and limits are in [bench/README.md](bench/README.md).

## Security you can inspect

The repository adds no telemetry, background service, auto-updater, or permission bypass. Its safeguards include explicit audio-upload selection, fixed transcription endpoints with redirects blocked, isolated yt-dlp options, constrained image paths, hashing of the actual rendered assets, escaped static HTML, a restrictive browser content policy, and regression tests with synthetic secrets and files.

**These are scoped controls, not a sandbox or a security certification.** The host agent, installed tools, model provider, and media decoders remain part of the trust boundary. Source speech, captions, metadata and OCR are untrusted content, never permission to execute instructions. [SECURITY.md](SECURITY.md) maps these claims to code/tests, describes data retention and residual risks, and provides private reporting.

## Troubleshooting

| Symptom | Next step |
|---|---|
| Doctor reports a missing or incompatible dependency | Install/update that tool explicitly, then rerun the doctor. |
| YouTube HTTP 403 / PO Token / JavaScript challenge error | Check current yt-dlp requirements and source access restrictions. Updating alone may not fix every video. Cookies/logins and remote component downloads are not enabled automatically. |
| No transcript / exit 6 | Choose a captioned source, or explicitly authorize a configured `--whisper` provider. Local files use the same opt-in fallback. `transcript.json` records `status: no_transcript` and the reason. |
| Unresolved chapter/target (exit 9) or audit failure (exit 5) | Correct the cited segments or frame selection. Inspect the report; do not bypass the audit to claim completion. |
| Invalid artifact (exit 10) | The message names the file and rows: unknown segment ids, a non-boolean `needs_frames`, an empty chapters file, zero selections for an illustrated request, a chapter that needs frames under a no-visuals decision, a no-visuals decision the visual probe contradicts, a summary in the wrong language, or `init --force` with a different video. For a genuinely non-visual video record `workflow.py decide no-visuals --reason …` (the user's confirmation is `--by user`). |
| Stale binding (exit 11) | A later artifact was made from different inputs, or the request (tier, sections, budget, transcription options), the source or the engine version changed since; `workflow.py run` re-executes the stale stage. Do not edit manifests. |
| `verify` incomplete (exit 12) | The report lists the failing stage; finish it before reporting completion. |
| Source unavailable (exit 13) | The video is private, removed, region-locked or the download was blocked; `transcript.json` records `status: source_unavailable` and the reason. Try another public source or a local recording; cookies and logins are never used automatically. |
| PDF unavailable / exit 4 | Open the HTML. Install a PDF engine yourself if you need PDF. |
| Faces or OCR unavailable | Optional signal absent; other extraction and verification stages still run. |
| Unsafe asset or active HTML rejected | Regenerate from the validated summary and original frames. Do not bundle an arbitrary web page. |

## The workflow loop, and what the stage scripts are for

The agent authors chapters, selections and prose between deterministic stages; the controller runs the rest and refuses to advance past a bad artifact. This is **not** an automatic one-command summarizer — `run` stops at every file only the agent can write:

```bash
python scripts/workflow.py init "<url-or-path>" --work WORK --lang he
python scripts/workflow.py run --work WORK       # repeat: each run ends in NEXT (<stage>) naming the file to author,
                                                 # in a non-zero exit naming the fix, or in the delivery report
python scripts/workflow.py shortlist --work WORK --ids c_0003,c_0011   # the one receipt the agent records by hand
python scripts/workflow.py verify --work WORK    # exit 0 only when every stage is proven (run prints it too)
```

`status`, `next` and `validate <stage>` inspect a run without executing anything; `decide no-visuals --reason …` records the one legitimate zero-frame outcome and is checked against a visual probe of the video (`decide illustrated` reverts); `init --force` keeps the files, re-binds a changed request and re-runs whatever no longer matches (a different video is a fresh work directory). `transcript.py`, `candidates.py`, `shortlist.py`, `grab.py`, `audit_summary.py` and `render.py` are the stages the controller runs; each prints `--help` and enforces the same gates when invoked directly, which is how the benchmark and development use them — but a summary produced that way has no `run.json` bindings and cannot pass `verify`. See [contracts](references/contracts.md#reliability-contract-18), [benchmark instructions](bench/README.md), and [changelog](CHANGELOG.md).

Tests synthesize media fixtures locally (the media classes need ffmpeg; the contact-sheet tests need Pillow) and run in GitHub CI as a fast cross-platform job (Linux, macOS, Windows; Python 3.10 and 3.12; no ffmpeg), a Linux media job (Python 3.10–3.12, the full suite) and a portability job (macOS and Windows with ffmpeg: the stage boundaries and the controller loop through a native fake `yt-dlp`, without `PYTHONUTF8`):

```bash
python -m compileall -q scripts tests bench
python -m unittest discover -s tests -v
```

Contributions are welcome: include a reproducible case and a focused test where appropriate. For visual or language changes, include a before/after output example. Use the private reporting route for vulnerabilities; never attach API keys or private recordings to a public issue.

## Credits and license

Maintained by **yosishe**, with pipeline and review contributions from **OpenAI Codex** and **Claude Code**. Frame-engine internals and the original Whisper helper were adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video); sharpness and targeting ideas draw on [CZX2244/dsh-bilibili](https://github.com/CZX2244/dsh-bilibili), and face demotion on [ConflictHQ/PlanOpticon](https://github.com/ConflictHQ/PlanOpticon). See [LICENSE](LICENSE) (MIT) and [NOTICE](NOTICE) for third-party attributions.
