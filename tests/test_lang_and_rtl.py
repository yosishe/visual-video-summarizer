"""Rendering in Hebrew: dir/lang on <html>, logical CSS, isolated LTR runs, fonts, STRINGS."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import render  # noqa: E402

TRANSCRIPT = {
    "source": "captions", "language": "en",
    "video": {"id": "vid", "title": "OpenClaw superteam", "uploader": "Riley", "duration": 100.0,
              "url": "https://www.youtube.com/watch?v=vid"},
    "segments": [{"seg_id": "seg_0000", "start": 0.0, "end": 50.0, "text": "seven to ten skills"},
                 {"seg_id": "seg_0001", "start": 50.0, "end": 100.0, "text": "run npm install"}],
}
CHAPTERS = [{"chapter_id": "ch01", "start": 0.0, "end": 50.0, "needs_frames": True},
            {"chapter_id": "ch02", "start": 50.0, "end": 100.0, "needs_frames": False}]
SUMMARIES = {
    "ch01": {"chapter_id": "ch01", "title": "הסוכנים שנבדקו", "blocks": [
        {"block_id": "ch01_b01", "kind": "prose", "lang": None, "text": "בין 7 עד 10 `skills` לסוכן.", "seg_ids": ["seg_0000"]}],
        "key_points": ["‏7 עד 10 skills"]},
    "ch02": {"chapter_id": "ch02", "title": "התקנה", "blocks": [
        {"block_id": "ch02_b01", "kind": "code", "lang": "bash", "text": "npm install", "seg_ids": ["seg_0001"]},
        {"block_id": "ch02_b02", "kind": "quote", "lang": None, "text": "Prompts are so late 2025.", "seg_ids": ["seg_0001"]}],
        "key_points": []},
}
FRAME = {
    "candidate_id": "c_0001", "name": "ch01_board", "chapter_id": "ch01", "actual_t": 12.5, "role": "evidence",
    "novelty": "new_state", "caption": {"shows": "הלוח עם `7-10 skills`", "why": "מוכיח את המספר", "look_at": "הכיתוב האדום"},
    "alt": "לוח לבן", "anchor_seg_ids": ["seg_0000"], "block_index": 0,
    "asset": {"full": {"file": "ch01_board-full.jpg"}, "thumb": {"file": "ch01_board-thumb.jpg"}},
}


class RtlRenderTests(unittest.TestCase):
    def _html(self, lang: str) -> str:
        return render._render_html(TRANSCRIPT, CHAPTERS, SUMMARIES, [FRAME], "הטענה: סוכנים צרים.", lang, 50)

    def test_hebrew_document_is_rtl_with_embedded_font(self):
        html = self._html("he")
        self.assertIn('<html lang="he" dir="rtl">', html)
        self.assertIn("@font-face { font-family: 'Heebo'", html)
        self.assertIn("data:font/woff;base64,", html)
        self.assertIn("סיכום חזותי של סרטון", html)
        self.assertIn('<h1 dir="auto">', html)

    def test_english_document_stays_ltr_without_font(self):
        html = self._html("en")
        self.assertIn('<html lang="en" dir="ltr">', html)
        self.assertNotIn("@font-face", html)
        self.assertIn("Visual video summary", html)

    def test_ltr_runs_are_isolated(self):
        html = self._html("he")
        self.assertIn('<bdi dir="ltr">00:12</bdi>', html)                # timestamp in the caption
        self.assertIn('<span class="range" dir="ltr">', html)            # chapter range
        self.assertIn('<code dir="ltr">skills</code>', html)             # backticks in prose
        self.assertIn('<pre dir="ltr"><code class="lang-bash">npm install</code></pre>', html)
        self.assertIn('<blockquote dir="auto">', html)
        self.assertIn('<span class="why">מוכיח את המספר</span>', html)
        self.assertIn("שימו לב:", html)

    def test_style_uses_logical_properties_only(self):
        physical = re.findall(r"(?<![-\w])(padding|margin|border)-(left|right)\s*:|text-align:\s*(left|right)\b", render.STYLE)
        # `pre { text-align: left }` is the one deliberate exception: code is LTR by definition.
        self.assertEqual([m for m in physical if m[2] != "left"], [])
        self.assertIn("border-inline-start", render.STYLE)
        self.assertIn("padding-inline-start", render.STYLE)

    def test_strings_cover_both_languages(self):
        self.assertEqual(set(render.STRINGS["he"]), set(render.STRINGS["en"]))

    def test_inline_escapes_then_marks_code(self):
        self.assertEqual(render._inline("a <b> `x<y`"), 'a &lt;b&gt; <code dir="ltr">x&lt;y</code>')
        self.assertEqual(render._inline("odd `tick"), "odd `tick")

    def test_resolve_lang_precedence(self):
        with mock.patch.dict(os.environ, {"SUMMARY_LANG": "he"}):
            self.assertEqual(render.resolve_lang(None, {}), "he")
            self.assertEqual(render.resolve_lang(None, {"lang": "en"}), "en")
            self.assertEqual(render.resolve_lang("en", {"lang": "he"}), "en")
        with mock.patch.dict(os.environ, {"SUMMARY_LANG": ""}), mock.patch.object(render, "CONFIG_ENV", Path("/nonexistent")):
            self.assertEqual(render.resolve_lang(None, {}), "en")
        with self.assertRaises(SystemExit):
            render.resolve_lang("fr", {})

    def test_hebrew_summary_needs_hebrew_prose(self):
        bad = {"chapters": [{"chapter_id": "ch01", "blocks": [{"text": "English only", "seg_ids": ["seg_0000"]}]},
                            {"chapter_id": "ch02", "blocks": [{"text": "עברית", "seg_ids": ["seg_0001"]}]}]}
        with self.assertRaisesRegex(SystemExit, "Hebrew"):
            render._normalized_summary(bad, CHAPTERS, "he")
        render._normalized_summary(bad, CHAPTERS, "en")

    def test_caption_object_and_build_stage_rule(self):
        self.assertEqual(render._caption_fields({"caption": "plain"}), {"shows": "plain", "why": None, "look_at": None})
        self.assertEqual(render._caption_fields({"caption": {"shows": "a", "why": "", "look_at": "c"}}),
                         {"shows": "a", "why": None, "look_at": "c"})

    def test_canonical_hash_is_order_independent(self):
        self.assertEqual(render.canonical_json_sha256({"a": 1, "b": "ב"}), render.canonical_json_sha256({"b": "ב", "a": 1}))


if __name__ == "__main__":
    unittest.main()
