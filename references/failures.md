# Failure modes and exit codes

`workflow.py run` propagates the failing stage's exit code and prints a `NEXT` block; `workflow.py status --work "<work>"` shows every stage. Never work around a gate: the codes below are the workflow refusing to claim something it cannot prove.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | done, or waiting for a model-authored file (`NEXT` says which) | author it, run again |
| 1 | a tool problem: **preflight** found a required tool missing (`NEXT (preflight, blocked)` with an install `hint`), or any other script error (message on stderr) | propose the hinted install, ask the user, then run again |
| 2 | `grab.py`/`shortlist.py`: an extraction failure, unsafe name/crop, or a frame that failed the pixel gate | fix the named selection or drop the id |
| 3 | `grab.py`: two selections render the same picture | keep the more complete one, fix `selections.json` |
| 4 | `render.py --pdf`: no PDF engine (Chrome/Edge/WeasyPrint) | deliver the HTML; tell the user a PDF needs an installed engine |
| 5 | audit errors in `summary.json` | open `<work>/audit.json`, fix the summary (numbers, identifiers and segment references must come from the cited segments), run again |
| 6 | no usable transcript (`transcript.json` says `status: no_transcript` and why) | explain the missing captions and the explicit `--whisper groq|openai` upload choice with its provider key, or a `--langs` track; there is no frames-only path |
| 7 | `candidates.py`: over the image-token budget | quote the planned and budgeted numbers; the user decides between a higher `--max-image-tokens`, a smaller `--max-candidates`, or `--tier standard` |
| 8 | `candidates.py`: video over 120 minutes | tell the user the length; re-run only with their `--allow-long`, preferably with `--sections` |
| 9 | unresolved visual coverage: a `needs_frames` chapter or a target has no candidate | correct its segments or window, or re-run in the high tier (`init --force --tier high`, then `run`); do not render around it |
| 10 | an artifact is structurally invalid (transcript missing/empty, chapters, selections, summary shape or a `lang` that is not the request's), the request is illustrated but no chapter needs frames, a chapter needs frames under a recorded no-visuals decision, the visual probe contradicts a model's no-visuals decision, or `init --force` names a different source | the message names the file and the rows; for a genuinely non-visual video record `workflow.py decide no-visuals --reason "…"` (the user's confirmation is `--by user`); `decide illustrated` reverts; a different video is a fresh work directory |
| 11 | a stale binding: a downstream artifact was made from different inputs — transcript, chapters, candidate pool, selected frames, download cache, the request options (tier, sections, budget, transcription), the source identity, the engine version, or a bundle that no longer matches its manifest | `workflow.py run` re-executes the stale stage (`init --force` re-binds a changed request); never hand-edit a manifest to make hashes match |
| 12 | `workflow.py verify`: delivery incomplete | the report lists the failing stage; finish it |
| 13 | the source is unavailable (`transcript.json` says `status: source_unavailable` and why: private, removed, region-locked, download blocked, file unreadable) | tell the user plainly with one practical step (another public link, a local recording, or their own updated yt-dlp); no cookies, logins, other downloaders or retry loops; `run --retry` re-attempts only when the user says it should work now |

Other stops and messages:

- **YouTube HTTP 403 / "PO Token" warnings**: yt-dlp is outdated — update it with the platform's package manager (the doctor prints a platform-appropriate hint) after the user approves, and retry once.
- **Download fails** (login/region-locked): the transcript stage records it as exit 13 with the sanitised reason; report it plainly; don't retry in a loop and don't use cookies without explicit authorization. A passing doctor proves installed tools, not access to this source.
- **`decide no-visuals` refused by the probe**: the video holds still on content (slides, UI, a drawn board, photos) for longer than the threshold; the message lists the spans and their chapters. Mark those chapters `needs_frames: true` with a target inside the span, or — only when the user confirms — record `decide no-visuals --by user`. **Probe unavailable** (no download possible) is a warning: the decision stands unverified and `verify` says so.
- **Section download rejected** (exact cut failed): re-run without `--sections` for a full download.
- **`faces: unavailable` / `OCR: unavailable` in a `high` report**: informational — the optional signal is missing on this machine; the run is still valid.
- **Contact sheets `font: default`**: no TrueType monospace font was found; burned-in ids may be less legible — read any doubtful tile individually.
- **`the dense scan decoded zero frames`**: ffmpeg could not read the download; check the ffmpeg messages above it, not the chapters.
- **Transcript health `thin`** (low coverage, large gaps, sparse or empty segments, a machine-translated track, skipped transcription chunks): the transcript under-represents the speech; say so in the summary's limitations rather than inventing coverage. The one-line health summary in the report and in `verify` is what to quote.
