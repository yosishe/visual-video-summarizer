from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidates  # noqa: E402
import render  # noqa: E402
import speech_to_text  # noqa: E402
import transcript  # noqa: E402
from frame_utils import candidate_identifier, signature_from_gray  # noqa: E402


def textured(value: int, accent: int | None = None) -> dict:
    pixels = bytearray(64 * 36)
    for y in range(36):
        for x in range(64):
            pixels[y * 64 + x] = min(255, value + ((x + y) % 13))
    if accent is not None:
        for y in range(12, 22):
            for x in range(24, 42):
                pixels[y * 64 + x] = accent
    return signature_from_gray(bytes(pixels))


class IndependentEngineTests(unittest.TestCase):
    def test_caption_compaction_removes_case_insensitive_rolling_overlap(self) -> None:
        rows = transcript.compact_captions([
            {"start": 0.0, "end": 1.0, "text": "Build the small visual evidence graph"},
            {"start": 1.0, "end": 2.0, "text": "small visual evidence graph first"},
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["text"], "first")

    def test_speech_config_never_falls_back_to_another_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            foreign = root / ".config" / "watch"
            foreign.mkdir(parents=True)
            (foreign / ".env").write_text("GROQ_API_KEY=fake-foreign-value\n", encoding="utf-8")
            with mock.patch.object(speech_to_text.Path, "home", return_value=root), mock.patch.dict(
                speech_to_text.os.environ,
                {"GROQ_API_KEY": "", "OPENAI_API_KEY": ""},
                clear=False,
            ):
                self.assertEqual(speech_to_text.load_api_key(), (None, None))

    def test_speech_config_reads_only_the_skill_owned_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owned = root / ".config" / "summarize-video"
            owned.mkdir(parents=True)
            (owned / ".env").write_text("GROQ_API_KEY=fake-owned-value\n", encoding="utf-8")
            with mock.patch.object(speech_to_text.Path, "home", return_value=root), mock.patch.dict(
                speech_to_text.os.environ,
                {"GROQ_API_KEY": "", "OPENAI_API_KEY": ""},
                clear=False,
            ):
                self.assertEqual(
                    speech_to_text.load_api_key(), ("groq", "fake-owned-value")
                )

    def test_candidate_ids_are_content_addressed_and_stable(self) -> None:
        first = candidate_identifier("ch01", ["vt01"], 12.345678, "abc")
        repeated = candidate_identifier("ch01", ["vt01"], 12.345678, "abc")
        changed = candidate_identifier("ch01", ["vt01"], 12.345679, "abc")
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)
        self.assertTrue(first.startswith("cand_"))

    def test_action_result_waits_for_stable_post_transition_state(self) -> None:
        before = textured(40, 80)
        result = textured(120, 230)
        samples = [
            {"t": 3.0, "signature": before},
            {"t": 3.5, "signature": result},
            {"t": 4.0, "signature": result},
            {"t": 4.5, "signature": result},
        ]
        target = {
            "target_id": "vt_result", "kind": "action_result", "anchor_t": 3.0,
            "window": [3.0, 5.0], "seg_ids": ["seg_1"], "chapter_id": "ch01",
        }
        selected = candidates.select_target_samples(samples, target, 1)
        self.assertEqual(len(selected), 1)
        self.assertGreaterEqual(selected[0]["t"], 4.0)
        self.assertTrue(selected[0]["recovered"])

    def test_triage_budget_counts_singleton_groups_instead_of_treating_them_as_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            frames = [
                {
                    "candidate_id": "cand_a", "chapter_id": "ch01", "target_ids": {"vt_a"},
                    "actual_t": 1.0, "path": str(Path(temporary) / "not-read-a.jpg"),
                },
                {
                    "candidate_id": "cand_b", "chapter_id": "ch02", "target_ids": {"vt_b"},
                    "actual_t": 2.0, "path": str(Path(temporary) / "not-read-b.jpg"),
                },
            ]
            report = candidates._make_triage(frames, Path(temporary))
        self.assertEqual(report["strips"], [])
        self.assertEqual(report["projected_individual_reads"], 2)
        self.assertAlmostEqual(report["projected_to_baseline_ratio"], 2 / 60, places=4)

    def test_renderer_rejects_silent_omission_of_a_covered_visual_target(self) -> None:
        transcript_payload = {"segments": [{"seg_id": "seg_1", "start": 0, "end": 1}]}
        chapters = [{
            "chapter_id": "ch01", "title": "Visual", "start": 0, "end": 1,
            "needs_frames": True,
            "visual_targets": [{"target_id": "vt_1", "kind": "state", "seg_ids": ["seg_1"]}],
        }]
        candidates_payload = {
            "coverage": {
                "chapters": [{"chapter_id": "ch01", "status": "covered"}],
                "targets": [{"target_id": "vt_1", "chapter_id": "ch01", "status": "covered"}],
            },
            "candidates": [],
        }
        summary = {
            "overview": "Overview",
            "chapters": [{
                "chapter_id": "ch01",
                "blocks": [{"text": "The state is shown.", "seg_ids": ["seg_1"]}],
            }],
        }
        with self.assertRaisesRegex(SystemExit, "omit required visual targets"):
            render._validate(transcript_payload, chapters, candidates_payload, [], {"assets": []}, summary)


if __name__ == "__main__":
    unittest.main()
