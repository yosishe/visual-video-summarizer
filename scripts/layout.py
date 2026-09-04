"""Persistent-motion overlays (webcam picture-in-picture, tab/subtitle bars).

A presenter thumbnail in the corner keeps moving while the slide behind it
stays put; a slide flip is a rare, whole-frame jump. Counting, per pixel, the
fraction of one-second frame pairs in which it moved therefore lights up only
the region that is always moving a little (the median-of-differences idea in
kovitking/video2slides, expressed as a count so it runs as one pure-Python
pass over a low-resolution decode — no numpy, no cv2). The cut-off is
relative to the video's own motion floor (see `motion_threshold`).

The mask is applied to *signatures* (dedup, the re-grab verification gate,
sharpness refinement); the frames written to the summary are never masked.

    overlays = detect_static_overlays(part)      # [] when nothing persistent
    signature = visual_signature(path, mask=overlay_mask(overlays))
"""
from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path

from frame_utils import SIGNATURE_HEIGHT, SIGNATURE_WIDTH

SCAN_WIDTH = 160
SCAN_HEIGHT = 90
DIFF_THRESHOLD = 4            # a pixel "moved" when |Δluma| > 4 (JPEG/AV1 noise stays below)
MOTION_FRACTION_FLOOR = 0.12  # never call fewer than 12 % of the pairs "persistent"
MIN_PAIRS = 24                # fewer consecutive frames than this: no verdict
MAX_PAIRS = 400               # bound the pure-Python counting pass
WEBCAM_AREA = (0.01, 0.20)    # fraction of the frame
WEBCAM_ASPECT = (0.8, 2.4)    # w/h — square webcams to 16:9 webcams
WEBCAM_MAX_SIDE = 0.6         # anything wider/taller than 60 % of the frame is content, not an overlay
BAR_MAX_HEIGHT = 0.10         # a browser tab strip / burned-in subtitle band
BAR_MIN_WIDTH = 0.80
PERSISTENCE_IOU = 0.5         # component must appear in both halves of the pairs
PAD_PX = 3                    # padding at scan resolution, applied to the mask
PAIR_GAP_S = 1.0              # compared frames are about one second apart


def _decode_gray(path: str | Path, media_start: float, duration: float, *, keyframes_only: bool) -> list[bytes]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-ss", f"{media_start:.3f}", "-t", f"{duration:.3f}", "-i", str(path),
            "-vf", f"scale={SCAN_WIDTH}:{SCAN_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={SCAN_WIDTH}:{SCAN_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray",
            "-an", "-f", "rawvideo", "-"]
    if not keyframes_only:
        cmd[cmd.index("-vf") + 1] = "fps=1," + cmd[cmd.index("-vf") + 1]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return []
    size = SCAN_WIDTH * SCAN_HEIGHT
    count = len(result.stdout) // size
    return [result.stdout[i * size:(i + 1) * size] for i in range(count)]


