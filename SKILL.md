---
name: summarize-video
version: "1.4.0"
description: Turn a video (YouTube URL or local file) into a detailed illustrated HTML summary in Hebrew (RTL, default) or English (optionally PDF) - transcript-driven chapters with timestamp-aligned, pixel-verified frames (slides, screens, demos) embedded next to the text they illustrate, each with a caption that says why it is there. Use when the user asks to summarize a video, wants a visual summary or HTML digest of a talk/lecture/screencast/demo, or types /summarize-video <url-or-path> [--tier high] [--lang en] [--pdf].
argument-hint: "<video-url-or-path> [--tier high] [--lang he|en] [--pdf] [notes / focus]"
user-invocable: true
allowed-tools: Bash, Read, Write, AskUserQuestion
license: MIT
homepage: https://github.com/yosishe/visual-video-summarizer
repository: https://github.com/yosishe/visual-video-summarizer
author: yosishe
metadata:
  version: "1.4.0"
  homepage: https://github.com/yosishe/visual-video-summarizer
  repository: https://github.com/yosishe/visual-video-summarizer
  author: yosishe
---

# /summarize-video

Pipeline: **transcript → chapters + visual targets → candidates (512px) → one visual triage by candidate ID → pixel-verified re-grab → rendered HTML → one self-contained file (+ optional PDF).**

The ordering is the point: every text decision happens before an image token is spent; frame placement is derived from decoded timestamps and transcript segment IDs, never typed by hand; the only image spend is one batched Read of the candidates; the selected frames are re-decoded at 1280px and verified against what you saw, without re-reading them.

**Tiers and language.** `--tier standard` (default) or `--tier high`, and `--lang he` (default) or `--lang en`, chosen by the user as arguments — never ask. Parse the arguments: a `--tier high` token means every `candidates.py` call below adds `--tier high` (grab.py picks the tier up from `candidates.json`); `--lang en` means the summary, captions and page are English (otherwise Hebrew, right-to-left, written directly from the transcript — never a translation of an English draft); a `--pdf` token means the final `render.py` call adds `--pdf`. Everything else is the source and free-text focus notes. When `--lang` is absent, `SUMMARY_LANG` in `~/.config/summarize-video/.env` decides, then the user's standing language preference (Hebrew for this user), then `en`; write `summary.json` with the matching `lang` field so the renderer and the audit agree with you. `high` buys accuracy with CPU and a larger triage: adaptive scene scoring, denser sampling, 3 alternatives per target, a 64-frame pool, blurdetect sharpness refinement at grab time (still pixel-verified), face demotion when OpenCV is importable, and OCR text density as a ranking signal for slide completeness. Both tiers print an honest cost line.

