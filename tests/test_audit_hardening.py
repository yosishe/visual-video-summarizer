"""audit_summary.py must not audit clean against nothing."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_summary import render_report, run_audit  # noqa: E402

TRANSCRIPT = {"video": {"id": "v", "duration": 40.0},
              "segments": [{"seg_id": f"seg_{i:04d}", "start": i * 2.0, "end": i * 2.0 + 2.0,
                            "text": f"segment {i} says value {i * 7}"} for i in range(20)]}
CHAPTERS = [{"chapter_id": "ch01", "title": "One", "start": 0.0, "end": 20.0, "needs_frames": True},
            {"chapter_id": "ch02", "title": "Two", "start": 20.0, "end": 40.0, "needs_frames": False}]


def summary(block_ids: list[str]) -> dict:
    return {"schema_version": 3, "lang": "en", "overview": "The claim.",
            "chapters": [{"chapter_id": "ch01", "blocks": [{"text": "It says value 7.", "seg_ids": block_ids}]},
                         {"chapter_id": "ch02", "blocks": [{"text": "Later.", "seg_ids": ["seg_0012"]}]}]}


def checks(result: dict, level: str) -> set[str]:
    return {row["check"] for row in result[level]}


class AuditHardeningTests(unittest.TestCase):
    def test_unknown_block_seg_ids_are_errors(self):
        result = run_audit(TRANSCRIPT, CHAPTERS, summary(["seg_9999", "seg_0001"]))
        self.assertIn("reference", checks(result, "errors"))
        self.assertTrue(any("seg_9999" in row["message"] for row in result["errors"]))

    def test_block_without_seg_ids_is_error(self):
        result = run_audit(TRANSCRIPT, CHAPTERS, summary([]))
        self.assertIn("reference", checks(result, "errors"))

    def test_empty_transcript_is_error_and_report_says_na(self):
        result = run_audit({"status": "no_transcript", "video": {}, "segments": []}, CHAPTERS, summary(["seg_0001"]))
        self.assertIn("transcript", checks(result, "errors"))
        self.assertIsNone(result["stats"]["coverage"])
        self.assertIn("n/a", render_report(result))

    def test_trivially_low_coverage_is_a_review_not_an_error(self):
        result = run_audit(TRANSCRIPT, CHAPTERS, summary(["seg_0001"]))
        self.assertIn("coverage", checks(result, "reviews"))
        self.assertNotIn("coverage", checks(result, "errors"))
        self.assertLess(result["stats"]["coverage"], 0.15)


if __name__ == "__main__":
    unittest.main()
