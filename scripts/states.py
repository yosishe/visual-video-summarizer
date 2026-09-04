"""Visual states: the video as a sequence of pictures, not a list of instants.

One dense, low-resolution decode per media part (2 fps, 160×90, gray, with
`showinfo` timestamps) feeds four passes that need no model and no numpy:

1. **overlay mask** — the persistent webcam / bar boxes (`layout.py`) from the
   same frames, applied to every signature so the presenter never counts;
2. **runs** — consecutive frames that are the same picture under per-mode
   thresholds, compared with the run's *anchor* (catches drift) and its
   *last* frame (catches cuts); runs shorter than a second are transitions;
3. **states** — runs clipped at chapter boundaries, with a *mode* (A talk,
   B static content, C building content, D dynamic UI), a representative
   time (last settled frame; the fullest board for C), build detection,
   a family id shared by revisits of the same picture, and the transcript
   segments the picture was on screen for (overlap ∪ a short lead ∪ cues);
4. **importance** — a bounded relevance score (targets, cues, YouTube's
   most-replayed heatmap, chapter need, mode prior) the selector uses.

`candidates.py --engine states` turns states into candidate points (one per
state, plus what targets need), so the old 300-seek scene pass is gone.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frame_utils import (  # noqa: E402
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    chapter_for_time,
    compare_signatures,
    signature_from_pixels,
)
from layout import SCAN_HEIGHT, SCAN_WIDTH, _overlays_from_frames, _stride_to_pair_gap, overlay_mask  # noqa: E402

SCAN_FPS = 2.0
SETTLED_CHANGED = 0.004       # changed_ratio vs the previous sample at or below this = still
MIN_RUN_S = 1.5               # shorter runs are transitions and join a neighbour
MODE_WINDOW_S = 20.0
LEAD_S = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 4.0}
MODE_PRIOR = {"A": 0.0, "B": 0.7, "C": 0.8, "D": 0.9}
MODE_LABEL = {"A": "talk", "B": "static content", "C": "building content", "D": "dynamic UI"}
# per-mode "same picture" thresholds on the masked 64×36 signature
MODE_THRESHOLDS = {
    "A": {"luma": 8.0, "edge": 6.0, "changed": 0.10, "drift": False},
    "B": {"luma": 3.0, "edge": 4.0, "changed": 0.06, "drift": False},
    # C compares with the LAST frame only: a slow pan or a growing drawing
    # stays one state (the anchor drifts with it); a cut or a wipe splits.
    "C": {"luma": 6.0, "edge": 6.0, "changed": 0.20, "drift": True},
    # D drifts too (a scroll or a growing form is one state whose last settled
    # frame is the picture); B keeps the anchor check so a cross-fade between
    # two slides can never merge them.
    "D": {"luma": 4.0, "edge": 5.0, "changed": 0.06, "drift": True},
}
CANVAS_MAX_EDGE = 14.0        # mean edge strength of a sparse whiteboard/slide (dense UI screens run above)
INK_DROP_SPLIT = 0.30         # C: a board wipe / new board
BUILD_MIN_GROWTH = 1.15
BUILD_MONOTONE = 0.80
PAN_MOTION = 0.03             # global (unmasked) motion above this = camera/pan, not drawing
FAMILY_THRESHOLDS = MODE_THRESHOLDS["B"]

CUE_PATTERNS = [
    r"\bas you can see\b", r"\byou can see\b", r"\bsee here\b", r"\bright here\b", r"\bover here\b",
    r"\bon (?:the|this) screen\b", r"\bon this slide\b", r"\bnotice\b", r"\blet me show\b", r"\bcheck (?:this|it) out\b",
    r"\bthis (?:chart|graph|diagram|table|example|slide|screen)\b", r"\bI(?:'ll| will) show you\b",
    r"\b(?:click|hit|press) (?:on )?(?:the |this )?(?:button|tab|menu|link|icon)\b",
    r"כפי שאתם רואים", r"אתם רואים", r"רואים פה", r"רואים כאן", r"אפשר לראות", r"תסתכלו על", r"שימו לב", r"הנה ",
    r"\bב?ה?גרף\b", r"\bב?ה?טבלה\b", r"\bב?ה?תרשים\b", r"\bב?ה?מסך\b", r"\bב?ה?שקף\b", r"\bב?ה?קוד\b", r"לחצתי", r"נלחץ על",
]
CUE_RE = re.compile("|".join(CUE_PATTERNS), re.IGNORECASE)

try:  # PIL is optional: it makes the 160×90 → 64×36 resample fast
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    Image = None


# --------------------------------------------------------------- decode

def decode_dense(part: dict, fps: float = SCAN_FPS) -> list[tuple[float, bytes]]:
    """One ffmpeg pass: (absolute_t, 160×90 gray bytes) per sample."""
    media_start = float(part.get("media_start", 0.0))
    duration = float(part["duration"])
    if duration <= 0:
        return []
    vf = (
        f"fps={fps}:round=near,"
        f"scale={SCAN_WIDTH}:{SCAN_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={SCAN_WIDTH}:{SCAN_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray,showinfo"
    )
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-copyts",
         "-ss", f"{media_start:.3f}", "-t", f"{duration:.3f}", "-i", str(part["path"]),
         "-vf", vf, "-an", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    size = SCAN_WIDTH * SCAN_HEIGHT
    count = len(result.stdout) // size
    times: list[float] = []
    for line in result.stderr.decode("utf-8", "replace").splitlines():
        match = re.search(r"pts_time:\s*(-?\d+(?:\.\d+)?)", line)
        if match and "showinfo" in line:
            times.append(float(match.group(1)))
    # positional join; a missing showinfo line falls back to the sample grid
    frames = []
    for index in range(count):
        pts = times[index] if index < len(times) else media_start + index / fps
        absolute = float(part["source_start"]) + (pts - media_start)
        frames.append((round(absolute, 3), result.stdout[index * size:(index + 1) * size]))
    return frames


def _downsample(pixels: bytes) -> bytes:
    """160×90 → 64×36 gray (box filter via PIL, nearest via stdlib fallback)."""
    if Image is not None:
        image = Image.frombytes("L", (SCAN_WIDTH, SCAN_HEIGHT), pixels)
        return image.resize((SIGNATURE_WIDTH, SIGNATURE_HEIGHT), Image.BOX).tobytes()
    out = bytearray(SIGNATURE_WIDTH * SIGNATURE_HEIGHT)
    for y in range(SIGNATURE_HEIGHT):
        sy = int(y * SCAN_HEIGHT / SIGNATURE_HEIGHT)
        for x in range(SIGNATURE_WIDTH):
            sx = int(x * SCAN_WIDTH / SIGNATURE_WIDTH)
            out[y * SIGNATURE_WIDTH + x] = pixels[sy * SCAN_WIDTH + sx]
    return bytes(out)


def frame_features(pixels: bytes, mask: bytes | None, previous: dict | None) -> dict:
    small = _downsample(pixels)
    signature = signature_from_pixels(small, mask)
    unmasked = signature_from_pixels(small, None)
    mean = signature["mean"]
    dark_text = mean < 128
    ink_pixels = sum(
        1 for index, value in enumerate(small)
        if (not mask or not mask[index]) and ((value >= 140) if dark_text else (value < 140))
    )
    total = sum(1 for index in range(len(small)) if not mask or not mask[index]) or 1
    changed = compare_signatures(previous["signature"], signature)["changed_ratio"] if previous else 0.0
    motion = compare_signatures(previous["_unmasked"], unmasked)["changed_ratio"] if previous else 0.0
    return {
        "signature": signature,
        "_unmasked": unmasked,
        "luma": mean,
        "edge_density": signature["sharpness"],
        "ink": round(ink_pixels / total, 4),
        "changed": changed,
        "global_motion": motion,
    }


def _near(a: dict, b: dict, thresholds: dict) -> bool:
    delta = compare_signatures(a, b)
    return (delta["luma_mad"] <= thresholds["luma"] and delta["edge_mad"] <= thresholds["edge"]
            and delta["changed_ratio"] <= thresholds["changed"])


# --------------------------------------------------------------- modes

def classify_modes(frames: list[dict], window_s: float = MODE_WINDOW_S) -> list[dict]:
    """Strategy class per window from cheap statistics:
    A  most frame pairs move and the moving area is large (a person filling the frame)
    B  almost nothing moves between samples
    C  bright canvas whose ink keeps growing (whiteboard / typing)
    D  frequent, small, compact changes (software UI)"""
    if not frames:
        return []
    windows: list[dict] = []
    start = frames[0]["t"]
    end = frames[-1]["t"]
    cursor = start
    while cursor <= end:
        rows = [f for f in frames if cursor <= f["t"] < cursor + window_s]
        cursor += window_s
        if len(rows) < 2:
            continue
        pairs = [f for f in rows[1:]]
        moving = [f for f in pairs if f["changed"] > 0.02]
        motion_freq = len(moving) / len(pairs)
        big_motion = sum(1 for f in pairs if f["global_motion"] > 0.25) / len(pairs)
        luma = sum(f["luma"] for f in rows) / len(rows)
        ink_first = sum(f["ink"] for f in rows[: max(1, len(rows) // 4)]) / max(1, len(rows) // 4)
        ink_last = sum(f["ink"] for f in rows[-max(1, len(rows) // 4):]) / max(1, len(rows) // 4)
        edge = sum(f["edge_density"] for f in rows) / len(rows)
        if big_motion > 0.5:
            mode = "A"
        elif motion_freq < 0.15:
            mode = "B"
        elif luma > 150 and edge < CANVAS_MAX_EDGE:
            mode = "C"   # a bright, sparse canvas that keeps moving: drawing, panning, typing
        else:
            mode = "D"
        windows.append({"start": rows[0]["t"], "end": rows[-1]["t"] + 1.0 / SCAN_FPS, "mode": mode,
                        "motion_freq": round(motion_freq, 3), "big_motion": round(big_motion, 3),
                        "luma": round(luma, 1), "edge": round(edge, 2),
                        "ink_first": round(ink_first, 4), "ink_last": round(ink_last, 4)})
    # smooth: a lone window between two equal neighbours takes their mode
    for index in range(1, len(windows) - 1):
        if windows[index - 1]["mode"] == windows[index + 1]["mode"] != windows[index]["mode"]:
            windows[index]["mode"] = windows[index - 1]["mode"]
    return windows


def mode_at(windows: list[dict], t: float) -> str:
    for window in windows:
        if window["start"] <= t < window["end"]:
            return window["mode"]
    return windows[-1]["mode"] if windows and t >= windows[-1]["end"] else "B"


# --------------------------------------------------------------- runs → states

def build_runs(frames: list[dict], windows: list[dict]) -> list[list[dict]]:
    runs: list[list[dict]] = []
    for frame in frames:
        mode = mode_at(windows, frame["t"])
        thresholds = MODE_THRESHOLDS[mode]
        if not runs:
            runs.append([frame])
            continue
        run = runs[-1]
        anchor, last = run[0], run[-1]
        same = _near(last["signature"], frame["signature"], thresholds) and (
            thresholds.get("drift") or _near(anchor["signature"], frame["signature"], thresholds)
        )
        if same and mode == "C":
            peak = max(f["ink"] for f in run)
            if peak > 0 and frame["ink"] < (1 - INK_DROP_SPLIT) * peak:
                same = False   # a wipe / a new board
        if same:
            run.append(frame)
        else:
            runs.append([frame])
    # transitions: runs shorter than MIN_RUN_S join the neighbour they resemble more
    merged: list[list[dict]] = []
    for run in runs:
        span = run[-1]["t"] - run[0]["t"] + 1.0 / SCAN_FPS
        if span < MIN_RUN_S and merged:
            merged[-1].extend(run)
        else:
            merged.append(run)
    return merged


def _settled_indices(run: list[dict]) -> list[int]:
    return [i for i in range(1, len(run)) if run[i]["changed"] <= SETTLED_CHANGED and run[i - 1]["changed"] <= SETTLED_CHANGED] \
        or [i for i in range(len(run)) if run[i]["changed"] <= SETTLED_CHANGED]


def _build_record(run: list[dict]) -> dict:
    inks = [f["ink"] for f in run]
    pans = sum(1 for f in run if f["global_motion"] > PAN_MOTION) / max(1, len(run))
    if len(run) < 3 or pans > 1 - BUILD_MONOTONE:
        return {"is_build": False, "ink_first": inks[0], "ink_last": inks[-1], "steps": 0, "pan": pans > 1 - BUILD_MONOTONE}
    nondec = sum(1 for a, b in zip(inks, inks[1:]) if b >= a * 0.98) / (len(inks) - 1)
    steps = sum(1 for a, b in zip(inks, inks[1:]) if a > 0 and b > a * 1.05)
    is_build = nondec >= BUILD_MONOTONE and inks[0] > 0 and inks[-1] >= BUILD_MIN_GROWTH * inks[0]
    return {"is_build": is_build, "ink_first": inks[0], "ink_last": inks[-1], "steps": steps, "pan": False}


def _representative(run: list[dict], mode: str) -> tuple[float, dict]:
    settled = _settled_indices(run)
    first_settled = run[settled[0]]["t"] if settled else run[0]["t"]
    last_settled = run[settled[-1]]["t"] if settled else run[-1]["t"]
    max_ink = max(run, key=lambda f: f["ink"])["t"]
    if mode == "C":
        tail = [i for i in settled if i >= len(run) * 2 // 3] or settled or [len(run) - 1]
        representative = max((run[i] for i in tail), key=lambda f: f["ink"])["t"]
    else:
        representative = last_settled
    return representative, {"first_settled": first_settled, "max_ink": max_ink, "last_settled": last_settled}


def runs_to_states(runs: list[list[dict]], chapters: list[dict], windows: list[dict]) -> list[dict]:
    states: list[dict] = []
    for run in runs:
        # clip at chapter boundaries: one state never spans two chapters
        pieces: list[list[dict]] = []
        for frame in run:
            chapter = chapter_for_time(chapters, frame["t"]) if chapters else None
            chapter_id = chapter["chapter_id"] if chapter else None
            if pieces and pieces[-1][0].get("_chapter") == chapter_id:
                pieces[-1].append(frame)
            else:
                frame = {**frame, "_chapter": chapter_id}
                pieces.append([frame])
        for piece in pieces:
            piece = [{**f, "_chapter": piece[0].get("_chapter")} for f in piece]
            mode = mode_at(windows, piece[0]["t"])
            representative, alt = _representative(piece, mode)
            states.append({
                "state_id": f"s_{len(states):04d}",
                "chapter_id": piece[0]["_chapter"],
                "start": piece[0]["t"],
                "end": round(piece[-1]["t"] + 1.0 / SCAN_FPS, 3),
                "mode": mode,
                "mode_label": MODE_LABEL[mode],
                "representative_t": representative,
                "alt_t": alt,
                "build": _build_record(piece),
                "luma": round(sum(f["luma"] for f in piece) / len(piece), 1),
                "ink": round(max(f["ink"] for f in piece), 4),
                "samples": len(piece),
                "_signature": max(piece, key=lambda f: f["t"] if f["t"] <= representative else -1)["signature"],
            })
    return states


def assign_families(states: list[dict]) -> None:
    keepers: list[dict] = []
    number = 0
    for state in states:
        match = next((k for k in keepers if _near(k["_signature"], state["_signature"], FAMILY_THRESHOLDS)), None)
        if match is None:
            state["family_id"] = None
            state["revisit_of"] = None
            keepers.append(state)
            continue
        if match["family_id"] is None:
            number += 1
            match["family_id"] = f"f_{number:03d}"
        state["family_id"] = match["family_id"]
        state["revisit_of"] = match["state_id"]


# --------------------------------------------------------------- alignment & importance

def align_states(states: list[dict], segments: list[dict]) -> None:
    for state in states:
        lead = LEAD_S[state["mode"]]
        overlap, lead_ids, cues = [], [], []
        for row in segments:
            s, e = float(row["start"]), float(row["end"])
            if s < state["end"] and e > state["start"]:
                overlap.append(str(row["seg_id"]))
            elif state["start"] - lead <= e <= state["start"]:
                lead_ids.append(str(row["seg_id"]))
            else:
                continue
            match = CUE_RE.search(str(row.get("text") or ""))
            if match:
                cues.append({"seg_id": str(row["seg_id"]), "phrase": match.group(0).strip()})
        state["seg_ids_overlap"] = overlap
        state["seg_ids_lead"] = lead_ids
        state["aligned_seg_ids"] = lead_ids + overlap
        state["cues"] = cues


def _heatmap_value(heatmap: list[dict], start: float, end: float) -> float:
    rows = [h for h in heatmap or [] if float(h.get("end_time", 0)) > start and float(h.get("start_time", 0)) < end]
    if not rows:
        return 0.0
    return sum(float(h.get("value", 0)) for h in rows) / len(rows)


def attach_targets(states: list[dict], chapters: list[dict]) -> None:
    for state in states:
        state["target_ids"] = []
        state["target_kinds"] = []
    for chapter in chapters:
        if not chapter.get("needs_frames"):
            continue
        for target in chapter.get("visual_targets", []):
            window = target.get("window") or (target.get("anchor_t", 0) - 0.5, target.get("anchor_t", 0) + 0.5)
            w0, w1 = float(window[0]), float(window[1])
            anchor = float(target.get("anchor_t", w0))
            chosen: list[dict] = []
            if target.get("kind") == "action_result":
                after = [s for s in states if s["chapter_id"] == chapter["chapter_id"] and anchor <= s["start"] <= anchor + 6.0]
                if after:
                    chosen = [after[0]]
            if not chosen:
                chosen = [s for s in states if s["chapter_id"] == chapter["chapter_id"] and s["start"] < w1 and s["end"] > w0]
            if not chosen:
                containing = [s for s in states if s["start"] <= anchor < s["end"]]
                chosen = containing[:1]
            for state in chosen:
                state["target_ids"].append(target["target_id"])
                state["target_kinds"].append(target.get("kind", "state"))


def score_states(states: list[dict], chapters: list[dict], heatmap: list[dict] | None) -> None:
    chapter_map = {c["chapter_id"]: c for c in chapters}
    for state in states:
        chapter = chapter_map.get(state["chapter_id"], {})
        target = 1.0 if state["target_ids"] else 0.0
        cue = 1.0 if state["cues"] else 0.0
        heat = _heatmap_value(heatmap or [], state["start"], state["end"])
        need = 1.0 if chapter.get("needs_frames") else 0.0
        prior = MODE_PRIOR[state["mode"]]
        state["heatmap"] = round(heat, 3)
        state["importance"] = round(0.35 * target + 0.15 * cue + 0.15 * heat + 0.10 * need + 0.25 * prior, 3)


# --------------------------------------------------------------- entry point

def scan_video(
    parts: list[dict], chapters: list[dict], segments: list[dict], *,
    fps: float = SCAN_FPS, heatmap: list[dict] | None = None, pip_mask: bool = True,
) -> dict:
    """Decode once per part, mask, classify, run, state, align, score."""
    frames: list[dict] = []
    overlays: list[dict] = []
    raw_by_part: list[list[tuple[float, bytes]]] = []
    for part in parts:
        raw = decode_dense(part, fps)
        raw_by_part.append(raw)
        if pip_mask and raw:
            gap = _stride_to_pair_gap([p for _, p in raw], float(part["duration"]))
            for overlay in _overlays_from_frames(gap):
                if not any(overlay["bbox"] == known["bbox"] for known in overlays):
                    overlays.append(overlay)
    mask = overlay_mask(overlays)
    previous: dict | None = None
    for raw in raw_by_part:
        previous = None
        for t, pixels in raw:
            features = frame_features(pixels, mask, previous)
            record = {"t": t, **features}
            frames.append(record)
            previous = record
    # only chapters that need frames are worth states; the rest is talk by declaration
    if chapters:
        keep = [(float(c["start"]), float(c["end"])) for c in chapters if c.get("needs_frames")]
        frames = [f for f in frames if any(s <= f["t"] < e for s, e in keep)]
    windows = classify_modes(frames)
    runs = build_runs(frames, windows)
    states = runs_to_states(runs, chapters, windows)
    assign_families(states)
    align_states(states, segments)
    attach_targets(states, chapters)
    score_states(states, chapters, heatmap)
    for state in states:
        state.pop("_signature", None)
    return {
        "fps": fps,
        "frames_scanned": len(frames),
        "overlays": overlays,
        "modes": [{k: v for k, v in w.items()} for w in windows],
        "states": states,
        "counts": {
            "runs": len(runs), "states": len(states),
            "by_mode": {m: sum(1 for s in states if s["mode"] == m) for m in "ABCD"},
            "families": len({s["family_id"] for s in states if s["family_id"]}),
            "builds": sum(1 for s in states if s["build"]["is_build"]),
        },
    }


def states_to_points(states: list[dict], chapters: list[dict]) -> list[dict]:
    """Candidate point descriptors: one per non-talk state (talk states only
    when a target lands on them), plus the first settled frame of a build whose
    target cites its beginning."""
    chapter_map = {c["chapter_id"]: c for c in chapters}
    descriptors: list[dict] = []
    for state in states:
        chapter = chapter_map.get(state["chapter_id"])
        if chapter is None or not chapter.get("needs_frames"):
            continue
        if state["mode"] == "A" and not state["target_ids"]:
            continue
        reason = "target" if state["target_ids"] else "state"
        descriptors.append({"t": state["representative_t"], "reason": reason, "state": state})
        if state["build"]["is_build"] and state["target_ids"] and state["alt_t"]["first_settled"] < state["representative_t"] - 2.0:
            descriptors.append({"t": state["alt_t"]["first_settled"], "reason": "target", "state": state, "stage": "first"})
    return descriptors
