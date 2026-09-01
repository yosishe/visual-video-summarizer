from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidates  # noqa: E402
import render as renderer  # noqa: E402
from frame_utils import (  # noqa: E402
    chapter_for_time,
    compare_signatures,
    is_hard_duplicate,
    is_near_duplicate,
)


def signature(value: int) -> dict:
    pixels = bytes([value] * (64 * 36))
    return {
        "pixels": pixels,
        "edges": bytes([0] * (64 * 36)),
        "digest": str(value),
        "mean": float(value),
        "contrast": 20.0,
        "sharpness": 10.0,
        "blank": False,
    }


def frame(timestamp: float, reason: str, target: str = "vt1") -> dict:
    return {
        "requested_t": timestamp,
        "actual_t": timestamp,
        "timestamp_error": 0.0,
        "path": f"/nonexistent/{timestamp}.jpg",
        "reasons": {reason},
        "chapter_id": "ch02",
        "target_ids": {target} if target else set(),
        "target_kinds": {"action_result"} if target else set(),
        "target_anchors": {target: 3.0} if target else {},
        "seg_ids": {"seg_0001"},
        "scene_score": 0.0,
        "priority": 100 if reason == "target" else 40,
        "quality": {"sharpness": 10.0, "contrast": 20.0},
        "_signature": signature(100),
    }


class ChapterAndCandidateTests(unittest.TestCase):
    def test_half_open_boundaries_assign_exact_start_to_next_chapter(self) -> None:
        chapters = [
            {"chapter_id": "ch01", "start": 0.0, "end": 154.0},
            {"chapter_id": "ch02", "start": 154.0, "end": 227.0},
            {"chapter_id": "ch03", "start": 227.0, "end": 300.0},
        ]
        self.assertEqual(chapter_for_time(chapters, 154.0)["chapter_id"], "ch02")
        self.assertEqual(chapter_for_time(chapters, 227.0)["chapter_id"], "ch03")
        self.assertEqual(chapter_for_time(chapters, 300.0)["chapter_id"], "ch03")

    def test_merge_prefers_post_action_target_not_earliest_scene(self) -> None:
        chapters = [{"chapter_id": "ch02", "start": 3.0, "end": 8.0}]
        segments = [{"seg_id": "seg_0001", "start": 3.0, "end": 4.0}]
        target = {
            "target_id": "vt1", "chapter_id": "ch02", "kind": "action_result",
            "seg_ids": ["seg_0001"], "anchor_t": 3.0, "window": [3.0, 5.5],
        }
        early = candidates.make_point(3.10, "scene", chapters, segments, target=target)
        late = candidates.make_point(3.20, "target", chapters, segments, target=target)
        merged = candidates.merge_points([early, late], epsilon=0.2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["requested_t"], 3.20)
        self.assertEqual(merged[0]["reasons"], {"scene", "target"})

    def test_dedup_keeps_best_protected_representative(self) -> None:
        early = frame(3.1, "scene")
        late = frame(3.8, "target")
        kept, dropped = candidates.deduplicate_frames([early, late])
        self.assertEqual(dropped, 1)
        self.assertEqual(kept[0]["actual_t"], 3.8)
        self.assertIn("target", kept[0]["reasons"])
        self.assertIn("scene", kept[0]["reasons"])

    def test_dedup_does_not_cross_semantic_targets(self) -> None:
        first = frame(3.1, "target", "vt1")
        second = frame(3.2, "target", "vt2")
        kept, dropped = candidates.deduplicate_frames([first, second])
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 2)

    def test_coverage_is_fail_closed(self) -> None:
        chapters = [
            {"chapter_id": "ch01", "start": 0, "end": 3, "needs_frames": False, "visual_targets": []},
            {"chapter_id": "ch02", "start": 3, "end": 6, "needs_frames": True, "visual_targets": [
                {"target_id": "vt1"}
            ]},
        ]
        report = candidates.coverage_report(chapters, [])
        self.assertEqual(report["chapters"][0]["status"], "not-required")
        self.assertEqual(report["chapters"][1]["status"], "unresolved")
        self.assertEqual(report["targets"][0]["status"], "unresolved")

    def test_overlap_part_selection_prefers_target_near_part_center(self) -> None:
        parts = [
            {"source_start": 0.0, "duration": 10.0},
            {"source_start": 8.0, "duration": 10.0},
        ]
        self.assertIs(candidates.part_for(parts, 8.5), parts[0])
        self.assertIs(candidates.part_for(parts, 12.0), parts[1])

    def test_cache_key_changes_with_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "video.mp4"
            source.write_bytes(b"fixture")
            first = candidates._cache_key(candidates._source_identity(str(source), [(0, 10)], False))
            second = candidates._cache_key(candidates._source_identity(str(source), [(0, 11)], False))
            exact = candidates._cache_key(candidates._source_identity(str(source), [(0, 10)], True))
            self.assertNotEqual(first, second)
            self.assertNotEqual(first, exact)

    def test_signature_comparison_has_explicit_hard_duplicate_gate(self) -> None:
        first = signature(100)
        second = signature(100)
        self.assertTrue(is_hard_duplicate(first, second))
        self.assertEqual(compare_signatures(first, second)["changed_ratio"], 0.0)

    def test_local_ui_change_survives_multiscale_dedup(self) -> None:
        first = signature(100)
        changed_pixels = bytearray(first["pixels"])
        for y in range(8):
            for x in range(8):
                changed_pixels[(y + 10) * 64 + (x + 20)] = 230
        second = {**first, "pixels": bytes(changed_pixels), "digest": "changed"}
        self.assertFalse(is_near_duplicate(first, second))

    def test_renderer_refuses_unresolved_required_chapter(self) -> None:
        transcript = {"segments": [{"seg_id": "seg_0001", "start": 0, "end": 1}]}
        chapters = [{
            "chapter_id": "ch01", "title": "Visual", "start": 0, "end": 1,
            "needs_frames": True,
        }]
        candidate_payload = {
            "coverage": {"chapters": [{"chapter_id": "ch01", "status": "unresolved"}]},
            "candidates": [],
        }
        summary = {"chapters": [{
            "chapter_id": "ch01", "blocks": [{"text": "Visual claim.", "seg_ids": ["seg_0001"]}]
        }]}
        with self.assertRaisesRegex(SystemExit, "remain unresolved"):
            renderer._validate(transcript, chapters, candidate_payload, [], {"assets": []}, summary)


if __name__ == "__main__":
    unittest.main()
