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
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
        "-ss", f"{_media_timestamp(part, timestamp):.3f}", "-i", part["path"],
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
        raise SystemExit(f"Bad selection name: {value!r}")
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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="grab", description="Re-extract selected candidate IDs at deliverable quality."
    )
    parser.add_argument("--work", required=True)
    parser.add_argument("--spec", required=True, help="selections.json keyed by candidate_id")
    parser.add_argument("--out-dir", required=True, help="Summary assets directory")
    parser.add_argument("--full-width", type=int, default=1280)
    parser.add_argument("--thumb-width", type=int, default=640)
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
            name = _safe_name(selection.get("name") or candidate_id)
            timestamp = float(candidate["actual_t"])
            source_frame = temp_dir / f"{name}-source.jpg"
            try:
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
            except (OSError, RuntimeError) as exc:
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
        "assets": assets,
        "duplicate_pairs": duplicate_pairs,
        "failures": failures,
    }
    manifest_path = out_dir / "assets-manifest.json"
    manifest_path.write_text(json.dumps(assets_manifest, indent=2), encoding="utf-8")

    print()
    print("# grab report")
    print()
    for asset in assets:
        print(
            f"- `{asset['candidate_id']}` -> `{asset['full']['file']}`, `{asset['thumb']['file']}` "
            f"at {format_time(asset['actual_t'])}"
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
