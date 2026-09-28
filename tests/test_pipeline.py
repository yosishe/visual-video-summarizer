import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

VSUM = Path(__file__).resolve().parents[1] / "scripts" / "vsum.py"
CAPTIONS = """WEBVTT

00:00:00.000 --> 00:00:08.000
Welcome, this is the first slide.

00:00:12.000 --> 00:00:20.000
The second slide shows the result.

00:00:26.000 --> 00:00:34.000
The last slide sums it up.
"""


def talk(folder: Path) -> Path:
    """34 s: slide A 8 s, blank 4 s, slide B 8 s, A again 6 s, slide C 8 s, with a sidecar VTT."""
    specs = [("a", 8), ("blank", 4), ("b", 8), ("a", 6), ("c", 8)]
    boxes = {"a": [(20, 20, 150, 160)], "b": [(170, 20, 300, 160)], "c": [(20, 20, 150, 80), (170, 100, 300, 160)]}
    for name in ("a", "b", "c", "blank"):
        image = Image.new("RGB", (320, 180), (128, 128, 128) if name == "blank" else (30, 34, 40))
        for box in boxes.get(name, []):
            ImageDraw.Draw(image).rectangle(box, fill=(240, 240, 240))
        image.save(folder / f"{name}.png")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for name, seconds in specs:
        cmd += ["-loop", "1", "-t", str(seconds), "-i", str(folder / f"{name}.png")]
    cmd += ["-filter_complex",
            "".join(f"[{i}:v]" for i in range(len(specs))) + f"concat=n={len(specs)}:v=1:a=0,format=yuv420p",
            "-r", "25", str(folder / "talk.mp4")]
    subprocess.run(cmd, check=True)
    (folder / "talk.vtt").write_text(CAPTIONS, encoding="utf-8")
    return folder / "talk.mp4"


def vsum(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VSUM), *args], capture_output=True, text=True, encoding="utf-8")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class Pipeline(unittest.TestCase):
    def test_prepare_then_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            video, work = talk(Path(tmp)), Path(tmp, "work")
            done = vsum("prepare", str(video), "--out", str(work))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertIn("notes.md", done.stdout)
            self.assertIn("[00:00] Welcome, this is the first slide.", (work / "transcript.md").read_text())
            picked = json.loads((work / "frames.json").read_text())
            self.assertEqual(len(picked), 3, picked)
            self.assertTrue((work / "sheets" / "sheet-01.jpg").is_file())
            self.assertEqual(json.loads((work / "source.json").read_text())["transcript"]["method"], "file")

            (work / "notes.md").write_text(
                "# סיכום\n\n> תקציר קצר.\n\n## [00:00] שקף ראשון\nהסבר.\n![F01](השקף הראשון)\n\n"
                "## [00:12] שקף שני\nעוד הסבר.\n![F02](השקף השני)\n", encoding="utf-8")
            done = vsum("render", str(work))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            page = (work / "summary.html").read_text(encoding="utf-8")
            self.assertIn('dir="rtl"', page)
            self.assertEqual(page.count("data:image/jpeg;base64,"), 2)

            (work / "notes.md").write_text("# x\n\n## פרק\n![F99](לא קיים)\n", encoding="utf-8")
            done = vsum("render", str(work))
            self.assertEqual(done.returncode, 1)
            self.assertIn("notes.md line 4: F99 is not one of the frames", done.stdout)

            again = vsum("prepare", str(video), "--out", str(work))
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("reusing", again.stdout)

    def test_missing_file_stops_with_one_line(self):
        done = vsum("prepare", "/no/such/talk.mp4")
        self.assertEqual(done.returncode, 1)
        self.assertIn("Stopped: no such file", done.stderr)


if __name__ == "__main__":
    unittest.main()
