#!/usr/bin/env python3
"""Transcript-bounded visual evidence planner and frame candidate generator.

The engine performs inexpensive grayscale scans only inside evidence windows.
It finds local transitions, stable post-action states, and visually distinct
alternatives before extracting any model-readable image.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import (  # noqa: E402
    candidate_identifier,
    chapter_for_time,
    compare_signatures,
    finite_number,
    format_time,
    is_hard_duplicate,
    is_near_duplicate,
    quality_payload,
    segment_ids_at,
    validate_chapters,
    validate_segments,
    visual_signature,
)
from media_backend import (  # noqa: E402
    cache_key,
    extract_frame,
    make_strip,
    merge_sections,
    part_for as mapped_part_for,
    prepare_media,
    scan_window,
    source_identity,
)


MODE = {
    "light": {"scan_rate": 2.0, "per_target": 2, "cap": 36, "window_pad": 0.7},
    "advanced": {"scan_rate": 5.0, "per_target": 3, "cap": 60, "window_pad": 1.3},
}


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def _source_identity(source: str, sections: list[tuple[float, float]], exact: bool) -> dict:
    return source_identity(source, sections, exact)


def _cache_key(identity: dict) -> str:
    return cache_key(identity)


def part_for(parts: list[dict], timestamp: float) -> dict | None:
    return mapped_part_for(parts, timestamp)


def _segment_map(segments: list[dict]) -> dict[str, dict]:
    return {str(row["seg_id"]): row for row in segments}


def _legacy_targets(chapter: dict) -> list[dict]:
    targets = list(chapter.get("visual_targets") or [])
    if targets:
        return targets
    for index, cue in enumerate(chapter.get("cues") or []):
        targets.append({
            "target_id": f"{chapter['chapter_id']}_legacy_{index + 1:02d}",
            "kind": "state",
            "seg_ids": [str(cue["seg_id"])] if cue.get("seg_id") else [],
            "anchor_t": finite_number(cue.get("t"), (chapter["start"] + chapter["end"]) / 2),
            "why": str(cue.get("why") or "legacy visual cue"),
            "legacy": True,
        })
    return targets


def normalize_targets(chapters: list[dict], segments: list[dict], mode: str) -> list[dict]:
    by_segment = _segment_map(segments)
    pad = MODE[mode]["window_pad"]
    normalized: list[dict] = []
    seen: set[str] = set()
    for chapter in chapters:
        targets = _legacy_targets(chapter)
        chapter["visual_targets"] = targets
        for target in targets:
            target_id = str(target.get("target_id") or "").strip()
            if not target_id or target_id in seen:
                raise SystemExit(f"{chapter['chapter_id']}: missing or duplicate target_id")
            seen.add(target_id)
            kind = str(target.get("kind") or "state")
            seg_ids = [str(value) for value in target.get("seg_ids", [])]
            unknown = [value for value in seg_ids if value not in by_segment]
            if unknown:
                raise SystemExit(f"{target_id}: unknown transcript segments {unknown}")
            references = [by_segment[value] for value in seg_ids]
            explicit_window = target.get("window")
            if isinstance(explicit_window, list) and len(explicit_window) == 2:
                start = finite_number(explicit_window[0])
                end = finite_number(explicit_window[1])
            elif references:
                start = min(row["start"] for row in references) - pad
                end = max(row["end"] for row in references) + pad
            else:
                anchor = finite_number(
                    target.get("anchor_t"),
                    (chapter["start"] + chapter["end"]) / 2,
                )
                radius = 2.4 if mode == "light" else 4.5
                start, end = anchor - radius, anchor + radius

            if kind == "action_result":
                action_seg_id = str(target.get("action_seg_id") or "")
                if action_seg_id:
                    if action_seg_id not in by_segment:
                        raise SystemExit(f"{target_id}: unknown action_seg_id {action_seg_id}")
                    anchor = by_segment[action_seg_id]["end"]
                elif references:
                    anchor = min(row["end"] for row in references)
                else:
                    anchor = finite_number(target.get("anchor_t"), start)
                start = max(start, anchor - 0.15)
                end = max(end, anchor + (3.0 if mode == "light" else 6.0))
            else:
                anchor = finite_number(
                    target.get("anchor_t"),
                    statistics.fmean([row["start"] + row["end"] for row in references]) / 2
                    if references else (start + end) / 2,
                )

            start = max(chapter["start"], start)
            end = min(chapter["end"], end)
            if end - start < 0.25:
                start = max(chapter["start"], anchor - 0.25)
                end = min(chapter["end"], anchor + 0.35)
            if end <= start:
                raise SystemExit(f"{target_id}: empty search window after chapter clamp")
            normalized.append({
                **target,
                "target_id": target_id,
                "kind": kind,
                "chapter_id": chapter["chapter_id"],
                "seg_ids": seg_ids,
                "anchor_t": round(anchor, 6),
                "window": [round(start, 6), round(end, 6)],
            })
    return normalized


def _motion(first: dict, second: dict) -> float:
    delta = compare_signatures(first, second)
    return (
        delta["luma_mad"]
        + 0.2 * delta["edge_mad"]
        + 45.0 * delta["changed_ratio"]
        + 25.0 * delta["active_tile_ratio"]
    )


def _annotate_scan(samples: list[dict]) -> list[dict]:
    if not samples:
        return samples
    motions = [0.0]
    for index in range(1, len(samples)):
        motions.append(_motion(samples[index - 1]["signature"], samples[index]["signature"]))
    nonzero = motions[1:] or [0.0]
    median = statistics.median(nonzero)
    deviations = [abs(value - median) for value in nonzero]
    mad = statistics.median(deviations) if deviations else 0.0
    scene_gate = max(2.0, median + 2.4 * max(mad, 0.25))
    stable_gate = max(1.2, median + 0.65 * max(mad, 0.2))
    for index, sample in enumerate(samples):
        previous = motions[index]
        following = motions[index + 1] if index + 1 < len(motions) else previous
        signature = sample["signature"]
        sample.update({
            "motion_in": previous,
            "motion_out": following,
            "scene": previous >= scene_gate,
            "stable": max(previous, following) <= stable_gate and not signature["blank"],
            "quality_score": (
                0.75 * signature["sharpness"]
                + 0.12 * signature["contrast"]
                - (120.0 if signature["blank"] else 0.0)
                - 0.7 * min(50.0, max(previous, following))
            ),
        })
    return samples


def _visually_distinct(candidate: dict, selected: list[dict]) -> bool:
    return all(not is_near_duplicate(candidate["signature"], row["signature"]) for row in selected)


def select_target_samples(samples: list[dict], target: dict, limit: int) -> list[dict]:
    rows = _annotate_scan(samples)
    if not rows:
        return []
    start, end = target["window"]
    span = max(0.2, end - start)
    anchor = target["anchor_t"]
    leading_blank = any(row["signature"]["blank"] for row in rows if row["t"] >= anchor)

    if target["kind"] == "action_result":
        post = [row for row in rows if row["t"] >= anchor]
        if not post:
            post = rows
        transition_index = max(range(len(post)), key=lambda index: post[index]["motion_in"])
        transition_time = post[transition_index]["t"]
        stable_after = [row for row in post if row["t"] > transition_time and row["stable"]]
        fallback = [row for row in post if not row["signature"]["blank"]]
        pool = stable_after or fallback
        for row in pool:
            delay = max(0.0, row["t"] - transition_time)
            row["evidence_score"] = row["quality_score"] + 32.0 / (1.0 + delay)
        pool.sort(key=lambda row: (-row["evidence_score"], row["t"]))
    else:
        pool = [row for row in rows if row["stable"]]
        if not pool:
            pool = [row for row in rows if not row["signature"]["blank"]]
        for row in pool:
            closeness = 1.0 - min(1.0, abs(row["t"] - anchor) / span)
            row["evidence_score"] = row["quality_score"] + 14.0 * closeness + (4.0 if row["scene"] else 0.0)
        pool.sort(key=lambda row: (-row["evidence_score"], abs(row["t"] - anchor)))

    selected: list[dict] = []
    for row in pool:
        if _visually_distinct(row, selected):
            selected.append(row)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for row in pool:
            if row not in selected and all(abs(row["t"] - item["t"]) >= 0.35 for item in selected):
                selected.append(row)
            if len(selected) >= limit:
                break
    for row in selected:
        row["recovered"] = bool(
            target["kind"] == "action_result"
            and (leading_blank or row["t"] > anchor + 0.25)
        )
    return sorted(selected, key=lambda row: row["t"])


def select_chapter_samples(samples: list[dict], chapter: dict, limit: int = 2) -> list[dict]:
    rows = _annotate_scan(samples)
    pool = [row for row in rows if row["stable"]] or [
        row for row in rows if not row["signature"]["blank"]
    ]
    middle = (chapter["start"] + chapter["end"]) / 2
    for row in pool:
        row["evidence_score"] = row["quality_score"] - 0.02 * abs(row["t"] - middle)
    pool.sort(key=lambda row: -row["evidence_score"])
    selected: list[dict] = []
    for row in pool:
        if _visually_distinct(row, selected):
            selected.append(row)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda row: row["t"])


def make_point(
    timestamp: float,
    reason: str,
    chapters: list[dict],
    segments: list[dict],
    target: dict | None = None,
) -> dict:
    chapter = chapter_for_time(chapters, timestamp)
    target_ids = {target["target_id"]} if target else set()
    target_kinds = {target["kind"]} if target else set()
    target_segments = set(target.get("seg_ids", [])) if target else set()
    return {
        "requested_t": round(float(timestamp), 6),
        "reasons": {reason},
        "chapter_id": chapter["chapter_id"] if chapter else None,
        "target_ids": target_ids,
        "target_kinds": target_kinds,
        "target_anchors": {target["target_id"]: target["anchor_t"]} if target else {},
        "target_windows": {target["target_id"]: target["window"]} if target else {},
        "seg_ids": target_segments or set(segment_ids_at(segments, timestamp)),
        "priority": 100 if target else (65 if reason == "pin" else 35),
        "scene_score": 0.0,
    }


def _point_value(point: dict) -> tuple:
    return (
        int(bool(point.get("target_ids"))),
        int("target" in point.get("reasons", set())),
        finite_number(point.get("priority")),
        finite_number(point.get("requested_t")),
    )


def merge_points(points: list[dict], epsilon: float = 0.12) -> list[dict]:
    merged: list[dict] = []
    for point in sorted(points, key=lambda row: row["requested_t"]):
        match = next(
            (
                row for row in reversed(merged)
                if abs(row["requested_t"] - point["requested_t"]) <= epsilon
                and row.get("chapter_id") == point.get("chapter_id")
                and (
                    not row.get("target_ids")
                    or not point.get("target_ids")
                    or row.get("target_ids") == point.get("target_ids")
                )
            ),
            None,
        )
        if match is None:
            merged.append(point)
            continue
        winner, other = (point, match) if _point_value(point) > _point_value(match) else (match, point)
        winner["reasons"] = set(winner.get("reasons", set())) | set(other.get("reasons", set()))
        winner["target_ids"] = set(winner.get("target_ids", set())) | set(other.get("target_ids", set()))
        winner["target_kinds"] = set(winner.get("target_kinds", set())) | set(other.get("target_kinds", set()))
        winner["seg_ids"] = set(winner.get("seg_ids", set())) | set(other.get("seg_ids", set()))
        winner["target_anchors"] = {**other.get("target_anchors", {}), **winner.get("target_anchors", {})}
        winner["target_windows"] = {**other.get("target_windows", {}), **winner.get("target_windows", {})}
        if winner is point:
            merged[merged.index(match)] = winner
    return sorted(merged, key=lambda row: row["requested_t"])


def _frame_value(frame: dict) -> tuple:
    quality = frame.get("quality", {})
    reasons = set(frame.get("reasons", set()))
    target_anchors = frame.get("target_anchors", {})
    post_action = 0.0
    if "action_result" in frame.get("target_kinds", set()) and target_anchors:
        post_action = min(
            max(0.0, finite_number(frame.get("actual_t")) - finite_number(anchor))
            for anchor in target_anchors.values()
        )
    return (
        int(bool(frame.get("target_ids"))),
        int("target" in reasons),
        int("recovered" in reasons),
        -int(bool(quality.get("blank"))),
        finite_number(quality.get("sharpness")) + 0.1 * finite_number(quality.get("contrast")),
        -post_action,
    )


def deduplicate_frames(frames: list[dict]) -> tuple[list[dict], int]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for frame in frames:
        semantic = (
            frame.get("chapter_id"),
            tuple(sorted(frame.get("target_ids", set()))),
        )
        groups[semantic].append(frame)
    kept: list[dict] = []
    dropped = 0
    for group in groups.values():
        clusters: list[list[dict]] = []
        for frame in sorted(group, key=lambda row: finite_number(row.get("actual_t"))):
            signature = frame.get("_signature")
            cluster = next(
                (
                    rows for rows in clusters
                    if signature and rows[0].get("_signature")
                    and is_near_duplicate(signature, rows[0]["_signature"])
                ),
                None,
            )
            if cluster is None:
                clusters.append([frame])
            else:
                cluster.append(frame)
        for cluster in clusters:
            winner = max(cluster, key=_frame_value)
            for other in cluster:
                if other is winner:
                    continue
                winner["reasons"] = set(winner.get("reasons", set())) | set(other.get("reasons", set()))
                winner["seg_ids"] = set(winner.get("seg_ids", set())) | set(other.get("seg_ids", set()))
            kept.append(winner)
            dropped += len(cluster) - 1
    # Pixel-identical evidence inside one chapter can safely carry multiple
    # semantic targets. Coalescing it prevents the renderer from being forced to
    # choose two hard-duplicate assets merely to satisfy two target IDs. Near-but-
    # non-identical states still remain separate across targets.
    coalesced: list[dict] = []
    for frame in sorted(kept, key=lambda row: finite_number(row.get("actual_t"))):
        match = next(
            (
                row for row in coalesced
                if row.get("chapter_id") == frame.get("chapter_id")
                and row.get("_signature") and frame.get("_signature")
                and is_hard_duplicate(row["_signature"], frame["_signature"])
            ),
            None,
        )
        if match is None:
            coalesced.append(frame)
            continue
        winner, other = (frame, match) if _frame_value(frame) > _frame_value(match) else (match, frame)
        for key in ("reasons", "target_ids", "target_kinds", "seg_ids"):
            winner[key] = set(winner.get(key, set())) | set(other.get(key, set()))
        for key in ("target_anchors", "target_windows"):
            winner[key] = {**other.get(key, {}), **winner.get(key, {})}
        if winner is frame:
            coalesced[coalesced.index(match)] = winner
        dropped += 1
    return sorted(coalesced, key=lambda row: finite_number(row.get("actual_t"))), dropped


def coverage_report(chapters: list[dict], frames: list[dict]) -> dict:
    chapter_rows: list[dict] = []
    target_rows: list[dict] = []
    for chapter in chapters:
        chapter_frames = [row for row in frames if row.get("chapter_id") == chapter["chapter_id"]]
        status = "covered" if chapter_frames else (
            "unresolved" if chapter.get("needs_frames") else "not-required"
        )
        chapter_rows.append({
            "chapter_id": chapter["chapter_id"], "status": status,
            "candidate_ids": [row.get("candidate_id") for row in chapter_frames if row.get("candidate_id")],
        })
        for target in chapter.get("visual_targets") or []:
            rows = [row for row in chapter_frames if target["target_id"] in row.get("target_ids", set())]
            target_rows.append({
                "target_id": target["target_id"], "chapter_id": chapter["chapter_id"],
                "status": "covered" if rows else "unresolved",
                "candidate_ids": [row.get("candidate_id") for row in rows if row.get("candidate_id")],
            })
    return {"chapters": chapter_rows, "targets": target_rows}


def _budget(frames: list[dict], chapters: list[dict], cap: int) -> tuple[list[dict], int]:
    """Guarantee one representative per target/chapter, then spend remaining budget by utility."""
    selected: list[dict] = []
    selected_keys: set[int] = set()

    def add(frame: dict | None) -> None:
        if frame is not None and id(frame) not in selected_keys and len(selected) < cap:
            selected.append(frame)
            selected_keys.add(id(frame))

    for chapter in chapters:
        chapter_frames = [row for row in frames if row.get("chapter_id") == chapter["chapter_id"]]
        for target in chapter.get("visual_targets") or []:
            target_frames = [row for row in chapter_frames if target["target_id"] in row.get("target_ids", set())]
            add(max(target_frames, key=_frame_value) if target_frames else None)
        if chapter.get("needs_frames"):
            add(max(chapter_frames, key=_frame_value) if chapter_frames else None)
    for frame in sorted(frames, key=_frame_value, reverse=True):
        add(frame)
    return sorted(selected, key=lambda row: row["actual_t"]), len(frames) - len(selected)


def _parse_times(value: str | None) -> list[float]:
    if not value:
        return []
    times: list[float] = []
    for token in value.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            times.append(float(token))
        except ValueError as exc:
            raise SystemExit(f"bad timestamp {token!r}") from exc
    return times


def _parse_sections(value: str | None) -> list[tuple[float, float]]:
    if not value:
        return []
    sections: list[tuple[float, float]] = []
    for token in value.split(","):
        pair = token.split("-", 1)
        if len(pair) != 2:
            raise SystemExit(f"bad section {token!r}; expected start-end")
        sections.append((float(pair[0]), float(pair[1])))
    return sections


def _extract_points(points: list[dict], parts: list[dict], out_dir: Path, width: int) -> tuple[list[dict], int]:
    frames: list[dict] = []
    dropped = 0
    for index, point in enumerate(points):
        temporary = out_dir / f"pending_{index:04d}.jpg"
        try:
            actual_t, part = extract_frame(parts, point["requested_t"], temporary, width)
            signature = visual_signature(temporary)
        except RuntimeError as exc:
            print(f"[vsum] candidate {point['requested_t']:.3f}s dropped: {exc}", file=sys.stderr)
            dropped += 1
            continue
        if signature["blank"] and point.get("target_ids"):
            recovered = None
            windows = list(point.get("target_windows", {}).values())
            bounds = windows[0] if windows else [point["requested_t"] - 1.0, point["requested_t"] + 1.0]
            for offset in (0.35, 0.7, 1.1, -0.35, -0.7):
                retry_t = min(bounds[1] - 0.001, max(bounds[0], point["requested_t"] + offset))
                retry = out_dir / f"pending_{index:04d}_retry.jpg"
                try:
                    retry_actual, retry_part = extract_frame(parts, retry_t, retry, width)
                    retry_signature = visual_signature(retry)
                except RuntimeError:
                    continue
                if not retry_signature["blank"]:
                    temporary.unlink(missing_ok=True)
                    retry.replace(temporary)
                    recovered = (retry_t, retry_actual, retry_part, retry_signature)
                    break
                retry.unlink(missing_ok=True)
            if recovered:
                point["requested_t"], actual_t, part, signature = recovered
                point["reasons"].add("recovered")

        frames.append({
            **point,
            "actual_t": actual_t,
            "timestamp_error": round(actual_t - point["requested_t"], 6),
            "path": str(temporary),
            "quality": quality_payload(signature),
            "media_part_id": part.get("part_id"),
            "_signature": signature,
        })
    return frames, dropped


def _json_frame(frame: dict) -> dict:
    return {
        "candidate_id": frame["candidate_id"],
        "requested_t": round(frame["requested_t"], 6),
        "actual_t": round(frame["actual_t"], 6),
        "timestamp_error": round(frame["timestamp_error"], 6),
        "chapter_id": frame["chapter_id"],
        "target_ids": sorted(frame.get("target_ids", set())),
        "target_kinds": sorted(frame.get("target_kinds", set())),
        "seg_ids": sorted(frame.get("seg_ids", set())),
        "reasons": sorted(frame.get("reasons", set())),
        "quality": frame["quality"],
        "media_part_id": frame.get("media_part_id"),
        "path": frame["path"],
    }


def _make_triage(frames: list[dict], work: Path) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for frame in frames:
        key = sorted(frame.get("target_ids", set()))[0] if frame.get("target_ids") else frame["chapter_id"]
        groups[key].append(frame)
    strips: list[dict] = []
    strip_dir = work / "triage-strips"
    pixel_area = 0
    strip_index = 0
    for key, rows in groups.items():
        rows.sort(key=lambda row: row["actual_t"])
        for offset in range(0, len(rows), 4):
            chunk = rows[offset:offset + 4]
            if len(chunk) < 2:
                continue
            output = strip_dir / f"strip_{strip_index:03d}.jpg"
            area = make_strip([Path(row["path"]) for row in chunk], output)
            pixel_area += area
            strips.append({
                "group_id": key, "path": str(output),
                "candidate_ids": [row["candidate_id"] for row in chunk], "pixel_area": area,
            })
            strip_index += 1
    # One 512px verification read is budgeted for every evidence group, including
    # singleton groups that do not need a strip. This is deliberately conservative:
    # it never reports a token saving by silently pretending singleton evidence is free.
    individual_reads = len(groups)
    projected = pixel_area + individual_reads * 512 * 288
    baseline = 60 * 512 * 288
    return {
        "instructions": "Read temporal strips first; open an individual candidate only when selected or uncertain.",
        "strips": strips,
        "pixel_area": pixel_area,
        "projected_individual_reads": individual_reads,
        "projected_total_pixel_area": projected,
        "baseline_60x512_pixel_area": baseline,
        "candidate_only_ratio": round((len(frames) * 512 * 288) / baseline, 4),
        "strip_to_baseline_ratio": round(pixel_area / baseline, 4),
        "projected_to_baseline_ratio": round(projected / baseline, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate transcript-aligned visual evidence candidates")
    parser.add_argument("source")
    parser.add_argument("--work", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--chapters", required=True)
    parser.add_argument("--mode", choices=sorted(MODE), default="light")
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--sections", help="Optional comma-separated source ranges start-end")
    parser.add_argument("--pins", help="Legacy comma-separated timestamps")
    parser.add_argument("--cues", help="Legacy comma-separated timestamps")
    parser.add_argument("--scene-threshold", type=float, default=None, help="Legacy compatibility flag")
    parser.add_argument("--no-dedup", action="store_true")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    transcript_payload, segments = validate_segments(_load_json(Path(args.transcript).expanduser().resolve()))
    duration = finite_number(transcript_payload.get("video", {}).get("duration"))
    chapters = validate_chapters(_load_json(Path(args.chapters).expanduser().resolve()), duration or None)
    targets = normalize_targets(chapters, segments, args.mode)
    requested_sections = _parse_sections(args.sections)
    if not requested_sections:
        requested_sections = [tuple(target["window"]) for target in targets]
        requested_sections.extend(
            (chapter["start"], chapter["end"])
            for chapter in chapters if chapter.get("needs_frames") and not chapter.get("visual_targets")
        )
    sections = merge_sections(requested_sections, duration, padding=1.0) if requested_sections else []
    parts, parts_manifest = prepare_media(args.source, work, sections, duration, exact=True)

    settings = MODE[args.mode]
    points: list[dict] = []
    for target in targets:
        samples = scan_window(parts, target["window"][0], target["window"][1], settings["scan_rate"])
        chosen = select_target_samples(samples, target, settings["per_target"])
        for sample in chosen:
            point = make_point(sample["t"], "target", chapters, segments, target)
            point["scene_score"] = sample.get("motion_in", 0.0)
            if sample.get("scene"):
                point["reasons"].add("transition")
            if sample.get("recovered"):
                point["reasons"].add("recovered")
            points.append(point)

    for chapter in chapters:
        if not chapter.get("needs_frames") or chapter.get("visual_targets"):
            continue
        samples = scan_window(parts, chapter["start"], chapter["end"], 1.0 if args.mode == "light" else 2.5)
        for sample in select_chapter_samples(samples, chapter, 1 if args.mode == "light" else 2):
            point = make_point(sample["t"], "coverage", chapters, segments)
            point["scene_score"] = sample.get("motion_in", 0.0)
            points.append(point)

    for timestamp in _parse_times(args.pins):
        points.append(make_point(timestamp, "pin", chapters, segments))
    for timestamp in _parse_times(args.cues):
        points.append(make_point(timestamp, "legacy-cue", chapters, segments))
    points = merge_points(points)

    candidate_dir = work / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    frames, extraction_dropped = _extract_points(
        points, parts, candidate_dir, max(32, min(512, args.resolution))
    )
    ownership_dropped = 0
    owned: list[dict] = []
    for frame in frames:
        owner = chapter_for_time(chapters, frame["actual_t"])
        if owner is None or owner["chapter_id"] != frame.get("chapter_id"):
            Path(frame["path"]).unlink(missing_ok=True)
            ownership_dropped += 1
            continue
        owned.append(frame)
    frames = owned

    dedup_dropped = 0
    if not args.no_dedup:
        frames, dedup_dropped = deduplicate_frames(frames)
    cap = min(settings["cap"], args.max_candidates or settings["cap"])
    frames, cap_dropped = _budget(frames, chapters, cap)

    for frame in frames:
        identifier = candidate_identifier(
            frame["chapter_id"], frame.get("target_ids", set()), frame["actual_t"],
            frame["quality"]["fingerprint"],
        )
        destination = candidate_dir / f"{identifier}_t{frame['actual_t']:010.3f}.jpg"
        current = Path(frame["path"])
        if current != destination:
            if destination.exists():
                current.unlink(missing_ok=True)
            else:
                current.replace(destination)
        frame["candidate_id"] = identifier
        frame["path"] = str(destination)

    coverage = coverage_report(chapters, frames)
    triage = _make_triage(frames, work)
    payload = {
        "schema_version": 3,
        "engine": "independent-visual-evidence-engine",
        "mode": args.mode,
        "media_cache_key": parts_manifest["cache_key"],
        "chapters": chapters,
        "counts": {
            "planned": len(points), "extraction_or_seek_dropped": extraction_dropped,
            "ownership_dropped": ownership_dropped, "dedup_dropped": dedup_dropped,
            "cap_dropped": cap_dropped, "final": len(frames),
        },
        "candidates": [_json_frame(frame) for frame in frames],
        "coverage": coverage,
        "triage": triage,
    }
    manifest_path = work / "candidates.json"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n# candidate frames report\n")
    print(f"- **Engine:** independent visual evidence engine v3")
    print(f"- **Mode:** {args.mode}")
    print(f"- **Candidates:** {len(frames)} (planned {len(points)}; dedup {dedup_dropped}; cap {cap_dropped})")
    print(f"- **Manifest:** `{manifest_path}`")
    print(
        f"- **Temporal strips:** {len(triage['strips'])}; projected image-read area vs "
        f"60×512 baseline: {triage['projected_to_baseline_ratio']:.1%}"
    )
    for strip in triage["strips"]:
        print(f"- `{strip['path']}` -> {', '.join(strip['candidate_ids'])}")
    for frame in frames:
        targets_text = ",".join(sorted(frame.get("target_ids", set()))) or "-"
        print(
            f"- `{frame['path']}` ({frame['candidate_id']}, actual_t={frame['actual_t']:.6f} "
            f"[{format_time(frame['actual_t'])}], chapter={frame['chapter_id']}, targets={targets_text})"
        )
    unresolved = [
        row for kind in ("chapters", "targets") for row in coverage[kind]
        if row["status"] == "unresolved"
    ]
    if unresolved:
        print("\n## Unresolved visual evidence")
        for row in unresolved:
            print(f"- {row.get('target_id') or row.get('chapter_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
