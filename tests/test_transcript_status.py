"""transcript.py must leave a machine-readable status, never a bare empty stub."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hostenv  # noqa: E402
import transcript  # noqa: E402
import whisper  # noqa: E402

INFO = {"id": "vid123", "title": "Fixture", "duration": 12, "language": "en",
        "subtitles": {"en": [{"ext": "vtt", "url": "https://example.invalid/en"}]},
        "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "https://example.invalid/en-orig"}],
                               "en-de": [{"ext": "vtt", "url": "https://example.invalid/x?tlang=de"}]}}
VTT = ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there\n\n"
       "00:00:02.000 --> 00:00:04.000\nsecond cue\n\n00:00:06.000 --> 00:00:08.000\nthird cue\n")


class TranscriptStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-ts-")
        self.work = Path(self.temporary.name) / "work"

    def tearDown(self):
        self.temporary.cleanup()

    def _fake_ytdlp(self, captions: bool):
        def run(args: list[str]) -> int:
            out_dir = Path(args[args.index("-o") + 1]).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            if "--write-info-json" in args:
                info = dict(INFO)
                if not captions:
                    info["subtitles"], info["automatic_captions"] = {}, {}
                (out_dir / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
                return 0
            if "--write-subs" in args:
                if captions:
                    (out_dir / "video.en.vtt").write_text(VTT, encoding="utf-8")
                return 0
            return 1
        return run

    def _main(self, *argv: str) -> int:
        with mock.patch.object(sys, "argv", ["transcript.py", "https://www.youtube.com/watch?v=vid123",
                                             "--work", str(self.work), *argv]):
            return transcript.main()

    def test_exit_6_writes_no_transcript_status_file(self):
        with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=False)):
            code = self._main()
        self.assertEqual(code, 6)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "no_transcript")
        self.assertEqual(payload["segments"], [])
        self.assertEqual(payload["source_detail"]["kind"], "none")
        self.assertIn("not authorized", payload["source_detail"]["reason"])
        self.assertIsNone(payload["source_detail"]["whisper_selected"])

    def test_ok_transcript_carries_schema_status_health_identity(self):
        with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=True)):
            code = self._main()
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["source_identity"], "https://www.youtube.com/watch?v=vid123")
        self.assertEqual(payload["health"]["segments"], 3)
        self.assertEqual(payload["health"]["largest_gap_s"], 4.0)
        self.assertIn(payload["source_detail"]["translated"], (False,))
        self.assertTrue(payload["generated_at"].endswith("+00:00"))

    def test_langs_bypass_reports_machine_translation_truthfully(self):
        def run(args: list[str]) -> int:
            out_dir = Path(args[args.index("-o") + 1]).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            if "--write-info-json" in args:
                (out_dir / "video.info.json").write_text(json.dumps(INFO), encoding="utf-8")
            elif "--write-subs" in args:
                (out_dir / "video.en-de.vtt").write_text(VTT, encoding="utf-8")
            return 0
        with mock.patch.object(transcript, "_run_ytdlp", run):
            code = self._main("--langs", "en-de")
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["source_detail"]["translated"])
        self.assertEqual(payload["source_detail"]["track"], "en-de")

    def test_whisper_chunk_failures_are_recorded_in_health(self):
        def fake_transcribe(*_args, **_kwargs):
            whisper.CHUNK_FAILURES[:] = [{"index": 1, "offset_s": 60.0, "error": "HTTP 500"}]
            return [{"start": 0.0, "end": 2.0, "text": "only the first chunk"}], "groq"
        with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=False)), \
                mock.patch.object(transcript, "load_api_key", return_value=("groq", "gsk-secret-value")), \
                mock.patch.object(transcript, "download_audio", return_value=self.work / "audio.m4a"), \
                mock.patch.object(transcript, "transcribe_video", fake_transcribe):
            code = self._main("--whisper", "groq")
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["health"]["whisper"]["chunks_failed"], 1)
        self.assertTrue(any("chunk" in w for w in payload["health"]["warnings"]))
        self.assertNotIn("gsk-secret-value", (self.work / "transcript.json").read_text(encoding="utf-8"))

    def test_transcribe_chunks_records_each_failure(self):
        whisper.CHUNK_FAILURES.clear()
        calls = {"n": 0}

        def one(_path):
            calls["n"] += 1
            if calls["n"] == 2:
                raise SystemExit("boom")
            return [{"start": 0.0, "end": 1.0, "text": "x"}]
        out = whisper.transcribe_chunks([(Path("a"), 0.0), (Path("b"), 30.0), (Path("c"), 60.0)], one)
        self.assertEqual(len(out), 2)
        self.assertEqual(whisper.CHUNK_FAILURES, [{"index": 1, "offset_s": 30.0, "error": "boom"}])

    def test_install_hint_replaces_brew_strings(self):
        with mock.patch.object(transcript.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                transcript._run_ytdlp(["--version"])
        self.assertIn("after the user approves", str(ctx.exception))
        if hostenv.platform_key() != "darwin":
            self.assertNotIn("brew", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
