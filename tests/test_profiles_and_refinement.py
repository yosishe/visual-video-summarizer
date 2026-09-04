from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidates  # noqa: E402
import render as renderer  # noqa: E402
from candidates import MODE_ALIASES, PROFILES, resolve_profile  # noqa: E402
from frame_utils import (  # noqa: E402
    choose_refined_frame,
    parse_metadata_series,
)


def signature(value: int) -> dict:
    pixels = bytes([value] * (64 * 36))
    return {
        "pixels": pixels, "edges": bytes([0] * (64 * 36)), "digest": str(value),
        "mean": float(value), "contrast": 20.0, "sharpness": 10.0, "blank": False,
    }


def series_row(t: float, blur: float, value: int = 100) -> dict:
    return {"t": t, "blur": blur, "signature": signature(value)}


class ProfileTests(unittest.TestCase):
    def test_mode_aliases_map_to_tiers_and_conflict_exits(self) -> None:
        self.assertEqual(resolve_profile(None, None)[0], "standard")
        self.assertEqual(resolve_profile(None, "light")[0], "standard")
        self.assertEqual(resolve_profile(None, "advanced")[0], "high")
        self.assertEqual(resolve_profile("high", "advanced")[0], "high")
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            resolve_profile("standard", "advanced")
        self.assertEqual(set(MODE_ALIASES.values()), set(PROFILES))

    def test_sample_times_are_profile_driven(self) -> None:
        window = {"window": [100.0, 120.0], "anchor_t": 110.0}
        standard, high = PROFILES["standard"], PROFILES["high"]
        action = {"kind": "action_result", **window}
        self.assertEqual(candidates.target_sample_times(action, standard), [110.2, 110.8, 111.6])
        self.assertEqual(len(candidates.target_sample_times(action, high)), 6)
        state = {"kind": "state", **window}
        self.assertEqual(candidates.target_sample_times(state, standard), [110.0, 110.6])
        self.assertEqual(len(candidates.target_sample_times(state, high)), 5)
        slide = {"kind": "slide", **window}
        times = candidates.target_sample_times(slide, standard)
        self.assertEqual(times, [110.0, 119.75])  # no probe → end of the segments
        measured = {"kind": "diagram", "terminal_t": 114.3, **window}
        times = candidates.target_sample_times(measured, standard)
        self.assertEqual(times, [110.0, 114.3])  # the measured terminal replaces end-0.25
        self.assertEqual(len(candidates.target_sample_times(measured, high)), 4)
        # legacy callers may still pass the mode name
        self.assertEqual(candidates.target_sample_times(state, "light"), [110.0, 110.6])

    def test_max_candidates_is_a_hard_ceiling(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="vsum-cap-"))
        chapters = [{"chapter_id": "ch01", "start": 0.0, "end": 1000.0, "needs_frames": True,
                     "visual_targets": [{"target_id": f"t{i}"} for i in range(20)]}]

        def make_frames() -> list[dict]:
            frames = []
            for i in range(20):
                for k in range(2):
                    p = tmp / f"t{i}_{k}.jpg"; p.write_bytes(b"x")
                    frames.append({"path": str(p), "actual_t": i * 10 + k, "chapter_id": "ch01",
                                   "target_ids": {f"t{i}"}, "target_kinds": {"slide"},
                                   "target_anchors": {}, "reasons": {"target"}, "priority": 100,
                                   "scene_score": 0.0, "quality": {"sharpness": 5, "contrast": 5}})
            for j in range(30):
                p = tmp / f"s{j}.jpg"; p.write_bytes(b"x")
                frames.append({"path": str(p), "actual_t": 500 + j, "chapter_id": "ch01",
                               "target_ids": set(), "target_kinds": set(), "target_anchors": {},
                               "reasons": {"scene"}, "priority": 40, "scene_score": 0.3,
                               "quality": {"sharpness": 5, "contrast": 5}})
            return frames

        selected, _, trimmed = candidates.select_with_budget(make_frames(), chapters, 30, 2, hard_cap=True)
        self.assertEqual(len(selected), 30)
        self.assertEqual(trimmed, 10)
        selected, _, trimmed = candidates.select_with_budget(make_frames(), chapters, 30, 2, hard_cap=False)
        self.assertEqual(trimmed, 0)  # the old lift: reserved + unplanned floor
        self.assertGreaterEqual(len(selected), 40 + candidates.UNPLANNED_FLOOR)


