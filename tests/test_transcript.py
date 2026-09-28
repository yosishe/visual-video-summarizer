import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import VsumError, fmt_time, parse_time  # noqa: E402
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

    def test_whitespace_only_separator_lines(self):
        srt = "1\n00:00:01,000 --> 00:00:03,000\nHello there\n \n2\n00:00:04,000 --> 00:00:06,000\nGeneral Kenobi\n"
        self.assertEqual(transcript.parse_captions(srt), [(1.0, 3.0, "Hello there"), (4.0, 6.0, "General Kenobi")])

    def test_an_orig_track_reveals_the_original_language(self):
        info = {"subtitles": {"en": vtt(), "iw": vtt()}, "automatic_captions": {"iw-orig": vtt()}}
        self.assertEqual(transcript.rank_tracks(info), [("iw", False), ("en", False), ("iw-orig", True)])

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


class Whisper(unittest.TestCase):
    def test_parse_json(self):
        data = {"transcription": [{"offsets": {"from": 0, "to": 2500}, "text": " שלום  עולם "},
                                  {"offsets": {"from": 2500, "to": 3000}, "text": " "}]}
        self.assertEqual(transcript.parse_whisper(data), [(0.0, 2.5, "שלום עולם")])

    def test_detected_language(self):
        self.assertEqual(transcript.detected_language("x auto-detected language: he (p = 0.99)"), "he")
        self.assertIsNone(transcript.detected_language("nothing here"))

    def test_model_from_environment(self):
        with tempfile.NamedTemporaryFile(suffix=".bin") as model, \
                mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": model.name}):
            self.assertEqual(transcript.whisper_model(), model.name)

    def test_model_from_v1_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp, "ggml.bin")
            model.write_bytes(b"x")
            registration = Path(tmp, "local-model.json")
            registration.write_text(json.dumps({"model_path": str(model)}))
            with mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": ""}), \
                    mock.patch.object(transcript, "_registration", return_value=registration):
                self.assertEqual(transcript.whisper_model(), str(model))

    def test_no_model_says_what_to_do(self):
        with mock.patch.dict(os.environ, {"VSUM_WHISPER_MODEL": ""}), \
                mock.patch.object(transcript, "_registration", return_value=None):
            with self.assertRaisesRegex(VsumError, "VSUM_WHISPER_MODEL"):
                transcript.whisper_model()


FAKE_WHISPER = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if "-dl" in args:
    print("auto-detected language: en (p = 0.99)", file=sys.stderr)
    sys.exit(0)
log = os.environ["FAKE_WHISPER_LOG"]
with open(log, "a") as f:
    f.write("run\\n")
runs = sum(1 for _ in open(log))
if runs == int(os.environ.get("FAKE_WHISPER_FAIL_AT", "0")):
    sys.exit(3)
with open(args[args.index("-of") + 1] + ".json", "w") as out:
    json.dump({"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": f" part {runs}"}]}, out)
"""


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class WhisperParts(unittest.TestCase):
    def test_a_stopped_transcription_resumes_at_the_first_unfinished_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            fake = tmp / "bin" / "whisper-cli"
            fake.write_text(FAKE_WHISPER)
            fake.chmod(0o755)
            (tmp / "model.bin").write_bytes(b"x")
            media = tmp / "talk.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=16000:cl=mono", "-t", "12", str(media)], check=True)
            env = {"PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", "VSUM_WHISPER_MODEL": str(tmp / "model.bin"),
                   "FAKE_WHISPER_LOG": str(tmp / "runs.log"), "FAKE_WHISPER_FAIL_AT": "2"}
            with mock.patch.dict(os.environ, env), mock.patch.object(transcript, "CHUNK", 5):
                with self.assertRaises(VsumError):
                    transcript.transcribe(media, tmp, "en", 12.0)
                os.environ["FAKE_WHISPER_FAIL_AT"] = "0"
                segments, info = transcript.transcribe(media, tmp, "en", 12.0)
            self.assertEqual(segments, [(0.0, 1.0, "part 1"), (5.0, 6.0, "part 3"), (10.0, 11.0, "part 4")])
            self.assertEqual(info["language"], "en")


class Times(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(fmt_time(59.9), "00:59")
        self.assertEqual(fmt_time(3723), "1:02:03")
        self.assertEqual(parse_time("12:34"), 754)
        self.assertEqual(parse_time("1:02:03"), 3723)


if __name__ == "__main__":
    unittest.main()