Frame-engine internals (scene detection, pts stamps, thumbnail dedup) are adapted from `bradautomates/claude-video` (MIT); the sharpness gate and "content gap" targeting follow `CZX2244/dsh-bilibili`; face demotion follows `ConflictHQ/PlanOpticon`. Whisper keys live in `~/.config/summarize-video/.env` (the `/watch` skill's `~/.config/watch/.env` is read as a legacy fallback).

## Resolve SKILL_DIR

Set `SKILL_DIR` to the absolute path of the directory containing THIS SKILL.md — your harness reported that path when this file was loaded (a plain clone lands at `~/.claude/skills/summarize-video`). The scripts are direct siblings at `SKILL_DIR/scripts/`. Guard once:

```bash
SKILL_DIR="<absolute path of the directory containing this SKILL.md>"
[ -f "$SKILL_DIR/scripts/transcript.py" ] || { echo "scripts not found under $SKILL_DIR" >&2; exit 1; }
```

Prereqs: `ffmpeg`, `ffprobe`, `yt-dlp` (`brew install ffmpeg yt-dlp`). A Whisper key (`GROQ_API_KEY` preferred, or `OPENAI_API_KEY`) is needed only when the source has no captions. JSON contracts for every file you author are in [references/contracts.md](references/contracts.md).

## Step 1 — Transcript (no video download)

```bash
python3 "$SKILL_DIR/scripts/transcript.py" "<source>" --work "<work>"
```

Options: `--langs "<yt-dlp pattern>"` (only to force a specific track) · `--wanted he,en` · `--language xx` (Whisper hint) · `--whisper groq|openai` · `--no-whisper`. Omit `--work` to get a fresh temp dir; the report prints it — use it for every later step.

The caption track is chosen by provenance, not by name: a manual track in the video's own language, then a manual track in Hebrew/English, then the original-language auto-captions (`xx-orig`), then untranslated auto-captions. YouTube's machine-translated tracks are never used — the Hebrew comes from you. (YouTube keys Hebrew as `iw`; the report says `he`.) The report also lists the creator's chapters when the video has them — a prior for Step 2, not a substitute for it.

The report prints the transcript as `seg_NNNN [MM:SS-MM:SS] text`. Those `seg_id`s are the join keys for everything that follows. Exit 6 = no usable captions and no Whisper key: tell the user which key to add (or which `--langs` track to force) — there is no frames-only path; every later step is anchored to segment ids.

## Step 2 — Chapters and visual targets (you, text only — no images yet)

Write `<work>/chapters.json`. Chapter windows are half-open `[start,end)`; only the last chapter owns the exact end.

```json
[{"chapter_id": "ch03", "title": "Export workflow", "start": 120.0, "end": 210.0,
  "needs_frames": true,
  "visual_targets": [
    {"target_id": "ch03_export_result", "kind": "action_result",
     "seg_ids": ["seg_0084", "seg_0085"], "action_seg_id": "seg_0084",
     "why": "the completed export dialog proves the narrated action"}
  ]}]
```

Rules:
- 5–12 chapters for a typical talk; each a coherent topic, not a fixed length.
- **`needs_frames: false` for pure-talk chapters** (intro banter, Q&A logistics, outro). Nothing is extracted there — the cheapest frame is the one you never pay for.
- A target is a moment the transcript says something is on screen: "as you can see", "look at this", "now I click", "notice the graph", a demo action being narrated. Skip rhetorical "look, the point is…". Be restrained — **≤2 targets per chapter** typically. Kinds: `action_result` (search *after* the action segment — the result appears after the words), `state`, `diagram`, `slide` (search inside the referenced segments).
- **The five content gaps** — a stretch of transcript that is incomplete *without* the picture is where a target belongs, whether or not the speaker says "look":
  1. **Dangling reference** — "this one here", "that number", "the second option": the words point at something unnamed.
  2. **Conclusion without its data** — "so it's clearly faster", "the numbers speak for themselves": the claim is on screen, the transcript only asserts it.
  3. **Unspoken operation** — the speaker narrates an action ("I'll just set that up") but not what changed; the result is a `action_result` target.
  4. **Silent demo** — a stretch with sparse or filler speech inside a `needs_frames` chapter; check the coverage table for it and add a `state` target.
  5. **Visual comparison** — "before and after", "these two side by side", "notice the difference".
- `slide` / `diagram` targets land on the **measured** terminal build state: the engine scores scene changes across the target window and samples the last stable frame before the screen flips (boards are drawn while they are discussed). You still reference the segments where it is discussed.
- **Reference segments, never seconds.** `anchor_t` / `window` exist for a known edge case only. The engine derives every timestamp from `seg_ids`; you do not type `--cues` or `--pins` any more.

## Step 3 — Candidates (cheap, 512px)

```bash
python3 "$SKILL_DIR/scripts/candidates.py" "<source>" --work "<work>" \
  --transcript "<work>/transcript.json" --chapters "<work>/chapters.json" --tier standard   # or --tier high
```

What it does: downloads the video once (≤720p; for videos over 20 minutes only the padded ranges of chapters that need frames, with exact cuts) → **scene detection across every `needs_frames` chapter** (so a slide flip nobody predicted still reaches the pool) → a terminal probe per `slide`/`diagram` target (where does the build-up end?) → dense samples around each target (`action_result` at +0.2/+0.8/+1.6s after the action) → chapter-coverage midpoints → every frame is extracted by seeking to its own timestamp with the decoded `actual_t` recorded and drift-checked → blank/black filter with recovery for target frames → **overlay mask**: a persistent picture-in-picture (webcam) or bar is detected once per video from one-second frame pairs and blanked in every signature, so the presenter moving in the corner never makes two frames of the same slide look different (written frames are untouched) → **family dedup across the whole video**: the same picture reached by a target and by a scene cut, or shown again chapters later, is one family; one representative survives per chapter that needs it (target/coverage), revisits are dropped and listed on the keeper → cap (standard 48, high 64), targets and chapter coverage never evicted unless `--max-candidates` forces a hard ceiling → `<work>/candidates/` + `candidates.json` with per-chapter and per-target coverage and a `cost` block.

The report includes a **per-chapter coverage table**. A `needs_frames` chapter showing 1 candidate is a static stretch: if its point is visual, add a target inside its window and re-run. `unresolved` is a failure, not a warning.

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

Measured on the 18-minute screencast used for every release (12 chapters, 24 targets, Apple Silicon): `standard` 50 candidates in 26 s ≈ 9.9k image tokens; `high` 76 candidates in 69 s ≈ 15k image tokens (OCR on 45 cluster frames), grab 29 s, PDF 3 s. Sharpness refinement moved 0/20 frames there — a crisp screen recording has nothing sharper to offer; the gate earns its keep on camera-recorded slides, whiteboards and transitions. The reserved-frame lift means many targets raise the pool above the nominal cap; the report says by how much.

Use `high` when the user asks for it, when a target stays unresolved, when the screen changes within a second, or when an action result is ambiguous. `--mode light|advanced` remain as aliases of the two tiers. `--strips` additionally renders 256px temporal strips for a cheaper first look — off by default because slide text is not legible at that size; accuracy comes first.

The report's **cost line** states the tier, the image-token estimate from the candidates' real dimensions, the CPU passes (scene pass, terminal probes, seeks, OCR frames, faces status, refinement), and the other tier's ceiling. Quote it to the user if they ask what a run costs.

## Step 4 — Triage (the ONLY image spend)

**Read every candidate path in a single message** (parallel Read calls). Then select per chapter:

- **Content test:** keeps information — slide, code, diagram, chart, UI state, demo result. A frame of the presenter's face fails (in `high`, candidates flagged `(people frame)` in the report were already demoted in ranking; still apply the test).
- **Placement test:** its chapter (printed per candidate, derived from the decoded timestamp) is where the transcript discusses it.
- **Novelty test — one frame per board/scene:** when several candidates show the same whiteboard, slide, or screen at different build stages, select ONLY the most complete one (last of the run) — never one per stage, unless the transcript discusses an intermediate stage at length as its own point (then that stage earns its own frame). This applies across chapters too.
- **Quota:** 1–3 per chapter, at most 20 total. A chapter can end with zero. A long chapter with several distinct points deserves its 2–3 frames; do not starve it to hit a low number.
- Assign `role`: `evidence` (proves a number/claim/action) or `illustration` (represents the chapter). Roles shape the caption.
- The report marks candidates that are the same picture (`family=f_003`, "same picture also at 07:14"): pick at most one per family unless two chapters each need it.

Write `<work>/selections.json` **by `candidate_id`** — never by timestamp (the ID resolves to the full-precision decoded time; copying a displayed time is how a rounded-down scene cut once embedded the wrong shot). The caption is an object that says what is shown and **why the picture is here**; `novelty` makes the novelty test auditable:

```json
[{"candidate_id": "c_0017", "name": "ch03_export_result", "chapter_id": "ch03",
  "role": "evidence", "novelty": "new_state",
  "caption": {"shows": "דיאלוג הייצוא שהושלם, עם ההגדרות `PDF` ו-`A4` שנבחרו.",
              "why": "התוצאה של הפעולה שהמרצה מתאר; מוכיחה שהייצוא הצליח.",
              "look_at": "השורה `Export complete` בתחתית."},
  "alt": "דיאלוג ייצוא שהושלם עם הגדרות הפלט שנבחרו",
  "anchor_seg_ids": ["seg_0084", "seg_0085"],
  "crop": "w:h:x:y (optional - only to blow up a small UI region)"}]
```

- `caption.shows`: what is in the picture, one sentence. On-screen UI strings, menu names, commands and numbers go in `backticks`, in English exactly as they appear (the renderer isolates them LTR). `caption.why`: why the picture is here and not just text — "proves the number", "the final state of the board", "the result of the described action". `caption.look_at` (optional): the small detail a reader would miss. `alt` ≤ 125 characters, visual content only, no "image of". In a Hebrew document all three open in Hebrew.
- `novelty`: `new_state` (default), `build_stage` (an intermediate stage kept on purpose — `why` must say what it adds), `reprise` (a picture shown again because a later chapter needs it).
- Optionally write `<work>/triage-rejections.json` — `[{"candidate_id": "c_0004", "reason": "people_frame|duplicate_of:c_0002|no_information|wrong_chapter|build_stage"}]` — you already looked; it costs nothing and it is what makes a missed visual explainable later.

`anchor_seg_ids` are the transcript segments the frame illustrates; the renderer places the figure right after the prose block that cites them.

## Step 5 — Re-grab at deliverable quality (zero tokens)

```bash
python3 "$SKILL_DIR/scripts/grab.py" --work "<work>" --spec "<work>/selections.json" \
  --out-dir "summary-<video-id>/assets"
```

For each selection this re-decodes the source at the candidate's `actual_t`, **verifies the new pixels match the candidate you looked at**, then writes `<name>-full.jpg` (1280px) + `<name>-thumb.jpg` (640px) and `assets-manifest.json` with hashes. Do **not** Read these.

In `high` (or with `--refine sharpness`) grab additionally looks ±1.5 s around the triaged frame, inside its chapter, for the **sharpest frame that is still a near-duplicate of the candidate you saw** (the same predicate as the verification gate), re-decodes it and runs the gate again on the new pixels. A refinement that fails the gate falls back to the triaged frame; refinement can never fail a run. The asset records both `triaged_t` and the written `actual_t`; captions and links use the written time. "Pixel-verified, no second Read" therefore holds in both tiers.

Exit 2 = an extraction failure or unsafe name/crop (fix it); exit 3 = two selections render the same picture (keep the more complete one, fix `selections.json`, re-run). Never work around the audit.

## Step 6 — Write the summary, then render

Write `<work>/summary.json` — the prose, with provenance. Every block cites the segments it synthesizes; a frame is inserted after the first block whose `seg_ids` overlap its `anchor_seg_ids`. Author it **chapters first, overview and key points last** (they are a synthesis of the blocks you already wrote, not a first impression):

```json
{"schema_version": 3, "lang": "he", "source_language": "en",
 "overview": "הטענה של הסרטון במשפט אחד או שניים.",
 "glossary": {"agent": "סוכן", "skill": "skill", "workflow": "זרימת עבודה"},
 "chapters": [{"chapter_id": "ch03", "title": "זרימת הייצוא",
   "blocks": [
     {"block_id": "ch03_b01", "kind": "prose", "text": "סינתזה מפורטת של מה שנאמר…", "seg_ids": ["seg_0084", "seg_0085"]},
     {"block_id": "ch03_b02", "kind": "code", "lang": "bash", "text": "npm run build", "seg_ids": ["seg_0086"]},
     {"block_id": "ch03_b03", "kind": "quote", "text": "Prompts are so late 2025.", "seg_ids": ["seg_0087"]}],
   "key_points": ["עובדה או מסקנה שאפשר לצטט"]}]}
```

`kind` is `prose` (default), `code` (rendered LTR in a `<pre>`), or `quote` (a verbatim line of the speaker, in the source language). Inside prose, `backticks` are the **only** markup: identifiers, commands, file names, UI strings, numbers with units and formulas go in backticks and the renderer isolates them left-to-right — never emit HTML, never bidi control characters.

Then run the audit and fix every `error` (a number, identifier or URL the cited segments do not contain; wrong segment order; niqqud; bidi controls; a non-Hebrew block in a Hebrew document). `review` lines are judgement calls (a name that is in the transcript but not in this block's segments; a negation the block dropped) — decide each one:

```bash
python3 "$SKILL_DIR/scripts/audit_summary.py" --work "<work>" --summary "<work>/summary.json" --selections "<work>/selections.json"
```

### כללי הסיכום בעברית (חלים כאשר `lang` הוא `he`)

**מבנה:** `overview` = משפט אחד או שניים שאומרים מה הסרטון **טוען**, לא על מה הוא "מדבר"; פותחים בעברית. כותרת פרק עד 8 מילים, שם או טענה, לא "הקדמה". בלוק = פסקה של 60 עד 140 מילים שמסכמת רעיון אחד; 2 עד 5 בלוקים לפרק; בלוק שמצטט יותר מ-25 מקטעים דחוס מדי. `key_points` = 2 עד 4 לפרק, כל אחת עובדה או מסקנה שאפשר לצטט, לא כותרת מחדש.

**חובה לשמור:** כל מספר, יחידה, אחוז, טווח וזמן שהדובר אומר, בספרות ("7 עד 10 skills", "כ-300 אלף", "כל 30 דקות"); שמות כלים, מוצרים, חברות, ממשקי API, פקודות, קבצים וכתובות בלטינית כפי שהם — ב-`backticks` כשהם מזהה טכני, בלי backticks כשהם שם מוצר בשטף המשפט; הדוגמאות שהדובר משתמש בהן כדי להוכיח טענה ושרשראות הנימוק ("כי", "ולכן", "אלא אם") — סיכום שמביא מסקנה בלי הסיבה שלה נכשל; הסתייגויות, שלילה ואי-ודאות במשמעותן המדויקת; הגדרות במילות הדובר (ציטוט קצר ב-`kind: "quote"` כשהניסוח עצמו חשוב).

**משמיטים:** ברכות, קריאה למנוי, ספונסרים, "כמו שאתם רואים", חזרות, דיבור על הסרטון עצמו, מילות מילוי; תיאור של מה שקורה על המסך כשיש לזה תמונה בסיכום — הכיתוב עושה את העבודה.

**סגנון:** עברית תקנית בלי ניקוד. משפט עד 25 מילים, רעיון אחד למשפט. בלי מקפים ארוכים — פסיק, נקודתיים או משפט חדש; טווחי מספרים "7 עד 10". מונח שיש לו עברית מקובלת בתעשייה נכתב בעברית (סוכן, שרת, זרימת עבודה, תמליל, פריסה); מונח שהעברית שלו אינה מקובלת נשאר באנגלית (skill, prompt, endpoint, token). לא מתעתקים שמות מוצרים (Notion, לא "נושן"). מונח שחוזר נכתב באותה צורה בכל הסיכום ונרשם ב-`glossary`. תחיליות לפני מילה לטינית עם מקף: ב-Notion, ה-API, ל-GitHub. בלי פנייה בגוף שני; הדובר מכונה בשמו או "הדובר"; ניסוח בלתי-אישי ("ניתן", "מומלץ", "הסרטון מציע"). כל משפט פותח בעברית, לא במונח לועזי — אם המונח הוא הנושא, מקדימים מילה: "הכלי OpenClaw…". לא מתרגמים את התמליל: מסכמים אותו — פחות מילים מהמקור, יותר מבנה. סרטון שמקורו בעברית מסוכם באותם כללים (בלי תרגום, אבל גם בלי הדבקה).

For `--lang en` write detailed English prose under the same structure: synthesize, don't paste the transcript; quote only the lines that matter. Then render — never hand-write the HTML:

```bash
python3 "$SKILL_DIR/scripts/render.py" --work "<work>" --summary "<work>/summary.json" \
  --selections "<work>/selections.json" --assets-dir "summary-<video-id>/assets" \
  --out-dir "summary-<video-id>"            # add --pdf when the user asked for a PDF; --lang en for English
```

The renderer runs the audit again and refuses (exit 5) while errors remain. Hebrew documents come out right-to-left with a subset of the Heebo typeface embedded (the file renders the same offline), English terms, code, timestamps and ranges isolated left-to-right, and the video title in its own direction.

The renderer validates chapter ownership, segment provenance, budgets, coverage, duplicates and assets, then writes `manifest.json` (the source of truth), a designed `index.html` (TOC, claim box, chapter sections with timestamp links to YouTube, side-by-side figures), and — automatically — **`summary-<video-id>.html`: one self-contained file with every image embedded**. That single file is the deliverable the user opens and shares; no server is ever needed to view it. The directory stays as the editable source — change a caption or a frame, re-run render.

`--pdf` prints that single file to **`summary-<video-id>.pdf`** next to it (A4, figures never split across pages) via Google Chrome headless, or WeasyPrint when Chrome is absent. Exit 4 = no PDF engine on this machine — tell the user, and still deliver the HTML.

## Step 7 — Verify & clean up

- `grab.py` and `render.py` exited 0; the bundle line reports all images embedded. Send the user the single `summary-<video-id>.html` (and the `.pdf` if requested).
- Delete the expensive intermediates: `<work>/candidates/` and `<work>/download/`. Keep the JSON files (transcript, chapters, candidates, selections, summary) until the session ends — a changed frame needs only Steps 4→5→6 again.
- Follow-up questions about the same video: answer from context; do not re-run anything.

## Token notes

- Candidates: one batched Read of the pool (48 / 64 nominal; reserved target frames may lift it — the report says by how much) at 512px. Cost per image is `⌈w/28⌉ × ⌈h/28⌉` visual tokens (Claude vision docs): 209 for a 16:9 candidate (512×288), 262 for 4:3, 627 for a vertical Short. The report prints the exact figure for the run. Transcript: a few thousand tokens.
- Never Read the `-full.jpg` outputs. Candidate resolution is capped at 512px; legibility in the *deliverable* comes from the 1280px re-grab (or a `crop`).

## Security & Permissions

**What this skill does:**
- Runs `yt-dlp` locally to fetch captions/metadata and download the video — network requests go only to the host the given URL points at (public data; no logins, no cookies, no posting)
- Runs `ffmpeg` / `ffprobe` locally to extract frames and, when Whisper is needed, a mono 16 kHz audio track
- Sends that extracted **audio only** to `api.groq.com` or `api.openai.com` — and only when the source has no captions AND the user has configured a Whisper API key (`--no-whisper` disables this entirely)
- Reads its own config at `~/.config/summarize-video/.env` (legacy fallback: `~/.config/watch/.env`) and the env vars `GROQ_API_KEY` / `OPENAI_API_KEY`
- Writes to a temp working directory and to `summary-<video-id>/` + `summary-<video-id>.html` (+ `.pdf` with `--pdf`) in the current directory — nowhere else
- With `--pdf`, launches a local headless Chrome (or WeasyPrint) on the generated file only; with `--tier high`, runs ffmpeg's `ocr` filter (tesseract, local) on the candidate frames and imports OpenCV **only if it is already installed**

**What this skill does NOT do:**
- Never uploads the video or frames to any API — the only outbound data is the audio clip for optional transcription
- Never reads `.env` files from the current directory or any project folder
- Never logs, prints, or stores API keys; each key is sent only to its own provider
- Never accesses accounts, browsers, or credentials; selection names and crop expressions are validated before they touch a path or an ffmpeg filter graph
- Never installs anything: OCR text is used only as a per-frame character count for ranking and is not written to any output

Review the bundled scripts before first use — they are dependency-free Python (stdlib + the ffmpeg/yt-dlp binaries). Optional: `opencv-python-headless` enables face demotion in `--tier high`; when absent the report says `faces: unavailable` and everything else runs.

## Failure modes

- **YouTube HTTP 403 / "PO Token" warnings**: yt-dlp is outdated — `brew upgrade yt-dlp` (or the platform equivalent) and retry once.
- **Download fails** (login/region-locked): report yt-dlp's stderr plainly; don't retry in a loop and don't use cookies without explicit authorization.
- **transcript.py exit 6** (no usable captions, no Whisper key): tell the user which key to add to `~/.config/summarize-video/.env`, or which `--langs` track to force; there is no frames-only path.
- **render.py exit 5** (audit errors): open `<work>/audit.json`, fix the summary or captions (numbers and identifiers must come from the cited segments), re-run render.
- **A required chapter/target is `unresolved`**: correct its segments or window, or re-run in `--tier high`; do not render around it.
- **Section download rejected** (exact cut failed): re-run without `--sections` for a full download.
- **grab.py exit 2/3**: fix the named extraction mismatch, unsafe name/crop, or duplicate selection.
- **render.py exit 4** (`--pdf`, no engine): deliver the HTML and tell the user a PDF needs Google Chrome or WeasyPrint.
- **`faces: unavailable` / `OCR: unavailable` in a `high` report**: informational — the optional signal is missing on this machine; the run is still valid.