class TerminalProbeTests(unittest.TestCase):
    def test_adaptive_maxima_honours_floor(self) -> None:
        scores = [(t / 10, 0.0) for t in range(100)]
        scores[50] = (5.0, 0.10)
        self.assertEqual(candidates.adaptive_maxima(scores, 0.04), [(5.0, 0.1)])
        self.assertEqual(candidates.adaptive_maxima(scores, 0.20), [])

    def test_stable_terminal_stops_before_flip(self) -> None:
        scores = [(t / 10, 0.0) for t in range(101)]
        for bump in (20, 40, 60):
            scores[bump] = (bump / 10, 0.03)
        scores[80] = (8.0, 0.6)
        probe = candidates.stable_terminal_from_scores(scores, 3.0, (0.0, 10.0))
        self.assertTrue(probe["flipped"])
        self.assertAlmostEqual(probe["terminal_t"], 7.8)
        self.assertEqual(probe["build_steps"], 3)  # 2, 4 and 6 s: the run starts at the window
        quiet = candidates.stable_terminal_from_scores([(t / 10, 0.0) for t in range(101)], 3.0, (0.0, 10.0))
        self.assertFalse(quiet["flipped"])
        self.assertAlmostEqual(quiet["terminal_t"], 9.75)
        after = candidates.stable_terminal_from_scores(scores, 9.0, (0.0, 10.0))
        self.assertAlmostEqual(after["run_start"], 8.0)
        self.assertAlmostEqual(after["terminal_t"], 9.75)


class RefinementTests(unittest.TestCase):
    def test_parse_metadata_series_keeps_nan_slots(self) -> None:
        stderr = "\n".join([
            "[Parsed_metadata_1 @ 0x1] frame:0    pts:30720   pts_time:3",
            "[Parsed_metadata_1 @ 0x1] lavfi.blur=nan",
            "[Parsed_metadata_1 @ 0x1] frame:1    pts:31744   pts_time:3.1",
            "[Parsed_metadata_1 @ 0x1] lavfi.blur=4.589331",
            "[Parsed_metadata_1 @ 0x1] frame:2    pts:32768   pts_time:3.2",
            "[Parsed_metadata_1 @ 0x1] lavfi.scene_score=0.120000",
        ])
        blur = parse_metadata_series(stderr, "blur")
        self.assertEqual(len(blur), 2)
        self.assertTrue(math.isnan(blur[0][1]))
        self.assertEqual(blur[1], (3.1, 4.589331))
        self.assertEqual(parse_metadata_series(stderr, "scene_score"), [(3.2, 0.12)])

    def test_choose_refined_prefers_sharpest_near_duplicate_only(self) -> None:
        candidate = signature(100)
        series = [
            series_row(3.0, 2.0, value=10),   # sharper but a different picture
            series_row(3.5, 8.0),
            series_row(3.7, 8.0),             # t0
            series_row(3.9, 5.0),
            series_row(4.1, 8.0, value=10),   # gap: not a duplicate
            series_row(4.3, 1.0),             # eligible again, but after the gap
        ]
        result = choose_refined_frame(candidate, series, 3.7)
        self.assertTrue(result["applied"])
        self.assertEqual(result["t"], 3.9)
        self.assertEqual(result["eligible"], 3)
        self.assertEqual(result["total"], 6)

    def test_choose_refined_hysteresis(self) -> None:
        candidate = signature(100)
        series = [series_row(3.5, 5.0), series_row(3.7, 5.0), series_row(3.9, 4.7)]
        self.assertFalse(choose_refined_frame(candidate, series, 3.7)["applied"])
        series[2] = series_row(3.9, 4.0)
        result = choose_refined_frame(candidate, series, 3.7)
        self.assertTrue(result["applied"])
        self.assertEqual(result["t"], 3.9)

    def test_choose_refined_rejects_nan_and_foreign_anchor(self) -> None:
        candidate = signature(100)
        series = [series_row(3.7, float("nan")), series_row(3.9, 1.0)]
        self.assertEqual(choose_refined_frame(candidate, series, 3.7)["reason"], "anchor-not-duplicate")
        self.assertEqual(choose_refined_frame(candidate, [], 3.7)["reason"], "empty-series")


