"""Opening synthesis: evidence validation and legacy chapter/render isolation."""
from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_summary
import render


TRANSCRIPT = {
    "video": {"title": "Cache freshness", "duration": 210,
              "url": "https://www.youtube.com/watch?v=fixture"},
    "segments": [
        {"seg_id": "s0", "start": 0, "end": 70,
         "text": "Caching reuses a result. In this example it took 20 ms instead of 200 ms."},
        {"seg_id": "s1", "start": 70, "end": 140,
         "text": "A high hit rate does not prove freshness after the source data changes."},
        {"seg_id": "s2", "start": 140, "end": 210,
         "text": "Do not choose one expiration interval for every application. Define acceptable staleness before setting TTL."},
    ],
}
CHAPTERS = [{"chapter_id": "ch01", "start": 0, "end": 140},
            {"chapter_id": "ch02", "start": 140, "end": 210}]


def item(text, *ids):
    return {"text": text, "seg_ids": list(ids)}


def summary(lang="en"):
    texts = ("Reuse saves work in this example.", "A high hit rate does not prove freshness.",
             "Do not choose one expiration interval for every application.") if lang == "en" else (
        "שימוש חוזר חוסך עבודה בדוגמה.", "שיעור פגיעות גבוה לא מוכיח שהמידע עדכני.",
        "אין לבחור זמן תפוגה אחיד לכל יישום.")
    return {"lang": lang, "overview": texts[0], "chapters": [
        {"chapter_id": "ch01", "blocks": [item(texts[0], "s0"), item(texts[1], "s1")]},
        {"chapter_id": "ch02", "blocks": [item(texts[2], "s2")]},
    ]}


def brief(lang="en"):
    if lang == "he":
        return {
            "synthesis": item("שימוש חוזר חוסך עבודה, אך אין לבחור זמן תפוגה אחיד לכל יישום.", "s0", "s2"),
            "main_points": [item("בדוגמה זמן הביצוע היה 20 מילישניות במקום 200 מילישניות.", "s0"),
                            item("שיעור פגיעות גבוה לא מוכיח שהמידע עדכני.", "s1")],
            "takeaways": [item("אין לקבוע `TTL` לפני הגדרת מידת ההתיישנות המותרת.", "s2")],
        }
    return {
        "synthesis": item("Caching saves work, but do not choose one expiration interval for every application.", "s0", "s2"),
        "main_points": [item("A high hit rate does not prove freshness.", "s1"),
                        item("In the example, reuse took 20 ms instead of 200 ms.", "s0")],
        "takeaways": [item("Do not set `TTL` before defining acceptable staleness.", "s2")],
    }


