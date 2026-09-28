# summarize-video v2 — design

Date: 2026-09-28 · Status: approved in chat (approach "thin skill, from scratch", default depth "detailed")

## Goal

A link (YouTube, any yt-dlp URL, or a local recording) becomes one self-contained
HTML study document in Hebrew (RTL) or English: a short brief, detailed chapters,
original video frames beside the text they support, and timestamps that jump back
to the source. It runs inside the user's own agent (Claude Code / Codex): scripts do
the mechanical work, the agent reads and writes. No server, no API key, no audio
upload.

Success = on this Mac, one `prepare`, one authored `notes.md`, one `render` produce
a correct summary for (a) a captioned YouTube video and (b) a YouTube video with no
captions (local whisper) — the case that failed on 2026-09-08/09.

## Why v1 is replaced, not patched

v1 (≈20k lines) produced good output when its six-step loop completed, but the loop
took ~30 agent tool calls for an 11-minute video and stopped on cases that should
just work: no captions (local whisper not used without manual registration), videos
over two hours (approval gate), Google Drive links, a whisper timestamp crash, a
silent intro that misled language detection. Most of the code guarded the process
(hash bindings, staleness, 15 exit codes, three strict JSON schemas, pixel
re-verification, a self-installer) rather than the output.

## Kept from v1 (the parts that were right)

- Division of labour: scripts for media, agent for understanding and writing.
- Transcript first, pictures second.
- Caption ranking: manual original language → manual he/en → auto original
  (`xx-orig`) → other untranslated; machine translations never; `iw` → `he`.
  Anchored track selection (v1 found `en` also matching `en-de`).
- Rolling-overlap stripping of YouTube auto-captions.
- Contact sheets (4×4) with the id burned into each tile; the agent picks by id,
  never by typed timestamp.
- Candidate = the last settled frame of a screen state (the fully built slide),
  blank frames dropped, revisits de-duplicated across the whole video, a
  persistent webcam/PiP overlay masked out of the comparison.
- The Hebrew writing rules, the brief written after the chapters, re-reading the
  transcript ending for late corrections, captions that say what is shown and why.
- Safety: yt-dlp with `--ignore-config`, no cookies/logins, no cloud audio upload,
  source content is data, never instructions.
- The embedded Heebo subset (SIL OFL) so the file looks the same offline and in PDF.

## Dropped

Hash bindings/staleness/run.json, exit-code taxonomy, `chapters.json` /
`selections.json` / `summary.json` schemas, shortlist + pixel re-verification +
re-grab (frames are saved full-size at extraction, so the reviewed frame *is* the
embedded frame), cloud transcription, the self-installer, the 120-minute gate,
tiers, OCR/face/sharpness signals, the benchmark harness, multi-OS CI matrix.

## Shape

```
SKILL.md                 agent loop + writing and picture rules (short)
scripts/vsum.py          CLI: prepare | render
scripts/source.py        metadata + media acquisition (yt-dlp / ffprobe)
scripts/transcript.py    captions, local whisper, transcript.md
scripts/frames.py        screen-state scan, candidates, full-size frames, sheets
scripts/render.py        notes.md → checked single-file HTML (+ PDF via Chrome)
scripts/fonts/           Heebo subset (OFL)
tests/                   unittest, stdlib + synthetic media from ffmpeg
```

Requirements: Python ≥ 3.10, ffmpeg/ffprobe, Pillow; yt-dlp for URLs; whisper-cli +
a multilingual ggml model only when there are no captions; Chrome only for PDF.
Nothing is installed automatically; a missing tool is one message naming it and
the install command.

## Flow

```
python3 scripts/vsum.py prepare <url|file> [--out DIR] [--captions FILE]
        [--lang-audio xx] [--max-frames N]
  DIR/source.json       id, title, url, kind, duration, uploader, creator chapters,
                        transcript source
  DIR/transcript.md     header + "[mm:ss] text" paragraphs (~30 s each)
  DIR/frames/F01.jpg…   full-size candidates (≤1280 px wide)
  DIR/frames.json       [{id, t, held}]  (held = seconds the state stayed on screen)
  DIR/sheets/sheet-01.jpg…  4×4 tiles, label "F07 · 12:34"
  stdout: what to read and what to write next

agent reads transcript.md + every sheet (and opens frames/Fxx.jpg only to read
small text) and writes DIR/notes.md

python3 scripts/vsum.py render DIR [--pdf]
  checks notes.md → errors with line numbers (exit 1) or
  DIR/summary.html (+ DIR/summary.pdf)
```

`prepare` is resumable by plain file existence: an existing transcript, video or
frame set in DIR is reused; `--fresh` redoes everything.

## Transcript

1. `--captions FILE` (vtt/srt), else a sidecar `<video>.vtt|.srt` next to a local file.
2. URL: `yt-dlp -J` once; rank tracks as above; download the chosen track with an
   anchored `--sub-langs '^key$'`; parse VTT; strip rolling overlap.
