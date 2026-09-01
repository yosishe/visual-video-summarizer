from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidates  # noqa: E402
from frame_utils import visual_signature  # noqa: E402
from transcript import _dedupe, _strip_overlap  # noqa: E402


class RecallTests(unittest.TestCase):
    def test_scene_windows_cover_whole_chapter_even_with_targets(self) -> None:
        """A target says where the transcript predicts a visual; it must not be
        the only place scene detection looks, or an unflagged slide flip never
        reaches the pool."""
        chapters = [
            {"chapter_id": "ch01", "start": 0.0, "end": 100.0, "needs_frames": True,
             "visual_targets": [{"target_id": "t1", "window": [40.0, 45.0]}]},
            {"chapter_id": "ch02", "start": 100.0, "end": 160.0, "needs_frames": False,
             "visual_targets": []},
            {"chapter_id": "ch03", "start": 160.0, "end": 200.0, "needs_frames": True,
             "visual_targets": []},
        ]
        self.assertEqual(candidates.visual_windows(chapters), [(0.0, 100.0), (160.0, 200.0)])


class TranscriptOverlapTests(unittest.TestCase):
    def test_overlap_is_matched_across_case_and_punctuation(self) -> None:
        previous = "workflows, mostly using Open Claw. But,"
        current = "using open claw but I also use Manas Claw code and even"
        self.assertEqual(_strip_overlap(previous, current), "I also use Manas Claw code and even")

    def test_fully_overlapping_segment_collapses_into_previous(self) -> None:
        rows = [
            {"start": 0.0, "end": 2.0, "text": "So, I spent the last 2 weeks"},
            {"start": 2.0, "end": 3.0, "text": "the last 2 weeks"},
        ]
        merged = _dedupe(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["end"], 3.0)

    def test_short_real_repetition_is_not_treated_as_overlap(self) -> None:
        previous = "and that's cool, you know"
        current = "you know it is really cool"
        self.assertEqual(_strip_overlap(previous, current), current)

    def test_reconstruction_is_lossless_on_interleaved_captions(self) -> None:
        raw = [
            {"start": 2, "end": 3, "text": "So, I spent the last 2 weeks building hundreds of different AI agent"},
            {"start": 3, "end": 5, "text": "hundreds of different AI agent workflows, mostly using Open Claw. But,"},
            {"start": 5, "end": 8, "text": "workflows, mostly using Open Claw. But, I also use Manas Claw code and even"},
            {"start": 8, "end": 9, "text": "I also use Manas Claw code and even Perplexity computer, which just came"},
        ]
        linear = " ".join(seg["text"] for seg in _dedupe(raw))
        self.assertEqual(
            linear,
            "So, I spent the last 2 weeks building hundreds of different AI agent workflows, "
            "mostly using Open Claw. But, I also use Manas Claw code and even Perplexity "
            "computer, which just came",
        )


@unittest.skipUnless(
    __import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe"), "ffmpeg required"
)
class BlankDetectionTests(unittest.TestCase):
    def _solid(self, color: str) -> Path:
        import subprocess
        import tempfile
        path = Path(tempfile.mkdtemp(prefix="vsum-blank-")) / f"{color}.jpg"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color={color}:s=320x180:d=1", "-frames:v", "1", str(path),
        ], check=True)
        return path

    def test_uniform_mid_gray_counts_as_blank(self) -> None:
        self.assertTrue(visual_signature(self._solid("gray"))["blank"])

    def test_black_and_white_still_count_as_blank(self) -> None:
        self.assertTrue(visual_signature(self._solid("black"))["blank"])
        self.assertTrue(visual_signature(self._solid("white"))["blank"])


if __name__ == "__main__":
    unittest.main()
