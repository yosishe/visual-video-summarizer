"""Visual-state engine: runs, states, builds, alignment, families, points."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import states  # noqa: E402
from frame_utils import SIGNATURE_HEIGHT, SIGNATURE_WIDTH  # noqa: E402

W, H = states.SCAN_WIDTH, states.SCAN_HEIGHT


def _frame(shade: int, *, box=None, box_shade=40, stripes=0) -> bytes:
    row = bytearray([shade] * (W * H))
    if box:
        x0, y0, x1, y1 = box
        for y in range(y0, y1):
            for x in range(x0, x1):
                row[y * W + x] = box_shade
    for s in range(stripes):
        for dy in range(3):               # 3 px tall so the stripe survives the 160×90 → 64×36 box filter
            y = 10 + s * 8 + dy
            for x in range(20, 120):
                row[y * W + x] = 30
    return bytes(row)


def _scan(frames: list[bytes], fps: float = 2.0, start: float = 0.0) -> list[dict]:
    out, previous = [], None
    for index, pixels in enumerate(frames):
        features = states.frame_features(pixels, None, previous)
        record = {"t": round(start + index / fps, 3), **features}
        out.append(record)
        previous = record
    return out


class RunTests(unittest.TestCase):
    def test_cut_splits_and_still_frames_merge(self):
        frames = [_frame(200)] * 10 + [_frame(60)] * 10
        scanned = _scan(frames)
        windows = [{"start": 0.0, "end": 100.0, "mode": "B"}]
        runs = states.build_runs(scanned, windows)
        self.assertEqual([len(r) for r in runs], [10, 10])

    def test_slow_pan_stays_one_run_in_canvas_mode_but_splits_in_static_mode(self):
        # a box that drifts one pixel per sample: each step is tiny, the total is large
        frames = [_frame(220, box=(20 + i, 20, 60 + i, 50)) for i in range(20)]
        scanned = _scan(frames)
        canvas = states.build_runs(scanned, [{"start": 0.0, "end": 100.0, "mode": "C"}])
        static = states.build_runs(scanned, [{"start": 0.0, "end": 100.0, "mode": "B"}])
        self.assertEqual(len(canvas), 1)
        self.assertGreater(len(static), 1)

    def test_transitions_join_a_neighbour(self):
        frames = [_frame(200)] * 8 + [_frame(120)] + [_frame(200)] * 8   # a one-sample flash
        runs = states.build_runs(_scan(frames), [{"start": 0.0, "end": 100.0, "mode": "B"}])
        self.assertEqual(len(runs), 2)   # flash merged into its predecessor; the second run stays
        self.assertEqual(len(runs[0]), 9)


class StateTests(unittest.TestCase):
    def test_build_detection_and_last_settled_representative(self):
        # stripes appear one by one, then the board stays still
        frames = [_frame(230, stripes=min(i // 2 + 1, 6)) for i in range(16)] + [_frame(230, stripes=6)] * 6
        scanned = _scan(frames)
        windows = [{"start": 0.0, "end": 100.0, "mode": "C"}]
        runs = states.build_runs(scanned, windows)
        self.assertEqual(len(runs), 1)
        chapters = [{"chapter_id": "ch01", "start": 0.0, "end": 100.0, "needs_frames": True, "visual_targets": []}]
        rows = states.runs_to_states(runs, chapters, windows)
        self.assertEqual(len(rows), 1)
        state = rows[0]
        self.assertTrue(state["build"]["is_build"], state["build"])
        self.assertGreaterEqual(state["representative_t"], scanned[12]["t"])   # a settled frame after the last stripe
        self.assertLessEqual(state["alt_t"]["first_settled"], state["representative_t"])

    def test_chapter_clipping_and_families(self):
        frames = [_frame(200)] * 10 + [_frame(60)] * 10 + [_frame(200)] * 10
        scanned = _scan(frames)   # 15 s at 2 fps
        windows = [{"start": 0.0, "end": 100.0, "mode": "B"}]
        chapters = [{"chapter_id": "ch01", "start": 0.0, "end": 3.0, "needs_frames": True, "visual_targets": []},
                    {"chapter_id": "ch02", "start": 3.0, "end": 100.0, "needs_frames": True, "visual_targets": []}]
        rows = states.runs_to_states(states.build_runs(scanned, windows), chapters, windows)
        self.assertEqual([s["chapter_id"] for s in rows], ["ch01", "ch02", "ch02", "ch02"])
        states.assign_families(rows)
        self.assertEqual(rows[0]["family_id"], rows[1]["family_id"])      # same picture, clipped at the chapter
        self.assertEqual(rows[3]["family_id"], rows[0]["family_id"])      # the revisit
        self.assertEqual(rows[3]["revisit_of"], rows[0]["state_id"])
        self.assertIsNone(rows[2]["family_id"])


class AlignmentTests(unittest.TestCase):
    def test_overlap_lead_and_cues(self):
        rows = [{"state_id": "s_0000", "start": 10.0, "end": 20.0, "mode": "B"}]
        segments = [
            {"seg_id": "seg_0000", "start": 0.0, "end": 6.0, "text": "intro"},
            {"seg_id": "seg_0001", "start": 6.5, "end": 9.5, "text": "as you can see on this slide"},   # lead (3 s)
            {"seg_id": "seg_0002", "start": 9.5, "end": 15.0, "text": "the numbers are"},
            {"seg_id": "seg_0003", "start": 19.0, "end": 25.0, "text": "תסתכלו על הגרף הזה"},
            {"seg_id": "seg_0004", "start": 30.0, "end": 35.0, "text": "later"},
        ]
        states.align_states(rows, segments)
        self.assertEqual(rows[0]["seg_ids_overlap"], ["seg_0002", "seg_0003"])
        self.assertEqual(rows[0]["seg_ids_lead"], ["seg_0001"])
        self.assertEqual(rows[0]["aligned_seg_ids"], ["seg_0001", "seg_0002", "seg_0003"])
        self.assertEqual({c["seg_id"] for c in rows[0]["cues"]}, {"seg_0001", "seg_0003"})

    def test_targets_and_importance(self):
        base = {"build": {"is_build": False}, "alt_t": {"first_settled": 0.0}, "aligned_seg_ids": []}
        rows = [{"state_id": "s_0000", "chapter_id": "ch01", "start": 0.0, "end": 10.0, "mode": "B", "cues": [], "representative_t": 9.0, **base},
                {"state_id": "s_0001", "chapter_id": "ch01", "start": 10.0, "end": 20.0, "mode": "B", "cues": [{"seg_id": "x", "phrase": "see"}], "representative_t": 19.0, **base},
                {"state_id": "s_0002", "chapter_id": "ch01", "start": 20.0, "end": 30.0, "mode": "A", "cues": [], "representative_t": 29.0, **base}]
        chapters = [{"chapter_id": "ch01", "start": 0.0, "end": 30.0, "needs_frames": True, "visual_targets": [
            {"target_id": "t_slide", "kind": "slide", "anchor_t": 4.0, "window": (3.0, 6.0), "seg_ids": []},
            {"target_id": "t_action", "kind": "action_result", "anchor_t": 9.0, "window": (8.9, 11.5), "seg_ids": []},
        ]}]
        states.attach_targets(rows, chapters)
        self.assertEqual(rows[0]["target_ids"], ["t_slide"])
        self.assertEqual(rows[1]["target_ids"], ["t_action"])      # first state starting after the action
        states.score_states(rows, chapters, heatmap=[{"start_time": 0, "end_time": 30, "value": 1.0}])
        self.assertGreater(rows[1]["importance"], rows[0]["importance"])   # cue on top of a target
        self.assertLess(rows[2]["importance"], rows[0]["importance"])      # talk mode prior is zero
        points = states.states_to_points(rows, chapters)
        self.assertEqual([p["state"]["state_id"] for p in points], ["s_0000", "s_0001"])   # A-mode state skipped
        self.assertEqual(points[0]["reason"], "target")


class DecodeHelperTests(unittest.TestCase):
    def test_downsample_shape(self):
        small = states._downsample(_frame(100))
        self.assertEqual(len(small), SIGNATURE_WIDTH * SIGNATURE_HEIGHT)
        self.assertEqual(set(small), {100})

    def test_mode_classification(self):
        still = _scan([_frame(200, stripes=3)] * 50)
        self.assertEqual(states.classify_modes(still)[0]["mode"], "B")
        talking = _scan([_frame(60, box=(0, 0, W, H), box_shade=40 + (i % 2) * 60) for i in range(50)])
        self.assertEqual(states.classify_modes(talking)[0]["mode"], "A")


if __name__ == "__main__":
    unittest.main()