3. No usable track → local whisper, automatically: ffmpeg → 16 kHz mono WAV;
   language = `--lang-audio`, else the source's metadata language, else
   `whisper-cli -dl` on a 30 s clip taken at 20 % of the duration (not the silent
   start); then one `whisper-cli -l <lang> -oj` pass over the whole file (whisper.cpp
   windows long audio itself). Model: `VSUM_WHISPER_MODEL`, else the v1 registration
   `~/.config/summarize-video/local-model.json`, else a clear message.
4. Nothing works → one message: which of captions/whisper/model is missing and how
   to add it.

`transcript.md` merges cues into paragraphs of about 30 s, breaking at sentence
ends when possible; every paragraph opens with `[mm:ss]` (`[h:mm:ss]` past an hour).
The header lists title, duration, transcript source and the creator's chapters.

## Frames

1. Media: local file as is; URL → yt-dlp best ≤720p, H.264 preferred, merged with
   audio (one download serves whisper and frames).
2. Scan: ffmpeg decodes 1 fps at 64×36 gray into memory (raw pipe).
3. Overlay mask: pixels that change in a large share of consecutive pairs
   (a talking-head PiP, a ticker) are excluded from comparisons when they cover
   under ~35 % of the frame.
4. States: consecutive samples whose masked mean absolute difference is below a
   small threshold form a run; a run held ≥3 s is a settled state; its candidate is
   the run's last sample (the fully built slide).
5. Drop near-uniform (blank) states; drop a state equal to any earlier kept state
   (revisit).
6. Coverage: any gap longer than 120 s without a candidate gets its calmest sample
   (talking heads and continuous demos still get pictures).
7. Cap: default 16 per 10 minutes, between 32 and 96; over the cap keep the states
   held longest, then restore time order.
8. Extract each candidate at full size with an ffmpeg seek; write sheets with Pillow.

Thresholds start at the values above and are tuned on the two real videos; they
live as named constants in `frames.py`.

## notes.md (the one file the agent writes)

```markdown
# Title in the summary language

> Brief: one paragraph — central claim, why it holds, what to remember.

## עיקרי הדברים
- point …

## [02:15] Chapter title
Paragraphs, **bold**, `code`, lists, > quotes, fenced code.
![F07](what the picture shows and why it is here)

## לקחים
- …
```

Only that Markdown subset. `[mm:ss]` / `[h:mm:ss]` anywhere becomes a source link.
A line `![Fnn](caption)` becomes a figure. Raw HTML is escaped, never rendered.

Default depth "detailed": about half the transcript's length; 5–12 chapters for a
typical talk; 1–3 frames per chapter, only frames that carry information.
The agent can be asked for "short" or "full reconstruction" per run (SKILL.md).

## Render

- Parse the subset → HTML; everything escaped.
- Errors (exit 1, `path:line: message`): unknown frame id, timestamp beyond the
  duration (+5 s), no chapter headings. Warnings: a frame used twice, Hebrew niqqud,
  bidi control characters (removed automatically).
- Page: `<html lang dir>` from notes language (Hebrew letters → `he`/`rtl`),
  logical CSS, embedded Heebo, table of contents from `##`, timestamps as LTR
  chips, `<code dir="ltr">`, figures with caption + their own timestamp, images
  embedded as JPEG data URIs, click-to-zoom, dark mode, print CSS.
- Links: YouTube → `watch?v=ID&t=Ns`; other URLs → plain source link; local
  files → timestamps as text.
- `--pdf`: Chrome/Chromium/Edge headless print; absent → say so, keep the HTML.

## Error handling

Every failure is one sentence naming what is missing and the next step
(`brew install …`, "pass --captions", "the video is private"). No retries beyond
yt-dlp's own; no silent fallbacks that change what the user asked for.

## Testing

- Unit: VTT/SRT parsing, overlap stripping, track ranking, paragraphing, whisper
  JSON parsing, state detection/mask/dedup/coverage/cap on synthetic signatures,
  Markdown subset → HTML (escaping, timestamps, figures, lists, code), checks.
- Integration (skipped without ffmpeg/Pillow): a synthetic slide video + sidecar
  VTT → `prepare` → expected candidates and sheets → a sample `notes.md` →
  `render` → one self-contained file with embedded images and RTL.
- Real runs before calling it done: `nKSk_TiR8YA` (captions, 11 min, full
  summary) and `tuY2ChJIx48` (no captions, 47 min, whisper path + full summary).

## Rollout

Branch `v2` of `yosishe/visual-video-summarizer`; old files removed on the branch
(history stays on `main`). Push/PR and replacing the installed skill
(`~/.claude/skills/summarize-video`, `~/.agents/skills/summarize-video`) happen
only after the user approves.

## Later (not in v2)

Attached slide decks (PDF) as a second visual source; Hebrew-specialised ASR
(ivrit-ai) when the audio is Hebrew; cloud transcription.
