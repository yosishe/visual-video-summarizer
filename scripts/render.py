#!/usr/bin/env python3
"""Validate summary evidence and deterministically render manifest.json + HTML."""
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from frame_utils import chapter_for_time, format_time

ROLES = {"evidence", "illustration"}


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
        timestamp = float(candidate["actual_t"])
        owning = chapter_for_time(chapters, timestamp)
        if owning is None or owning["chapter_id"] != chapter_id:
            raise SystemExit(
                f"{candidate_id}: t={timestamp:.3f} belongs to "
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
            "requested_t": candidate.get("requested_t"),
            "seg_ids": candidate.get("seg_ids", []),
            "target_ids": candidate.get("target_ids", []),
            "selection_reasons": candidate.get("reasons", []),
            "quality": candidate.get("quality", {}),
            "asset": asset,
            "block_index": block_index,
        })
    return summaries, normalized, assets


def _figure(frame: dict, source_url: str | None) -> str:
    asset = frame["asset"]
    full = f"assets/{html.escape(asset['full']['file'])}"
    thumb = f"assets/{html.escape(asset['thumb']['file'])}"
    timestamp = format_time(frame["actual_t"])
    timestamp_link = _timestamp_url(source_url, frame["actual_t"])
    time_html = (
        f'<a class="timestamp" href="{html.escape(timestamp_link)}">{html.escape(timestamp)}</a>'
        if timestamp_link
        else f'<span class="timestamp">{html.escape(timestamp)}</span>'
    )
    return (
        f'<figure data-candidate-id="{html.escape(frame["candidate_id"])}" '
        f'data-time="{frame["actual_t"]:.3f}" data-role="{html.escape(frame["role"])}">'
        f'<a href="{full}"><img src="{thumb}" loading="lazy" '
        f'alt="{html.escape(frame["alt"])}"></a>'
        f'<figcaption>{time_html} — {html.escape(frame["caption"])}</figcaption>'
        "</figure>"
    )


def _render_html(
    transcript: dict,
    chapters: list[dict],
    summaries: dict[str, dict],
    frames: list[dict],
    overview: str,
) -> str:
    video = transcript.get("video", {})
    title = str(video.get("title") or "Video summary")
    source_url = video.get("url")
    frames_by_chapter: dict[str, list[dict]] = {}
    for frame in frames:
        frames_by_chapter.setdefault(frame["chapter_id"], []).append(frame)
    sections: list[str] = []
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        summary = summaries[chapter_id]
        block_frames: dict[int, list[dict]] = {}
        for frame in frames_by_chapter.get(chapter_id, []):
            block_frames.setdefault(frame["block_index"], []).append(frame)
        body: list[str] = []
        for index, block in enumerate(summary["blocks"]):
            body.append(f"<p>{html.escape(block['text'])}</p>")
            body.extend(_figure(frame, source_url) for frame in block_frames.get(index, []))
        if summary["key_points"]:
            items = "".join(f"<li>{html.escape(point)}</li>" for point in summary["key_points"])
            body.append(f'<ul class="key-points">{items}</ul>')
        sections.append(
            f'<section id="{html.escape(chapter_id)}">'
            f'<div class="chapter-heading"><h2>{html.escape(summary["title"])}</h2>'
            f'<span>{html.escape(format_time(chapter["start"]))}–{html.escape(format_time(chapter["end"]))}</span></div>'
            + "".join(body)
            + "</section>"
        )
    source_link = (
        f'<a href="{html.escape(source_url)}">Open source video</a>'
        if isinstance(source_url, str) and source_url.startswith(("http://", "https://"))
        else ""
    )
    return f"""<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} — Visual summary</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#d8dee9; --accent:#3157d5; --paper:#fff; --bg:#f4f6fa; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink); font:17px/1.65 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(900px,calc(100% - 32px)); margin:40px auto 80px; }}
    header,section {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; padding:clamp(22px,4vw,42px); margin:0 0 24px; }}
    h1 {{ line-height:1.15; margin:0 0 14px; font-size:clamp(2rem,5vw,3.6rem); }} h2 {{ margin:0; line-height:1.25; }}
    .eyebrow,.chapter-heading span {{ color:var(--muted); font-size:.9rem; font-weight:650; letter-spacing:.02em; }}
    .chapter-heading {{ display:flex; align-items:baseline; justify-content:space-between; gap:20px; border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:22px; }}
    p {{ max-width:72ch; }} figure {{ margin:28px 0 32px; }} figure a {{ display:block; }}
    img {{ width:100%; height:auto; display:block; border-radius:12px; border:1px solid var(--line); background:#0b1020; }}
    figcaption {{ color:var(--muted); font-size:.94rem; margin-top:9px; }} .timestamp {{ color:var(--accent); font-weight:700; }}
    a {{ color:var(--accent); }} .key-points {{ padding-left:1.2rem; }}
    @media (max-width:600px) {{ main {{ width:min(100% - 20px,900px); margin-top:12px; }} .chapter-heading {{ display:block; }} }}
  </style>
</head>
<body><main>
  <header><div class="eyebrow">Visual video summary</div><h1>{html.escape(title)}</h1><p>{html.escape(overview)}</p>{source_link}</header>
  {''.join(sections)}
</main></body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render validated English HTML from summary evidence")
    parser.add_argument("--work", required=True)
    parser.add_argument("--summary", required=True, help="Model-authored summary.json")
    parser.add_argument("--selections", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--out-dir", required=True)
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
                "candidate_id", "name", "chapter_id", "requested_t", "actual_t",
                "seg_ids", "target_ids", "role", "caption", "alt", "anchor_seg_ids",
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
    html_text = _render_html(transcript, chapters, summaries, frames, overview)
    temporary_html = out_dir / "index.html.tmp"
    temporary_html.write_text(html_text, encoding="utf-8")
    temporary_html.replace(out_dir / "index.html")
    print(f"Rendered `{out_dir / 'index.html'}` and `{out_dir / 'manifest.json'}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
