#!/usr/bin/env python3
"""Score a benchmark run against human annotations.

    python3 bench/score.py --run bench/runs/<run> [--seeds 5] [--allow-draft]

Reads, per video directory under the run: `candidates.json`, `selections.json`
(optional — without it only pool metrics are reported), `dropped.json`
(optional, engine ≥ 1.4), `chapters.json`, `summary.json` (optional),
`cost.json` (optional, written by run.py). Reads the human annotation from
`bench/annotations/<id>.json` (a `.draft.json` only with --allow-draft).

Writes `<run>/scores.json` and `<run>/REPORT.md`. Metrics are reported
separately and never fused into one F-score (Otani et al., CVPR 2019); every
recall figure sits next to uniform and random baselines at the same budget and
the Performance-over-Random ratio (Apostolidis et al., ACM MM 2020).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path

LEAD_TOLERANCE = 1.0  # a frame this far before the annotated interval still counts
PATCH = 28            # Anthropic visual token = 28×28 patch (verified 2026-09-04)

NIQQUD_RE = re.compile(r"[ְ-ׇ]")
BIDI_CONTROL_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")
HEBREW_RE = re.compile(r"[א-ת]")
LATIN_RE = re.compile(r"[A-Za-z]")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def image_tokens(width: int, height: int) -> int:
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


# ---------------------------------------------------------------- annotations

def group_root(states: dict[str, dict], state_id: str) -> str:
    seen = set()
    while state_id in states and states[state_id].get("duplicates_of") and state_id not in seen:
        seen.add(state_id)
        state_id = states[state_id]["duplicates_of"]
    return state_id


def state_at(states: list[dict], t: float) -> dict | None:
    """The annotated state a frame at `t` belongs to (lead tolerance applied)."""
    best = None
    for state in states:
        if state["start"] - LEAD_TOLERANCE <= t <= state["end"]:
            if best is None or state["start"] > best["start"]:
                best = state
    return best


# ---------------------------------------------------------------- windows

def needs_frames_windows(chapters: list[dict], duration: float) -> list[tuple[float, float]]:
    windows = [(float(c["start"]), float(c["end"])) for c in chapters if c.get("needs_frames", True)]
    return windows or [(0.0, duration)]


def uniform_times(windows: list[tuple[float, float]], budget: int) -> list[float]:
    total = sum(end - start for start, end in windows)
    if budget <= 0 or total <= 0:
        return []
    times = []
    for i in range(budget):
        offset = (i + 0.5) / budget * total
        for start, end in windows:
            span = end - start
            if offset <= span:
                times.append(round(start + offset, 3))
                break
            offset -= span
    return times


def random_times(windows: list[tuple[float, float]], budget: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    total = sum(end - start for start, end in windows)
    times = []
    for _ in range(budget):
        offset = rng.random() * total
        for start, end in windows:
            span = end - start
            if offset <= span:
                times.append(round(start + offset, 3))
                break
            offset -= span
    return times


# ---------------------------------------------------------------- metrics

def recall_of(times: list[float], essential: list[dict]) -> tuple[float, list[str]]:
    hit_ids = []
    for state in essential:
        if any(state["start"] - LEAD_TOLERANCE <= t <= state["end"] for t in times):
            hit_ids.append(state["state_id"])
    return (len(hit_ids) / len(essential) if essential else float("nan")), hit_ids


def score_video(video_dir: Path, annotation: dict, seeds: int) -> dict:
    candidates_payload = load_json(video_dir / "candidates.json")
    candidates = candidates_payload.get("candidates") or candidates_payload.get("frames") or []
    chapters = load_json(video_dir / "chapters.json") if (video_dir / "chapters.json").exists() else []
    selections = load_json(video_dir / "selections.json") if (video_dir / "selections.json").exists() else None
    dropped = load_json(video_dir / "dropped.json") if (video_dir / "dropped.json").exists() else None
    summary = load_json(video_dir / "summary.json") if (video_dir / "summary.json").exists() else None
    cost_file = load_json(video_dir / "cost.json") if (video_dir / "cost.json").exists() else {}

    duration = float(annotation.get("duration") or candidates_payload.get("video", {}).get("duration") or 0)
    states = annotation["states"]
    by_id = {s["state_id"]: s for s in states}
    essential = [s for s in states if s.get("class") == "essential"]
    acceptable_ids = {s["state_id"] for s in states if s.get("class") in ("essential", "acceptable")}

    by_candidate = {c["candidate_id"]: c for c in candidates}
    pool_times = [float(c["actual_t"]) for c in candidates]
    pool_recall, pool_hits = recall_of(pool_times, essential)

    result = {
        "video_id": annotation["video_id"],
        "duration": duration,
        "essential_states": len(essential),
        "candidates": len(candidates),
        "pool_recall": pool_recall,
        "pool_missed": [s["state_id"] for s in essential if s["state_id"] not in pool_hits],
        "image_tokens": sum(image_tokens(int(c.get("width", 512)), int(c.get("height", 288))) for c in candidates),
        "cpu_seconds": cost_file.get("candidates_wall_s"),
        "engine_cost": candidates_payload.get("cost"),
    }
    result["image_tokens_per_min"] = result["image_tokens"] / (duration / 60) if duration else None

    if selections is None:
        result["selected"] = None
        return result

    selected_times = []
    selected_rows = []
    for selection in selections:
        candidate = by_candidate.get(selection["candidate_id"])
        if candidate is None:
            continue
        t = float(candidate["actual_t"])
        state = state_at(states, t)
        selected_times.append(t)
        selected_rows.append({
            "candidate_id": selection["candidate_id"],
            "t": t,
            "chapter_id": selection.get("chapter_id"),
            "state_id": state["state_id"] if state else None,
            "class": state.get("class") if state else "outside",
            "group": group_root(by_id, state["state_id"]) if state else None,
            "anchor_seg_ids": selection.get("anchor_seg_ids", []),
        })

    budget = len(selected_rows)
    recall, hits = recall_of(selected_times, essential)
    precision = (sum(1 for r in selected_rows if r["state_id"] in acceptable_ids) / budget) if budget else float("nan")
    seen_groups: set[str] = set()
    redundant = 0
    for row in selected_rows:
        if row["group"] is None:
            continue
        if row["group"] in seen_groups:
            redundant += 1
        seen_groups.add(row["group"])
    essential_hits = sum(1 for r in selected_rows if r["class"] == "essential")
    aligned = 0
    alignable = 0
    for row in selected_rows:
        if row["class"] != "essential":
            continue
        spoken = set(by_id[row["state_id"]].get("spoken_seg_ids") or [])
        if not spoken:
            continue
        alignable += 1
        if spoken & set(row["anchor_seg_ids"]):
            aligned += 1

    windows = needs_frames_windows(chapters, duration)
    uniform_recall, _ = recall_of(uniform_times(windows, budget), essential)
    random_recalls = [recall_of(random_times(windows, budget, seed), essential)[0] for seed in range(seeds)]
    random_mean = statistics.fmean(random_recalls) if random_recalls else float("nan")
    # Uniform over the *pool* — the fair baseline for triage (same candidates, no judgement).
    if candidates and budget:
        indices = sorted({round(i * (len(candidates) - 1) / max(budget - 1, 1)) for i in range(budget)})
        pool_uniform_recall, _ = recall_of([pool_times[i] for i in indices], essential)
    else:
        pool_uniform_recall = float("nan")

    missed = []
    for state in essential:
        if state["state_id"] in hits:
            continue
        in_pool = any(state["start"] - LEAD_TOLERANCE <= t <= state["end"] for t in pool_times)
        if in_pool:
            reason = "triage_rejected"
        elif dropped:
            reasons = {d.get("reason") for d in dropped
                       if state["start"] - LEAD_TOLERANCE <= float(d.get("t", -1)) <= state["end"]}
            reason = ("dedup_dropped" if "dedup" in reasons else
                      "cap_dropped" if "cap" in reasons else
                      "blank_dropped" if "blank" in reasons else "not_in_pool")
        else:
            reason = "not_in_pool"
        missed.append({"state_id": state["state_id"], "label": state.get("label"), "reason": reason})

    result.update({
        "selected": budget,
        "important_visual_recall": recall,
        "precision": precision,
        "redundancy_rate": (redundant / budget) if budget else float("nan"),
        "frame_efficiency": (essential_hits / budget) if budget else float("nan"),
        "alignment_accuracy": (aligned / alignable) if alignable else None,
        "alignment_evaluated_frames": alignable,
        "baselines": {
            "uniform_time": uniform_recall,
            "uniform_pool": pool_uniform_recall,
            "random_mean": random_mean,
            "random_sd": statistics.pstdev(random_recalls) if len(random_recalls) > 1 else 0.0,
        },
        "por": (recall / random_mean) if random_mean else None,
        "missed": missed,
        "selected_rows": selected_rows,
    })
    if summary is not None:
        result["summary"] = score_summary(summary, annotation)
    return result


def score_summary(summary: dict, annotation: dict) -> dict:
    blocks = [(b.get("text", ""), set(b.get("seg_ids", []))) for ch in summary.get("chapters", []) for b in ch.get("blocks", [])]
    prose_blocks = [(b.get("text", ""), set(b.get("seg_ids", []))) for ch in summary.get("chapters", [])
                    for b in ch.get("blocks", []) if (b.get("kind") or "prose") == "prose"]
    checklist = annotation.get("summary_checklist") or []
    got = 0.0
    total = 0.0
    details = []
    for claim in checklist:
        weight = float(claim.get("weight", 1))
        total += weight
        cited = {s for s in claim.get("seg_ids", [])}
        texts = [t for t, segs in blocks if not cited or (segs & cited)] or [t for t, _ in blocks]
        haystack = " ".join(texts)
        # each entry is a token or a list of alternatives (language variants: ["memory", "זיכרון"])
        tokens = claim.get("must_tokens") or []
        folded = haystack.casefold()
        present = all(
            any(alt.casefold() in folded for alt in (tok if isinstance(tok, list) else [tok]))
            for tok in tokens
        ) if tokens else None
        if present:
            got += weight
        details.append({"claim_id": claim.get("claim_id"), "present": present})
    all_text = " ".join(t for t, _ in blocks) + " " + str(summary.get("overview", ""))
    hebrew = len(HEBREW_RE.findall(all_text))
    latin = len(LATIN_RE.findall(all_text))
    leading_latin = sum(1 for t, _ in prose_blocks if t.strip() and LATIN_RE.match(t.strip()[0]))
    return {
        "coverage": (got / total) if total else None,
        "claims": details,
        "lang": summary.get("lang", "en"),
        "words": len(all_text.split()),
        "hebrew_ratio": (hebrew / (hebrew + latin)) if (hebrew + latin) else None,
        "niqqud": len(NIQQUD_RE.findall(all_text)),
        "bidi_controls": len(BIDI_CONTROL_RE.findall(all_text)),
        "blocks_leading_latin": leading_latin,
        "dashes": all_text.count("—") + all_text.count("–"),
    }


# ---------------------------------------------------------------- report

def pct(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:.0f}%"


def render_report(run_name: str, results: list[dict]) -> str:
    lines = [f"# Benchmark report — `{run_name}`", ""]
    lines.append("| video | ess. | pool | pool recall | sel. | IVR | precision | redund. | eff. | align | uniform(t) | uniform(pool) | random | PoR | img tokens/min |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        b = r.get("baselines", {})
        align = r.get("alignment_accuracy")
        lines.append(
            f"| {r['video_id']} | {r['essential_states']} | {r['candidates']} | {pct(r['pool_recall'])} | "
            f"{r.get('selected') if r.get('selected') is not None else '—'} | {pct(r.get('important_visual_recall'))} | "
            f"{pct(r.get('precision'))} | {pct(r.get('redundancy_rate'))} | {pct(r.get('frame_efficiency'))} | "
            f"{pct(align) if align is not None else '—'} | {pct(b.get('uniform_time'))} | {pct(b.get('uniform_pool'))} | "
            f"{pct(b.get('random_mean'))} | {('%.2f' % r['por']) if r.get('por') else '—'} | "
            f"{r['image_tokens_per_min']:.0f} |" if r.get("image_tokens_per_min") else
            f"| {r['video_id']} | {r['essential_states']} | {r['candidates']} | {pct(r['pool_recall'])} | — | — | — | — | — | — | — | — | — | — | — |"
        )
    lines.append("")
    for r in results:
        if r.get("missed"):
            lines.append(f"## {r['video_id']} — missed essential visuals")
            for m in r["missed"]:
                lines.append(f"- `{m['state_id']}` {m.get('label') or ''} → **{m['reason']}**")
            lines.append("")
        if r.get("pool_missed") and r.get("selected") is None:
            lines.append(f"## {r['video_id']} — essential visuals absent from the pool")
            lines.append(", ".join(f"`{s}`" for s in r["pool_missed"]))
            lines.append("")
        if r.get("summary"):
            s = r["summary"]
            lines.append(f"## {r['video_id']} — summary ({s['lang']}, {s['words']} words)")
            lines.append(f"- coverage: {pct(s['coverage'])}; hebrew ratio: {pct(s['hebrew_ratio'])}; niqqud: {s['niqqud']}; "
                         f"bidi controls: {s['bidi_controls']}; blocks opening with Latin: {s['blocks_leading_latin']}; dashes: {s['dashes']}")
            lines.append("")
    lines.append("IVR = Important Visual Recall (essential states with ≥1 selected frame inside [start−1 s, end]). "
                 "pool recall = the same over all candidates (triage upper bound). PoR = IVR / random baseline (mean of seeds). "
                 "Image tokens use ⌈w/28⌉×⌈h/28⌉ per candidate read.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", required=True, help="Run directory (bench/runs/<run>)")
    parser.add_argument("--annotations", default=None)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--allow-draft", action="store_true", help="Score against .draft.json annotations too")
    args = parser.parse_args()

    bench_dir = Path(__file__).resolve().parent
    run_dir = Path(args.run).expanduser().resolve()
    ann_dir = Path(args.annotations) if args.annotations else bench_dir / "annotations"
    results = []
    for video_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if not (video_dir / "candidates.json").exists():
            continue
        video_id = video_dir.name
        annotation_path = ann_dir / f"{video_id}.json"
        if not annotation_path.exists() and args.allow_draft:
            annotation_path = ann_dir / f"{video_id}.draft.json"
        if not annotation_path.exists():
            print(f"[score] {video_id}: no annotation — skipped", file=sys.stderr)
            continue
        annotation = load_json(annotation_path)
        if annotation.get("status") == "draft" and not args.allow_draft:
            print(f"[score] {video_id}: annotation is a draft — skipped (use --allow-draft)", file=sys.stderr)
            continue
        result = score_video(video_dir, annotation, args.seeds)
        result["annotation_status"] = annotation.get("status")
        results.append(result)
    if not results:
        raise SystemExit("nothing scored")
    (run_dir / "scores.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    report = render_report(run_dir.name, results)
    (run_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
