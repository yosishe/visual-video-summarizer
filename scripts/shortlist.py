#!/usr/bin/env python3
"""Second-stage triage read: re-decode a shortlist of candidates at a legible
width (640 standard / 768 high) — the sheets said *which* pictures matter;
this says *what they contain* — while the pixel-verification gate still holds:
every shortlist frame is checked to be a near-duplicate (overlay-masked) of
the 512 px candidate the ids refer to, so nothing the model sees is a
different picture from what grab will later write.

    python shortlist.py --work <work> --ids c_0003,c_0011,... [--width 768]

Writes `<work>/shortlist/<candidate_id>.jpg`, prints the paths with their
visual-token cost, and records a `shortlist` receipt inside candidates.json
(ids requested, frames written with hashes, failures, and the digest of the
candidate pool it belongs to) so later stages can prove the triage happened.
Exit 10 = an id is not a candidate; exit 2 = a frame could not be written or
failed the gate (the others are still listed); exit 0 = every id was written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from candidates import resolve_cached_parts  # noqa: E402
from frame_utils import is_near_duplicate, probe_media, visual_signature  # noqa: E402
from gates import GateError, candidates_digest, sha256_file  # noqa: E402
from grab import _extract_source, _load_candidates  # noqa: E402
from hostenv import utf8_stdio  # noqa: E402
from layout import overlay_mask  # noqa: E402
from safety import atomic_write  # noqa: E402
from sheets import image_tokens  # noqa: E402

HISTORY_LIMIT = 5


def write_receipt(work: Path, payload: dict, receipt: dict) -> None:
    """Record the shortlist inside candidates.json without changing the pool's digest."""
    history = list(payload.get("shortlist_history") or [])
    previous = payload.get("shortlist")
    if previous:
        history = ([previous] + history)[:HISTORY_LIMIT]
    payload["shortlist"] = receipt
    if history:
        payload["shortlist_history"] = history
    atomic_write(work / "candidates.json", json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", required=True, help="the work directory holding candidates.json")
    parser.add_argument("--ids", required=True, help="comma-separated candidate ids kept from the contact sheets")
    parser.add_argument("--width", type=int, default=None, help="default: profile shortlist_px, else 640")
    args = parser.parse_args()
    utf8_stdio()
    work = Path(args.work).expanduser().resolve()
    payload, candidates = _load_candidates(work)
    width = args.width or int(payload.get("profile", {}).get("shortlist_px") or 640)
    parts = resolve_cached_parts(work, (payload.get("inputs") or {}).get("cache_key"))
    mask = overlay_mask(payload.get("overlays") or [])
    out_dir = work / "shortlist"
    out_dir.mkdir(exist_ok=True)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        raise GateError("--ids is empty; list the candidate ids kept from the contact sheets")
    unknown = [candidate_id for candidate_id in ids if candidate_id not in candidates]
    if unknown:
        raise GateError(f"not candidate ids in candidates.json: {unknown} — copy the burned-in ids exactly")
    limit = min(30, int((payload.get("token_budget") or {}).get("shortlist_max", 30)))
    if len(ids) > limit:
        raise SystemExit(f"a shortlist is at most {limit} frames here (token budget "
                         f"{(payload.get('token_budget') or {}).get('budget', 'n/a')}); narrow it on the sheets first")
    written, failures, tokens = [], [], 0
    for candidate_id in ids:
        candidate = candidates[candidate_id]
        out = out_dir / f"{candidate_id}.jpg"
        try:
            actual = _extract_source(parts, float(candidate["actual_t"]), out, width)
        except RuntimeError as exc:
            failures.append(f"{candidate_id}: {exc}")
            continue
        if not is_near_duplicate(visual_signature(candidate["path"], mask), visual_signature(out, mask)):
            failures.append(f"{candidate_id}: re-decoded frame is not the candidate's picture")
            out.unlink(missing_ok=True)
            continue
        dims = probe_media(str(out))
        cost = image_tokens(int(dims["width"]), int(dims["height"]))
        tokens += cost
        written.append((candidate_id, out, actual, cost))
    receipt = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "width": width,
        "requested_ids": ids,
        "written": [{"candidate_id": candidate_id, "path": str(out), "actual_t": round(actual, 3),
                     "tokens": cost, "sha256": sha256_file(out)} for candidate_id, out, actual, cost in written],
        "failures": failures,
        "image_tokens": tokens,
        "candidates_sha256": candidates_digest(payload),
    }
    write_receipt(work, payload, receipt)
    print("# shortlist\n")
    print(f"- **{len(written)} frames at {width}px ≈ {tokens:,} image tokens** — read them all in one message, "
          "then write selections.json by candidate_id (these are the same pictures as the 512px candidates, verified).\n")
    for candidate_id, out, actual, cost in written:
        print(f"- `{out}` ({candidate_id}, actual_t={actual:.3f}, {cost} tokens)")
    if failures:
        print("\nNot written (drop these ids or re-run candidates.py if the download changed):")
        for failure in failures:
            print(f"- {failure}")
    print(f"\nReceipt recorded in `{work / 'candidates.json'}` (`shortlist` block).")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
