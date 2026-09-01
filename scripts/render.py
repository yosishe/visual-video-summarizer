#!/usr/bin/env python3
"""Fail-closed evidence validation and deterministic English HTML rendering."""
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from frame_utils import chapter_for_time, file_sha256, format_time


ALLOWED_ROLES = {"evidence", "illustration"}


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def _timestamp_link(url: object, timestamp: float) -> str | None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["t"] = str(max(0, int(round(timestamp))))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _summary_by_chapter(summary: dict, chapters: list[dict], known_segments: set[str]) -> dict[str, dict]:
    rows = summary.get("chapters")
    if not isinstance(rows, list):
        raise SystemExit("summary.json chapters must be an array")
    supplied = {str(row.get("chapter_id")): row for row in rows if isinstance(row, dict)}
    normalized: dict[str, dict] = {}
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        row = supplied.get(chapter_id)
        if row is None:
            raise SystemExit(f"summary.json is missing chapter {chapter_id}")
        raw_blocks = row.get("blocks")
        if not isinstance(raw_blocks, list) or not raw_blocks:
            raise SystemExit(f"{chapter_id}: summary requires non-empty blocks[]")
        blocks = []
        for index, block in enumerate(raw_blocks):
            text = str(block.get("text") or "").strip() if isinstance(block, dict) else ""
            seg_ids = [str(value) for value in block.get("seg_ids", [])] if isinstance(block, dict) else []
            if not text or not seg_ids:
                raise SystemExit(f"{chapter_id}: block {index} requires text and seg_ids")
            unknown = sorted(set(seg_ids) - known_segments)
            if unknown:
                raise SystemExit(f"{chapter_id}: block {index} has unknown segments {unknown}")
            blocks.append({"text": text, "seg_ids": seg_ids})
        normalized[chapter_id] = {
            "chapter_id": chapter_id,
            "title": str(row.get("title") or chapter.get("title") or chapter_id),
            "blocks": blocks,
            "key_points": [str(value).strip() for value in row.get("key_points", []) if str(value).strip()],
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
    if assets_payload.get("failures"):
        raise SystemExit("asset extraction failures must be resolved before rendering")
    if assets_payload.get("duplicate_pairs"):
        raise SystemExit("hard duplicate assets must be resolved before rendering")
    if len(selections) > 20:
        raise SystemExit("global HTML frame budget exceeded (maximum 20)")
    chapter_counts = Counter(str(row.get("chapter_id")) for row in selections)
    over_budget = sorted(chapter for chapter, count in chapter_counts.items() if count > 3)
    if over_budget:
        raise SystemExit("per-chapter frame budget exceeded: " + ", ".join(over_budget))

    chapter_map = {str(row["chapter_id"]): row for row in chapters}
    known_segments = {str(row["seg_id"]) for row in transcript.get("segments", [])}
    summaries = _summary_by_chapter(summary_payload, chapters, known_segments)
    candidate_map = {
        str(row.get("candidate_id")): row for row in candidate_payload.get("candidates", [])
    }
    asset_map = {str(row.get("candidate_id")): row for row in assets_payload.get("assets", [])}

    unresolved_chapters = sorted(
        row["chapter_id"]
        for row in candidate_payload.get("coverage", {}).get("chapters", [])
        if row.get("status") == "unresolved"
        and chapter_map.get(str(row.get("chapter_id")), {}).get("needs_frames")
    )
    unresolved_targets = sorted(
        row["target_id"]
        for row in candidate_payload.get("coverage", {}).get("targets", [])
        if row.get("status") == "unresolved"
    )
    if unresolved_chapters or unresolved_targets:
        values = unresolved_chapters + unresolved_targets
        raise SystemExit("required visual evidence units remain unresolved: " + ", ".join(values))

    normalized: list[dict] = []
    selected_targets: set[str] = set()
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            raise SystemExit(f"selection {index} must be an object")
        candidate_id = str(selection.get("candidate_id") or "")
        candidate = candidate_map.get(candidate_id)
        asset = asset_map.get(candidate_id)
        if candidate is None or asset is None:
            raise SystemExit(f"selection {index} lacks a candidate/assets pair: {candidate_id!r}")
        chapter_id = str(selection.get("chapter_id") or "")
        chapter = chapter_map.get(chapter_id)
        if chapter is None or chapter_id != candidate.get("chapter_id"):
            raise SystemExit(f"{candidate_id}: invalid chapter binding {chapter_id!r}")
        timestamp = float(candidate["actual_t"])
        owner = chapter_for_time(chapters, timestamp)
        if owner is None or owner["chapter_id"] != chapter_id:
            raise SystemExit(f"{candidate_id}: decoded timestamp belongs to another chapter")
        role = str(selection.get("role") or "")
        caption = str(selection.get("caption") or "").strip()
        alt = str(selection.get("alt") or "").strip()
        name = str(selection.get("name") or "").strip()
        anchors = [str(value) for value in selection.get("anchor_seg_ids", [])]
        if role not in ALLOWED_ROLES or not caption or not alt or not name or not anchors:
            raise SystemExit(f"{candidate_id}: name, role, caption, alt, and anchor_seg_ids are required")
        unknown = sorted(set(anchors) - known_segments)
        if unknown:
            raise SystemExit(f"{candidate_id}: unknown anchor segments {unknown}")
        candidate_segments = set(candidate.get("seg_ids", []))
        if candidate_segments and not candidate_segments.intersection(anchors):
            raise SystemExit(f"{candidate_id}: selection anchors do not overlap candidate provenance")
        block_index = next(
            (
                block_index
                for block_index, block in enumerate(summaries[chapter_id]["blocks"])
                if set(block["seg_ids"]).intersection(anchors)
            ),
            None,
        )
        if block_index is None:
            raise SystemExit(f"{candidate_id}: no prose block overlaps the frame anchors")
        for variant in ("full", "thumb"):
            variant_row = asset.get(variant, {})
            variant_path = Path(variant_row.get("path", ""))
            if not variant_path.is_file():
                raise SystemExit(f"{candidate_id}: missing {variant} asset {variant_path}")
            filename = str(variant_row.get("file") or "")
            if not filename or Path(filename).name != filename or variant_path.name != filename:
                raise SystemExit(f"{candidate_id}: unsafe or inconsistent {variant} asset filename")
            expected_hash = str(variant_row.get("sha256") or "")
            if not expected_hash:
                raise SystemExit(f"{candidate_id}: {variant} asset has no content hash")
            if file_sha256(variant_path) != expected_hash:
                raise SystemExit(f"{candidate_id}: {variant} asset hash mismatch")
        selected_targets.update(str(value) for value in candidate.get("target_ids", []))
        normalized.append({
            **selection,
            "candidate_id": candidate_id,
            "chapter_id": chapter_id,
            "requested_t": candidate.get("requested_t"),
            "actual_t": timestamp,
            "seg_ids": candidate.get("seg_ids", []),
            "target_ids": candidate.get("target_ids", []),
            "selection_reasons": candidate.get("reasons", []),
            "quality": candidate.get("quality", {}),
            "asset": asset,
            "block_index": block_index,
        })

    required_targets = {
        str(target["target_id"])
        for chapter in chapters
        for target in chapter.get("visual_targets", [])
    }
    omitted_targets = sorted(required_targets - selected_targets)
    if omitted_targets:
        raise SystemExit("HTML selections omit required visual targets: " + ", ".join(omitted_targets))
    for chapter in chapters:
        if chapter.get("needs_frames") and not any(
            row["chapter_id"] == chapter["chapter_id"] for row in normalized
        ):
            raise SystemExit(f"HTML selections omit required visual chapter {chapter['chapter_id']}")
    return summaries, normalized, asset_map


def _figure(frame: dict, source_url: object) -> str:
    full = "assets/" + html.escape(frame["asset"]["full"]["file"])
    thumb = "assets/" + html.escape(frame["asset"]["thumb"]["file"])
    timestamp_text = html.escape(format_time(frame["actual_t"]))
    link = _timestamp_link(source_url, frame["actual_t"])
    time_markup = (
        f'<a class="time" href="{html.escape(link)}">{timestamp_text}</a>'
        if link else f'<span class="time">{timestamp_text}</span>'
    )
    return (
        f'<figure data-candidate-id="{html.escape(frame["candidate_id"])}" '
        f'data-time="{frame["actual_t"]:.6f}" data-role="{html.escape(frame["role"])}">'
        f'<a href="{full}"><img src="{thumb}" loading="lazy" decoding="async" '
        f'alt="{html.escape(frame["alt"])}"></a>'
        f'<figcaption>{time_markup}<span aria-hidden="true"> — </span>'
        f'{html.escape(frame["caption"])}</figcaption></figure>'
    )


def _html_document(
    transcript: dict,
    chapters: list[dict],
    summaries: dict[str, dict],
    frames: list[dict],
    overview: str,
) -> str:
    video = transcript.get("video", {})
    title = str(video.get("title") or "Video summary")
    source_url = video.get("url")
    by_chapter: dict[str, list[dict]] = defaultdict(list)
    for frame in frames:
        by_chapter[frame["chapter_id"]].append(frame)
    sections: list[str] = []
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        summary = summaries[chapter_id]
        after_block: dict[int, list[dict]] = defaultdict(list)
        for frame in sorted(by_chapter.get(chapter_id, []), key=lambda row: row["actual_t"]):
            after_block[frame["block_index"]].append(frame)
        content: list[str] = []
        for block_index, block in enumerate(summary["blocks"]):
            content.append(f'<p>{html.escape(block["text"])}</p>')
            content.extend(_figure(frame, source_url) for frame in after_block.get(block_index, []))
        if summary["key_points"]:
            content.append(
                '<ul class="key-points">'
                + "".join(f"<li>{html.escape(point)}</li>" for point in summary["key_points"])
                + "</ul>"
            )
        sections.append(
            f'<section id="{html.escape(chapter_id)}"><div class="chapter-title">'
            f'<h2>{html.escape(summary["title"])}</h2><span>'
            f'{html.escape(format_time(chapter["start"]))}–{html.escape(format_time(chapter["end"]))}'
            f'</span></div>{"".join(content)}</section>'
        )
    source_markup = (
        f'<a class="source" href="{html.escape(source_url)}">Open source video</a>'
        if isinstance(source_url, str) and source_url.startswith(("http://", "https://")) else ""
    )
    return f'''<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} — Visual summary</title>
  <style>
    :root{{--ink:#152033;--muted:#657086;--paper:#fff;--bg:#f3f5f9;--line:#d7dde8;--accent:#2455d6}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.65 system-ui,sans-serif}}
    main{{width:min(920px,calc(100% - 28px));margin:36px auto 72px}} header,section{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:clamp(22px,4vw,42px);margin-bottom:24px}}
    h1{{font-size:clamp(2rem,5vw,3.5rem);line-height:1.12;margin:.15em 0}} h2{{margin:0;line-height:1.25}} .eyebrow,.chapter-title span{{color:var(--muted);font-size:.9rem;font-weight:700}}
    .chapter-title{{display:flex;align-items:baseline;justify-content:space-between;gap:18px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:22px}} p{{max-width:72ch}}
    figure{{margin:28px 0 34px}} figure a{{display:block}} img{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:12px;background:#111827}}
    figcaption{{color:var(--muted);font-size:.94rem;margin-top:9px}} a{{color:var(--accent)}} .time{{font-weight:750}} .key-points{{padding-left:1.25rem}}
    @media(max-width:600px){{main{{width:calc(100% - 18px);margin-top:9px}}.chapter-title{{display:block}}}}
  </style>
</head>
<body><main><header><div class="eyebrow">Evidence-linked visual summary</div><h1>{html.escape(title)}</h1><p>{html.escape(overview)}</p>{source_markup}</header>{''.join(sections)}</main></body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate evidence and render deterministic HTML")
    parser.add_argument("--work", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--selections", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    source_assets = Path(args.assets_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    if source_assets != assets_dir:
        if assets_dir.exists():
            raise SystemExit(f"refusing to replace existing assets directory: {assets_dir}")
        shutil.copytree(source_assets, assets_dir)

    transcript = _load(work / "transcript.json")
    authored_chapters = _load(work / "chapters.json")
    candidates = _load(work / "candidates.json")
    selections = _load(Path(args.selections).expanduser().resolve())
    summary = _load(Path(args.summary).expanduser().resolve())
    assets = _load(assets_dir / "assets-manifest.json")
    if not all(isinstance(value, expected) for value, expected in (
        (transcript, dict), (authored_chapters, list), (candidates, dict),
        (selections, list), (summary, dict), (assets, dict),
    )):
        raise SystemExit("one or more input files has the wrong top-level JSON type")
    chapters = candidates.get("chapters") or authored_chapters
    if not isinstance(chapters, list):
        raise SystemExit("candidate manifest chapters must be an array")
    for asset in assets.get("assets", []):
        for variant in ("full", "thumb"):
            filename = str(asset.get(variant, {}).get("file") or "")
            if filename:
                asset[variant]["path"] = str(assets_dir / filename)
    overview = str(summary.get("overview") or "").strip()
    if not overview:
        raise SystemExit("summary.json requires a non-empty overview")
    summaries, frames, _ = _validate(transcript, chapters, candidates, selections, assets, summary)

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
                variant: {
                    key: asset[variant][key]
                    for key in ("file", "width", "height", "sha256")
                }
                for variant in ("full", "thumb")
            }
        })
    coverage_by_chapter = {
        row["chapter_id"]: row["status"]
        for row in candidates.get("coverage", {}).get("chapters", [])
    }
    manifest = {
        "schema_version": 3,
        "engine": "independent-visual-evidence-engine",
        "video": transcript.get("video", {}),
        "overview": overview,
        "chapters": [
            summaries[chapter["chapter_id"]] | {
                "start": chapter["start"], "end": chapter["end"],
                "coverage_status": coverage_by_chapter.get(chapter["chapter_id"], "unknown"),
            }
            for chapter in chapters
        ],
        "frames": manifest_frames,
    }
    manifest_tmp = out_dir / ".manifest.json.tmp"
    manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_tmp.replace(out_dir / "manifest.json")
    html_tmp = out_dir / ".index.html.tmp"
    html_tmp.write_text(_html_document(transcript, chapters, summaries, frames, overview), encoding="utf-8")
    html_tmp.replace(out_dir / "index.html")
    print(f"Rendered `{out_dir / 'index.html'}` and `{out_dir / 'manifest.json'}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
