"""The pictures: every screen state that held still, saved full size, and contact sheets to pick from."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

from common import VsumError, fmt_time, run

# Samples are compared block by block (4×4 pixels of a 128×72 grey scan, a 32×18 grid). Measured on
# a screencast and a conference talk (nKSk_TiR8YA, tuY2ChJIx48): two different white slides with
# small text differ by only 1-2 grey levels on average, while a moving mouse pointer changes a few
# blocks a lot. So a change is either broad (the mean) or wide enough to be text (ten blocks).
W, H, BLOCK = 128, 72, 4
GRID = (W // BLOCK, H // BLOCK)
MOVING = 8            # a block moved between two samples when its mean changed by more than this
OVERLAY_SHARE = 0.30  # blocks moving in more than this share of pairs are an overlay (webcam, ticker)
OVERLAY_MAX = 0.35    # the overlay is ignored only while it covers less than this share of the frame
STILL = 3.0           # a mean change of this much is a new picture...
LOCAL, LOCAL_BLOCKS = 12, 10  # ...and so are ten blocks that changed by 12 (a pointer moves fewer; one
                              # line of small text on a white slide changes 10 to 22 per block)
MIN_HELD = 3          # seconds a state must hold to count
BLANK = 4.0           # pixel spread under this: a blank or fading frame (sparse white slides are 7 to 8)
GAP = 120             # seconds without a candidate before one is added
TILE, BAR, COLS, ROWS = (320, 180), 26, 4, 4  # contact sheet layout


def scan(video: Path) -> list[Image.Image]:
    proc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps=1,scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"],
               "video scan", binary=True)
    size, data = W * H, proc.stdout
    return [Image.frombytes("L", (W, H), data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def _blocks(a: Image.Image, b: Image.Image) -> bytes:
    """Mean absolute difference of every block."""
    return ImageChops.difference(a, b).resize(GRID, Image.BOX).tobytes()


def overlay(samples: list[Image.Image]) -> set[int]:
    """Blocks that never stop moving (a webcam, a ticker); every comparison ignores them."""
    if len(samples) < 3:
        return set()
    step = max(1, (len(samples) - 1) // 1000)
    pairs = range(0, len(samples) - 1, step)
    counts = [0] * (GRID[0] * GRID[1])
    for p in pairs:
        for j, value in enumerate(_blocks(samples[p], samples[p + 1])):
            if value > MOVING:
                counts[j] += 1
    busy = {j for j, count in enumerate(counts) if count > OVERLAY_SHARE * len(pairs)}
    return busy if len(busy) <= OVERLAY_MAX * len(counts) else set()


def changed(a: Image.Image, b: Image.Image, busy: set[int]) -> bool:
    values = [v for j, v in enumerate(_blocks(a, b)) if j not in busy]
    return sum(values) / len(values) >= STILL or sum(v >= LOCAL for v in values) >= LOCAL_BLOCKS


def _mask(busy: set[int]) -> Image.Image:
    mask = Image.new("L", (W, H), 255)
    for j in busy:
        y, x = divmod(j, GRID[0])
        mask.paste(0, (x * BLOCK, y * BLOCK, (x + 1) * BLOCK, (y + 1) * BLOCK))
    return mask


def settled(samples: list[Image.Image], busy: set[int]) -> list[tuple[int, int]]:
    """(first, last) second of every run in which the screen held still for MIN_HELD seconds."""
    runs, first = [], 0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or changed(samples[i - 1], samples[i], busy):
            if i - first >= MIN_HELD:
                runs.append((first, i - 1))
            first = i
    return runs


def pick(samples: list[Image.Image], cap: int) -> list[dict]:
    """Candidate moments in time order: the last second of each distinct settled state (the fully
    built slide), plus the calmest second of any stretch longer than GAP without one."""
    if not samples:
        return []
    busy = overlay(samples)
    mask = _mask(busy)
    kept = []  # (second, seconds held)

    def novel(i: int) -> bool:
        return (ImageStat.Stat(samples[i], mask).stddev[0] >= BLANK
                and all(changed(samples[i], samples[k], busy) for k, _ in kept))

    def motion(i: int) -> float:
        return ImageStat.Stat(ImageChops.difference(samples[i], samples[min(i + 1, len(samples) - 1)]), mask).mean[0]

    for first, last in settled(samples, busy):
        if novel(last):
            kept.append((last, last - first + 1))
    bounds = [-1] + sorted(k for k, _ in kept) + [len(samples)]
    for a, b in zip(bounds, bounds[1:]):
        for centre in range(a + GAP, b, GAP):
            calmest = min(range(max(a + 1, centre - 15), min(b, centre + 15)), key=motion)
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
            path.unlink(missing_ok=True)  # never pass off a file from an interrupted run as this frame
            try:
                run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                     "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(path)], f"frame {fid}")
            except VsumError:
                continue
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
