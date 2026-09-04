#!/usr/bin/env python3
"""Validate summary evidence and deterministically render manifest.json + HTML."""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from bundle import bundle as bundle_summary  # noqa: E402
from frame_utils import chapter_for_time, format_time  # noqa: E402

ENGINE_VERSION = "1.3.0"
ROLES = {"evidence", "illustration"}
CHROME_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def _timestamp_url(source_url: str | None, timestamp: float) -> str | None:
    if not source_url or not source_url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(source_url)
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["t"] = str(int(round(timestamp)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _normalized_summary(summary_payload: dict, chapters: list[dict]) -> dict[str, dict]:
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
            if not text or not seg_ids:
                raise SystemExit(f"{chapter_id} block {index} needs text and seg_ids")
            normalized_blocks.append({"text": text, "seg_ids": seg_ids})
        normalized[chapter_id] = {
            "chapter_id": chapter_id,
            "title": str(row.get("title") or chapter.get("title") or chapter_id),
            "blocks": normalized_blocks,
            "key_points": [str(point) for point in row.get("key_points", []) if str(point).strip()],
        }
    return normalized


def _validate(
    transcript: dict,
    chapters: list[dict],
    candidate_payload: dict,
    selections: list[dict],
    assets_payload: dict,
    summary_payload: dict,
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
    summaries = _normalized_summary(summary_payload, chapters)
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
        caption = str(selection.get("caption") or "").strip()
        alt = str(selection.get("alt") or "").strip()
        anchors = [str(seg_id) for seg_id in selection.get("anchor_seg_ids", [])]
        if role not in ROLES or not name or not caption or not alt or not anchors:
            raise SystemExit(f"{candidate_id}: name, role, caption, alt and anchor_seg_ids are required")
        unknown_segments = [seg_id for seg_id in anchors if seg_id not in segment_ids]
        if unknown_segments:
            raise SystemExit(f"{candidate_id}: unknown anchor segments {unknown_segments}")
        candidate_segments = set(candidate.get("seg_ids", []))
        if candidate_segments and not candidate_segments.intersection(anchors):
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
        normalized.append({
            **selection,
            "candidate_id": candidate_id,
            "chapter_id": chapter_id,
            "actual_t": timestamp,
            "triaged_t": float(asset.get("triaged_t", triaged_t)),
            "refinement": asset.get("refinement"),
            "requested_t": candidate.get("requested_t"),
            "seg_ids": candidate.get("seg_ids", []),
            "target_ids": candidate.get("target_ids", []),
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
         font: 17px/1.65 "Avenir Next", "Segoe UI", system-ui, sans-serif; }
  header.hero { border-bottom: 3px solid var(--ink); background: var(--card); padding: 3.5rem 1.5rem 2.5rem; }
  .measure { max-width: 46rem; margin: 0 auto; }
  .kicker { text-transform: uppercase; letter-spacing: .14em; font-size: .75rem; color: var(--accent); font-weight: 700; }
  h1 { font-size: clamp(1.7rem, 4vw, 2.5rem); line-height: 1.15; margin: .4rem 0 .8rem; }
  .meta { color: var(--muted); font-size: .92rem; }
  .meta a { color: var(--accent); }
  .thesis { margin-top: 1.4rem; padding: 1rem 1.2rem; background: var(--accent-soft);
            border-left: 4px solid var(--accent); font-size: 1.02rem; }
  nav.toc { margin: 2rem auto 0; max-width: 46rem; }
  nav.toc ol { columns: 2; gap: 2rem; padding-left: 1.2rem; margin: .4rem 0 0; font-size: .92rem; }
  nav.toc li { margin: .25rem 0; break-inside: avoid; }
  nav.toc a { color: var(--ink); text-decoration: none; border-bottom: 1px solid var(--line); }
  nav.toc a:hover { color: var(--accent); border-color: var(--accent); }
  main { padding: 1rem 1.5rem 4rem; }
  section.chapter { max-width: 46rem; margin: 3rem auto 0; padding-top: 2.4rem; border-top: 1px solid var(--line); }
  .ch-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
  .ch-num { font-weight: 800; color: var(--accent); font-size: .95rem; letter-spacing: .06em; }
  h2 { font-size: 1.35rem; margin: 0; line-height: 1.3; }
  .range { color: var(--muted); font-size: .85rem; white-space: nowrap; }
  .range a { color: var(--muted); text-decoration: none; border-bottom: 1px dotted var(--muted); }
  .range a:hover { color: var(--accent); border-color: var(--accent); }
  p { margin: .9rem 0; }
  figure { margin: 1.6rem 0; background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: .7rem; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  figure a { display: block; }
  figure img { width: 100%; height: auto; display: block; border-radius: 6px; }
  figcaption { font-size: .87rem; color: var(--muted); padding: .65rem .3rem .1rem; }
  figcaption b a { color: var(--accent); text-decoration: none; }
  figcaption b a:hover { text-decoration: underline; }
  .duo { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .duo figure { margin: 1.6rem 0 0; }
  ul.key-points { margin: 1rem 0 0; padding-left: 1.2rem; color: var(--muted); font-size: .95rem; }
  @media (max-width: 640px) { .duo { grid-template-columns: 1fr; } nav.toc ol { columns: 1; } }
  footer { border-top: 3px solid var(--ink); background: var(--card); padding: 1.6rem; text-align: center;
           color: var(--muted); font-size: .85rem; }
  @media print {
    @page { size: A4; margin: 14mm; }
    body { background: #fff; font-size: 11pt; }
    header.hero { padding: 1.2rem 0 1rem; }
    main { padding: 0; }
    section.chapter { margin-top: 1.6rem; padding-top: 1.2rem; }
    h2 { break-after: avoid; }
    figure { break-inside: avoid; box-shadow: none; }
    figure img { max-height: 110mm; object-fit: contain; }
    .duo { grid-template-columns: 1fr 1fr; }
    a { color: inherit; text-decoration: none; }
    nav.toc ol { columns: 2; }
  }
"""


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


def export_pdf(single_html: Path, out_pdf: Path) -> dict:
    """Print the self-contained HTML to PDF: Chrome headless first, WeasyPrint
    second. Works on a temporary copy without `loading="lazy"` (headless
    printers skip lazy images); the bundle itself is untouched."""
    print_html = single_html.with_name(single_html.stem + ".print.html")
    text = single_html.read_text(encoding="utf-8").replace(' loading="lazy"', "")
    print_html.write_text(text, encoding="utf-8")
    attempts: list[str] = []
    try:
        chrome = _find_chrome()
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
        weasy = _find_weasyprint()
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


def _figure(frame: dict, source_url: str | None) -> str:
    asset = frame["asset"]
    full = f"assets/{html.escape(asset['full']['file'])}"
    thumb = f"assets/{html.escape(asset['thumb']['file'])}"
    timestamp = format_time(frame["actual_t"])
    timestamp_link = _timestamp_url(source_url, frame["actual_t"])
    time_html = (
        f'<b><a href="{html.escape(timestamp_link)}">{html.escape(timestamp)}</a></b>'
        if timestamp_link
        else f"<b>{html.escape(timestamp)}</b>"
    )
    seg_ids = " ".join(html.escape(str(seg)) for seg in frame.get("anchor_seg_ids", []))
    return (
        f'<figure data-frame-id="{html.escape(frame["name"])}" '
        f'data-candidate-id="{html.escape(frame["candidate_id"])}" '
        f'data-time="{frame["actual_t"]:.3f}" data-segment-ids="{seg_ids}" '
        f'data-role="{html.escape(frame["role"])}">'
        f'<a href="{full}"><img src="{thumb}" loading="lazy" '
        f'alt="{html.escape(frame["alt"])}"></a>'
        f"<figcaption>{time_html} — {html.escape(frame['caption'])}</figcaption>"
        "</figure>"
    )


def _figures_block(figures: list[str]) -> str:
    """Two frames anchored to the same prose block sit side by side."""
    if len(figures) == 2:
        return '<div class="duo">' + "".join(figures) + "</div>"
    return "".join(figures)


def _render_html(
    transcript: dict,
    chapters: list[dict],
    summaries: dict[str, dict],
    frames: list[dict],
    overview: str,
    candidate_count: int | None = None,
) -> str:
    video = transcript.get("video", {})
    title = str(video.get("title") or "Video summary")
    source_url = video.get("url")
    is_link = isinstance(source_url, str) and source_url.startswith(("http://", "https://"))
    frames_by_chapter: dict[str, list[dict]] = {}
    for frame in frames:
        frames_by_chapter.setdefault(frame["chapter_id"], []).append(frame)

    sections: list[str] = []
    toc: list[str] = []
    for number, chapter in enumerate(chapters, start=1):
        chapter_id = chapter["chapter_id"]
        summary = summaries[chapter_id]
        toc.append(f'<li><a href="#{html.escape(chapter_id)}">{html.escape(summary["title"])}</a></li>')
        block_frames: dict[int, list[dict]] = {}
        for frame in sorted(frames_by_chapter.get(chapter_id, []), key=lambda row: row["actual_t"]):
            block_frames.setdefault(frame["block_index"], []).append(frame)
        body: list[str] = []
        for index, block in enumerate(summary["blocks"]):
            body.append(f"<p>{html.escape(block['text'])}</p>")
            body.append(_figures_block([_figure(frame, source_url) for frame in block_frames.get(index, [])]))
        if summary["key_points"]:
            items = "".join(f"<li>{html.escape(point)}</li>" for point in summary["key_points"])
            body.append(f'<ul class="key-points">{items}</ul>')
        range_text = f"{format_time(chapter['start'])}–{format_time(chapter['end'])}"
        range_link = _timestamp_url(source_url, chapter["start"]) if is_link else None
        range_html = (
            f'<a href="{html.escape(range_link)}">{html.escape(range_text)}</a>' if range_link
            else html.escape(range_text)
        )
        sections.append(
            f'<section class="chapter" id="{html.escape(chapter_id)}">'
            f'<div class="ch-head"><span class="ch-num">{number:02d}</span>'
            f'<h2>{html.escape(summary["title"])}</h2>'
            f'<span class="range">{range_html}</span></div>'
            + "".join(body)
            + "</section>"
        )

    meta_bits: list[str] = []
    if video.get("uploader"):
        meta_bits.append(html.escape(str(video["uploader"])))
    if video.get("duration"):
        meta_bits.append(html.escape(format_time(float(video["duration"]))))
    if is_link:
        meta_bits.append(f'<a href="{html.escape(source_url)}">Watch the source video</a>')
    if video.get("transcript_source"):
        meta_bits.append("transcript: " + html.escape(str(video["transcript_source"])))
    frames_note = f"{len(frames)} frames selected"
    if candidate_count:
        frames_note += f" from {candidate_count} candidates"
    meta_bits.append(frames_note)
    footer_source = (
        f'source: <a href="{html.escape(source_url)}">{html.escape(source_url)}</a> · '
        if is_link else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Visual Summary</title>
<style>{STYLE}</style>
</head>
<body>

<header class="hero">
  <div class="measure">
    <div class="kicker">Visual video summary</div>
    <h1>{html.escape(title)}</h1>
    <div class="meta">{' · '.join(meta_bits)}</div>
    <div class="thesis"><b>The claim in one line:</b> {html.escape(overview)}</div>
  </div>
  <nav class="toc">
    <div class="kicker">Chapters</div>
    <ol>{''.join(toc)}</ol>
  </nav>
</header>

<main>
{''.join(sections)}
</main>

<footer>
  Summary generated from the video's transcript and {len(frames)} selected frames ·
  {footer_source}frame provenance in <code>manifest.json</code>
</footer>

</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render validated English HTML from summary evidence")
    parser.add_argument("--work", required=True)
    parser.add_argument("--summary", required=True, help="Model-authored summary.json")
    parser.add_argument("--selections", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pdf", action="store_true",
                        help="Also print the single-file HTML to summary-<id>.pdf "
                             "(Chrome headless, or WeasyPrint as a fallback)")
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
    summaries, frames, _ = _validate(
        transcript, chapters, candidate_payload, selections, assets_payload, summary_payload
    )
    overview = str(summary_payload.get("overview") or "").strip()
    if not overview:
        raise SystemExit("summary.json requires a non-empty overview")

    manifest_frames = []
    for frame in frames:
        asset = frame["asset"]
        manifest_frames.append({
            key: frame[key]
            for key in (
                "candidate_id", "name", "chapter_id", "requested_t", "actual_t", "triaged_t",
                "refinement", "seg_ids", "target_ids", "role", "caption", "alt", "anchor_seg_ids",
                "selection_reasons", "quality",
            )
        } | {
            "assets": {
                "full": {key: asset["full"][key] for key in ("file", "width", "height", "sha256")},
                "thumb": {key: asset["thumb"][key] for key in ("file", "width", "height", "sha256")},
            }
        })
    manifest = {
        "schema_version": 2,
        "engine_version": ENGINE_VERSION,
        "tier": candidate_payload.get("tier"),
        "video": transcript.get("video", {}),
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
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    candidate_count = len(candidate_payload.get("candidates", [])) or None
    html_text = _render_html(transcript, chapters, summaries, frames, overview, candidate_count)
    temporary_html = out_dir / "index.html.tmp"
    temporary_html.write_text(html_text, encoding="utf-8")
    temporary_html.replace(out_dir / "index.html")
    print(f"Rendered `{out_dir / 'index.html'}` and `{out_dir / 'manifest.json'}`")
    # The single self-contained file is the deliverable people open and share;
    # the directory stays as the editable source.
    single = bundle_summary(out_dir, None)
    size_mb = single.stat().st_size / (1024 * 1024)
    print(f"Bundled single-file deliverable: `{single}` ({size_mb:.1f} MB) — opens with a double click.")
    if args.pdf:
        pdf_path = single.with_suffix(".pdf")
        outcome = export_pdf(single, pdf_path)
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
