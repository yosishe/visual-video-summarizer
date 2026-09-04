#!/usr/bin/env python3
"""Verified deliverable-quality extraction for selected candidate IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from candidates import part_for, resolve_parts  # noqa: E402
from frame_utils import (  # noqa: E402
    blur_signature_series,
    choose_refined_frame,
    compare_signatures,
    format_time,
    is_hard_duplicate,
    is_near_duplicate,
    probe_media,
    visual_signature,
)

MAX_READ_DIMENSION = 1998
CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")
SHOWINFO_TS_RE = re.compile(r"pts_time:([-0-9.]+)")
# Sharpness refinement (adapted from CZX2244/dsh-bilibili): look ±1.5 s around
# the triaged frame for the sharpest frame that is still the same picture.
REFINE_HALF_WINDOW = 1.5
REFINE_CHAPTER_MARGIN = 0.25


def _media_timestamp(part: dict, absolute_timestamp: float) -> float:
    return float(part.get("media_start", 0.0)) + (
        absolute_timestamp - float(part["source_start"])
    )


def _absolute_timestamp(part: dict, media_timestamp: float) -> float:
    return float(part["source_start"]) + (
        media_timestamp - float(part.get("media_start", 0.0))
    )


def _scale_filter(width: int) -> str:
    return (
        f"scale=w='min({width},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _extract_source(parts: list[dict], timestamp: float, output: Path, width: int) -> float:
    part = part_for(parts, timestamp)
    if part is None:
        raise RuntimeError(f"t={format_time(timestamp)} is outside downloaded media")
    # `timestamp` is a decoded pts rounded to 3 decimals. Seeking to it exactly
    # lands on the NEXT frame whenever the rounding went up (pts 903.8029 →
    # 903.803): harmless on a static slide, a different picture mid-pan — and
    # the verification gate then rightly refuses. Aim half a frame early so
    # the first frame at or after the target is the intended one.
    seek_t = _media_timestamp(part, timestamp) - float(part.get("frame_duration", 0.04)) / 2
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
        "-ss", f"{max(0.0, seek_t):.4f}", "-i", part["path"],
        "-frames:v", "1", "-vf", f"showinfo,{_scale_filter(width)}",
        "-q:v", "2", str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = SHOWINFO_TS_RE.search(result.stderr)
    if result.returncode != 0 or not output.exists() or not match:
        raise RuntimeError(f"ffmpeg grab failed: {result.stderr.strip()}")
    actual = _absolute_timestamp(part, float(match.group(1)))
    tolerance = max(0.10, float(part.get("frame_duration", 0.04)) * 2.5)
    if abs(actual - timestamp) > tolerance:
        raise RuntimeError(
            f"seek drift for {format_time(timestamp)}: decoded {actual:.3f}s "
            f"(tolerance {tolerance:.3f}s)"
        )
    return round(actual, 3)


def _render_asset(source: Path, output: Path, width: int, crop: str | None) -> None:
    filters: list[str] = []
    if crop:
        filters.append(f"crop={crop}")
    filters.append(_scale_filter(width))
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-frames:v", "1", "-vf", ",".join(filters), "-q:v", "2", str(output),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"asset render failed for {output.name}: {result.stderr.strip()}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError(f"bad selection name {value!r}; use letters, digits, _ or -")
    return name


def _load_candidates(work: Path) -> tuple[dict, dict[str, dict]]:
    path = work / "candidates.json"
    if not path.exists():
        raise SystemExit(f"Missing candidate manifest: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = {
        str(candidate.get("candidate_id") or candidate.get("frame_id")): candidate
        for candidate in payload.get("candidates", [])
    }
    return payload, candidates


def _load_chapters(work: Path) -> dict[str, tuple[float, float]]:
    """Chapter windows by id; empty when chapters.json is absent (refinement is
    then skipped — it must never move a frame across a chapter boundary)."""
    path = work / "chapters.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    windows: dict[str, tuple[float, float]] = {}
    if isinstance(rows, list):
        for index, row in enumerate(rows):
            chapter_id = str(row.get("chapter_id") or f"ch{index + 1:02d}")
            try:
                windows[chapter_id] = (float(row["start"]), float(row["end"]))
            except (KeyError, TypeError, ValueError):
                continue
    return windows


def _refine_selection(
    parts: list[dict],
    candidate_signature: dict,
    triaged_t: float,
    chapter_window: tuple[float, float] | None,
) -> dict:
    """Sharpness refinement, zero tokens: one blurdetect+signature pass over
    ±1.5 s (clamped inside the chapter and the media), then the sharpest frame
    that is still a near-duplicate of the triaged candidate. Never raises."""
    part = part_for(parts, triaged_t)
    if part is None:
        return {"t": triaged_t, "applied": False, "reason": "outside-media"}
    part_start = float(part["source_start"])
    part_end = part_start + float(part["duration"])
    lo = max(triaged_t - REFINE_HALF_WINDOW, part_start)
    hi = min(triaged_t + REFINE_HALF_WINDOW, part_end)
    if chapter_window is not None:
        lo = max(lo, chapter_window[0])
        hi = min(hi, chapter_window[1] - REFINE_CHAPTER_MARGIN)
    if hi - lo < 0.1:
        return {"t": triaged_t, "applied": False, "reason": "window-too-small"}
    series = blur_signature_series(part["path"], _media_timestamp(part, lo), hi - lo)
    for row in series:
        row["t"] = _absolute_timestamp(part, row["t"])
    return choose_refined_frame(candidate_signature, series, triaged_t)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="grab", description="Re-extract selected candidate IDs at deliverable quality."
    )
    parser.add_argument("--work", required=True)
    parser.add_argument("--spec", required=True, help="selections.json keyed by candidate_id")
    parser.add_argument("--out-dir", required=True, help="Summary assets directory")
    parser.add_argument("--full-width", type=int, default=1280)
    parser.add_argument("--thumb-width", type=int, default=640)
    parser.add_argument("--refine", choices=("sharpness", "none"), default=None,
                        help="Sharpest near-duplicate within ±1.5 s of the triaged frame "
                             "(default: the tier recorded in candidates.json; high = sharpness)")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_payload, candidates = _load_candidates(work)
    selections = json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8"))
    if not isinstance(selections, list) or not selections:
        raise SystemExit("selections.json must be a non-empty array")
    if len(selections) > 20:
        raise SystemExit("HTML frame budget exceeded: selections.json contains more than 20 frames")
    parts = resolve_parts(None, work)
    refine = args.refine or str(candidate_payload.get("profile", {}).get("refine") or "none")
    chapter_windows = _load_chapters(work) if refine != "none" else {}
    if refine != "none" and not chapter_windows:
        print("[vsum] chapters.json not found in work dir: refinement skipped", file=sys.stderr)
        refine = "none"

    assets: list[dict] = []
    failures: list[str] = []
    full_signatures: list[tuple[dict, dict]] = []
    with tempfile.TemporaryDirectory(prefix="vsum-grab-", dir=str(out_dir)) as temporary:
        temp_dir = Path(temporary)
        for index, selection in enumerate(selections):
            candidate_id = str(selection.get("candidate_id") or "")
            candidate = candidates.get(candidate_id)
            if candidate is None:
                failures.append(f"selection {index}: unknown candidate_id {candidate_id!r}")
                continue
            if selection.get("chapter_id") and selection["chapter_id"] != candidate.get("chapter_id"):
                failures.append(
                    f"{candidate_id}: selection chapter {selection['chapter_id']} != candidate chapter {candidate.get('chapter_id')}"
                )
                continue
            timestamp = float(candidate["actual_t"])
            try:
                name = _safe_name(selection.get("name") or candidate_id)
                source_frame = temp_dir / f"{name}-source.jpg"
                # 1. The frame the model triaged, re-decoded and verified.
                actual = _extract_source(parts, timestamp, source_frame, args.full_width)
                candidate_signature = visual_signature(candidate["path"])
                source_signature = visual_signature(source_frame)
                if not is_near_duplicate(candidate_signature, source_signature):
                    delta = compare_signatures(candidate_signature, source_signature)
                    raise RuntimeError(
                        "re-grab does not match candidate "
                        f"(luma={delta['luma_mad']:.2f}, edge={delta['edge_mad']:.2f}, "
                        f"changed={delta['changed_ratio']:.1%})"
                    )
                # 2. Optional refinement: sharpest frame that is still that picture,
                #    verified again after the seek. A failed refinement keeps step 1.
                refinement: dict | None = None
                if refine == "sharpness":
                    chapter_window = chapter_windows.get(str(candidate.get("chapter_id")))
                    refinement = _refine_selection(parts, candidate_signature, timestamp, chapter_window)
                    if refinement.get("applied"):
                        refined_frame = temp_dir / f"{name}-refined.jpg"
                        try:
                            refined_actual = _extract_source(
                                parts, float(refinement["t"]), refined_frame, args.full_width
                            )
                            refined_signature = visual_signature(refined_frame)
                            inside_chapter = chapter_window is None or (
                                chapter_window[0] <= refined_actual < chapter_window[1]
                            )
                            if inside_chapter and is_near_duplicate(candidate_signature, refined_signature):
                                source_frame = refined_frame
                                actual = refined_actual
                                refinement["t"] = refined_actual
                                refinement["delta_s"] = round(refined_actual - timestamp, 3)
                            else:
                                refinement["applied"] = False
                                refinement["fallback"] = "gate-failed" if inside_chapter else "outside-chapter"
                        except RuntimeError as exc:
                            refinement["applied"] = False
                            refinement["fallback"] = f"extract-failed: {exc}"
                crop = selection.get("crop")
                if crop is not None and not CROP_RE.fullmatch(str(crop)):
                    raise RuntimeError(
                        f"bad crop {crop!r}; expected integer ffmpeg crop syntax w:h:x:y"
                    )
                full_path = out_dir / f"{name}-full.jpg"
                thumb_path = out_dir / f"{name}-thumb.jpg"
                _render_asset(source_frame, full_path, args.full_width, crop)
                _render_asset(source_frame, thumb_path, args.thumb_width, crop)
                full_signature = visual_signature(full_path)
                full_signatures.append((selection, full_signature))
                full_meta = probe_media(full_path)
                thumb_meta = probe_media(thumb_path)
                assets.append({
                    "candidate_id": candidate_id,
                    "name": name,
                    "chapter_id": candidate.get("chapter_id"),
                    "requested_t": candidate.get("requested_t"),
                    "actual_t": actual,
                    "triaged_t": timestamp,
                    "refinement": refinement,
                    "seg_ids": candidate.get("seg_ids", []),
                    "target_ids": candidate.get("target_ids", []),
                    "full": {
                        "path": str(full_path), "file": full_path.name,
                        "width": full_meta["width"], "height": full_meta["height"],
                        "sha256": _sha256(full_path),
                    },
                    "thumb": {
                        "path": str(thumb_path), "file": thumb_path.name,
                        "width": thumb_meta["width"], "height": thumb_meta["height"],
                        "sha256": _sha256(thumb_path),
                    },
                })
            except (OSError, RuntimeError, ValueError) as exc:
                failures.append(f"{candidate_id}: {exc}")

    duplicate_pairs: list[dict] = []
    for first_index in range(len(full_signatures)):
        for second_index in range(first_index + 1, len(full_signatures)):
            first_selection, first_signature = full_signatures[first_index]
            second_selection, second_signature = full_signatures[second_index]
            if is_hard_duplicate(first_signature, second_signature):
                duplicate_pairs.append({
                    "first": first_selection["candidate_id"],
                    "second": second_selection["candidate_id"],
                    "delta": compare_signatures(first_signature, second_signature),
                })

    assets_manifest = {
        "schema_version": 2,
        "candidate_manifest_schema": candidate_payload.get("schema_version", 1),
        "tier": candidate_payload.get("tier"),
        "refine": refine,
        "assets": assets,
        "duplicate_pairs": duplicate_pairs,
        "failures": failures,
    }
    manifest_path = out_dir / "assets-manifest.json"
    manifest_path.write_text(json.dumps(assets_manifest, indent=2), encoding="utf-8")

    print()
    print("# grab report")
    print()
    applied = [asset for asset in assets if (asset.get("refinement") or {}).get("applied")]
    if refine != "none":
        print(f"- **Refinement:** {refine}; moved {len(applied)}/{len(assets)} frames to a sharper near-duplicate")
    for asset in assets:
        note = ""
        refinement = asset.get("refinement") or {}
        if refinement.get("applied"):
            note = (
                f" (refined {refinement['delta_s']:+.3f}s: blur "
                f"{refinement['blur_before']:.2f} → {refinement['blur_after']:.2f})"
            )
        print(
            f"- `{asset['candidate_id']}` -> `{asset['full']['file']}`, `{asset['thumb']['file']}` "
            f"at {format_time(asset['actual_t'])}{note}"
        )
    if failures:
        print("\n## Failures")
        for failure in failures:
            print(f"- {failure}")
    if duplicate_pairs:
        print("\n## Duplicate selections")
        for pair in duplicate_pairs:
            print(f"- `{pair['first']}` duplicates `{pair['second']}`")
    print(f"\n**Assets manifest:** `{manifest_path}`")
    if failures:
        return 2
    if duplicate_pairs:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
