import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image, ImageDraw  # noqa: E402

import frames  # noqa: E402
from frames import H, W  # noqa: E402


def slide(box, background=40):
    image = Image.new("L", (W, H), background)
    ImageDraw.Draw(image).rectangle(box, fill=215)
    return image


A = slide((4, 4, 30, 30))
B = slide((34, 4, 60, 30))
BLANK = Image.new("L", (W, H), 128)


def noise(rng, base=BLANK, box=(0, 0, W, H)):
    """`base` with random pixels inside `box` (a webcam, or a whole moving frame)."""
    x0, y0, x1, y1 = box
    size = (x1 - x0) * (y1 - y0)
    image = base.copy()
    image.paste(Image.frombytes("L", (x1 - x0, y1 - y0), bytes(rng.randrange(256) for _ in range(size))), (x0, y0))
    return image


class Pick(unittest.TestCase):
    def test_one_frame_per_distinct_state_blank_and_revisit_dropped(self):
        samples = [A] * 10 + [BLANK] * 5 + [B] * 10 + [A] * 10
        self.assertEqual(frames.pick(samples, 32), [{"t": 9.0, "held": 10}, {"t": 24.0, "held": 10}])

    def test_cap_keeps_the_states_held_longest(self):
        samples = [A] * 10 + [B] * 6
        self.assertEqual(frames.pick(samples, 1), [{"t": 9.0, "held": 10}])

    def test_webcam_overlay_is_masked(self):
        rng = random.Random(0)
        webcam = (W - 16, H - 12, W, H)
        samples = [noise(rng, A, webcam) for _ in range(20)]
        mask = frames.overlay_mask(samples)
        self.assertEqual(mask.getpixel((W - 5, H - 5)), 0)
        self.assertEqual(mask.getpixel((5, 5)), 255)
        self.assertEqual(len(frames.pick(samples, 32)), 1)

    def test_long_stretch_without_a_settled_screen_gets_pictures(self):
        rng = random.Random(1)
        samples = [noise(rng) for _ in range(300)]
        picked = frames.pick(samples, 32)
        self.assertEqual(len(picked), 2)
        self.assertTrue(104 <= picked[0]["t"] <= 134 and 224 <= picked[1]["t"] <= 254, picked)

    def test_default_cap(self):
        self.assertEqual(frames.default_cap(600), 32)
        self.assertEqual(frames.default_cap(1800), 48)
        self.assertEqual(frames.default_cap(7200), 96)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class Files(unittest.TestCase):
    def test_extract_and_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp, "v.mp4")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=s=640x360:d=20:r=10", str(video)], check=True)
            picks = [{"t": float(t), "held": 3} for t in range(0, 20)]
            made = frames.extract(video, picks, Path(tmp, "frames"))
            self.assertEqual([f["id"] for f in made[:2]], ["F01", "F02"])
            self.assertTrue(Path(tmp, "frames", "F20.jpg").is_file())
            with Image.open(Path(tmp, "frames", "F01.jpg")) as im:
                self.assertEqual(im.size, (640, 360))
            paths = frames.sheets(made, Path(tmp, "frames"), Path(tmp, "sheets"))
            self.assertEqual([p.name for p in paths], ["sheet-01.jpg", "sheet-02.jpg"])
            with Image.open(paths[0]) as first, Image.open(paths[1]) as second:
                self.assertEqual(first.size, (4 * 320, 4 * (180 + 26)))
                self.assertEqual(second.size, (4 * 320, 1 * (180 + 26)))


if __name__ == "__main__":
    unittest.main()
