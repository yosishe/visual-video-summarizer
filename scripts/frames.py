"""The pictures: every screen state that held still, saved full size, and contact sheets to pick from."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from common import fmt_time, run

W, H = 64, 36         # scan size; one sample per second
MOVING = 10           # a pixel moved between two samples when it changed by more than this
OVERLAY_SHARE = 0.30  # pixels moving in more than this share of pairs form an overlay (webcam, ticker)
OVERLAY_MAX = 0.35    # the overlay is masked only while it covers less than this share of the frame
STILL = 3.0           # mean change under this: the screen held still, or two samples show the same
                      # picture (a revisit). At 64×36, dark code screens that differ only in their
                      # text are 3 to 5 apart, so no looser threshold is safe.
MIN_HELD = 3          # seconds a state must hold to count
BLANK = 8.0           # pixel spread under this: a blank frame
GAP = 120             # seconds without a candidate before one is added
TILE, BAR, COLS, ROWS = (320, 180), 26, 4, 4  # contact sheet layout


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
                and all(difference(samples[i], samples[k], mask) >= STILL for k, _ in kept))

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
    """24 candidates per 10 minutes, between 48 and 96 (three to six contact sheets, about 1,300
    image tokens each): a dense screencast holds a new screen every 15-20 seconds."""
    return max(48, min(96, round(24 * duration / 600)))


def extract(video: Path, picks: list[dict], folder: Path) -> list[dict]:
    """Full-size JPEGs F01.jpg… (at most 1280 px wide); returns [{id, t, held}]."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for chosen in picks:
        fid = f"F{len(made) + 1:02d}"
        path = folder / f"{fid}.jpg"
        for t in (chosen["t"], max(0.0, chosen["t"] - 1)):  # a seek to the very end can land past the last frame
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                 "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(path)], f"frame {fid}")
            if path.is_file():
                made.append({"id": fid, "t": t, "held": chosen["held"]})
                break
    return made


def _font(size: int = 20):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def sheets(made: list[dict], folder: Path, out: Path) -> list[Path]:
    """4×4 grids of the candidates, 'F07 · 12:34' burned under each tile."""
    out.mkdir(parents=True, exist_ok=True)
    font, per, paths = _font(), COLS * ROWS, []
    for start in range(0, len(made), per):
        page = made[start:start + per]
        rows = -(-len(page) // COLS)
        sheet = Image.new("RGB", (COLS * TILE[0], rows * (TILE[1] + BAR)), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for n, frame in enumerate(page):
            x, y = (n % COLS) * TILE[0], (n // COLS) * (TILE[1] + BAR)
            with Image.open(folder / f"{frame['id']}.jpg") as image:
                tile = image.convert("RGB")
            tile.thumbnail(TILE)
            sheet.paste(tile, (x + (TILE[0] - tile.width) // 2, y + (TILE[1] - tile.height) // 2))
            draw.rectangle([x, y + TILE[1], x + TILE[0] - 1, y + TILE[1] + BAR - 1], fill=(0, 0, 0))
            draw.text((x + 8, y + TILE[1] + 2), f"{frame['id']} · {fmt_time(frame['t'])}", fill=(255, 255, 255),
                      font=font)
        path = out / f"sheet-{start // per + 1:02d}.jpg"
        sheet.save(path, quality=85)
        paths.append(path)
    return paths
