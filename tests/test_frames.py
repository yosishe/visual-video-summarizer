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


def screen(rows, background, ink, cursor=None):
    """A 1280×720 slide or editor with lines of small text (2 px strokes in 50 px words), scaled to
    the scan size the way ffmpeg's area filter does. Calibrated on the talk tuY2ChJIx48: at 64×36
    two different white slides differed by 1.2-2.2 grey levels and had a pixel spread of 7-8."""
    image = Image.new("L", (1280, 720), background)
    draw = ImageDraw.Draw(image)
    for row in rows:
        for word in range(100, 800, 90):
            for stroke in range(word, word + 50, 9):
                draw.rectangle((stroke, row, stroke + 1, row + 20), fill=ink)
    if cursor:
        draw.rectangle((cursor[0], cursor[1], cursor[0] + 12, cursor[1] + 18), fill=255)
    return image.resize((W, H), Image.BOX)


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

    def test_dark_code_screens_that_differ_only_in_text_are_both_kept(self):
        samples = [screen((100, 140, 180), 30, 170)] * 10 + [screen((100, 160, 200), 30, 170)] * 10
        self.assertEqual([p["t"] for p in frames.pick(samples, 32)], [9.0, 19.0])

    def test_white_slides_that_differ_only_in_small_text_are_both_kept(self):
        """A conference template: white slides with the same title and a few lines of body text."""
        samples = [screen((100, 300, 360), 245, 60)] * 10 + [screen((100, 330, 390), 245, 60)] * 10
        self.assertEqual([p["t"] for p in frames.pick(samples, 32)], [9.0, 19.0])

    def test_a_moving_cursor_does_not_end_a_settled_screen(self):
        samples = [screen((100, 140, 180), 30, 170, cursor=(300 + 40 * i, 400)) for i in range(20)]
        self.assertEqual(len(frames.pick(samples, 32)), 1)

    def test_cap_keeps_the_states_held_longest(self):
        samples = [A] * 10 + [B] * 6
        self.assertEqual(frames.pick(samples, 1), [{"t": 9.0, "held": 10}])

    def test_webcam_overlay_is_masked(self):
        rng = random.Random(0)
        webcam = (W - 16, H - 12, W, H)
        samples = [noise(rng, A, webcam) for _ in range(20)]
        overlay = frames.overlay(samples)
        grid = W // frames.BLOCK
        self.assertIn(((H - 5) // frames.BLOCK) * grid + (W - 5) // frames.BLOCK, overlay)
        self.assertNotIn(0, overlay)
        self.assertEqual(len(frames.pick(samples, 32)), 1)

    def test_long_stretch_without_a_settled_screen_gets_pictures(self):
        rng = random.Random(1)
        samples = [noise(rng) for _ in range(300)]
        picked = frames.pick(samples, 32)
        self.assertEqual(len(picked), 2)
        self.assertTrue(104 <= picked[0]["t"] <= 134 and 224 <= picked[1]["t"] <= 254, picked)

    def test_default_cap(self):
        self.assertEqual(frames.default_cap(600), 48)
        self.assertEqual(frames.default_cap(2400), 96)
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
