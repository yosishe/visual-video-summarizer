"""transcript.py must leave a machine-readable status, never a bare empty stub."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gates  # noqa: E402
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
                                             "--work", str(self.work), *argv]), \
                mock.patch.object(transcript, "_fetch_caption_url", return_value=VTT.encode()):
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
        self.assertEqual(payload["health"]["status"], "thin")
        self.assertIn("translated", payload["health"]["flags"])

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

    def test_metadata_failure_is_exit_13_with_a_sanitised_reason_and_no_upload(self):
        def run(args: list[str]) -> int:
            if "--write-info-json" in args:
                transcript.YTDLP_LAST["stderr"] = ("WARNING: something first\nERROR: [youtube] vid123: Video unavailable. "
                                                   "This video is private https://example.invalid/?token=SECRET-VALUE\n")
                return 1
            return 0
        with mock.patch.object(transcript, "_run_ytdlp", run), \
                mock.patch.object(transcript, "load_api_key", return_value=("groq", "gsk-secret-value")), \
                mock.patch.object(transcript, "download_audio", side_effect=AssertionError("must not upload")):
            code = self._main("--whisper", "groq")
        self.assertEqual(code, 13)
        text = (self.work / "transcript.json").read_text(encoding="utf-8")
        payload = json.loads(text)
        self.assertEqual(payload["status"], "source_unavailable")
        self.assertEqual(payload["acquisition_error"]["category"], "source_unavailable")
        self.assertIn("unavailable", payload["source_detail"]["reason"].lower())
        self.assertEqual(payload["segments"], [])
        self.assertNotIn("SECRET-VALUE", text)
        self.assertNotIn("example.invalid", text)
        self.assertNotIn("gsk-secret-value", text)

    def test_ok_transcript_records_status_and_provenance(self):
        with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=True)):
            code = self._main()
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        health = payload["health"]
        # three 2-second cues over a 12-second video: truthfully thin (50 % coverage)
        self.assertEqual(health["status"], "thin")
        self.assertEqual(health["flags"], ["low_coverage"])
        self.assertEqual(health["provenance"]["track"], "en")
        self.assertTrue(health["provenance"]["manual"])
        self.assertTrue(health["provenance"]["language_match"])
        self.assertIn("health thin", gates.health_summary(health))

    def test_ok_transcript_records_the_request_options(self):
        with mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=True)):
            code = self._main("--langs", "en")
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["inputs"], {"whisper": None, "no_whisper": False, "langs": "en", "wanted": None,
                                             "local_model": None})

    def test_long_url_is_refused_before_caption_media_or_asr(self):
        info = dict(INFO, duration=7201)
        with mock.patch.object(transcript, "_discover_info", return_value=info), \
                mock.patch.object(transcript, "_fetch_caption_url", side_effect=AssertionError("no caption fetch")), \
                mock.patch.object(transcript, "download_audio", side_effect=AssertionError("no media")), \
                mock.patch.object(transcript, "transcribe_video", side_effect=AssertionError("no ASR")):
            code = self._main()
        self.assertEqual(code, 8)

    def test_partial_transcription_has_exact_ranges_and_content_fingerprint(self):
        failure = [{"index": 1, "offset_s": 60.0, "end_s": 120.0, "error": "temporary_network"}]
        partial = transcript.PartialTranscription(
            [{"start": 0.0, "end": 2.0, "text": "first chunk"}], failure)
        with mock.patch.object(transcript, "fetch_captions", return_value={
                "info": {**INFO, "subtitles": {}, "automatic_captions": {}}, "track": None,
                "subtitle_path": None, "segments": [], "tracks_considered": 0, "rejected_translated": 0}), \
                mock.patch.object(transcript, "load_api_key", return_value=("groq", "secret")), \
                mock.patch.object(transcript, "download_audio", return_value=self.work / "audio.m4a"), \
                mock.patch.object(transcript, "transcribe_video", side_effect=partial):
            code = self._main("--whisper", "groq")
        self.assertEqual(code, 15)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["failed_chunks"], failure)
        expected = transcript.canonical_hash({
            "source_identity": payload["source_identity"], "inputs": payload["inputs"],
            "segments": payload["segments"], "failed_chunks": failure,
        })
        self.assertEqual(payload["partial_fingerprint"], expected)

    def test_caption_rate_limit_writes_acquisition_failed_and_never_starts_asr(self):
        error = transcript.AcquisitionError("rate_limit", "source cooldown", retryable=True)
        with mock.patch.object(transcript, "fetch_captions", side_effect=error), \
                mock.patch.object(transcript, "download_audio", side_effect=AssertionError("no media")), \
                mock.patch.object(transcript, "transcribe_video", side_effect=AssertionError("no ASR")):
            code = self._main("--whisper", "groq")
        self.assertEqual(code, 14)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "acquisition_failed")
        self.assertEqual(payload["acquisition_error"]["category"], "rate_limit")

    def test_local_model_is_normalized_recorded_and_passed_only_for_local_mode(self):
        configured = self.work / "models" / "ggml.bin"
        configured.parent.mkdir(parents=True)
        configured.write_bytes(b"model")
        captured = {}

        def fake_transcribe(*args, **kwargs):
            captured.update(kwargs)
            return ([{"start": 0.0, "end": 2.0, "text": "local words"}], "local")

        with mock.patch.object(transcript, "fetch_captions", return_value={
                "info": {**INFO, "subtitles": {}, "automatic_captions": {}}, "track": None,
                "subtitle_path": None, "segments": [], "tracks_considered": 0, "rejected_translated": 0}), \
                mock.patch.object(transcript, "configured_local_model", return_value=configured), \
                mock.patch.object(transcript, "download_audio", return_value=self.work / "audio.m4a"), \
                mock.patch.object(transcript, "transcribe_video", side_effect=fake_transcribe):
            code = self._main("--whisper", "local")
        self.assertEqual(code, 0)
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["inputs"]["local_model"], str(configured.resolve()))
        self.assertEqual(captured["local_model"], configured.resolve())
        self.assertNotIn("cache_dir", captured)
        self.assertNotIn("retry_uncertain", captured)

    def test_explicitly_configured_local_model_is_the_no_provider_default(self):
        configured = self.work / "configured" / "ggml.bin"
        configured.parent.mkdir(parents=True)
        configured.write_bytes(b"model")
        with mock.patch.object(transcript, "configured_local_model", return_value=configured), \
                mock.patch.object(transcript, "fetch_captions", return_value={
                    "info": {**INFO, "subtitles": {}, "automatic_captions": {}}, "track": None,
                    "subtitle_path": None, "segments": [], "tracks_considered": 0, "rejected_translated": 0}), \
                mock.patch.object(transcript, "download_audio", return_value=self.work / "audio.m4a"), \
                mock.patch.object(transcript, "transcribe_video",
                                  return_value=([{"start": 0, "end": 1, "text": "configured"}], "local")) as call:
            code = self._main()
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args.kwargs["local_model"], configured.resolve())
        payload = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["inputs"]["whisper"], "local")

    def test_configured_local_model_is_not_consulted_when_captions_succeed(self):
        with mock.patch.object(transcript, "configured_local_model",
                               side_effect=AssertionError("must be deferred")), \
                mock.patch.object(transcript, "_run_ytdlp", self._fake_ytdlp(captions=True)):
            code = self._main()
        self.assertEqual(code, 0)

    def test_competing_direct_cli_writer_is_refused_by_work_lock(self):
        self.work.mkdir(parents=True)
        with transcript.file_lock(self.work / ".transcript.lock"):
            with self.assertRaises(transcript.AcquisitionError) as caught:
                self._main()
        self.assertEqual(caught.exception.category, "busy")

    def test_install_hint_replaces_brew_strings(self):
        with mock.patch.object(transcript.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                transcript._run_ytdlp(["--version"])
        self.assertIn("after the user approves", str(ctx.exception))
        if hostenv.platform_key() != "darwin":
            self.assertNotIn("brew", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