class SignalTests(unittest.TestCase):
    def _frame(self, quality: dict) -> dict:
        return {"priority": 100, "actual_t": 10.0, "target_ids": {"t1"}, "target_kinds": {"slide"},
                "target_anchors": {"t1": 10.0}, "scene_score": 0.0, "quality": {"sharpness": 5, "contrast": 5, **quality}}

    def test_face_signal_unavailable_is_neutral(self) -> None:
        base = candidates._frame_score(self._frame({}))
        self.assertEqual(candidates._frame_score(self._frame({"faces": "unavailable"})), base)
        self.assertEqual(candidates._frame_score(self._frame({"faces": {"people_frame": False}})), base)

    def test_people_frame_demotion_keeps_target_above_scene(self) -> None:
        target = self._frame({"faces": {"people_frame": True}})
        clean = self._frame({"faces": {"people_frame": False}})
        scene = {**self._frame({}), "priority": 40, "target_ids": set(), "target_kinds": set(), "target_anchors": {}}
        self.assertLess(candidates._frame_score(target), candidates._frame_score(clean))
        self.assertGreater(candidates._frame_score(target), candidates._frame_score(scene))

    def test_ocr_signal_only_in_high(self) -> None:
        self.assertEqual(PROFILES["standard"]["ocr"], "off")
        self.assertEqual(PROFILES["high"]["ocr"], "on")
        dense = candidates._frame_score(self._frame({"text_chars": 400}))
        sparse = candidates._frame_score(self._frame({"text_chars": 20}))
        none = candidates._frame_score(self._frame({}))
        self.assertGreater(dense, sparse)
        self.assertGreater(sparse, none)
        self.assertAlmostEqual(dense - none, 15.0)  # capped at 200 chars

    def test_dedup_cluster_hook_chooses_the_fuller_slide(self) -> None:
        def frame(t: float, text: int | None) -> dict:
            quality = {"sharpness": 10.0, "contrast": 20.0}
            return {"requested_t": t, "actual_t": t, "timestamp_error": 0.0, "path": f"/nonexistent/{t}.jpg",
                    "reasons": {"target"}, "chapter_id": "ch02", "target_ids": {"vt1"}, "target_kinds": {"slide"},
                    "target_anchors": {"vt1": 3.0}, "seg_ids": {"seg_0001"}, "scene_score": 0.0, "priority": 100,
                    "quality": quality, "_signature": signature(100), "_text": text}

        seen: list[int] = []

        def hook(cluster: list[dict]) -> None:
            seen.append(len(cluster))
            for member in cluster:  # the hook stands in for OCR: later frame = fuller slide
                member["quality"] = {**member["quality"], "text_chars": member["_text"]}

        early, late = frame(3.0, 40), frame(3.4, 160)
        kept, dropped = candidates.deduplicate_frames([late, early], cluster_hook=hook)
        self.assertEqual(seen, [2])
        self.assertEqual(dropped, 1)
        self.assertEqual(kept[0]["actual_t"], 3.4)
        # the hook never runs on a singleton
        seen.clear()
        candidates.deduplicate_frames([frame(3.0, 40)], cluster_hook=hook)
        self.assertEqual(seen, [])

    def test_cost_estimate_uses_real_dimensions(self) -> None:
        wide = [{"width": 512, "height": 288} for _ in range(48)]
        cost = candidates.cost_estimate(
            wide, "standard", PROFILES["standard"], scene_seconds=100.0, terminal_probes=0,
            seeks=48, faces_status="off", ocr_frames=0,
        )
        self.assertEqual(cost["image_tokens_estimate"], 48 * 197)
        self.assertEqual(cost["other_tier"]["tier"], "high")
        square = [{"width": 512, "height": 384} for _ in range(48)]
        taller = candidates.cost_estimate(
            square, "standard", PROFILES["standard"], scene_seconds=100.0, terminal_probes=0,
            seeks=48, faces_status="off", ocr_frames=0,
        )
        self.assertGreater(taller["image_tokens_estimate"], cost["image_tokens_estimate"])
        self.assertEqual(candidates._scaled_dimensions(1280, 720, 512), (512, 288))
        self.assertEqual(candidates._scaled_dimensions(640, 480, 512), (512, 384))


