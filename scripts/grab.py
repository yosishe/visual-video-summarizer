#!/usr/bin/env python3
"""Re-decode selected candidate IDs and produce verified HTML assets."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import (  # noqa: E402
    compare_signatures,
    file_sha256,
    format_time,
    is_hard_duplicate,
    is_near_duplicate,
    probe_media,
    visual_signature,
)
from media_backend import extract_frame, load_parts  # noqa: E402


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
CROP_RE = re.compile(r"^(\d+):(\d+):(\d+):(\d+)$")


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe asset name {value!r}")
    return name


def _crop_filter(value: object) -> str | None:
    if value is None:
        return None
    match = CROP_RE.fullmatch(str(value))
    if not match:
        raise ValueError("crop must use integer w:h:x:y syntax")
    width, height, left, top = (int(group) for group in match.groups())
    if width <= 0 or height <= 0 or left < 0 or top < 0:
        raise ValueError("crop dimensions must be positive and offsets non-negative")
    return f"crop={width}:{height}:{left}:{top}"


def _render_variant(source: Path, output: Path, width: int, crop: str | None) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required")
    filters = []
    if crop:
        filters.append(crop)
    filters.append(f"scale=w='min({max(16, min(width, 1998))},iw)':h=-2:flags=lanczos")
    result = subprocess.run([
        executable, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source.resolve()), "-frames:v", "1", "-vf", ",".join(filters),
        "-q:v", "2", str(output.resolve()),
    ], capture_output=True, text=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(result.stderr.strip() or f"failed to render {output.name}")


def _asset_record(candidate: dict, name: str, actual_t: float, full: Path, thumb: Path) -> dict:
    full_meta = probe_media(full)
    thumb_meta = probe_media(thumb)
    return {
        "candidate_id": candidate["candidate_id"],
        "name": name,
        "chapter_id": candidate["chapter_id"],
        "requested_t": candidate["requested_t"],
        "actual_t": actual_t,
        "seg_ids": candidate.get("seg_ids", []),
        "target_ids": candidate.get("target_ids", []),
        "full": {
            "path": str(full), "file": full.name,
            "width": full_meta["width"], "height": full_meta["height"],
            "sha256": file_sha256(full),
        },
        "thumb": {
            "path": str(thumb), "file": thumb.name,
            "width": thumb_meta["width"], "height": thumb_meta["height"],
            "sha256": file_sha256(thumb),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and render selected frame assets")
    parser.add_argument("--work", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--full-width", type=int, default=1280)
    parser.add_argument("--thumb-width", type=int, default=640)
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_payload = _load(work / "candidates.json")
    if not isinstance(candidate_payload, dict) or candidate_payload.get("schema_version") != 3:
        raise SystemExit("candidates.json is not an independent-engine schema v3 manifest")
    candidate_map = {
        str(row.get("candidate_id")): row for row in candidate_payload.get("candidates", [])
    }
    selections = _load(Path(args.spec).expanduser().resolve())
    if not isinstance(selections, list) or not selections:
        raise SystemExit("selections.json must be a non-empty array")
    if len(selections) > 20:
        raise SystemExit("global HTML frame budget exceeded (maximum 20)")
    if len({str(row.get("candidate_id")) for row in selections}) != len(selections):
        raise SystemExit("selections.json contains a repeated candidate_id")
    parts = load_parts(work)

    assets: list[dict] = []
    signatures: list[tuple[str, dict]] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="independent-grab-", dir=str(out_dir)) as temporary:
        temp_dir = Path(temporary)
        for index, selection in enumerate(selections):
            candidate_id = str(selection.get("candidate_id") or "")
            candidate = candidate_map.get(candidate_id)
            if candidate is None:
                failures.append(f"selection {index}: unknown candidate_id {candidate_id!r}")
                continue
            try:
                if selection.get("chapter_id") != candidate.get("chapter_id"):
                    raise ValueError("selection chapter does not match timestamp-derived candidate chapter")
                name = _safe_name(selection.get("name"))
                crop = _crop_filter(selection.get("crop"))
                decoded = temp_dir / f"{candidate_id}.jpg"
                actual_t, decoded_part = extract_frame(
                    parts, float(candidate["actual_t"]), decoded, args.full_width
                )
                frame_tolerance = max(
                    0.1,
                    float(decoded_part.get("frame_duration") or 0.04) * 2.5,
                )
                if abs(actual_t - float(candidate["actual_t"])) > frame_tolerance:
                    raise RuntimeError(
                        f"re-decode timestamp drifted from {candidate['actual_t']:.6f}s to {actual_t:.6f}s"
                    )
                candidate_signature = visual_signature(Path(candidate["path"]))
                decoded_signature = visual_signature(decoded)
                if not is_near_duplicate(candidate_signature, decoded_signature):
                    delta = compare_signatures(candidate_signature, decoded_signature)
                    raise RuntimeError(
                        "re-decoded pixels do not match the candidate "
                        f"(luma={delta['luma_mad']:.2f}, changed={delta['changed_ratio']:.1%}, "
                        f"tiles={delta['active_tile_ratio']:.1%})"
                    )
                full = out_dir / f"{name}-full.jpg"
                thumb = out_dir / f"{name}-thumb.jpg"
                _render_variant(decoded, full, args.full_width, crop)
                _render_variant(decoded, thumb, args.thumb_width, crop)
                full_signature = visual_signature(full)
                signatures.append((candidate_id, full_signature))
                assets.append(_asset_record(candidate, name, actual_t, full, thumb))
            except (OSError, RuntimeError, ValueError) as exc:
                failures.append(f"{candidate_id or index}: {exc}")

    duplicates: list[dict] = []
    for first_index, (first_id, first_signature) in enumerate(signatures):
        for second_id, second_signature in signatures[first_index + 1:]:
            if is_hard_duplicate(first_signature, second_signature):
                duplicates.append({
                    "first": first_id, "second": second_id,
                    "delta": compare_signatures(first_signature, second_signature),
                })
    manifest = {
        "schema_version": 3,
        "engine": "independent-visual-evidence-engine",
        "candidate_manifest_schema": candidate_payload.get("schema_version"),
        "assets": assets,
        "duplicate_pairs": duplicates,
        "failures": failures,
    }
    manifest_path = out_dir / "assets-manifest.json"
    temporary_manifest = out_dir / ".assets-manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary_manifest.replace(manifest_path)

    print("\n# verified asset report\n")
    for asset in assets:
        print(
            f"- `{asset['candidate_id']}` -> `{asset['full']['file']}` and "
            f"`{asset['thumb']['file']}` at {asset['actual_t']:.6f}s "
            f"[{format_time(asset['actual_t'])}]"
        )
    if failures:
        print("\n## Failures")
        for failure in failures:
            print(f"- {failure}")
    if duplicates:
        print("\n## Hard duplicates")
        for pair in duplicates:
            print(f"- {pair['first']} duplicates {pair['second']}")
    print(f"\n**Assets manifest:** `{manifest_path}`")
    return 2 if failures else (3 if duplicates else 0)


if __name__ == "__main__":
    raise SystemExit(main())
