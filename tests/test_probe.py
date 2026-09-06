"""The visual probe's verdict, without ffmpeg: synthetic state timelines in, verdict out."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gates  # noqa: E402
import states as states_module  # noqa: E402


def state(index: int, start: float, seconds: float, *, mode: str = "B", settled: float | None = None,
          family: str | None = None, build: bool = False, fps: float = 1.0) -> dict:
    settled_s = seconds if settled is None else settled
    return {"state_id": f"s_{index:04d}", "start": start, "end": start + seconds, "mode": mode,
            "mode_label": states_module.MODE_LABEL[mode], "family_id": family,
            "settled_samples": int(round(settled_s * fps)), "build": {"is_build": build}}


class ProbeVerdictTests(unittest.TestCase):
    def test_cover_image_and_short_cards_support(self):
        rows = [state(0, 0, 8), state(1, 8, 570, family="f_001"), state(2, 578, 12)]   # title, cover, outro
        probe = gates.probe_verdict(rows, 600, fps=1.0)
        self.assertEqual(probe["verdict"], "supports")
        self.assertEqual(probe["backdrop"]["family"], "f_001")
        self.assertEqual(probe["distinct_still_pictures"], 3)
        self.assertEqual(probe["non_talk_seconds"], 20.0)
        self.assertEqual(probe["threshold_s"], 90.0)

    def test_slideshow_contradicts_with_spans_sorted_and_chapters_named(self):
        rows = [state(i, i * 50, 45, family=f"f_{i:03d}") for i in range(12)]
        chapters = [{"chapter_id": "ch01", "start": 0, "end": 300}, {"chapter_id": "ch02", "start": 300, "end": 600}]
        probe = gates.probe_verdict(rows, 600, fps=1.0, chapters=chapters)
        self.assertEqual(probe["verdict"], "contradicts")
        self.assertEqual(probe["non_talk_seconds"], 45.0 * 11)
        self.assertEqual(len(probe["non_talk_spans"]), gates.PROBE_MAX_SPANS)
        self.assertTrue(all(span["settled_s"] == 45.0 for span in probe["non_talk_spans"]))
        self.assertIn(probe["non_talk_spans"][0]["chapter_ids"][0], ("ch01", "ch02"))
        self.assertIn("ch0", gates.probe_refusal(probe))
        self.assertIn("--by user", gates.probe_refusal(probe))

    def test_talking_head_never_settles_supports(self):
        rows = [state(0, 0, 600, mode="D", settled=3)]
        probe = gates.probe_verdict(rows, 600, fps=1.0)
        self.assertEqual(probe["verdict"], "supports")
        self.assertEqual(probe["modes"]["D"], 600.0)

    def test_a_single_drawn_board_is_never_the_backdrop(self):
        rows = [state(0, 0, 240, mode="C", build=True)]
        probe = gates.probe_verdict(rows, 600, fps=1.0)
        self.assertEqual(probe["verdict"], "contradicts")
        self.assertIsNone(probe["backdrop"])

    def test_short_videos_use_the_ratio(self):
        rows = [state(0, 0, 70, family="f_001"), state(1, 70, 25)]
        self.assertEqual(gates.probe_verdict(rows, 100, fps=1.0)["threshold_s"], 20.0)
        self.assertEqual(gates.probe_verdict(rows, 100, fps=1.0)["verdict"], "contradicts")
        rows = [state(0, 0, 70, family="f_001"), state(1, 70, 15)]
        self.assertEqual(gates.probe_verdict(rows, 100, fps=1.0)["verdict"], "supports")

    def test_nothing_scanned_is_unavailable_and_one_sample_does_not_count(self):
        self.assertEqual(gates.probe_verdict([], 0, fps=1.0)["verdict"], "unavailable")
        self.assertEqual(gates.probe_verdict([], 100, fps=1.0)["verdict"], "unavailable")
        rows = [state(0, 0, 100, settled=1), state(1, 100, 100, settled=1)]
        probe = gates.probe_verdict(rows, 200, fps=1.0)
        self.assertEqual(probe["verdict"], "supports")
        self.assertEqual(probe["distinct_still_pictures"], 0)

    def test_two_fps_counts_seconds_not_samples(self):
        rows = [state(0, 0, 100, family="f_001", fps=2.0), state(1, 100, 30, fps=2.0)]
        probe = gates.probe_verdict(rows, 130, fps=2.0)
        self.assertEqual(probe["non_talk_seconds"], 30.0)

    def test_validate_candidates_refuses_a_contradicted_model_decision_but_never_a_user_one(self):
        def manifest(verdict: str, by: str) -> dict:
            probe = gates.probe_verdict([state(0, 0, 200, family="f_001"), state(1, 200, 200)], 400, fps=1.0) \
                if verdict == "contradicts" else gates.probe_verdict([state(0, 0, 400, family="f_001")], 400, fps=1.0)
            if verdict == "unavailable":
                probe = gates.probe_verdict([], 0, fps=1.0)
            return {"status": "no_visual_chapters", "candidates": [], "coverage": {"chapters": [], "targets": []},
                    "inputs": {"visual_decided_by": by}, "visual_probe": probe}
        refused = gates.validate_candidates(manifest("contradicts", "model"), visual_decision="none")
        self.assertFalse(refused.ok)
        self.assertTrue(any("visual probe contradicts" in e for e in refused.errors))
        override = gates.validate_candidates(manifest("contradicts", "user"), visual_decision="none")
        self.assertTrue(override.ok)
        self.assertTrue(any("user override" in w for w in override.warnings))
        fine = gates.validate_candidates(manifest("supports", "model"), visual_decision="none")
        self.assertTrue(fine.ok)
        unavailable = gates.validate_candidates(manifest("unavailable", "model"), visual_decision="none")
        self.assertTrue(unavailable.ok)
        self.assertTrue(any("unavailable" in w for w in unavailable.warnings))
        legacy = {"status": "no_visual_chapters", "candidates": [], "coverage": {"chapters": [], "targets": []},
                  "inputs": {}}
        old = gates.validate_candidates(legacy, visual_decision="none")
        self.assertTrue(old.ok)
        self.assertTrue(any("no visual probe" in w for w in old.warnings))
        init = dict(legacy, inputs={"visual_decided_by": "init"})
        self.assertEqual(gates.validate_candidates(init, visual_decision="none").warnings, [])


class StatesSettledSamplesTests(unittest.TestCase):
    def test_states_record_how_long_the_picture_held_still(self):
        signature = {"mean": 100.0, "sharpness": 5.0}

        def frame(t: float, changed: float) -> dict:
            return {"t": t, "changed": changed, "global_motion": 0.0, "ink": 0.1, "luma": 100.0,
                    "signature": signature}
        run = [frame(0.0, 0.0), frame(1.0, 0.001), frame(2.0, 0.05), frame(3.0, 0.0), frame(4.0, 0.0)]
        rows = states_module.runs_to_states([run], [], [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["settled_samples"], 4)
        self.assertEqual(rows[0]["samples"], 5)


if __name__ == "__main__":
    unittest.main()