def _motion_counts(frames: list[bytes]) -> tuple[list[int], int]:
    pairs = [(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    if len(pairs) > MAX_PAIRS:
        step = len(pairs) / MAX_PAIRS
        pairs = [pairs[int(i * step)] for i in range(MAX_PAIRS)]
    counts = [0] * (SCAN_WIDTH * SCAN_HEIGHT)
    for a, b in pairs:
        for index, (x, y) in enumerate(zip(a, b)):
            if (x - y if x > y else y - x) > DIFF_THRESHOLD:
                counts[index] += 1
    return counts, len(pairs)


def motion_threshold(counts: list[int], pairs: int) -> float:
    """Fraction of pairs a pixel must move in to count as persistent motion.

    Absolute "more than half" fails on a webcam whose background is a still
    room: only the face moves, in 25–40 % of one-second pairs, while the
    slide/whiteboard around it moves in 5–15 % (pans, drawing). The threshold
    is therefore relative to the video's own motion floor — median + 4·MAD of
    the per-pixel fractions — with a hard floor so a static screen recording
    (median ≈ 0) does not promote noise."""
    fractions = sorted(c / pairs for c in counts)
    median = fractions[len(fractions) // 2]
    mad = sorted(abs(f - median) for f in fractions)[len(fractions) // 2]
    return max(MOTION_FRACTION_FLOOR, min(0.5, median + 4 * mad))


def _binary_map(counts: list[int], pairs: int) -> list[int]:
    need = pairs * motion_threshold(counts, pairs)
    return [1 if c > need else 0 for c in counts]


def _close(bitmap: list[int], width: int, height: int) -> list[int]:
    """3×3 morphological close: dilate then erode — fills the gaps a moving face
    leaves inside an otherwise still webcam box."""
    def neighbours(index: int):
        y, x = divmod(index, width)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width:
                    yield ny * width + nx
    dilated = [1 if any(bitmap[n] for n in neighbours(i)) else 0 for i in range(len(bitmap))]
    return [1 if all(dilated[n] for n in neighbours(i)) else 0 for i in range(len(bitmap))]


def _components(bitmap: list[int], width: int, height: int) -> list[dict]:
    seen = [False] * len(bitmap)
    components = []
    for start in range(len(bitmap)):
        if seen[start] or not bitmap[start]:
            continue
        queue = deque([start])
        seen[start] = True
        xs, ys, area = [], [], 0
        while queue:
            index = queue.popleft()
            y, x = divmod(index, width)
            xs.append(x)
            ys.append(y)
            area += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width:
                    n = ny * width + nx
                    if bitmap[n] and not seen[n]:
                        seen[n] = True
                        queue.append(n)
        components.append({"x0": min(xs), "y0": min(ys), "x1": max(xs) + 1, "y1": max(ys) + 1, "area": area})
    return components


def _classify(component: dict, width: int, height: int) -> str | None:
    w = component["x1"] - component["x0"]
    h = component["y1"] - component["y0"]
    box_area = (w * h) / (width * height)
    if h / height <= BAR_MAX_HEIGHT and w / width >= BAR_MIN_WIDTH:
        return "bar"
    if w / width > WEBCAM_MAX_SIDE or h / height > WEBCAM_MAX_SIDE:
        return None
    if not (WEBCAM_AREA[0] <= box_area <= WEBCAM_AREA[1]):
        return None
    aspect = w / h if h else 0.0
    if not (WEBCAM_ASPECT[0] <= aspect <= WEBCAM_ASPECT[1]):
        return None
    # the box must be reasonably filled — a sparse diagonal of moving pixels is
    # not a box; a face moving inside a still room fills roughly a third
    if component["area"] < 0.3 * w * h:
        return None
    return "webcam"


def _iou(a: dict, b: dict) -> float:
    ix0, iy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    ix1, iy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union else 0.0


def _overlays_from_frames(frames: list[bytes]) -> list[dict]:
    if len(frames) < MIN_PAIRS + 1:
        return []
    counts, pairs = _motion_counts(frames)
    bitmap = _close(_binary_map(counts, pairs), SCAN_WIDTH, SCAN_HEIGHT)
    candidates = [c for c in _components(bitmap, SCAN_WIDTH, SCAN_HEIGHT)]
    # persistence: the same box must show up when only half of the pairs are used
    half = len(frames) // 2
    check = []
    for subset in (frames[:half + 1], frames[half:]):
        if len(subset) >= MIN_PAIRS // 2 + 1:
            c, p = _motion_counts(subset)
            check.append(_components(_close(_binary_map(c, p), SCAN_WIDTH, SCAN_HEIGHT), SCAN_WIDTH, SCAN_HEIGHT))
    overlays = []
    for component in candidates:
        kind = _classify(component, SCAN_WIDTH, SCAN_HEIGHT)
        if kind is None:
            continue
        persistent = all(any(_iou(component, other) >= PERSISTENCE_IOU for other in group) for group in check) if check else True
        if not persistent:
            continue
        x0 = max(0, component["x0"] - PAD_PX)
        y0 = max(0, component["y0"] - PAD_PX)
        x1 = min(SCAN_WIDTH, component["x1"] + PAD_PX)
        y1 = min(SCAN_HEIGHT, component["y1"] + PAD_PX)
        overlays.append({
            "kind": kind,
            "bbox": [round(x0 / SCAN_WIDTH, 4), round(y0 / SCAN_HEIGHT, 4),
                     round(x1 / SCAN_WIDTH, 4), round(y1 / SCAN_HEIGHT, 4)],
            "motion_fraction": round(sum(counts[y * SCAN_WIDTH + x] for y in range(component["y0"], component["y1"])
                                         for x in range(component["x0"], component["x1"]))
                                     / max(1, (component["x1"] - component["x0"]) * (component["y1"] - component["y0"]) * pairs), 3),
            "pairs": pairs,
        })
    overlays.sort(key=lambda o: (o["kind"], o["bbox"]))
    return overlays


def detect_static_overlays(part: dict) -> list[dict]:
    """Overlay boxes for one media part, as fractions of the frame.

    Decodes keyframes only (≈0.5 s for 18 min); falls back to one frame per
    second when the file has too few keyframes. Returns [] when nothing
    persistent and box-shaped is found — talking heads (everything moves) and
    static slides (nothing moves) both yield [].
    """
    duration = float(part["duration"])
    # One frame per second, decoded once. `-skip_frame nokey` would be cheaper
    # but libdav1d (AV1, the usual YouTube 720p codec) ignores it and emits
    # duplicated frames, which look perfectly still — so it is not used.
    frames = _decode_gray(part["path"], float(part.get("media_start", 0.0)), duration, keyframes_only=False)
    return _overlays_from_frames(_stride_to_pair_gap(frames, duration))


def _stride_to_pair_gap(frames: list[bytes], duration: float, gap: float = PAIR_GAP_S) -> list[bytes]:
    """Some decoders ignore `-skip_frame nokey` (libdav1d for AV1 returns every
    frame). Consecutive frames 33 ms apart barely differ, so subsample until
    neighbouring frames are about `gap` seconds apart."""
    if len(frames) < 2 or duration <= 0:
        return frames
    interval = duration / len(frames)
    if interval >= gap:
        return frames
    stride = max(1, round(gap / interval))
    return frames[::stride]


def overlay_mask(overlays: list[dict] | None, width: int = SIGNATURE_WIDTH, height: int = SIGNATURE_HEIGHT) -> bytes | None:
    """Byte map (1 = masked) at signature resolution, or None when no overlay."""
    if not overlays:
        return None
    mask = bytearray(width * height)
    for overlay in overlays:
        fx0, fy0, fx1, fy1 = overlay["bbox"]
        x0, x1 = int(fx0 * width), min(width, int(fx1 * width + 0.999))
        y0, y1 = int(fy0 * height), min(height, int(fy1 * height + 0.999))
        for y in range(y0, y1):
            for x in range(x0, x1):
                mask[y * width + x] = 1
    return bytes(mask) if any(mask) else None


def mask_fraction(mask: bytes | None) -> float:
    return (sum(mask) / len(mask)) if mask else 0.0
