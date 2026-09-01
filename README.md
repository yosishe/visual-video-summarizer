# Visual Video Summarizer

Private, independently implemented `/summarize-video` skill for producing evidence-linked English HTML summaries from a URL or local video.

The active version 2.0 implementation does not use another video skill's source or runtime. It uses Python's standard library, FFmpeg/FFprobe, and yt-dlp. See [the provenance boundary](references/provenance.md).

## What is different

- Transcript and captions are acquired before video.
- Only transcript-linked visual windows are scanned; pure-speech chapters are skipped.
- The scan uses tiny grayscale frames and per-window motion statistics, not a global scene threshold.
- Action results wait for the first stable non-blank state after a transition.
- Tile-aware comparison preserves small menu, button, code, and status changes.
- Pixel-identical evidence within one chapter is stored once with combined target provenance.
- Candidate IDs are content-addressed and stable across identical reruns.
- Coverage is audited after extraction, deduplication, and budgeting.
- Selected assets are re-decoded and pixel-verified before HTML.
- The renderer refuses omitted targets, unresolved evidence, cross-chapter timestamps, duplicates, provenance gaps, and missing assets.
- The editable page is bundled into one self-contained HTML file only after its manifest and assets validate.

## Runtime

Required commands:

```text
python3
ffmpeg
ffprobe
yt-dlp          # URL sources only
```

No Python package installation is required. Optional audio-only speech transcription uses `GROQ_API_KEY` or `OPENAI_API_KEY` from the environment or `~/.config/summarize-video/.env`.

## Pipeline

```bash
python3 scripts/transcript.py "<source>" --work "<work>"

python3 scripts/candidates.py "<source>" \
  --work "<work>" \
  --transcript "<work>/transcript.json" \
  --chapters "<work>/chapters.json" \
  --mode light

python3 scripts/grab.py \
  --work "<work>" \
  --spec "<work>/selections.json" \
  --out-dir "<summary>/assets"

python3 scripts/render.py \
  --work "<work>" \
  --summary "<work>/summary.json" \
  --selections "<work>/selections.json" \
  --assets-dir "<summary>/assets" \
  --out-dir "<summary>"

python3 scripts/bundle.py "<summary>"
```

`<summary>/` remains the editable, manifest-backed source. The primary shareable deliverable is the adjacent `<summary>.html`, with verified full-resolution frames embedded as data URIs and no sidecar assets.

Use [SKILL.md](SKILL.md) for the operating workflow and [contracts.md](references/contracts.md) for JSON schemas.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

The test suite creates its own synthetic FFmpeg videos. It covers half-open chapter boundaries, progressive state changes, local UI edits, post-action stabilization, black transitions, semantic deduplication, stable IDs, token-budget accounting, cache invalidation, verified re-grab, required-target selection, deterministic rendering, and safe single-file bundling.

Cached real-video benchmark, light mode:

| Metric | Baseline | v2 | Independent v3 |
|---|---:|---:|---:|
| Model-readable candidates | 60 | 36 | 24 |
| Temporal strips | — | 13 | 7 |
| Required chapters/targets unresolved | — | 0 | 0 |
| Conservative projected image-read area vs 60×512 | 100% | not directly comparable | 34.2% |

The v3 estimate counts one 512px verification read for every evidence group, including singleton groups. It is intentionally conservative and provider-neutral.

## Privacy and permissions

- Video and frames remain local.
- Caption discovery requests no video.
- Speech fallback uploads audio only and can be disabled.
- Project `.env`, browsers, cookies, accounts, and unrelated skill directories are never read.
- Login-, cookie-, region-, or DRM-protected acquisition is not attempted without explicit authorization.
- Work files stay under the chosen work/output directories.

## Contributors

- **yosishe** — project owner and maintainer.
- **OpenAI Codex** — implementation, testing, benchmarking, conflict-resolution, and integration assistance.

## License

Private proprietary code. See [LICENSE](LICENSE).
