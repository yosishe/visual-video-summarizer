"""audit_summary: hard checks on numbers/identifiers, soft checks on names, Hebrew hygiene."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_summary  # noqa: E402

TRANSCRIPT = {
    "video": {"duration": 120.0, "title": "OpenClaw superteam"},
    "segments": [
        {"seg_id": "seg_0000", "start": 0.0, "end": 10.0, "text": "We tested Open Claw, Manus and Claude Code, seven to ten skills each."},
        {"seg_id": "seg_0001", "start": 10.0, "end": 20.0, "text": "It is not a chatbot; run npm install first."},
        {"seg_id": "seg_0002", "start": 20.0, "end": 30.0, "text": "The journal bot posts every 30 minutes to Telegram."},
        {"seg_id": "seg_0003", "start": 60.0, "end": 70.0, "text": "Prompts are so late 2025."},
        {"seg_id": "seg_0004", "start": 48.0, "end": 55.0, "text": "a segment straddling the chapter cut"},
    ],
}
CHAPTERS = [
    {"chapter_id": "ch01", "start": 0.0, "end": 50.0, "needs_frames": True},
    {"chapter_id": "ch02", "start": 50.0, "end": 120.0, "needs_frames": True},
]


def _summary(blocks, lang="he", overview="הסרטון טוען שסוכנים צרים מנצחים."):
    return {"lang": lang, "overview": overview, "chapters": [{"chapter_id": "ch01", "blocks": blocks}, {
        "chapter_id": "ch02", "blocks": [{"text": "הציוץ אומר שפרומפטים הם עניין של 2025.", "seg_ids": ["seg_0003"]}]}]}


def _by_check(result, level):
    return [row["check"] for row in result[level]]


class AuditTests(unittest.TestCase):
    def test_clean_hebrew_summary_passes(self):
        summary = _summary([
            {"text": "נבדקו OpenClaw, Manus ו-Claude Code, עם 7 עד 10 skills לכל סוכן.", "seg_ids": ["seg_0000"]},
            {"text": "זה לא צ'אטבוט; קודם מריצים `npm install`.", "seg_ids": ["seg_0001"]},
            {"text": "בוט היומן מפרסם כל 30 דקות ל-Telegram.", "seg_ids": ["seg_0002"]},
        ])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertEqual(result["errors"], [], result["errors"])
        self.assertEqual(result["stats"]["lang"], "he")
        self.assertGreater(result["stats"]["hebrew_ratio"], 0.6)

    def test_unsupported_number_is_an_error(self):
        summary = _summary([{"text": "נבדקו 12 סוכנים.", "seg_ids": ["seg_0000"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertIn("number", _by_check(result, "errors"))

    def test_number_words_count_as_numbers(self):
        summary = _summary([{"text": "שבעה עד עשרה skills לכל סוכן.", "seg_ids": ["seg_0000"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertNotIn("number", _by_check(result, "errors"))

    def test_entity_fuzzy_match_and_review_levels(self):
        # "OpenClaw" ≈ "Open Claw" in the cited segment → fine; "Telegram" is in the
        # transcript but not in this block's segments → review; "Zapier" nowhere → error.
        summary = _summary([{"text": "OpenClaw שולח ל-Telegram דרך Zapier.", "seg_ids": ["seg_0000"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertIn("entity", _by_check(result, "reviews"))
        errors = [row for row in result["errors"] if row["check"] == "entity"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Zapier", errors[0]["message"])

    def test_metadata_is_an_authority_for_names(self):
        summary = _summary([{"text": "הערוץ Supadata מציג את OpenClaw.", "seg_ids": ["seg_0000"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary, info={"tags": ["supadata"]})
        self.assertEqual([row for row in result["errors"] if row["check"] == "entity"], [])

    def test_backtick_identifier_must_exist(self):
        summary = _summary([{"text": "מריצים `pip install openclaw`.", "seg_ids": ["seg_0001"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertIn("identifier", _by_check(result, "errors"))

    def test_negation_parity_is_a_review(self):
        summary = _summary([{"text": "זה צ'אטבוט רגיל.", "seg_ids": ["seg_0001"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertIn("negation", _by_check(result, "reviews"))

    def test_hebrew_hygiene(self):
        summary = _summary([
            {"text": "OpenClaw הוא כלי — עם נִקּוּד ותו ‏ בקרה.", "seg_ids": ["seg_0000"]},
            {"text": "Only English here.", "seg_ids": ["seg_0001"]},
        ])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        errors = _by_check(result, "errors")
        self.assertIn("niqqud", errors)
        self.assertIn("bidi", errors)
        self.assertIn("hebrew", errors)
        warnings = _by_check(result, "warnings")
        self.assertIn("dash", warnings)
        self.assertIn("leading-latin", warnings)

    def test_order_ownership_and_coverage(self):
        summary = _summary([
            {"text": "בוט היומן מפרסם כל 30 דקות.", "seg_ids": ["seg_0002"]},
            {"text": "נבדקו OpenClaw ו-Manus.", "seg_ids": ["seg_0000", "seg_0003"]},   # out of order + seg_0003 in ch02
        ])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        errors = _by_check(result, "errors")
        self.assertIn("order", errors)
        self.assertIn("ownership", errors)
        # a segment that straddles the cut belongs to either chapter
        straddle = _summary([{"text": "מקטע על הגבול.", "seg_ids": ["seg_0004"]}])
        self.assertNotIn("ownership", _by_check(audit_summary.run_audit(TRANSCRIPT, CHAPTERS, straddle), "errors"))

    def test_code_blocks_and_english_documents_skip_hebrew_checks(self):
        summary = _summary([
            {"kind": "code", "text": "npm install\nnpm run build", "seg_ids": ["seg_0001"]},
            {"text": "Run npm install first; it is not a chatbot.", "seg_ids": ["seg_0001"]},
        ], lang="en", overview="Narrow agents win.")
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        self.assertEqual([row for row in result["errors"] if row["check"] in ("hebrew", "identifier")], [])

    def test_caption_grounding_uses_frame_segments_and_ocr(self):
        summary = _summary([{"text": "נבדקו OpenClaw ו-Manus.", "seg_ids": ["seg_0000"]}])
        selections = [{"candidate_id": "c_0001", "caption": {"shows": "מסך Manus עם `Manage Skills`", "why": "מוכיח"}},
                      {"candidate_id": "c_0002", "caption": "English only caption"}]
        candidates = {"candidates": [
            {"candidate_id": "c_0001", "seg_ids": ["seg_0000"], "quality": {"ocr_text": "Manage Skills Official library"}},
            {"candidate_id": "c_0002", "seg_ids": ["seg_0000"], "quality": {}},
        ]}
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary, selections=selections, candidates=candidates)
        self.assertEqual([row for row in result["reviews"] if row["check"] == "caption" and row["where"] == "c_0001"], [])
        self.assertIn("caption", _by_check(result, "errors"))

    def test_report_and_exit_code_shape(self):
        summary = _summary([{"text": "נבדקו 12 סוכנים.", "seg_ids": ["seg_0000"]}])
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, summary)
        report = audit_summary.render_report(result)
        self.assertIn("## errors", report)
        self.assertIn("`number`", report)


if __name__ == "__main__":
    unittest.main()
