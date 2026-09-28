import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import fmt_time, parse_time  # noqa: E402
import transcript  # noqa: E402

# YouTube auto-captions: every cue repeats the previous line, echo cues hold a line of one space.
ROLLING = "\n".join([
    "WEBVTT", "Kind: captions", "Language: en", "",
    "00:00:00.080 --> 00:00:02.230 align:start position:0%", " ",
    "So<00:00:00.320><c> many</c><00:00:00.480><c> devs</c>", "",
    "00:00:02.230 --> 00:00:02.240 align:start position:0%", "So many devs", " ", "",
    "00:00:02.240 --> 00:00:04.070 align:start position:0%", "So many devs",
    "work<00:00:02.399><c> with</c><00:00:02.639><c> LLMs.</c>", ""])

SRT = """1
00:00:01,000 --> 00:00:03,500
Hello &amp; welcome

2
00:00:03,500 --> 00:00:06,000
<i>to the</i> lecture
"""

OVERLAP = """WEBVTT

00:00:00.000 --> 00:00:02.000
we will talk about tokens today

00:00:02.000 --> 00:00:04.000
about tokens today and how they cost money
"""


def vtt(url="u"):
    return [{"ext": "vtt", "url": url}]


class Captions(unittest.TestCase):
    def test_youtube_rolling_captions_keep_each_phrase_once(self):
        self.assertEqual(transcript.parse_captions(ROLLING),
                         [(0.08, 2.23, "So many devs"), (2.24, 4.07, "work with LLMs.")])

    def test_srt_with_entities_and_tags(self):
        self.assertEqual(transcript.parse_captions(SRT),
                         [(1.0, 3.5, "Hello & welcome"), (3.5, 6.0, "to the lecture")])

    def test_overlapping_cues_are_stripped(self):
        self.assertEqual(transcript.parse_captions(OVERLAP)[1][2], "and how they cost money")

    def test_rank_prefers_original_and_drops_translations(self):
        info = {"language": "en-US", "subtitles": {"de": vtt()},
                "automatic_captions": {"en-orig": vtt(), "en": vtt(), "iw": vtt("u&tlang=iw"),
                                       "fr": [{"ext": "json3", "url": "u"}]}}
        self.assertEqual(transcript.rank_tracks(info), [("en-orig", True), ("en", True), ("de", False)])

    def test_manual_hebrew_beats_automatic_original(self):
        info = {"language": "en", "subtitles": {"iw": vtt()}, "automatic_captions": {"en-orig": vtt()}}
        self.assertEqual(transcript.rank_tracks(info), [("iw", False), ("en-orig", True)])

    def test_base_lang(self):
        self.assertEqual(transcript.base_lang("iw"), "he")
        self.assertEqual(transcript.base_lang("en-US"), "en")
        self.assertIsNone(transcript.base_lang(None))


class Paragraphs(unittest.TestCase):
    def test_break_near_30_seconds_at_sentence_end(self):
        segments = [(i * 10.0, i * 10.0 + 10, f"sentence {i}.") for i in range(7)]
        self.assertEqual(transcript.paragraphs(segments), [
            (0.0, "sentence 0. sentence 1. sentence 2."),
            (30.0, "sentence 3. sentence 4. sentence 5."),
            (60.0, "sentence 6.")])

    def test_without_punctuation_break_at_60_seconds(self):
        segments = [(i * 10.0, i * 10.0 + 10, f"words {i}") for i in range(8)]
        self.assertEqual([p[0] for p in transcript.paragraphs(segments)], [0.0, 60.0])

    def test_sentence_ending_inside_an_auto_caption_cue_counts(self):
        segments = [(i * 10.0, i * 10.0 + 10, f"end of one. start of {i}") for i in range(7)]
        self.assertEqual([p[0] for p in transcript.paragraphs(segments)], [0.0, 30.0, 60.0])

    def test_markdown_header_and_lines(self):
        meta = {"title": "Talk", "url": "https://youtu.be/x", "duration": 75,
                "chapters": [{"t": 0, "title": "Intro"}, {"t": 33, "title": "Tokens"}],
                "transcript": {"method": "captions", "track": "en-orig", "auto": True, "language": "en"}}
        text = transcript.markdown(meta, [(0.0, 5.0, "Hello there."), (40.0, 45.0, "Tokens cost money.")])
        self.assertIn("- Creator chapters: 00:00 Intro · 00:33 Tokens", text)
        self.assertIn("- Transcript: automatic captions, track en-orig", text)
        self.assertIn("- Words: 5", text)
        self.assertIn("[00:00] Hello there. Tokens cost money.", text)


class Times(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(fmt_time(59.9), "00:59")
        self.assertEqual(fmt_time(3723), "1:02:03")
        self.assertEqual(parse_time("12:34"), 754)
        self.assertEqual(parse_time("1:02:03"), 3723)


if __name__ == "__main__":
    unittest.main()
