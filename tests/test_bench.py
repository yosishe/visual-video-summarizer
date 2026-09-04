"""Benchmark plumbing: drop log, profile overrides, and the scorer's metrics."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "bench"))

import candidates  # noqa: E402
import score  # noqa: E402


class DropLogTests(unittest.TestCase):
    def test_drop_frame_records_reason_and_keeper(self):
        candidates.DROP_LOG.clear()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.jpg"
            path.write_bytes(b"x")
            frame = {"path": str(path), "actual_t": 12.5, "requested_t": 12.4, "reasons": {"scene"},
                     "chapter_id": "ch02", "target_ids": set()}
            keeper = {"actual_t": 12.0}
            candidates.drop_frame(frame, "dedup", keeper)
            self.assertFalse(path.exists())
        self.assertEqual(candidates.DROP_LOG, [{
            "t": 12.5, "requested_t": 12.4, "reason": "dedup", "reasons": ["scene"],
            "chapter_id": "ch02", "target_ids": [], "kept_by_t": 12.0,
        }])
        candidates.DROP_LOG.clear()

    def test_budget_and_dedup_paths_tag_their_reason(self):
        candidates.DROP_LOG.clear()
        with tempfile.TemporaryDirectory() as tmp:
            def frame(index, t):
                p = Path(tmp) / f"{index}.jpg"
                p.write_bytes(b"x")
                return {"path": str(p), "actual_t": t, "requested_t": t, "reasons": {"scene"},
                        "chapter_id": "ch01", "target_ids": set(), "target_kinds": set(), "seg_ids": set(),
                        "target_anchors": {}, "quality": {"sharpness": 1.0, "contrast": 1.0}, "scene_score": 0.5}
            frames = [frame(i, float(i)) for i in range(6)]
            chapters = [{"chapter_id": "ch01", "start": 0.0, "end": 10.0, "needs_frames": True, "visual_targets": []}]
            selected, dropped, trimmed = candidates.select_with_budget(frames, chapters, 3, 2, unplanned_floor=0, hard_cap=True)
        self.assertEqual(len(selected), 3)
        self.assertEqual({row["reason"] for row in candidates.DROP_LOG}, {"cap"})
        self.assertEqual(len(candidates.DROP_LOG), dropped)
        candidates.DROP_LOG.clear()


class ProfileOverrideTests(unittest.TestCase):
    def test_override_merges_known_keys_only(self):
        profile = candidates.PROFILES["standard"]
        override = candidates.parse_profile_override('{"scene_threshold": 0.1, "action_offsets": [0.5, 1.0]}', profile)
        self.assertEqual(override["scene_threshold"], 0.1)
        self.assertEqual(override["action_offsets"], (0.5, 1.0))
        with self.assertRaises(SystemExit):
            candidates.parse_profile_override('{"pip_mask": "on"}', profile)
        with self.assertRaises(SystemExit):
            candidates.parse_profile_override('[1, 2]', profile)
        self.assertEqual(candidates.parse_profile_override(None, profile), {})

    def test_digest_is_stable_and_sensitive(self):
        base = candidates.PROFILES["standard"]
        self.assertEqual(candidates.profile_digest(base), candidates.profile_digest(dict(base)))
        self.assertNotEqual(candidates.profile_digest(base), candidates.profile_digest({**base, "cap": 1}))


class ScorerTests(unittest.TestCase):
    def _run_dir(self, tmp: Path, selections):
        video = tmp / "vid01"
        video.mkdir()
        cands = [{"candidate_id": f"c_{i:04d}", "actual_t": t, "width": 512, "height": 288, "chapter_id": "ch01"}
                 for i, t in enumerate([5.0, 15.0, 25.0, 35.0, 45.0, 55.0])]
        (video / "candidates.json").write_text(json.dumps({"candidates": cands}))
        (video / "chapters.json").write_text(json.dumps([
            {"chapter_id": "ch01", "start": 0.0, "end": 60.0, "needs_frames": True}]))
        (video / "selections.json").write_text(json.dumps(selections))
        (video / "dropped.json").write_text(json.dumps([{"t": 41.0, "reason": "dedup"}]))
        return video

    def test_metrics_and_loss_attribution(self):
        annotation = {
            "video_id": "vid01", "duration": 60.0, "status": "reviewed",
            "states": [
                {"state_id": "s1", "start": 3.0, "end": 8.0, "class": "essential", "spoken_seg_ids": ["seg_0001"]},
                {"state_id": "s2", "start": 13.0, "end": 18.0, "class": "acceptable", "duplicates_of": "s1"},
                {"state_id": "s3", "start": 23.0, "end": 28.0, "class": "essential", "spoken_seg_ids": ["seg_0003"]},
                {"state_id": "s4", "start": 40.0, "end": 42.0, "class": "essential"},   # dropped by dedup
                {"state_id": "s5", "start": 58.0, "end": 59.0, "class": "essential"},   # never sampled
            ],
            "summary_checklist": [],
        }
        selections = [
            {"candidate_id": "c_0000", "chapter_id": "ch01", "anchor_seg_ids": ["seg_0001"]},
            {"candidate_id": "c_0001", "chapter_id": "ch01", "anchor_seg_ids": ["seg_0009"]},   # duplicate of s1
            {"candidate_id": "c_0002", "chapter_id": "ch01", "anchor_seg_ids": ["seg_0002"]},   # s3 hit, misaligned
        ]
        with tempfile.TemporaryDirectory() as tmp:
            video = self._run_dir(Path(tmp), selections)
            result = score.score_video(video, annotation, seeds=3)
        self.assertEqual(result["selected"], 3)
        self.assertAlmostEqual(result["pool_recall"], 2 / 4)
        self.assertAlmostEqual(result["important_visual_recall"], 2 / 4)
        self.assertAlmostEqual(result["precision"], 1.0)
        self.assertAlmostEqual(result["redundancy_rate"], 1 / 3)
        self.assertAlmostEqual(result["frame_efficiency"], 2 / 3)
        self.assertAlmostEqual(result["alignment_accuracy"], 1 / 2)
        reasons = {m["state_id"]: m["reason"] for m in result["missed"]}
        self.assertEqual(reasons, {"s4": "dedup_dropped", "s5": "not_in_pool"})
        self.assertEqual(result["image_tokens"], 6 * 19 * 11)
        self.assertIn("uniform_time", result["baselines"])

    def test_uniform_times_span_windows(self):
        times = score.uniform_times([(0.0, 10.0), (50.0, 60.0)], 4)
        self.assertEqual(times, [2.5, 7.5, 52.5, 57.5])
        self.assertEqual(score.uniform_times([(0.0, 10.0)], 0), [])

    def test_group_root_follows_duplicate_chain(self):
        states = {"a": {"duplicates_of": None}, "b": {"duplicates_of": "a"}, "c": {"duplicates_of": "b"}}
        self.assertEqual(score.group_root(states, "c"), "a")
        self.assertEqual(score.group_root(states, "a"), "a")

    def test_summary_checks_count_hebrew_hygiene(self):
        summary = {"lang": "he", "overview": "סיכום", "chapters": [{"blocks": [
            {"text": "OpenClaw הוא כלי — עם נִקּוּד", "seg_ids": ["seg_0001"]},
            {"text": "הכלי OpenClaw נבדק", "seg_ids": ["seg_0002"]}]}]}
        annotation = {"summary_checklist": [
            {"claim_id": "k1", "must_tokens": ["OpenClaw"], "seg_ids": ["seg_0002"], "weight": 1},
            {"claim_id": "k2", "must_tokens": ["Manus"], "seg_ids": [], "weight": 1}]}
        result = score.score_summary(summary, annotation)
        self.assertAlmostEqual(result["coverage"], 0.5)
        self.assertEqual(result["blocks_leading_latin"], 1)
        self.assertGreater(result["niqqud"], 0)
        self.assertEqual(result["dashes"], 1)


if __name__ == "__main__":
    unittest.main()
