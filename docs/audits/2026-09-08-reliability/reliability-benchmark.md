# Reliability benchmark

Deterministic offline decision benchmark. All reliability values below are measured from real functions with mocked network/provider/sleep boundaries. No media, ASR, model, or network call ran.

- Baseline: `b52d26811b9f8bac99e0ce217482903375a05923`
- Current: `b52d26811b9f8bac99e0ce217482903375a05923` with dirty fingerprint `c5778e462cfc1108ce35a80765dc6d77e6b1e1f0deb479b784599fdbd335bf2b`
- Harness latency: baseline `0.023543s`, current `0.025206s` (measurement overhead only; not media wall-clock or API billing)

| Scenario | Baseline measured | Current measured |
|---|---:|---:|
| `caption_429_no_automatic_asr` | returned; metadata 1, caption HTTP 1 | rate_limit; metadata 1, caption HTTP 1 |
| `caption_429_pipeline_fallback` | media 1, ASR 1; status ok | media 0, ASR 0; status acquisition_failed |
| `permanent_401_four_chunks` | provider calls 4/4 | provider calls 1/4 |
| `transient_5xx_bounded_attempts` | provider calls 4 | provider calls 3 |
| `retry_after_http_date` | HTTP-date unsupported | delay 10.0s |
| `chunk_cache_resume` | chunk cache unsupported | resume calls 0 |
| `malformed_provider_response` | accepted | rejected |
| `unknown_upload_outcome` | calls across two invocations 8 | calls across two invocations 1 |

## Frozen six-video corpus

The committed `2026-09-04-v15-high` scorer corpus and human annotations were replayed in temporary copies. This is a scorer regression check, not a new download, ASR, frame extraction, or human-quality evaluation.

- Baseline: 6 videos, score hash `420fc672bac9335746388258506dde01def3f9c09ada07453eec0baf59a8b263`, rc 0
- Current: 6 videos, score hash `420fc672bac9335746388258506dde01def3f9c09ada07453eec0baf59a8b263`, rc 0
- Unchanged scorer output: `true`

## Static skill size

Exact file measurements and a rough characters/4 estimate. The estimate is not tokenizer output or measured model usage.

| Snapshot | UTF-8 bytes (measured) | Unicode characters (measured) | Characters/4 (estimated) |
|---|---:|---:|---:|
| Baseline | 10508 | 10447 | 2611.75 |
| Current | 8913 | 8897 | 2224.25 |

## Interpretation and limits

Decision assertions: `true` (13/13 pass).

Measured: metadata and selected-caption attempts, CLI fallback activation, provider calls, retry attempts, classifications, chunk-cache reuse, malformed-response rejection, HTTP-date parsing, and frozen-corpus score identity. The redesign preserves the selected-caption 429 as a rate-limit failure and does not start media/ASR fallback, stops provider-wide 401 failures after one chunk, bounds 5xx retries at three, resumes cached chunks with zero calls, parses HTTP-date Retry-After, rejects untimed responses, and prevents an automatic repeat after an unknown upload outcome.

Inferred: these call reductions should reduce wasted provider work and duplicate-upload risk. Dollar cost is `null`: no provider pricing, media duration, encoded bytes, or live billing data were measured.

Unavailable here: live source behavior, real media wall-clock, API billing, ASR quality, model quality, and new frame correctness. The quality result only proves identical scoring over the frozen committed artifacts.
