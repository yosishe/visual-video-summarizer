"""The pictures: every screen state that held still, saved full size, and contact sheets to pick from."""
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

from common import run

W, H = 64, 36         # scan size; one sample per second
MOVING = 10           # a pixel moved between two samples when it changed by more than this
OVERLAY_SHARE = 0.30  # pixels moving in more than this share of pairs form an overlay (webcam, ticker)
OVERLAY_MAX = 0.35    # the overlay is masked only while it covers less than this share of the frame
STILL = 3.0           # mean change under this: the screen held still
MIN_HELD = 3          # seconds a state must hold to count
BLANK = 8.0           # pixel spread under this: a blank frame
SAME = 5.0            # mean change under this: the same picture as one already kept
GAP = 120             # seconds without a candidate before one is added


def scan(video: Path) -> list[Image.Image]:
    proc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps=1,scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"],
               "video scan", binary=True)
    size, data = W * H, proc.stdout
    return [Image.frombytes("L", (W, H), data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def overlay_mask(samples: list[Image.Image]) -> Image.Image:
    """255 where samples are compared; 0 over a small overlay that never stops moving."""
    everything = Image.new("L", (W, H), 255)
    if len(samples) < 3:
        return everything
    step = max(1, (len(samples) - 1) // 1000)
    pairs = range(0, len(samples) - 1, step)
    counts = [0] * (W * H)
    for p in pairs:
        moved = ImageChops.difference(samples[p], samples[p + 1]).point(lambda v: 255 if v > MOVING else 0)
        for i, value in enumerate(moved.tobytes()):
            if value:
                counts[i] += 1
    limit = OVERLAY_SHARE * len(pairs)
    hot = Image.new("L", (W, H))
    hot.putdata([255 if c > limit else 0 for c in counts])
    hot = hot.filter(ImageFilter.MaxFilter(3))
    share = sum(1 for v in hot.tobytes() if v) / (W * H)
    return everything if share == 0 or share > OVERLAY_MAX else ImageChops.invert(hot)


def difference(a: Image.Image, b: Image.Image, mask: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b), mask).mean[0]


def settled(samples: list[Image.Image], mask: Image.Image) -> list[tuple[int, int]]:
    """(first, last) second of every run in which the screen held still for MIN_HELD seconds."""
    runs, first = [], 0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or difference(samples[i - 1], samples[i], mask) >= STILL:
            if i - first >= MIN_HELD:
                runs.append((first, i - 1))
            first = i
    return runs


def pick(samples: list[Image.Image], cap: int) -> list[dict]:
    """Candidate moments in time order: the last second of each distinct settled state (the fully
    built slide), plus the calmest second of any stretch longer than GAP without one."""
    if not samples:
        return []
    mask = overlay_mask(samples)
    kept = []  # (second, seconds held)

    def novel(i: int) -> bool:
        return (ImageStat.Stat(samples[i], mask).stddev[0] >= BLANK
                and all(difference(samples[i], samples[k], mask) >= SAME for k, _ in kept))

    for first, last in settled(samples, mask):
        if novel(last):
            kept.append((last, last - first + 1))
    bounds = [-1] + sorted(k for k, _ in kept) + [len(samples)]
    for a, b in zip(bounds, bounds[1:]):
        for centre in range(a + GAP, b, GAP):
            window = range(max(a + 1, centre - 15), min(b, centre + 15))
            calmest = min(window, key=lambda i: difference(samples[i], samples[min(i + 1, len(samples) - 1)], mask))
            if novel(calmest):
                kept.append((calmest, 1))
    if len(kept) > cap:
        kept = sorted(kept, key=lambda k: -k[1])[:cap]
    return [{"t": float(t), "held": held} for t, held in sorted(kept)]


def default_cap(duration: float) -> int:
    """16 candidates per 10 minutes, between 32 and 96 (two to six contact sheets)."""
    return max(32, min(96, round(16 * duration / 600)))