class RenderTests(unittest.TestCase):
    def test_style_has_print_rules(self) -> None:
        self.assertIn("@media print", renderer.STYLE)
        self.assertIn("break-inside: avoid", renderer.STYLE)

    def test_export_pdf_reports_missing_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            single = Path(temporary) / "summary-x.html"
            single.write_text('<img src="data:image/png;base64,AA==" loading="lazy">', encoding="utf-8")
            with mock.patch.object(renderer, "_find_chrome", return_value=None), \
                    mock.patch.object(renderer, "_find_weasyprint", return_value=None):
                outcome = renderer.export_pdf(single, single.with_suffix(".pdf"))
            self.assertIsNone(outcome["engine"])
            self.assertFalse(single.with_name("summary-x.print.html").exists())

    def test_validate_uses_asset_time_and_checks_both_chapters(self) -> None:
        transcript = {"segments": [{"seg_id": "seg_0001", "start": 0, "end": 10}]}
        chapters = [
            {"chapter_id": "ch01", "title": "A", "start": 0, "end": 5, "needs_frames": True},
            {"chapter_id": "ch02", "title": "B", "start": 5, "end": 10, "needs_frames": False},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            full = Path(temporary) / "f-full.jpg"; full.write_bytes(b"x")
            thumb = Path(temporary) / "f-thumb.jpg"; thumb.write_bytes(b"x")
            candidate_payload = {
                "coverage": {"chapters": [{"chapter_id": "ch01", "status": "covered"}]},
                "candidates": [{"candidate_id": "c_0001", "actual_t": 4.0, "chapter_id": "ch01",
                                "seg_ids": ["seg_0001"], "reasons": ["target"], "quality": {}}],
            }
            selections = [{"candidate_id": "c_0001", "name": "f", "chapter_id": "ch01", "role": "evidence",
                           "caption": "c", "alt": "a", "anchor_seg_ids": ["seg_0001"]}]
            summary = {"chapters": [
                {"chapter_id": "ch01", "blocks": [{"text": "t", "seg_ids": ["seg_0001"]}]},
                {"chapter_id": "ch02", "blocks": [{"text": "t", "seg_ids": ["seg_0001"]}]},
            ]}

            def assets(actual_t: float) -> dict:
                return {"assets": [{"candidate_id": "c_0001", "actual_t": actual_t, "triaged_t": 4.0,
                                    "refinement": {"applied": actual_t != 4.0},
                                    "full": {"path": str(full)}, "thumb": {"path": str(thumb)}}]}

            _, frames, _ = renderer._validate(transcript, chapters, candidate_payload, selections, assets(4.6), summary)
            self.assertEqual(frames[0]["actual_t"], 4.6)
            self.assertEqual(frames[0]["triaged_t"], 4.0)
            self.assertTrue(frames[0]["refinement"]["applied"])
            with self.assertRaisesRegex(SystemExit, "asset t=5.200 belongs to ch02"):
                renderer._validate(transcript, chapters, candidate_payload, selections, assets(5.2), summary)


if __name__ == "__main__":
    unittest.main()