class BriefTests(unittest.TestCase):
    def test_legacy_absence_and_valid_languages(self):
        for lang in ("en", "he"):
            with self.subTest(lang=lang):
                payload = summary(lang)
                baseline = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)
                self.assertEqual(audit_summary.brief_items(payload, TRANSCRIPT), [])
                payload["brief"] = brief(lang)
                result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["stats"], baseline["stats"])

    def test_brief_cannot_hide_missing_detailed_coverage(self):
        payload = summary()
        payload["chapters"][1]["blocks"] = []
        baseline = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)
        payload["brief"] = brief()
        result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)
        coverage = lambda r: [x for x in r["reviews"] if x["check"] == "coverage"]
        self.assertTrue(coverage(baseline))
        self.assertEqual(coverage(result), coverage(baseline))
        self.assertEqual(result["stats"], baseline["stats"])

    def test_malformed_briefs_are_reported_without_crashing(self):
        cases = [None, [], "summary", {}, {**brief(), "main_points": "point"},
                 {**brief(), "synthesis": None}, {**brief(), "takeaways": ["text"]}]
        bad_items = [{}, item(" ", "s0"), item(7, "s0"), item("text"),
                     {"text": "text", "seg_ids": "s0"}, item("text", {}),
                     item("text", "missing"), item("text", "s0", "s0"),
                     item("text", "s2", "s0")]
        cases += [{**brief(), "synthesis": bad} for bad in bad_items]
        for bad in cases:
            with self.subTest(brief=bad):
                result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, {**summary(), "brief": bad})
                self.assertTrue(any(row["check"] == "brief" for row in result["errors"]))

    def test_numbers_must_appear_in_the_items_own_citations(self):
        for text, ids in [("It took 999 ms.", ["s0"]), ("It took 20 ms.", ["s1"])]:
            payload = {**summary(), "brief": brief()}
            payload["brief"]["main_points"] = [item(text, *ids)]
            errors = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)["errors"]
            self.assertTrue(any(x["check"] == "number" and x["where"] == "brief/main_points/1" for x in errors))

    def test_grounding_and_language_checks_apply_to_every_part(self):
        for part in ("synthesis", "main_points", "takeaways"):
            payload = {**summary("he"), "brief": brief("he")}
            bad = item("English `invented_command` \u2067", "s2")
            payload["brief"][part] = bad if part == "synthesis" else [bad]
            result = audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)
            checks = {x["check"] for x in result["errors"] if x["where"].startswith(f"brief/{part}")}
            self.assertTrue({"identifier", "hebrew", "bidi"} <= checks, checks)
            self.assertTrue(any(x["check"] == "negation" for x in result["reviews"]))

    def test_short_brief_has_no_count_quota(self):
        payload = {**summary(), "brief": {"synthesis": item("Caching reuses results.", "s0"),
                                        "main_points": [], "takeaways": []}}
        self.assertEqual(audit_summary.run_audit(TRANSCRIPT, CHAPTERS, payload)["errors"], [])
        fragment = render._brief_html(payload["brief"], TRANSCRIPT, render.STRINGS["en"])
        self.assertNotIn("<h3>", fragment)

    def test_render_preserves_chapters_and_renders_both_languages(self):
        for lang in ("en", "he"):
            payload = summary(lang)
            normalized = render._normalized_summary(payload, CHAPTERS, lang)
            args = (TRANSCRIPT, CHAPTERS, normalized, [], payload["overview"], lang)
            legacy = render._render_html(*args)
            updated = render._render_html(*args, brief=brief(lang))
            self.assertNotIn('class="brief"', legacy)
            self.assertLess(updated.index('class="brief"'), updated.index('class="chapter"'))
            chapter_html = lambda h: re.findall(r'<section class="chapter".*?</section>', h, re.S)
            self.assertEqual(chapter_html(legacy), chapter_html(updated))
            self.assertIn(render.STRINGS[lang]["main_points"], updated)
            self.assertIn(render.STRINGS[lang]["takeaways"], updated)
            self.assertIn('<code dir="ltr">TTL</code>', updated)
            self.assertEqual(payload, summary(lang))

    def test_source_links_use_actual_disjoint_runs_and_escape_text(self):
        content = brief()
        content["synthesis"]["text"] = '<script>alert("x")</script> `x<y`'
        fragment = render._brief_html(content, TRANSCRIPT, render.STRINGS["en"])
        self.assertNotIn("<script>", fragment)
        self.assertIn("&lt;script&gt;", fragment)
        self.assertIn('<code dir="ltr">x&lt;y</code>', fragment)
        self.assertIn('class="brief-sources" dir="ltr"', fragment)
        synthesis = fragment.split("</p>")[0]
        self.assertIn("&amp;t=0", synthesis)
        self.assertIn("&amp;t=140", synthesis)
        self.assertNotIn("&amp;t=70", synthesis)
        content["synthesis"]["seg_ids"] = ["s0", "s1", "s2"]
        contiguous = render._brief_html(content, TRANSCRIPT, render.STRINGS["en"]).split("</p>")[0]
        self.assertEqual(contiguous.count("<a href="), 1)

    def test_local_source_has_plain_timestamps(self):
        transcript = copy.deepcopy(TRANSCRIPT)
        transcript["video"]["url"] = "/tmp/lecture.mp4"
        fragment = render._brief_html(brief(), transcript, render.STRINGS["en"])
        self.assertNotIn("href=", fragment)
        self.assertIn("02:20", fragment)


if __name__ == "__main__":
    unittest.main()
