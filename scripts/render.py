#!/usr/bin/env python3
"""Validate summary evidence and deterministically render manifest.json + HTML.

Output language is a document property (`--lang he|en`, default from
summary.json, then `SUMMARY_LANG`, then `en`): Hebrew documents are rendered
right-to-left per W3C bidi guidance — `dir` on <html>, logical CSS, every
opposite-direction run (timestamps, code, English terms in backticks) tightly
isolated — with a subset of the Heebo typeface embedded so the file renders the
same offline. A deterministic audit (`audit_summary.py`) gates rendering: a
number, identifier or URL in the summary that the cited transcript segments do
not contain is an error, not a warning.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from audit_summary import HEBREW_RE, brief_items, run_audit  # noqa: E402
from bundle import bundle as bundle_summary  # noqa: E402
from frame_utils import chapter_for_time, format_time  # noqa: E402

ENGINE_VERSION = "1.6.0"
MANIFEST_SCHEMA = 3
ROLES = {"evidence", "illustration"}
NOVELTY = {"new_state", "build_stage", "reprise"}
BLOCK_KINDS = {"prose", "code", "quote"}
LANGS = {"he", "en"}
CHROME_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
FONT_DIR = SCRIPT_DIR / "fonts"
CONFIG_ENV = Path.home() / ".config" / "summarize-video" / ".env"
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

STRINGS = {
    "en": {
        "kicker": "Visual video summary",
        "title_suffix": "Visual Summary",
        "claim": "The claim in one line:",
        "brief": "Short summary",
        "main_points": "Main points",
        "takeaways": "Takeaways",
        "brief_sources": "Source timestamps",
        "chapters": "Chapters",
        "watch": "Watch the source video",
        "transcript": "transcript: {source}",
        "frames": "{n} frames selected",
        "frames_of": "{n} frames selected from {m} candidates",
        "footer": "Summary generated from the video's transcript and {n} selected frames",
        "source": "source:",
        "provenance": "frame provenance in",
        "look_at": "Look at:",
    },
    "he": {
        "kicker": "סיכום חזותי של סרטון",
        "title_suffix": "סיכום חזותי",
        "claim": "הטענה במשפט אחד:",
        "brief": "סיכום קצר",
        "main_points": "עיקרי הדברים",
        "takeaways": "תובנות ומסקנות",
        "brief_sources": "זמנים במקור",
        "chapters": "פרקים",
        "watch": "לצפייה בסרטון המקורי",
        "transcript": "מקור התמליל: {source}",
        "frames": "{n} פריימים נבחרו",
        "frames_of": "{n} פריימים נבחרו מתוך {m} מועמדים",
        "footer": "הסיכום נוצר מהתמליל של הסרטון ומ-{n} פריימים נבחרים",
        "source": "מקור:",
        "provenance": "מקור הפריימים מתועד ב-",
        "look_at": "שימו לב:",
    },
}


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def canonical_json_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _env_lang() -> str | None:
    value = os.environ.get("SUMMARY_LANG")
    if value:
        return value.strip().lower()
    try:
        for line in CONFIG_ENV.read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition("=")
            if key.strip() == "SUMMARY_LANG" and raw.strip():
                return raw.strip().strip("'\"").lower()
    except OSError:
        pass
    return None


def resolve_lang(cli: str | None, summary_payload: dict) -> str:
    """CLI flag → summary.json `lang` → SUMMARY_LANG (env or the skill's .env) → en."""
    for value in (cli, summary_payload.get("lang"), _env_lang()):
        if value:
            value = str(value).lower()
            if value not in LANGS:
                raise SystemExit(f"unsupported output language {value!r}; use one of {sorted(LANGS)}")
            return value
    return "en"


def _timestamp_url(source_url: str | None, timestamp: float) -> str | None:
    if not source_url or not source_url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(source_url)
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["t"] = str(int(round(timestamp)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _inline(text: str) -> str:
    """Escape, then turn `backticks` into LTR-isolated code — the only markup
    the model may emit. Everything else stays literal."""
    escaped = html.escape(text)
    return INLINE_CODE_RE.sub(lambda m: f'<code dir="ltr">{m.group(1)}</code>', escaped)


def _normalized_summary(summary_payload: dict, chapters: list[dict], lang: str) -> dict[str, dict]:
    rows = summary_payload.get("chapters", [])
    if not isinstance(rows, list):
        raise SystemExit("summary.json chapters must be an array")
    by_id = {str(row.get("chapter_id")): row for row in rows}
    normalized: dict[str, dict] = {}
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        row = by_id.get(chapter_id)
        if row is None:
            raise SystemExit(f"summary.json is missing chapter {chapter_id}")
        blocks = row.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise SystemExit(f"summary chapter {chapter_id} needs non-empty blocks[]")
        normalized_blocks = []
        for index, block in enumerate(blocks):
            text = str(block.get("text") or "").strip()
            seg_ids = [str(seg_id) for seg_id in block.get("seg_ids", [])]
            kind = str(block.get("kind") or "prose")
            if not text or not seg_ids:
                raise SystemExit(f"{chapter_id} block {index} needs text and seg_ids")
            if kind not in BLOCK_KINDS:
                raise SystemExit(f"{chapter_id} block {index}: unknown kind {kind!r} (prose|code|quote)")
            if lang == "he" and kind == "prose" and not HEBREW_RE.search(text):
                raise SystemExit(f"{chapter_id} block {index}: a Hebrew document needs Hebrew prose here")
            normalized_blocks.append({
                "block_id": str(block.get("block_id") or f"{chapter_id}_b{index + 1:02d}"),
                "kind": kind,
                "lang": str(block.get("lang") or "") or None,
                "text": text,
                "seg_ids": seg_ids,
            })
        normalized[chapter_id] = {
            "chapter_id": chapter_id,
            "title": str(row.get("title") or chapter.get("title") or chapter_id),
            "blocks": normalized_blocks,
            "key_points": [str(point) for point in row.get("key_points", []) if str(point).strip()],
        }
    return normalized


def _caption_fields(selection: dict) -> dict:
    """`caption` is a string (legacy) or {shows, why, look_at}. Returns the
    parts with `shows` always present."""
    raw = selection.get("caption")
    if isinstance(raw, dict):
        shows = str(raw.get("shows") or "").strip()
        why = str(raw.get("why") or "").strip()
        look = str(raw.get("look_at") or "").strip()
        return {"shows": shows, "why": why or None, "look_at": look or None}
    return {"shows": str(raw or "").strip(), "why": None, "look_at": None}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(
    transcript: dict,
    chapters: list[dict],
    candidate_payload: dict,
    selections: list[dict],
    assets_payload: dict,
    summary_payload: dict,
    lang: str = "en",
) -> tuple[dict[str, dict], list[dict], dict[str, dict]]:
    if assets_payload.get("duplicate_pairs"):
        raise SystemExit("assets-manifest.json contains hard duplicate selections")
    if assets_payload.get("failures"):
        raise SystemExit("assets-manifest.json contains extraction failures")
    if len(selections) > 20:
        raise SystemExit("HTML frame budget exceeded: more than 20 selections")
    chapter_map = {chapter["chapter_id"]: chapter for chapter in chapters}
    segment_ids = {str(segment["seg_id"]) for segment in transcript.get("segments", [])}
    candidates = {
        str(candidate.get("candidate_id") or candidate.get("frame_id")): candidate
        for candidate in candidate_payload.get("candidates", [])
    }
    assets = {str(asset["candidate_id"]): asset for asset in assets_payload.get("assets", [])}
    summaries = _normalized_summary(summary_payload, chapters, lang)
    unresolved_required = {
        row["chapter_id"]
        for row in candidate_payload.get("coverage", {}).get("chapters", [])
        if row.get("status") == "unresolved"
        and chapter_map.get(row.get("chapter_id"), {}).get("needs_frames", False)
    }
    if unresolved_required:
        raise SystemExit(
            "Required visual chapters remain unresolved: " + ", ".join(sorted(unresolved_required))
        )
    for chapter_id, summary in summaries.items():
        for block in summary["blocks"]:
            unknown = [seg_id for seg_id in block["seg_ids"] if seg_id not in segment_ids]
            if unknown:
                raise SystemExit(f"{chapter_id}: summary block has unknown segments {unknown}")
    counts = Counter(str(selection.get("chapter_id")) for selection in selections)
    overfull = [chapter_id for chapter_id, count in counts.items() if count > 3]
    if overfull:
        raise SystemExit("Per-chapter frame budget exceeded: " + ", ".join(overfull))

    normalized: list[dict] = []
    for index, selection in enumerate(selections):
        candidate_id = str(selection.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        asset = assets.get(candidate_id)
        if candidate is None or asset is None:
            raise SystemExit(f"selection {index} has no candidate/assets pair: {candidate_id!r}")
        chapter_id = str(selection.get("chapter_id") or candidate.get("chapter_id") or "")
        chapter = chapter_map.get(chapter_id)
        if chapter is None:
            raise SystemExit(f"{candidate_id}: unknown chapter {chapter_id!r}")
        # The caption time is the time of the pixels that were WRITTEN — the
        # asset's — which grab-time refinement may have moved off the triaged
        # candidate's time. Both must sit inside the selection's chapter.
        triaged_t = float(candidate["actual_t"])
        timestamp = float(asset.get("actual_t", triaged_t))
        for label, value in (("candidate", triaged_t), ("asset", timestamp)):
            owning = chapter_for_time(chapters, value)
            if owning is None or owning["chapter_id"] != chapter_id:
                raise SystemExit(
                    f"{candidate_id}: {label} t={value:.3f} belongs to "
                    f"{owning['chapter_id'] if owning else 'no chapter'}, not {chapter_id}"
                )
        role = str(selection.get("role") or "")
        name = str(selection.get("name") or "").strip()
        caption = _caption_fields(selection)
        alt = str(selection.get("alt") or "").strip()
        anchors = [str(seg_id) for seg_id in selection.get("anchor_seg_ids", [])]
        if role not in ROLES or not name or not caption["shows"] or not alt or not anchors:
            raise SystemExit(f"{candidate_id}: name, role, caption, alt and anchor_seg_ids are required")
        novelty = str(selection.get("novelty") or "new_state")
        if novelty not in NOVELTY:
            raise SystemExit(f"{candidate_id}: novelty must be one of {sorted(NOVELTY)}")
        if novelty == "build_stage" and not caption["why"]:
            raise SystemExit(f"{candidate_id}: a build_stage frame must say in caption.why what the stage adds")
        if lang == "he" and not HEBREW_RE.search(caption["shows"]):
            raise SystemExit(f"{candidate_id}: Hebrew document — caption must be Hebrew")
        if len(alt) > 160:
            raise SystemExit(f"{candidate_id}: alt text over 160 characters")
        unknown_segments = [seg_id for seg_id in anchors if seg_id not in segment_ids]
        if unknown_segments:
            raise SystemExit(f"{candidate_id}: unknown anchor segments {unknown_segments}")
        # Anchors must sit inside what the engine knows the frame illustrates:
        # its own segments plus any engine-derived alignment (`aligned_seg_ids`).
        allowed = set(candidate.get("seg_ids", [])) | set(candidate.get("aligned_seg_ids", []))
        if allowed and not allowed.intersection(anchors):
            raise SystemExit(f"{candidate_id}: anchor_seg_ids do not overlap candidate provenance")
        block_index = next(
            (
                block_index for block_index, block in enumerate(summaries[chapter_id]["blocks"])
                if set(block["seg_ids"]).intersection(anchors)
            ),
            None,
        )
        if block_index is None:
            raise SystemExit(f"{candidate_id}: no summary block overlaps anchor_seg_ids")
        for variant in ("full", "thumb"):
            path = Path(asset[variant]["path"])
            if not path.exists():
                raise SystemExit(f"{candidate_id}: missing asset {path}")
            expected = asset[variant].get("sha256")
            if expected and _sha256(path) != expected:
                raise SystemExit(f"{candidate_id}: asset {path.name} does not match its recorded sha256")
        normalized.append({
            **selection,
            "candidate_id": candidate_id,
            "chapter_id": chapter_id,
            "role": role,
            "novelty": novelty,
            "caption": caption,
            "alt": alt,
            "anchor_seg_ids": anchors,
            "actual_t": timestamp,
            "triaged_t": float(asset.get("triaged_t", triaged_t)),
            "refinement": asset.get("refinement"),
            "requested_t": candidate.get("requested_t"),
            "seg_ids": candidate.get("seg_ids", []),
            "target_ids": candidate.get("target_ids", []),
            "family_id": candidate.get("family_id"),
            "selection_reasons": candidate.get("reasons", []),
            "quality": candidate.get("quality", {}),
            "asset": asset,
            "block_index": block_index,
        })
    return summaries, normalized, assets


STYLE = """
  :root {
    --ink: #1c1e21; --muted: #6b7280; --accent: #b3372c; --accent-soft: #f6e8e6;
    --line: #e5e2dc; --bg: #faf9f7; --card: #ffffff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 17px/1.65 var(--font-stack); }
  header.hero { border-block-end: 3px solid var(--ink); background: var(--card); padding: 3.5rem 1.5rem 2.5rem; }
  .measure { max-inline-size: 46rem; margin-inline: auto; }
  .kicker { text-transform: uppercase; letter-spacing: .14em; font-size: .75rem; color: var(--accent); font-weight: 700; }
  [dir=rtl] .kicker { text-transform: none; letter-spacing: 0; }
  h1 { font-size: clamp(1.7rem, 4vw, 2.5rem); line-height: 1.15; margin: .4rem 0 .8rem; }
  .meta { color: var(--muted); font-size: .92rem; }
  .meta a { color: var(--accent); }
  .thesis { margin-block-start: 1.4rem; padding: 1rem 1.2rem; background: var(--accent-soft);
            border-inline-start: 4px solid var(--accent); font-size: 1.02rem; }
  nav.toc { margin: 2rem auto 0; max-inline-size: 46rem; }
  nav.toc ol { columns: 2; gap: 2rem; padding-inline-start: 1.2rem; margin: .4rem 0 0; font-size: .92rem; }
  nav.toc li { margin: .25rem 0; break-inside: avoid; }
  nav.toc a { color: var(--ink); text-decoration: none; border-block-end: 1px solid var(--line); }
  nav.toc a:hover { color: var(--accent); border-color: var(--accent); }
  main { padding: 1rem 1.5rem 4rem; }
  section.brief { max-inline-size: 46rem; margin: 2rem auto 0; }
  .brief h3 { font-size: 1.05rem; margin-block: 1.2rem .4rem; }
  .brief ul { padding-inline-start: 1.3rem; margin-block: .4rem; }
  .brief li { margin-block: .55rem; }
  .brief-sources { font-size: .8em; color: var(--muted); unicode-bidi: isolate; }
  .brief-sources a { color: inherit; text-decoration: none; border-block-end: 1px dotted var(--muted); }
  section.chapter { max-inline-size: 46rem; margin: 3rem auto 0; padding-block-start: 2.4rem; border-block-start: 1px solid var(--line); }
  .ch-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
  .ch-num { font-weight: 800; color: var(--accent); font-size: .95rem; letter-spacing: .06em; }
  h2 { font-size: 1.35rem; margin: 0; line-height: 1.3; }
  .range { color: var(--muted); font-size: .85rem; white-space: nowrap; direction: ltr; unicode-bidi: isolate; }
  .range a { color: var(--muted); text-decoration: none; border-block-end: 1px dotted var(--muted); }
  .range a:hover { color: var(--accent); border-color: var(--accent); }
  p { margin: .9rem 0; }
  code, pre { direction: ltr; unicode-bidi: isolate; font-family: "Menlo", "SF Mono", Consolas, var(--font-stack), monospace; }
  code { font-size: .92em; background: #f0eeea; padding: .05em .3em; border-radius: 4px; }
  pre { text-align: left; overflow-x: auto; background: #1f2328; color: #e6edf3; padding: .9rem 1rem; border-radius: 8px; font-size: .88rem; line-height: 1.5; }
  pre code { background: none; padding: 0; color: inherit; }
  blockquote { margin: .9rem 0; padding-inline-start: 1rem; border-inline-start: 3px solid var(--line); color: var(--muted); font-style: italic; }
  figure { margin: 1.6rem 0; background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: .7rem; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  figure a { display: block; }
  figure img { inline-size: 100%; block-size: auto; display: block; border-radius: 6px; }
  figcaption { font-size: .87rem; color: var(--muted); padding: .65rem .3rem .1rem; }
  figcaption b { color: var(--accent); }
  figcaption b a { color: var(--accent); text-decoration: none; }
  figcaption b a:hover { text-decoration: underline; }
  figcaption .why { display: block; margin-block-start: .25rem; color: var(--ink); }
  figcaption .look { display: block; margin-block-start: .15rem; }
  .duo { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .duo figure { margin: 1.6rem 0 0; }
  ul.key-points { margin: 1rem 0 0; padding-inline-start: 1.2rem; color: var(--muted); font-size: .95rem; }
  @media (max-width: 640px) { .duo { grid-template-columns: 1fr; } nav.toc ol { columns: 1; } }
  footer { border-block-start: 3px solid var(--ink); background: var(--card); padding: 1.6rem; text-align: center;
           color: var(--muted); font-size: .85rem; }
  @media print {
    @page { size: A4; margin: 14mm; }
    body { background: #fff; font-size: 11pt; }
    header.hero { padding: 1.2rem 0 1rem; }
    main { padding: 0; }
    section.chapter { margin-block-start: 1.6rem; padding-block-start: 1.2rem; }
    h2 { break-after: avoid; }
    .brief h3 { break-after: avoid; }
    .brief li { break-inside: avoid; }
    figure { break-inside: avoid; box-shadow: none; }
    figure img { max-block-size: 110mm; object-fit: contain; }
    pre { white-space: pre-wrap; break-inside: avoid; background: #f4f4f4; color: #111; }
    .duo { grid-template-columns: 1fr 1fr; }
    a { color: inherit; text-decoration: none; }
    nav.toc ol { columns: 2; }
  }
"""

FONT_STACKS = {
    "en": '"Avenir Next", "Segoe UI", system-ui, sans-serif',
    "he": '"Heebo", "Arial Hebrew", "Noto Sans Hebrew", system-ui, sans-serif',
}


def font_face_css(lang: str) -> str:
    """Embedded Heebo subsets (SIL OFL, see scripts/fonts/OFL.txt) for Hebrew
    documents, so the single file renders identically offline."""
    if lang != "he":
        return ""
    faces = []
    for weight, name in ((400, "Heebo-Regular.subset.woff"), (700, "Heebo-Bold.subset.woff")):
        path = FONT_DIR / name
        if not path.exists():
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        faces.append(
            "  @font-face { font-family: 'Heebo'; font-style: normal; font-weight: %d; "
            "font-display: swap; src: url(data:font/woff;base64,%s) format('woff'); }" % (weight, data)
        )
    return "\n".join(faces)


def _find_chrome() -> str | None:
    """A Chrome/Chromium binary for headless print-to-pdf, if any."""
    explicit = os.environ.get("CHROME_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    for candidate in CHROME_CANDIDATES:
        if candidate.startswith("/"):
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def _find_weasyprint() -> list[str] | None:
    """A WeasyPrint invocation, if any: in-process module, CLI, or `uv run`."""
    try:
        import weasyprint  # type: ignore  # noqa: F401 — optional
    except Exception:
        pass
    else:
        return [sys.executable, "-m", "weasyprint"]
    if shutil.which("weasyprint"):
        return ["weasyprint"]
    if shutil.which("uv"):
        return ["uv", "run", "--with", "weasyprint", "weasyprint"]
    return None


def export_pdf(single_html: Path, out_pdf: Path, engine: str = "auto") -> dict:
    """Print the self-contained HTML to PDF: Chrome headless first, WeasyPrint
    second (or one of them with `engine`). Works on a temporary copy without
    `loading="lazy"` (headless printers skip lazy images); the bundle itself is
    untouched. Both engines implement the Unicode bidi algorithm for HTML text,
    so Hebrew documents print correctly on either."""
    print_html = single_html.with_name(single_html.stem + ".print.html")
    text = single_html.read_text(encoding="utf-8").replace(' loading="lazy"', "")
    print_html.write_text(text, encoding="utf-8")
    attempts: list[str] = []
    try:
        chrome = _find_chrome() if engine in ("auto", "chrome") else None
        if chrome:
            command = [
                chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-pdf-header-footer", "--virtual-time-budget=10000",
                f"--print-to-pdf={out_pdf}", print_html.as_uri(),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=240)
            if result.returncode == 0 and out_pdf.exists() and out_pdf.stat().st_size > 0:
                return {"engine": "chrome", "binary": chrome, "path": str(out_pdf)}
            attempts.append(f"chrome: exit {result.returncode} {result.stderr.strip()[-200:]}")
        weasy = _find_weasyprint() if engine in ("auto", "weasyprint") else None
        if weasy:
            result = subprocess.run(
                weasy + [str(print_html), str(out_pdf)], capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0 and out_pdf.exists() and out_pdf.stat().st_size > 0:
                return {"engine": "weasyprint", "binary": weasy[0], "path": str(out_pdf)}
            attempts.append(f"weasyprint: exit {result.returncode} {result.stderr.strip()[-200:]}")
        return {"engine": None, "attempts": attempts}
    finally:
        print_html.unlink(missing_ok=True)


def _figure(frame: dict, source_url: str | None, strings: dict) -> str:
    asset = frame["asset"]
    full = f"assets/{html.escape(asset['full']['file'])}"
    thumb = f"assets/{html.escape(asset['thumb']['file'])}"
    timestamp = format_time(frame["actual_t"])
    timestamp_link = _timestamp_url(source_url, frame["actual_t"])
    stamp = f'<bdi dir="ltr">{html.escape(timestamp)}</bdi>'
    time_html = (
        f'<b><a href="{html.escape(timestamp_link)}">{stamp}</a></b>' if timestamp_link else f"<b>{stamp}</b>"
    )
    caption = frame["caption"]
    # no-break spaces keep "01:02 ·" together when the caption wraps
    parts = [f"{time_html} · {_inline(caption['shows'])}"]
    if caption.get("why"):
        parts.append(f'<span class="why">{_inline(caption["why"])}</span>')
    if caption.get("look_at"):
        parts.append(f'<span class="look">{html.escape(strings["look_at"])} {_inline(caption["look_at"])}</span>')
    seg_ids = " ".join(html.escape(str(seg)) for seg in frame.get("anchor_seg_ids", []))
    return (
        f'<figure data-frame-id="{html.escape(frame["name"])}" '
        f'data-candidate-id="{html.escape(frame["candidate_id"])}" '
        f'data-time="{frame["actual_t"]:.3f}" data-segment-ids="{seg_ids}" '
        f'data-role="{html.escape(frame["role"])}" data-novelty="{html.escape(frame["novelty"])}">'
        f'<a href="{full}"><img src="{thumb}" loading="lazy" '
        f'alt="{html.escape(frame["alt"])}"></a>'
        f"<figcaption>{''.join(parts)}</figcaption>"
        "</figure>"
    )


def _figures_block(figures: list[str]) -> str:
    """Two frames anchored to the same prose block sit side by side (the earlier
    one at inline-start, which is the reading order in both directions)."""
    if len(figures) == 2:
        return '<div class="duo">' + "".join(figures) + "</div>"
    return "".join(figures)


def _block_html(block: dict) -> str:
    kind = block["kind"]
    if kind == "code":
        lang_class = f' class="lang-{html.escape(block["lang"])}"' if block.get("lang") else ""
        return f'<pre dir="ltr"><code{lang_class}>{html.escape(block["text"])}</code></pre>'
    if kind == "quote":
        return f'<blockquote dir="auto">{_inline(block["text"])}</blockquote>'
    return f"<p>{_inline(block['text'])}</p>"


def _brief_html(brief: dict, transcript: dict, strings: dict) -> str:
    """Render a validated brief without touching chapter blocks or frame anchors."""
    brief_items({"brief": brief}, transcript)
    rows = transcript.get("segments", [])
    segments = {str(row["seg_id"]): row for row in rows}
    order = {str(row["seg_id"]): i for i, row in enumerate(rows)}
    source_url = transcript.get("video", {}).get("url")

    def item_html(item: dict) -> str:
        # One start link per contiguous cited run keeps cross-chapter evidence
        # compact without inventing a continuous range between distant claims.
        links = []
        previous = -2
        for seg_id in item["seg_ids"]:
            position = order[seg_id]
            if position != previous + 1:
                start = float(segments[seg_id]["start"])
                label = html.escape(format_time(start))
                url = _timestamp_url(source_url, start)
                links.append(f'<a href="{html.escape(url)}">{label}</a>' if url else label)
            previous = position
        sources = (
            f'<span class="brief-sources" dir="ltr" aria-label="{html.escape(strings["brief_sources"])}">'
            f'[{" · ".join(links)}]</span>'
        )
        return f'{_inline(item["text"])} {sources}'

    body = [f'<p>{item_html(brief["synthesis"])}</p>']
    for key in ("main_points", "takeaways"):
        if brief[key]:
            body.append(f'<h3>{html.escape(strings[key])}</h3><ul>')
            body.extend(f'<li>{item_html(item)}</li>' for item in brief[key])
            body.append('</ul>')
    return (
        f'<section class="brief" aria-labelledby="brief-title">'
        f'<h2 id="brief-title">{html.escape(strings["brief"])}</h2>'
        + ''.join(body) + '</section>'
    )


def _render_html(
    transcript: dict,
    chapters: list[dict],
    summaries: dict[str, dict],
    frames: list[dict],
    overview: str,
    lang: str,
    candidate_count: int | None = None,
    brief: dict | None = None,
) -> str:
    strings = STRINGS[lang]
    direction = "rtl" if lang == "he" else "ltr"
    video = transcript.get("video", {})
    title = str(video.get("title") or "Video summary")
    source_url = video.get("url")
    is_link = isinstance(source_url, str) and source_url.startswith(("http://", "https://"))
    frames_by_chapter: dict[str, list[dict]] = {}
    for frame in frames:
        frames_by_chapter.setdefault(frame["chapter_id"], []).append(frame)

    sections: list[str] = [_brief_html(brief, transcript, strings)] if brief is not None else []
    toc: list[str] = []
    for number, chapter in enumerate(chapters, start=1):
        chapter_id = chapter["chapter_id"]
        summary = summaries[chapter_id]
        toc.append(f'<li><a href="#{html.escape(chapter_id)}">{_inline(summary["title"])}</a></li>')
        block_frames: dict[int, list[dict]] = {}
        for frame in sorted(frames_by_chapter.get(chapter_id, []), key=lambda row: row["actual_t"]):
            block_frames.setdefault(frame["block_index"], []).append(frame)
        body: list[str] = []
        for index, block in enumerate(summary["blocks"]):
            body.append(_block_html(block))
            body.append(_figures_block([_figure(frame, source_url, strings) for frame in block_frames.get(index, [])]))
        if summary["key_points"]:
            items = "".join(f"<li>{_inline(point)}</li>" for point in summary["key_points"])
            body.append(f'<ul class="key-points">{items}</ul>')
        range_text = f"{format_time(chapter['start'])}–{format_time(chapter['end'])}"
        range_link = _timestamp_url(source_url, chapter["start"]) if is_link else None
        range_html = (
            f'<a href="{html.escape(range_link)}">{html.escape(range_text)}</a>' if range_link
            else html.escape(range_text)
        )
        sections.append(
            f'<section class="chapter" id="{html.escape(chapter_id)}">'
            f'<div class="ch-head"><span class="ch-num"><bdi dir="ltr">{number:02d}</bdi></span>'
            f'<h2>{_inline(summary["title"])}</h2>'
            f'<span class="range" dir="ltr">{range_html}</span></div>'
            + "".join(body)
            + "</section>"
        )

    meta_bits: list[str] = []
    if video.get("uploader"):
        meta_bits.append(f'<bdi>{html.escape(str(video["uploader"]))}</bdi>')
    if video.get("duration"):
        meta_bits.append(f'<bdi dir="ltr">{html.escape(format_time(float(video["duration"])))}</bdi>')
    if is_link:
        meta_bits.append(f'<a href="{html.escape(source_url)}">{html.escape(strings["watch"])}</a>')
    provenance = transcript.get("source")
    if provenance:
        language = transcript.get("language")
        label = f"{provenance}" + (f" ({language})" if language else "")
        meta_bits.append(html.escape(strings["transcript"].format(source=label)))
    if candidate_count:
        meta_bits.append(html.escape(strings["frames_of"].format(n=len(frames), m=candidate_count)))
    else:
        meta_bits.append(html.escape(strings["frames"].format(n=len(frames))))
    footer_source = (
        f'{html.escape(strings["source"])} <a href="{html.escape(source_url)}"><bdi dir="ltr">{html.escape(source_url)}</bdi></a> · '
        if is_link else ""
    )
    font_css = font_face_css(lang)
    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — {html.escape(strings["title_suffix"])}</title>
<style>
{font_css}
  :root {{ --font-stack: {FONT_STACKS[lang]}; }}
{STYLE}</style>
</head>
<body>

<header class="hero">
  <div class="measure">
    <div class="kicker">{html.escape(strings["kicker"])}</div>
    <h1 dir="auto">{html.escape(title)}</h1>
    <div class="meta">{' · '.join(meta_bits)}</div>
    <div class="thesis"><b>{html.escape(strings["claim"])}</b> {_inline(overview)}</div>
  </div>
  <nav class="toc">
    <div class="kicker">{html.escape(strings["chapters"])}</div>
    <ol>{''.join(toc)}</ol>
  </nav>
</header>

<main>
{''.join(sections)}
</main>

<footer>
  {html.escape(strings["footer"].format(n=len(frames)))} ·
  {footer_source}{html.escape(strings["provenance"])} <code dir="ltr">manifest.json</code>
</footer>

</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render validated HTML (Hebrew RTL or English) from summary evidence")
    parser.add_argument("--work", required=True)
    parser.add_argument("--summary", required=True, help="Model-authored summary.json")
    parser.add_argument("--selections", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--lang", choices=sorted(LANGS), default=None,
                        help="Output language (default: summary.json lang, then SUMMARY_LANG, then en)")
    parser.add_argument("--pdf", action="store_true",
                        help="Also print the single-file HTML to summary-<id>.pdf")
    parser.add_argument("--pdf-engine", choices=("auto", "chrome", "weasyprint"), default="auto")
    parser.add_argument("--allow-audit-errors", action="store_true",
                        help="Render even when audit_summary.py reports errors (benchmark use only)")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    assets_dir = Path(args.assets_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_assets = out_dir / "assets"
    if assets_dir != expected_assets:
        if expected_assets.exists():
            raise SystemExit(f"Refusing to replace existing assets directory: {expected_assets}")
        shutil.copytree(assets_dir, expected_assets)
        assets_dir = expected_assets

    transcript = _load_json(work / "transcript.json")
    chapters = _load_json(work / "chapters.json")
    candidate_payload = _load_json(work / "candidates.json")
    selections = _load_json(Path(args.selections).expanduser().resolve())
    summary_payload = _load_json(Path(args.summary).expanduser().resolve())
    assets_payload = _load_json(assets_dir / "assets-manifest.json")
    if not isinstance(chapters, list) or not isinstance(selections, list):
        raise SystemExit("chapters.json and selections.json must be arrays")
    if not isinstance(summary_payload, dict):
        raise SystemExit("summary.json must be an object")
    lang = resolve_lang(args.lang, summary_payload)
    summaries, frames, _ = _validate(
        transcript, chapters, candidate_payload, selections, assets_payload, summary_payload, lang
    )
    overview = str(summary_payload.get("overview") or "").strip()
    if not overview:
        raise SystemExit("summary.json requires a non-empty overview")
    if lang == "he" and not HEBREW_RE.search(overview):
        raise SystemExit("Hebrew document — overview must be Hebrew")

    info = None
    info_path = work / "download" / "video.info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {key: raw.get(key) for key in ("title", "description", "tags", "uploader")}
        except (OSError, json.JSONDecodeError):
            info = None
    audit = run_audit(transcript, chapters, summary_payload, selections=selections,
                      candidates=candidate_payload, info=info, lang=lang)
    (work / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if audit["errors"]:
        print(f"[vsum] audit: {len(audit['errors'])} error(s), {len(audit['reviews'])} review(s)", file=sys.stderr)
        for row in audit["errors"][:20]:
            print(f"  error {row['check']} {row.get('where', '')}: {row['message']}", file=sys.stderr)
        if not args.allow_audit_errors:
            print(f"[vsum] fix the summary (see {work / 'audit.json'}) and re-run render", file=sys.stderr)
            return 5

    source_language = transcript.get("language")
    translation_mode = "hebrew_passthrough" if (lang == "he" and str(source_language or "").startswith("he")) \
        else ("translated" if lang == "he" else "same_language")
    manifest_frames = []
    for frame in frames:
        asset = frame["asset"]
        manifest_frames.append({
            key: frame[key]
            for key in (
                "candidate_id", "name", "chapter_id", "requested_t", "actual_t", "triaged_t",
                "refinement", "seg_ids", "target_ids", "family_id", "role", "novelty", "caption", "alt",
                "anchor_seg_ids", "selection_reasons", "quality",
            )
        } | {
            "assets": {
                "full": {key: asset["full"][key] for key in ("file", "width", "height", "sha256")},
                "thumb": {key: asset["thumb"][key] for key in ("file", "width", "height", "sha256")},
            }
        })
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "tier": candidate_payload.get("tier"),
        "lang": lang,
        "direction": "rtl" if lang == "he" else "ltr",
        "source_language": source_language,
        "translation_mode": translation_mode,
        "video": transcript.get("video", {}),
        "transcript_source": transcript.get("source"),
        "overview": overview,
        "chapters": [summaries[chapter["chapter_id"]] | {
            "start": chapter["start"], "end": chapter["end"],
            "coverage_status": next(
                (
                    row["status"] for row in candidate_payload.get("coverage", {}).get("chapters", [])
                    if row["chapter_id"] == chapter["chapter_id"]
                ),
                "unknown",
            ),
        } for chapter in chapters],
        "frames": manifest_frames,
        "audit": {"errors": len(audit["errors"]), "reviews": len(audit["reviews"]),
                  "warnings": len(audit["warnings"]), "stats": audit.get("stats", {})},
        "summary_sha256": canonical_json_sha256(summary_payload),
        "selections_sha256": canonical_json_sha256(selections),
    }
    if "brief" in summary_payload:
        # Structural errors cannot be bypassed, even in benchmark mode.
        try:
            brief_items(summary_payload, transcript)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        manifest["brief"] = summary_payload["brief"]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    candidate_count = len(candidate_payload.get("candidates", [])) or None
    html_text = _render_html(transcript, chapters, summaries, frames, overview, lang, candidate_count,
                             brief=summary_payload.get("brief"))
    temporary_html = out_dir / "index.html.tmp"
    temporary_html.write_text(html_text, encoding="utf-8")
    temporary_html.replace(out_dir / "index.html")
    print(f"Rendered `{out_dir / 'index.html'}` and `{out_dir / 'manifest.json'}` (lang={lang}, "
          f"audit: {len(audit['errors'])} errors / {len(audit['reviews'])} reviews / {len(audit['warnings'])} warnings)")
    # The single self-contained file is the deliverable people open and share;
    # the directory stays as the editable source.
    single = bundle_summary(out_dir, None)
    size_mb = single.stat().st_size / (1024 * 1024)
    print(f"Bundled single-file deliverable: `{single}` ({size_mb:.1f} MB) — opens with a double click.")
    if args.pdf:
        pdf_path = single.with_suffix(".pdf")
        outcome = export_pdf(single, pdf_path, args.pdf_engine)
        if not outcome.get("engine"):
            print("PDF: no engine (install Google Chrome or WeasyPrint)", file=sys.stderr)
            for attempt in outcome.get("attempts", []):
                print(f"  {attempt}", file=sys.stderr)
            return 4
        pdf_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"PDF: `{pdf_path}` ({pdf_mb:.1f} MB via {outcome['engine']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
