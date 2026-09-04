"""Caption track ranking: manual original first, never machine-translated, iw → he."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import transcript  # noqa: E402


def _entries(translated: bool = False) -> list[dict]:
    url = "https://www.youtube.com/api/timedtext?v=x&lang=en" + ("&tlang=he" if translated else "")
    return [{"ext": "vtt", "url": url}]


class TrackRankingTests(unittest.TestCase):
    def test_hebrew_video_prefers_original_asr_and_normalizes_iw(self):
        info = {"language": "iw", "subtitles": {}, "automatic_captions": {
            "en": _entries(translated=True), "iw": _entries(), "iw-orig": _entries(), "fr": _entries(translated=True),
        }}
        tracks = transcript.rank_caption_tracks(info)
        self.assertEqual([t["key"] for t in tracks], ["iw-orig", "iw"])
        self.assertEqual(tracks[0]["language"], "he")
        self.assertTrue(tracks[0]["original"])
        self.assertEqual(tracks[0]["kind"], "auto")

    def test_manual_original_beats_everything_and_translations_are_dropped(self):
        info = {"language": "en-US", "subtitles": {"en-US": _entries(), "he-en": _entries(translated=True)},
                "automatic_captions": {"en-orig": _entries(), "en": _entries(), "iw": _entries(translated=True)}}
        tracks = transcript.rank_caption_tracks(info)
        self.assertEqual(tracks[0]["key"], "en-US")
        self.assertEqual(tracks[0]["kind"], "manual")
        self.assertEqual(tracks[0]["score"], 0)
        self.assertNotIn("iw", [t["key"] for t in tracks])
        self.assertNotIn("he-en", [t["key"] for t in tracks])
        self.assertEqual([t["key"] for t in tracks][1], "en-orig")

    def test_human_translation_in_a_wanted_language_outranks_original_asr(self):
        info = {"language": "de", "subtitles": {"he": _entries()}, "automatic_captions": {"de-orig": _entries()}}
        tracks = transcript.rank_caption_tracks(info)
        self.assertEqual([t["key"] for t in tracks], ["he", "de-orig"])

    def test_unknown_language_metadata_still_ranks(self):
        info = {"subtitles": {}, "automatic_captions": {"en": _entries(), "es": _entries()}}
        tracks = transcript.rank_caption_tracks(info)
        self.assertEqual(tracks[0]["key"], "en")
        self.assertEqual(transcript.normalize_lang(None), None)
        self.assertEqual(transcript.normalize_lang("iw-IL"), "he")

    def test_geresh_is_word_internal_for_overlap_stripping(self):
        prev = "אנחנו פותחים צ׳אט חדש עם הסוכן"
        cur = "צ׳אט חדש עם הסוכן ומבקשים ממנו"
        self.assertEqual(transcript._strip_overlap(prev, cur), "ומבקשים ממנו")


if __name__ == "__main__":
    unittest.main()
