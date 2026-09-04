#!/usr/bin/env python3
"""Second-stage triage read: re-decode a shortlist of candidates at a legible
width (640 standard / 768 high) — the sheets said *which* pictures matter;
this says *what they contain* — while the pixel-verification gate still holds:
every shortlist frame is checked to be a near-duplicate (overlay-masked) of
the 512 px candidate the ids refer to, so nothing the model sees is a
different picture from what grab will later write.

    python3 shortlist.py --work <work> --ids c_0003,c_0011,... [--width 768]

Writes `<work>/shortlist/<candidate_id>.jpg` and prints the paths with their
visual-token cost. A frame that fails the gate is reported and left out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from candidates import resolve_parts  # noqa: E402
from frame_utils import is_near_duplicate, visual_signature  # noqa: E402
from grab import _extract_source, _load_candidates  # noqa: E402
from layout import overlay_mask  # noqa: E402
from sheets import image_tokens  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", required=True)
    parser.add_argument("--ids", required=True, help="comma-separated candidate ids")
    parser.add_argument("--width", type=int, default=None, help="default: profile shortlist_px, else 640")
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve()
    payload, candidates = _load_candidates(work)
    width = args.width or int(payload.get("profile", {}).get("shortlist_px") or 640)
    parts = resolve_parts(None, work)
    mask = overlay_mask(payload.get("overlays") or [])
    out_dir = work / "shortlist"
    out_dir.mkdir(exist_ok=True)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    limit = min(30, int((payload.get("token_budget") or {}).get("shortlist_max", 30)))
    if len(ids) > limit:
        raise SystemExit(f"a shortlist is at most {limit} frames here (token budget "
                         f"{(payload.get('token_budget') or {}).get('budget', 'n/a')}); narrow it on the sheets first")
    written, failures, tokens = [], [], 0
    for candidate_id in ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            failures.append(f"{candidate_id}: unknown candidate id")
            continue
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
        from frame_utils import probe_media
        dims = probe_media(str(out))
        cost = image_tokens(int(dims["width"]), int(dims["height"]))
        tokens += cost
        written.append((candidate_id, out, actual, cost))
    print("# shortlist\n")
    print(f"- **{len(written)} frames at {width}px ≈ {tokens:,} image tokens** — read them all in one message, "
          "then write selections.json by candidate_id (these are the same pictures as the 512px candidates, verified).\n")
    for candidate_id, out, actual, cost in written:
        print(f"- `{out}` ({candidate_id}, actual_t={actual:.3f}, {cost} tokens)")
    if failures:
        print("\nNot written:")
        for failure in failures:
            print(f"- {failure}")
    return 2 if failures and not written else 0


if __name__ == "__main__":
    sys.exit(main())
