# Failure modes and exit codes

## Reported-run recovery

- No caption tracks: distinguish the downloader's empty inventory from a failed request. With no configured backend, explain local `whisper-cli` plus a compatible multilingual model first; request approval before downloading missing software/models. Cloud is a separate explicit choice, not the default suggested provider. Respect `--no-whisper` and do not suggest uploading when it is set.
- Explicit proxy/tunnel policy denial: `environment_blocked` identifies the execution environment, not a deleted video. Preserve the same URL and requested output and name the two fixes: allow `youtube.com`, `*.youtube.com`, `*.googlevideo.com` and `*.ytimg.com` in the environment's network settings (a new session picks it up), or run the same request in a local agent session. Do not install more downloaders, try unrelated hosts or switch transcription providers.
- Missing tools are installed for the current user before the first stage (`bootstrap.py`, recorded under `setup` in `run.json`). A `NEXT (preflight, blocked)` after that names the setup error (`setup: ffmpeg: …`) and the platform hint; the remaining options are `bootstrap.py --system` (host package manager, needs the user's approval) or a manual install. Do not retry the same failed setup without a change.
- Windows caption caches written by the earlier implementation may contain doubled carriage returns. Validated acquisition now rejects unusable cached cues and reacquires the selected track under the normal bounded policy; no manual receipt editing is needed.
- Record the doctor-reported engine version and skill directory when debugging. An installed copy does not automatically follow a repository branch. Local tool readiness and a stopped run do not establish successful summary delivery.

`workflow.py run` propagates the failing stage's exit code and prints a `NEXT` block; `workflow.py status --work "<work>"` shows every stage. Never work around a gate: the codes below are the workflow refusing to claim something it cannot prove.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | done, or waiting for a model-authored file (`NEXT` says which) | author it, run again |
| 1 | a tool problem: **preflight** found a required tool missing after the user-level setup ran (`NEXT (preflight, blocked)` with the `setup:` error and an install `hint`), or any other script error (message on stderr) | read the setup error; a system-wide install (`bootstrap.py --system` or the hint) needs the user's approval; then run again |
| 2 | `grab.py`/`shortlist.py`: an extraction failure, unsafe name/crop, or a frame that failed the pixel gate | fix the named selection or drop the id |
| 3 | `grab.py`: two selections render the same picture | keep the more complete one, fix `selections.json` |
| 4 | `render.py --pdf`: no PDF engine (Chrome/Edge/WeasyPrint) | deliver the HTML; tell the user a PDF needs an installed engine |
| 5 | audit errors in `summary.json` | open `<work>/audit.json`, fix the summary (numbers, identifiers and segment references must come from the cited segments), run again |
| 6 | no usable transcript (`transcript.json` says `status: no_transcript` and why) | explain the missing captions and the explicit `--whisper groq|openai` upload choice with its provider key, or a `--langs` track; there is no frames-only path |
| 7 | `candidates.py`: over the image-token budget | quote the planned and budgeted numbers; the user decides between a higher `--max-image-tokens`, a smaller `--max-candidates`, or `--tier standard` |
| 8 | source over 120 minutes, checked before audio acquisition/transcription and frame processing | tell the user the length; re-run only with their `--allow-long`, preferably with `--sections` |
| 9 | unresolved visual coverage: a `needs_frames` chapter or a target has no candidate | correct its segments or window, or re-run in the high tier (`init --force --tier high`, then `run`); do not render around it |
| 10 | an artifact is structurally invalid (transcript missing/empty, chapters, selections, summary shape or a `lang` that is not the request's), the request is illustrated but no chapter needs frames, a chapter needs frames under a recorded no-visuals decision, the visual probe contradicts a model's no-visuals decision, or `init --force` names a different source | the message names the file and the rows; for a genuinely non-visual video record `workflow.py decide no-visuals --reason "…"` (the user's confirmation is `--by user`); `decide illustrated` reverts; a different video is a fresh work directory |
| 11 | a stale binding: a downstream artifact was made from different inputs — transcript, chapters, candidate pool, selected frames, download cache, the request options (tier, sections, budget, transcription), the source identity, the engine version, or a bundle that no longer matches its manifest | `workflow.py run` re-executes the stale stage (`init --force` re-binds a changed request); never hand-edit a manifest to make hashes match |
| 12 | `workflow.py verify`: delivery incomplete | the report lists the failing stage; finish it |
| 14 | acquisition failed, deferred, or uncertain (`acquisition_error` gives the category/outcome) | read the matrix below; preserve work; explicit retry only after repair/cooldown |
| 15 | transcription incomplete (`failed_chunks` contains missing ranges) | resume unfinished eligible chunks; only explicit user acceptance permits labelled PARTIAL delivery |
| 13 | the source is unavailable (`transcript.json` says `status: source_unavailable` and why: private, removed, region-locked, download blocked, file unreadable) | tell the user plainly with one practical step (another public link, a local recording, or a separately diagnosed dependency repair); no cookies, logins, other downloaders or retry loops; `run --retry` re-attempts only when the user says it should work now |

Other stops and messages:

- **YouTube HTTP 403 / "PO Token" warnings**: these may indicate access restrictions or provider requirements; they do not establish an outdated downloader. Stop and preserve the error. A capability/version check may identify an upgrade separately; installation still requires approval. Do not add another scraper, cookies, proxies or anti-abuse workarounds.
- **Download fails** (login/region-locked): the transcript stage records it as exit 13 with the sanitised reason; report it plainly; don't retry in a loop and don't use cookies without explicit authorization. A passing doctor proves installed tools, not access to this source.
- **`decide no-visuals` refused by the probe**: the video holds still on content (slides, UI, a drawn board, photos) for longer than the threshold; the message lists the spans and their chapters. Mark those chapters `needs_frames: true` with a target inside the span, or — only when the user confirms — record `decide no-visuals --by user`. **Probe unavailable** (no source or no cached media) is a warning. Typed acquisition restrictions and failures stop the probe; they cannot be used to manufacture a no-visuals completion.
- **Section download rejected** (exact cut failed): re-run without `--sections` for a full download.
- **`faces: unavailable` / `OCR: unavailable` in a `high` report**: informational — the optional signal is missing on this machine; the run is still valid.
- **Contact sheets `font: default`**: no TrueType monospace font was found; burned-in ids may be less legible — read any doubtful tile individually.
- **`the dense scan decoded zero frames`**: ffmpeg could not read the download; check the ffmpeg messages above it, not the chapters.
- **Transcript health `thin`** (low coverage, large gaps, sparse or empty segments, a machine-translated track, skipped transcription chunks): the transcript under-represents the speech; say so in the summary's limitations rather than inventing coverage. The one-line health summary in the report and in `verify` is what to quote.


## Acquisition failure matrix (1.9)

The application is the sole retry owner. A retryable operation gets **three attempts total**, never three retries. Backoff is exponential with jitter; valid `Retry-After` seconds or HTTP dates set the minimum delay. Waiting across attempts is capped at **60 seconds**. A longer delay writes `next_retry_at` and `outcome: deferred`; resuming before it performs no request. yt-dlp's extraction, fragment, file and download retries are zero; concurrent fragments is one. Discovery has a 120-second deadline, media/local inference 1,800 seconds, provider upload 300 seconds, and controller child execution 3,600 seconds. Local helper commands also have finite defaults.

| Category | Detection | Retry / stop | Fallback and retained evidence | User action |
|---|---|---|---|---|
| Captions absent | valid inventory with no eligible track | no request retry | configured local model or explicitly named cloud provider; inventory retained | configure local model or select cloud upload if wanted |
| temporary_network | timeout/network failure, HTTP 408/5xx | up to 3 total; jitter/Retry-After | retain successful chunks/media; no automatic alternate provider | explicit resume after budget exhausted |
| rate_limit | 429 without quota code | same bounded policy; durable cooldown | no transcription fallback from caption 429 | wait until recorded deadline |
| quota_exceeded | 429 with quota/credit/billing/spend code | one attempt; stop later chunks | checkpoints retained; no alternate paid provider | fix provider quota/spend scope |
| authentication | 401 or missing selected-provider credential | stop | no key guessing or key display | configure the selected provider legitimately |
| authorization / access_restricted | 403, login/CAPTCHA/PO-token restrictions | stop | no scraper/provider cascade | authorized alternative source or provider access repair |
| expired_resource | selected resource 403/410 with expired `expire` timestamp | one recorded inventory refresh for that URL, same track | refresh counts as acquisition; changed/missing track stops | repair source if refresh fails |
| source_unavailable | removed/private/not-found/region-unavailable content | exit 13; stop | no upload | another source or local recording |
| malformed_response / invalid_response / empty_response | invalid inventory, VTT/JSON/schema/timestamps, zero usable cues | stop automatic fallback | raw acquisition/checkpoint files remain; a received cloud success that cannot be validated remains uncertain | investigate provider format, then deliberate retry |
| dependency / unsupported_resource / invalid_input | missing/incompatible model/tool, invalid source or oversized body | stop | no automatic installation | approve a specific repair if needed |
| incomplete_acquisition | missing fragment, absent output or wrong exact-cut duration | never adopt partial media | completed sections retain hash-bound checkpoints | explicit resume/repair |
| uncertain_upload | timeout, lost response, interrupted request or uncheckpointed success | **no automatic replay**; later chunks untouched | pending receipt plus successful checkpoints | explicit `run --retry --retry-uncertain` |
| busy | OS work/cache writer lock held | no requests | other process retains ownership; lock releases on exit | wait for its completion; do not delete locks |
| partial | some bounded chunks failed | exit 15; delivery paused | exact ranges + successful chunks retained | resume, or accept those exact gaps |
| unknown | unrecognized tool failure | stop conservatively | preserve local report | diagnose instead of guessing |

`decide partial-transcript --by user --reason "..."` is valid only after the user accepts the listed missing ranges. Its fingerprint binds source, extraction choices, segments and gaps. Changes invalidate acceptance. `verify` returns a labelled PARTIAL delivery (`complete: false`) after all remaining gates pass. Report neither full speech coverage nor COMPLETE.

HTTP-upload success is not task completion. A pending receipt survives malformed success bodies and interruption until a validated provider response is stored atomically. No provider idempotency guarantee is assumed.
