"""Caption track ranking: manual original first, never machine-translated, iw → he."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class FetchCaptionsTests(unittest.TestCase):
    """yt-dlp's --sub-langs is a regex: a bare `en` also downloads `en-de`
    (a translation). The ranked key must be anchored and the exact file chosen."""

    def _fake_ytdlp(self, out: Path, calls: list, keys: tuple[str, ...]):
        def fake(args: list[str]) -> int:
            calls.append(list(args))
            if "--write-info-json" in args:
                info = {"id": "x", "language": "en",
                        "subtitles": {"en": _entries()},
                        "automatic_captions": {"en-orig": _entries(), "en-de": _entries(translated=True)}}
                (out / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
            else:
                for key in keys:
                    (out / f"video.{key}.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n", encoding="utf-8")
            return 0
        return fake

    def test_ranked_key_is_anchored_and_the_exact_file_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            calls: list = []
            with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(out, calls, ("en-de", "en-en", "en"))):
                result = transcript.fetch_captions("https://www.youtube.com/watch?v=x", out, None)
            sub_args = calls[1]
            self.assertEqual(sub_args[sub_args.index("--sub-langs") + 1], "^en$")
            self.assertEqual(result["track"]["key"], "en")
            self.assertTrue(result["subtitle_path"].endswith("video.en.vtt"))

    def test_explicit_pattern_prefers_the_shortest_track_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            calls: list = []
            with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(out, calls, ("en-de", "en-orig", "en"))):
                result = transcript.fetch_captions("https://www.youtube.com/watch?v=x", out, "en.*")
            self.assertEqual(result["track"]["key"], "en")
            self.assertEqual(result["track"]["kind"], "manual")


if __name__ == "__main__":
    unittest.main()
