# visual-video-summarizer

**A `/summarize-video` skill that turns a video into a page worth reading.** It produces a detailed English HTML summary with transcript-aligned, non-duplicate visual evidence beside the exact prose it supports.

```text
captions → chapters + visual targets → temporal strips → candidate IDs
         → verified high-resolution re-grab → deterministic manifest + HTML
```

## Quick start

```bash
brew install ffmpeg yt-dlp
git clone https://github.com/yosishe/visual-video-summarizer ~/.claude/skills/summarize-video
```

Then invoke:

```text
/summarize-video https://www.youtube.com/watch?v=VIDEO_ID
```

Output lands in `summary-<video-id>/` with `index.html`, `manifest.json`, validated full/thumbnail assets, and timestamp links back to YouTube.

## Why this pipeline is precise

- Captions are requested with `yt-dlp --skip-download`; video is downloaded only after visual windows are known.
- Chapters use half-open `[start,end)` intervals, so a frame exactly at a boundary belongs to the next chapter.
- Visual targets reference transcript `seg_id` values. `action_result` targets search after the action instead of using a fixed global offset.
- Each frame records both the requested timestamp and the actual decoded FFmpeg timestamp. Excess seek drift is rejected.
- Near-duplicates use multi-scale luma, edge, and changed-pixel signatures. Dedup is scoped by chapter and semantic target and chooses the best stable representative instead of always keeping the earliest frame.
- Coverage is checked after blank filtering, dedup, and caps. Missing required evidence is reported as `unresolved` and blocks rendering.
- Selections use immutable `candidate_id` values. The deliverable re-grab must visually match the candidate.
- The renderer validates provenance, budgets, duplicates, assets, and HTML placement before producing output.

## Token-efficient triage

`light` is the default. It searches only visual transcript windows, keeps at most two alternatives per target, and caps the pool at 36 candidates.

`advanced` uses adaptive local scene scores, denser action windows, transition recovery, and up to 60 candidates. Partial downloads in both modes use exact cuts so timestamps remain trustworthy. Advanced spends more local compute, not more model context unless the extra alternatives are opened.

Both modes generate small temporal strips. Read those first, then open individual 512px candidates only when selected or uncertain. The manifest reports provider-neutral pixel-area metrics against the former 60-individual-frame baseline instead of claiming a provider-specific token price.

## Requirements and optional configuration

| Requirement | Purpose |
|---|---|
| `ffmpeg` / `ffprobe` | timestamps, scene scores, signatures, frames and audio |
| `yt-dlp` | captions and selective video acquisition |
| Python 3.9+ | standard-library runtime; nothing to install with pip |
| Whisper API key | optional, only for sources without captions |

For captionless sources, put `GROQ_API_KEY` or `OPENAI_API_KEY` in the environment or in `~/.config/summarize-video/.env` with mode `0600`. `~/.config/watch/.env` remains a legacy fallback.

## Standalone workflow

```bash
python3 scripts/transcript.py "<source>" --work WORK

# Author WORK/chapters.json from transcript segment IDs.
python3 scripts/candidates.py "<source>" \
  --work WORK --transcript WORK/transcript.json --chapters WORK/chapters.json \
  --mode light

# Author WORK/selections.json from candidate IDs.
python3 scripts/grab.py \
  --work WORK --spec WORK/selections.json --out-dir summary-VIDEO/assets

# Author WORK/summary.json from transcript segment IDs.
python3 scripts/render.py \
  --work WORK --summary WORK/summary.json --selections WORK/selections.json \
  --assets-dir summary-VIDEO/assets --out-dir summary-VIDEO
```

See [`references/contracts.md`](references/contracts.md) for the JSON contracts. Legacy `--cues` and `--pins` are still accepted, but new runs should use transcript-linked `visual_targets`.

## Security and privacy

`yt-dlp` talks to the public URL supplied by the user. If captions are unavailable and a Whisper key is configured, only the extracted audio is sent to Groq or OpenAI. Video and frames are never uploaded, and `--no-whisper` disables transcription uploads.

The skill reads only the source and its own config; it does not read project `.env` files, browser sessions, cookies, accounts, or unrelated credentials. Keys are never logged or written to output. Files are written only to the temporary work directory and the requested summary directory.

Names and FFmpeg crop expressions are validated before they enter paths or filter graphs.

## Tests

The standard-library suite synthesizes its own FFmpeg fixtures; no third-party media is committed.

```bash
python3 -m unittest discover -s tests -v
```

Tests cover half-open chapter boundaries, semantic dedup, localized UI changes, protected-frame replacement, cache invalidation, overlapping sections, unresolved coverage, light/advanced extraction, re-grab identity, duplicate gates, and deterministic HTML rendering.

## Troubleshooting

- **YouTube HTTP 403 or PO-token warning:** update `yt-dlp` and retry once.
- **No transcript:** configure an optional Whisper key, use `--no-whisper`, or proceed with an explicitly frames-only summary.
- **Unresolved coverage:** correct the target segments/window or rerun that target in `advanced` mode.
- **Grab exits 2 or 3:** fix the reported extraction mismatch, unsafe crop, or duplicate selection; do not bypass the audit.

## Credits

Scene-detection concepts and Whisper plumbing are adapted from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT). See [LICENSE](LICENSE).
