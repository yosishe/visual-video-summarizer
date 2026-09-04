"""Overlay (webcam PiP) detection, masked signatures, and family-scoped dedup."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidates  # noqa: E402
import frame_utils  # noqa: E402
import layout  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def _synthetic_frames(width: int, height: int, count: int, *, box=None, flips=(), noise=0) -> list[bytes]:
    """Gray frames: flat background 200, a `box` (x0, y0, x1, y1) whose content
    alternates every frame (a 'presenter'), and whole-frame flips at the given
    indices (a slide change)."""
    frames = []
    for index in range(count):
        base = 200 if not any(index >= f for f in flips) or (sum(1 for f in flips if index >= f) % 2 == 0) else 60
        row = bytearray([base] * (width * height))
        if box:
            x0, y0, x1, y1 = box
            shade = 30 if index % 2 == 0 else 120
            for y in range(y0, y1):
                for x in range(x0, x1):
                    # a still frame border, a moving inner face
                    inner = x0 + 3 <= x < x1 - 3 and y0 + 3 <= y < y1 - 3
                    row[y * width + x] = shade if inner else 90
        if noise:
            for y in range(0, height, 7):
                row[y * width + (index * 5) % width] = base - noise
        frames.append(bytes(row))
    return frames


class OverlayDetectionTests(unittest.TestCase):
    def test_webcam_box_found_and_flips_ignored(self):
        W, H = layout.SCAN_WIDTH, layout.SCAN_HEIGHT
        box = (4, 60, 34, 86)  # bottom-left, 30×26 of 160×90 ≈ 5 % of the frame
        frames = _synthetic_frames(W, H, 120, box=box, flips=(40, 80))
        overlays = layout._overlays_from_frames(frames)
        self.assertEqual([o["kind"] for o in overlays], ["webcam"])
        x0, y0, x1, y1 = overlays[0]["bbox"]
        self.assertLess(x0, 0.05)
        self.assertGreater(y0, 0.55)
        self.assertLess(x1, 0.30)
        self.assertGreater(overlays[0]["motion_fraction"], 0.3)

    def test_nothing_persistent_yields_no_overlay(self):
        W, H = layout.SCAN_WIDTH, layout.SCAN_HEIGHT
        still = _synthetic_frames(W, H, 60)
        self.assertEqual(layout._overlays_from_frames(still), [])
        # everything moves (a talking head filling the frame): no box either
        moving = _synthetic_frames(W, H, 60, box=(0, 0, W, H))
        self.assertEqual(layout._overlays_from_frames(moving), [])

    def test_bar_classification(self):
        W, H = layout.SCAN_WIDTH, layout.SCAN_HEIGHT
        component = {"x0": 0, "y0": 0, "x1": W, "y1": 6, "area": W * 6}
        self.assertEqual(layout._classify(component, W, H), "bar")
        big = {"x0": 0, "y0": 0, "x1": int(W * 0.7), "y1": int(H * 0.7), "area": 10}
        self.assertIsNone(layout._classify(big, W, H))

    def test_stride_subsamples_dense_decodes(self):
        frames = [bytes([i % 256]) for i in range(300)]
        strided = layout._stride_to_pair_gap(frames, duration=10.0)   # 30 fps → every 30th
        self.assertEqual(len(strided), 10)
        self.assertEqual(layout._stride_to_pair_gap(frames[:5], duration=10.0), frames[:5])


class MaskedSignatureTests(unittest.TestCase):
    def test_mask_hides_overlay_motion(self):
        W, H = frame_utils.SIGNATURE_WIDTH, frame_utils.SIGNATURE_HEIGHT
        a = bytearray([180] * (W * H))
        b = bytearray(a)
        for y in range(24, 36):           # a 12×12 'webcam' in the bottom-left corner changes a lot
            for x in range(0, 12):
                b[y * W + x] = 40
        self.assertFalse(frame_utils.is_near_duplicate(
            frame_utils.signature_from_pixels(bytes(a)), frame_utils.signature_from_pixels(bytes(b))))
        mask = layout.overlay_mask([{"kind": "webcam", "bbox": [0.0, 0.66, 0.19, 1.0]}])
        self.assertTrue(frame_utils.is_near_duplicate(
            frame_utils.signature_from_pixels(bytes(a), mask), frame_utils.signature_from_pixels(bytes(b), mask)))
        self.assertAlmostEqual(layout.mask_fraction(mask), (12 * 13) / (W * H), delta=0.02)

    def test_content_change_survives_the_mask(self):
        W, H = frame_utils.SIGNATURE_WIDTH, frame_utils.SIGNATURE_HEIGHT
        a = bytearray([180] * (W * H))
        b = bytearray(a)
        for y in range(5, 15):
            for x in range(20, 40):
                b[y * W + x] = 40
        mask = layout.overlay_mask([{"kind": "webcam", "bbox": [0.0, 0.66, 0.19, 1.0]}])
        self.assertFalse(frame_utils.is_near_duplicate(
            frame_utils.signature_from_pixels(bytes(a), mask), frame_utils.signature_from_pixels(bytes(b), mask)))

    def test_no_overlay_means_no_mask(self):
        self.assertIsNone(layout.overlay_mask([]))
        self.assertIsNone(layout.overlay_mask(None))


class FamilyDedupTests(unittest.TestCase):
    def _frame(self, tmp: Path, name: str, t: float, chapter: str, *, reasons, target_ids=(), shade=100):
        path = tmp / f"{name}.jpg"
        path.write_bytes(b"x")
        pixels = bytes([shade] * (frame_utils.SIGNATURE_WIDTH * frame_utils.SIGNATURE_HEIGHT))
        return {
            "path": str(path), "actual_t": t, "requested_t": t, "chapter_id": chapter,
            "reasons": set(reasons), "target_ids": set(target_ids), "target_kinds": set(),
            "seg_ids": {f"seg_{int(t):04d}"}, "target_anchors": {}, "scene_score": 0.1,
            "quality": {"sharpness": 5.0, "contrast": 10.0}, "_signature": frame_utils.signature_from_pixels(pixels),
        }

    def test_family_keeps_one_per_protected_chapter_and_drops_revisits(self):
        candidates.DROP_LOG.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [
                self._frame(root, "a", 100.0, "ch02", reasons={"target"}, target_ids={"ch02_x"}),
                self._frame(root, "b", 105.0, "ch02", reasons={"scene"}),            # same slide, unplanned, same chapter
                self._frame(root, "c", 400.0, "ch05", reasons={"scene"}),            # reprise in a chapter with nothing protected
                self._frame(root, "d", 700.0, "ch08", reasons={"coverage"}),         # reprise where coverage needs it
                self._frame(root, "e", 900.0, "ch09", reasons={"scene"}, shade=30),  # a different picture
            ]
            kept, dropped = candidates.deduplicate_frames(frames, scope="family")
        kept_times = [f["actual_t"] for f in kept]
        self.assertEqual(kept_times, [100.0, 700.0, 900.0])
        self.assertEqual(dropped, 2)
        first = kept[0]
        self.assertEqual(first["family_id"], "f_001")
        self.assertEqual(kept[1]["family_id"], "f_001")
        self.assertIsNone(kept[2]["family_id"])
        self.assertIn("scene", first["reasons"])                       # same-chapter member merged in
        self.assertIn("seg_0105", first["seg_ids"])
        self.assertEqual(first["family_revisits"], [400.0])             # cross-chapter revisit remembered
        self.assertNotIn("seg_0400", first["seg_ids"])                  # ...but its segments not merged
        reasons = [row["reason"] for row in candidates.DROP_LOG]
        self.assertEqual(reasons, ["dedup", "dedup"])
        candidates.DROP_LOG.clear()

    def test_chapter_scope_is_the_legacy_behaviour(self):
        candidates.DROP_LOG.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [
                self._frame(root, "a", 100.0, "ch02", reasons={"target"}, target_ids={"ch02_x"}),
                self._frame(root, "b", 105.0, "ch02", reasons={"scene"}),
                self._frame(root, "c", 400.0, "ch05", reasons={"scene"}),
            ]
            kept, dropped = candidates.deduplicate_frames(frames, scope="chapter")
        # target frame and target-less scene frame never compared; other chapter untouched
        self.assertEqual([f["actual_t"] for f in kept], [100.0, 105.0, 400.0])
        self.assertEqual(dropped, 0)
        candidates.DROP_LOG.clear()


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class OverlayOnRealDecodeTests(unittest.TestCase):
    def test_detects_pip_in_a_synthetic_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "pip.mp4"
            # 40 s: white 'slide' with a text-like block, a 'webcam' box bottom-left whose
            # inner colour blinks every second, and a slide flip at 20 s.
            filter_graph = (
                "color=c=white:s=320x180:d=40,"
                "drawbox=x=40:y=20:w=200:h=30:color=black:t=fill,"
                "drawbox=x=8:y=120:w=64:h=48:color=gray:t=fill,"
                "drawbox=x=16:y=128:w=48:h=32:color=red:t=fill:enable='lt(mod(t\\,2)\\,1)',"
                "drawbox=x=16:y=128:w=48:h=32:color=blue:t=fill:enable='gte(mod(t\\,2)\\,1)',"
                "drawbox=x=40:y=60:w=240:h=40:color=black:t=fill:enable='gte(t\\,20)'"
            )
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", filter_graph,
                            "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
            part = {"path": str(video), "media_start": 0.0, "duration": 40.0}
            overlays = layout.detect_static_overlays(part)
        self.assertEqual(len(overlays), 1, overlays)
        self.assertEqual(overlays[0]["kind"], "webcam")
        x0, y0, x1, y1 = overlays[0]["bbox"]
        self.assertLess(x0, 0.06)
        self.assertGreater(y0, 0.6)
        self.assertLess(x1, 0.28)


if __name__ == "__main__":
    unittest.main()
