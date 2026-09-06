"""audit_summary.py must not audit clean against nothing."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_summary  # noqa: E402
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


class AuditReviewTests(unittest.TestCase):
    """The 1.8 structural reviews: pointers to re-read, never verdicts, never exit 5."""

    def _late(self, text: str, cite: bool = False, brief: bool = False) -> dict:
        transcript = {"video": {"id": "v", "duration": 40.0},
                      "segments": [dict(row) for row in TRANSCRIPT["segments"]]}
        transcript["segments"][18]["text"] = text   # 36–38 s: inside the last 15 %
        doc = summary(["seg_0001", "seg_0018"] if cite else ["seg_0001"])
        if brief:
            doc["brief"] = {"synthesis": {"text": "The claim.", "seg_ids": ["seg_0018"]}, "main_points": [],
                            "takeaways": []}
        return run_audit(transcript, CHAPTERS, doc)

    def test_late_strong_correction_uncited_is_a_review(self):
        result = self._late("I misspoke, the limit is 45 minutes, not 30.")
        self.assertIn("late-correction", checks(result, "reviews"))
        self.assertTrue(any("00:36" in row["message"] for row in result["reviews"]))
        self.assertNotIn("late-correction", checks(result, "errors"))
        self.assertNotIn("late-correction", checks(self._late("I misspoke, the limit is 45 minutes, not 30.",
                                                              cite=True), "reviews"))
        self.assertNotIn("late-correction", checks(self._late("I misspoke, the limit is 45 minutes, not 30.",
                                                              brief=True), "reviews"))

    def test_weak_cue_needs_a_negation_and_early_cues_are_ignored(self):
        self.assertNotIn("late-correction", checks(self._late("actually the same thing"), "reviews"))
        self.assertIn("late-correction", checks(self._late("actually, not 30 but 45"), "reviews"))
        transcript = {"video": {"id": "v", "duration": 40.0}, "segments": [dict(row) for row in TRANSCRIPT["segments"]]}
        transcript["segments"][2]["text"] = "I misspoke earlier"   # 4 s: not late
        self.assertNotIn("late-correction", checks(run_audit(transcript, CHAPTERS, summary(["seg_0001"])), "reviews"))

    def test_hebrew_correction_cues(self):
        self.assertIn("late-correction", checks(self._late("בעצם זה לא 30 דקות אלא 45"), "reviews"))
        self.assertIn("late-correction", checks(self._late("תיקון: המספר הוא 45"), "reviews"))
        self.assertNotIn("late-correction", checks(self._late("בעצם זה אותו דבר"), "reviews"))

    def test_chapter_with_few_citations_is_a_review_and_small_chapters_are_not(self):
        thin = run_audit(TRANSCRIPT, CHAPTERS, summary(["seg_0001"]))   # ch01: 1 of 10 segments cited
        self.assertIn("chapter-coverage", checks(thin, "reviews"))
        self.assertTrue(any(row["where"] == "ch01" for row in thin["reviews"] if row["check"] == "chapter-coverage"))
        fuller = run_audit(TRANSCRIPT, CHAPTERS, summary(["seg_0001", "seg_0002", "seg_0003"]))   # 3 of 10
        self.assertFalse(any(row["where"] == "ch01" for row in fuller["reviews"] if row["check"] == "chapter-coverage"))
        small_chapters = [{"chapter_id": "ch01", "title": "One", "start": 0.0, "end": 12.0, "needs_frames": True},
                          {"chapter_id": "ch02", "title": "Two", "start": 12.0, "end": 40.0, "needs_frames": False}]
        small = run_audit(TRANSCRIPT, small_chapters, summary(["seg_0001"]))
        self.assertFalse(any(row["where"] == "ch01" for row in small["reviews"] if row["check"] == "chapter-coverage"))

    def test_new_reviews_never_change_the_exit_code(self):
        result = self._late("I misspoke, the limit is 45 minutes, not 30.")
        self.assertEqual(result["errors"], [])
        self.assertIn("late-correction", render_report(result))
        self.assertIn("chapter-coverage", render_report(result))


class AuditCliTests(unittest.TestCase):
    """audit_summary.py as workflow.py and render.py call it: it records what it judged."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-audit-")
        self.work = Path(self.temporary.name)
        (self.work / "transcript.json").write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
        (self.work / "chapters.json").write_text(json.dumps(CHAPTERS), encoding="utf-8")
        (self.work / "summary.json").write_text(json.dumps(summary(["seg_0001"])), encoding="utf-8")
        (self.work / "selections.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _main(self, *extra: str) -> int:
        argv = ["audit_summary.py", "--work", str(self.work), "--summary", str(self.work / "summary.json"), *extra]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            return audit_summary.main()

    def test_cli_with_selections_requires_the_candidate_pool(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main("--selections", str(self.work / "selections.json"))
        self.assertEqual(ctx.exception.code, 10)
        self.assertFalse((self.work / "audit.json").exists())

    def test_cli_writes_the_inputs_it_judged(self):
        (self.work / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
        code = self._main("--selections", str(self.work / "selections.json"), "--lang", "en")
        self.assertEqual(code, 0)
        audit = json.loads((self.work / "audit.json").read_text(encoding="utf-8"))
        inputs = audit["inputs"]
        self.assertEqual(set(inputs), {"summary_sha256", "selections_sha256", "transcript_sha256",
                                       "chapters_sha256", "candidates_sha256", "lang"})
        self.assertEqual(inputs["summary_sha256"], hashlib.sha256((self.work / "summary.json").read_bytes()).hexdigest())
        self.assertEqual(inputs["lang"], "en")
        self.assertTrue(inputs["candidates_sha256"])


if __name__ == "__main__":
    unittest.main()
