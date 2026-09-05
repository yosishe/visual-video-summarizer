# Failure modes and exit codes

`workflow.py run` propagates the failing stage's exit code and prints a `NEXT` block; `workflow.py status --work "<work>"` shows every stage. Never work around a gate: the codes below are the workflow refusing to claim something it cannot prove.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | done, or waiting for a model-authored file (`NEXT` says which) | author it, run again |
| 1 | any other script error (message on stderr) | read the message; a tool or media problem |
| 2 | `grab.py`/`shortlist.py`: an extraction failure, unsafe name/crop, or a frame that failed the pixel gate | fix the named selection or drop the id |
| 3 | `grab.py`: two selections render the same picture | keep the more complete one, fix `selections.json` |
| 4 | `render.py --pdf`: no PDF engine (Chrome/Edge/WeasyPrint) | deliver the HTML; tell the user a PDF needs an installed engine |
| 5 | audit errors in `summary.json` | open `<work>/audit.json`, fix the summary (numbers, identifiers and segment references must come from the cited segments), run again |
| 6 | no usable transcript (`transcript.json` says `status: no_transcript` and why) | explain the missing captions and the explicit `--whisper groq|openai` upload choice with its provider key, or a `--langs` track; there is no frames-only path |
| 7 | `candidates.py`: over the image-token budget | quote the planned and budgeted numbers; the user decides between a higher `--max-image-tokens`, a smaller `--max-candidates`, or `--tier standard` |
| 8 | `candidates.py`: video over 120 minutes | tell the user the length; re-run only with their `--allow-long`, preferably with `--sections` |
| 9 | unresolved visual coverage: a `needs_frames` chapter or a target has no candidate | correct its segments or window, or re-run in `--tier high`; do not render around it |
| 10 | an artifact is structurally invalid (transcript missing/empty, chapters, selections, summary shape) or the request is illustrated but no chapter needs frames | the message names the file and the rows; for a genuinely non-visual video record `workflow.py decide no-visuals --reason "…"` |
| 11 | a stale binding: a downstream artifact was made from different inputs (transcript, chapters, candidate pool, selected frames, download cache) | `workflow.py run` re-executes the stale stage; never hand-edit a manifest to make hashes match |
| 12 | `workflow.py verify`: delivery incomplete | the report lists the failing stage; finish it |

Other stops and messages:

- **YouTube HTTP 403 / "PO Token" warnings**: yt-dlp is outdated — update it with the platform's package manager (the doctor prints a platform-appropriate hint) after the user approves, and retry once.
- **Download fails** (login/region-locked): report yt-dlp's stderr plainly; don't retry in a loop and don't use cookies without explicit authorization. A passing doctor proves installed tools, not access to this source.
- **Section download rejected** (exact cut failed): re-run without `--sections` for a full download.
- **`faces: unavailable` / `OCR: unavailable` in a `high` report**: informational — the optional signal is missing on this machine; the run is still valid.
- **Contact sheets `font: default`**: no TrueType monospace font was found; burned-in ids may be less legible — read any doubtful tile individually.
- **`the dense scan decoded zero frames`**: ffmpeg could not read the download; check the ffmpeg messages above it, not the chapters.
- **Transcript health warnings** (low coverage, large gaps, repetition, skipped transcription chunks): the transcript is thin; say so in the summary's limitations rather than inventing coverage.
