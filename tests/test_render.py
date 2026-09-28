import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image  # noqa: E402

import render  # noqa: E402

YT = {"kind": "youtube", "id": "abcdefghijk", "url": "https://www.youtube.com/watch?v=abcdefghijk",
      "title": "Original title", "duration": 600, "uploader": "Speaker",
      "transcript": {"method": "captions", "track": "en-orig", "auto": True, "language": "en"}}
NOTES = """# איך טוקנים עובדים

> הטענה המרכזית: טוקן אינו מילה.
>
> פסקה שנייה בתקציר.

## עיקרי הדברים
- נקודה ראשונה עם `tiktoken`
  שממשיכה בשורה שנייה.

## [02:15] מה זה טוקן
טקסט עם **הדגשה** ו-<script>alert(1)</script> וקישור https://example.com/a?b=1&c=2.
![F01](התרשים המלא של `Split`)

```bash
echo [99:99]
```
"""


def frame_dir(tmp):
    folder = Path(tmp, "frames")
    folder.mkdir()
    Image.new("RGB", (32, 18), (200, 30, 30)).save(folder / "F01.jpg")
    return folder


class Parse(unittest.TestCase):
    def test_blocks(self):
        blocks = render.parse(NOTES)
        self.assertEqual([(b["kind"], b["line"]) for b in blocks],
                         [("title", 1), ("quote", 3), ("h2", 7), ("ul", 8), ("h2", 11), ("p", 12),
                          ("figure", 13), ("code", 15)])
        self.assertEqual(blocks[3]["items"], ["נקודה ראשונה עם `tiktoken` שממשיכה בשורה שנייה."])
        self.assertEqual(blocks[6]["id"], "F01")

    def test_short_frame_ids_are_normalised(self):
        self.assertEqual(render.parse("![F7](x)")[0]["id"], "F07")


class Inline(unittest.TestCase):
    def test_escaping_code_bold(self):
        self.assertEqual(render.inline("a <b> **c** `x<y`", None),
                         'a &lt;b&gt; <strong>c</strong> <code dir="ltr">x&lt;y</code>')

    def test_timestamps(self):
        def link(seconds):
            return f"https://www.youtube.com/watch?v=abcdefghijk&t={seconds}s"
        self.assertIn('href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=754s"', render.inline("[12:34]", link))
        self.assertEqual(render.inline("[1:02:03]", None), '<span class="ts" dir="ltr">1:02:03</span>')

    def test_quoted_latin_is_isolated_so_its_punctuation_stays_put(self):
        self.assertEqual(render.inline('ענה "Hello! How are you?" והמשיך', None),
                         'ענה <bdi dir="ltr">"Hello! How are you?"</bdi> והמשיך')
        self.assertEqual(render.inline('שלב ה"חשיבה" וצה"ל', None), 'שלב ה"חשיבה" וצה"ל')
        self.assertIn('<a href="https://x.com/a" dir="ltr">', render.inline('"see https://x.com/a"', None))

    def test_url_keeps_trailing_period_outside(self):
        self.assertEqual(render.inline("see https://x.com/a.", None),
                         'see <a href="https://x.com/a" dir="ltr">https://x.com/a</a>.')


class Check(unittest.TestCase):
    def test_problems_have_line_numbers(self):
        blocks = render.parse("# t\n\n## [20:00] late\n![F09](x)\n![F01]()\n![F01](again)\nשָׁלוֹם\n")
        errors, warnings = render.check(blocks, {"F01": 10.0}, 600)
        self.assertIn("line 3: [20:00] is after the end of the video (10:00)", errors)
        self.assertIn("line 4: F09 is not one of the frames (see frames.json)", errors)
        self.assertIn("line 5: F01 needs a caption: ![F01](what it shows and why it is here)", errors)
        self.assertIn("line 6: F01 is already used on line 5", warnings)
        self.assertTrue(any("niqqud" in w for w in warnings))

    def test_chapters_required(self):
        errors, _ = render.check(render.parse("# only a title\n\ntext"), {}, 60)
        self.assertEqual(errors, ["line 1: no chapters; start each with a `## ` heading"])


class Build(unittest.TestCase):
    def test_hebrew_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, errors, _ = render.build(NOTES, YT, [{"id": "F01", "t": 135.0, "held": 4}], frame_dir(tmp))
        self.assertEqual(errors, [])
        self.assertIn('<html lang="he" dir="rtl">', page)
        self.assertIn("data:image/jpeg;base64,", page)
        self.assertIn('<nav class="toc"', page)
        self.assertIn('class="brief"', page)
        self.assertIn("&amp;t=135s", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("echo [99:99]", page)
        self.assertIn('dir="ltr">02:15</a> מה זה טוקן</h2>', page)  # a space, not a margin on the wrong side
        self.assertIn('>מה זה טוקן</a> <span class="t" dir="ltr">02:15</span>', page)
        self.assertIn('dir="ltr">02:15</a> התרשים המלא', page)
        self.assertIn("אורך\u00a0<bdi", page)  # the label never wraps away from its value

    def test_english_page_and_bidi_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, errors, warnings = render.build("# Tokens‏\n\n## Intro\nText.\n", dict(YT, kind="url"), [],
                                                  frame_dir(tmp))
        self.assertEqual(errors, [])
        self.assertIn('<html lang="en" dir="ltr">', page)
        self.assertNotIn("‏", page)
        self.assertTrue(warnings)

    def test_missing_frame_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, errors, _ = render.build("# t\n\n## a\n![F02](x)\n", YT, [{"id": "F02", "t": 1.0, "held": 3}],
                                        frame_dir(tmp))
        self.assertTrue(errors and "F02.jpg is missing" in errors[0], errors)


@unittest.skipUnless(render.browser(), "needs Chrome, Chromium or Edge")
class Pdf(unittest.TestCase):
    def test_pdf_is_printed_and_the_browser_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            page, out = Path(tmp, "page.html"), Path(tmp, "page.pdf")
            page.write_text("<!doctype html><p>שלום</p>", encoding="utf-8")
            result = {}
            worker = threading.Thread(target=lambda: result.setdefault("ok", render.pdf(page, out)), daemon=True)
            worker.start()
            worker.join(60)
            self.assertTrue(result.get("ok"), "pdf() did not return a printed file within 60 s")


if __name__ == "__main__":
    unittest.main()
