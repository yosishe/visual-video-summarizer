from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acquisition  # noqa: E402
import whisper  # noqa: E402


def _run_python_cli_fixture(command, **kwargs):
    """Run the fake CLI as a real subprocess without relying on Unix shebangs."""
    return acquisition.run_process([sys.executable, *command], **kwargs)


class WhisperAcquisitionTests(unittest.TestCase):
    def test_malformed_segments_and_untimed_text_are_rejected(self):
        bad = [
            {"segments": [{"start": 0, "end": float("nan"), "text": "x"}]},
            {"segments": [{"start": 4, "end": 2, "text": "x"}]},
            {"text": "words without timestamps"},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(acquisition.AcquisitionError):
                whisper._segments_from_response(value)

    def test_upload_caps_audio_and_actual_multipart_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"x" * 100)
            with mock.patch.object(whisper, "MAX_AUDIO_BYTES", 99):
                with self.assertRaisesRegex(acquisition.AcquisitionError, "audio limit"):
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio)
            with mock.patch.object(whisper, "MAX_MULTIPART_BYTES", 120):
                with self.assertRaisesRegex(acquisition.AcquisitionError, "multipart"):
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio)

    def test_auth_stops_later_chunks_after_one_call(self):
        for category in ("authentication", "quota_exceeded"):
            calls = []
            def one(path):
                calls.append(path.name)
                raise acquisition.AcquisitionError(category, "Provider cannot continue")
            chunks = [(Path("a"), 0.0, 10.0), (Path("b"), 10.0, 10.0)]
            with self.subTest(category=category), self.assertRaises(acquisition.AcquisitionError):
                whisper.transcribe_chunks(chunks, one)
            self.assertEqual(calls, ["a"])

    def test_partial_transcription_exposes_bounded_failures(self):
        def one(path):
            if path.name == "b":
                raise acquisition.AcquisitionError("invalid_input", "bad chunk")
            return [{"start": 0.0, "end": 1.0, "text": path.name}]

        chunks = [(Path("a"), 0.0, 10.0), (Path("b"), 10.0, 10.0), (Path("c"), 20.0, 5.0)]
        with self.assertRaises(whisper.PartialTranscription) as caught:
            whisper.transcribe_chunks(chunks, one)
        self.assertEqual([s["text"] for s in caught.exception.segments], ["a"])
        self.assertEqual(caught.exception.failures[-1]["status"], "not_attempted")
        self.assertEqual(caught.exception.failures[0]["range"], {"start_s": 10.0, "end_s": 20.0})

    def test_overlap_removes_only_boundary_duplicate(self):
        prior = [{"start": 0.0, "end": 10.5, "text": "alpha boundary words"}]
        later = [
            {"start": 9.5, "end": 10.6, "text": "boundary words"},
            {"start": 10.6, "end": 12.0, "text": "new speech"},
        ]
        merged = whisper.merge_segments(prior, later, overlap_start=9.0, overlap_end=11.0)
        self.assertEqual([x["text"] for x in merged], ["alpha boundary words", "new speech"])

    def test_uncertain_attempt_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            opener = mock.MagicMock()
            opener.open.side_effect = TimeoutError("secret body")
            with mock.patch.object(whisper, "build_opener", return_value=opener):
                with self.assertRaises(acquisition.AcquisitionError) as first:
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio, work=Path(tmp) / "work")
                self.assertTrue(first.exception.uncertain)
                with self.assertRaises(acquisition.AcquisitionError) as second:
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio, work=Path(tmp) / "work")
                self.assertTrue(second.exception.uncertain)
            self.assertEqual(opener.open.call_count, 1)

    def test_uncertain_chunk_stops_and_marks_all_later_ranges_unattempted(self):
        whisper.CHUNK_FAILURES.clear()
        calls = []
        def one(path):
            calls.append(path.name)
            raise acquisition.AcquisitionError("uncertain_upload", "response lost", uncertain=True)
        chunks = [(Path("a"), 0.0, 10.0), (Path("b"), 10.0, 10.0), (Path("c"), 20.0, 5.0)]
        with self.assertRaises(acquisition.AcquisitionError):
            whisper.transcribe_chunks(chunks, one)
        self.assertEqual(calls, ["a"])
        self.assertEqual([x["status"] for x in whisper.CHUNK_FAILURES],
                         ["uncertain_upload", "not_attempted", "not_attempted"])
        self.assertEqual(whisper.CHUNK_FAILURES[-1]["range"], {"start_s": 20.0, "end_s": 25.0})

    def test_response_body_is_bounded(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, amount): return b"x" * amount
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            opener = mock.MagicMock()
            opener.open.return_value = Response()
            with mock.patch.object(whisper, "build_opener", return_value=opener):
                with self.assertRaisesRegex(acquisition.AcquisitionError, "response body"):
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio)

    def test_shared_retry_owner_caps_temporary_http_attempts_at_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            errors = [urllib.error.HTTPError(whisper.GROQ_ENDPOINT, 503, "private", {}, io.BytesIO(b"private"))
                      for _ in range(3)]
            opener = mock.MagicMock()
            opener.open.side_effect = errors
            with mock.patch.object(whisper, "build_opener", return_value=opener), \
                    mock.patch.object(acquisition.time, "sleep"):
                with self.assertRaises(acquisition.AcquisitionError) as caught:
                    whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio)
            self.assertEqual(caught.exception.category, "temporary_network")
            self.assertEqual(caught.exception.attempts, 3)
            self.assertEqual(opener.open.call_count, 3)

    def test_local_model_resolution_does_not_read_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-large-v3.bin"
            model.write_bytes(b"lmgg" + (51865).to_bytes(4, "little") + b"fixture")
            with mock.patch.dict(os.environ, {"LOCAL_WHISPER_MODEL": str(model)}, clear=True), \
                    mock.patch.object(whisper, "load_api_key", side_effect=AssertionError("no credentials")), \
                    mock.patch.object(whisper, "_transcribe_local_video", return_value=[{"start": 0, "end": 1, "text": "ok"}]):
                segments, used = whisper.transcribe_video("video.mp4", Path(tmp) / "audio.mp3", backend="local")
            self.assertEqual(used, "local")
            self.assertEqual(segments[0]["text"], "ok")

    def test_chunk_cache_hit_avoids_second_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "chunk.mp3"
            audio.write_bytes(b"chunk")
            calls = {"n": 0}

            def call():
                calls["n"] += 1
                return [{"start": 0.0, "end": 1.0, "text": "cached"}]

            identity = {"engine": "groq", "model": "m", "version": "v", "language": "he",
                        "extraction": "mp3-16k-mono", "start": 0.0, "end": 10.0}
            first = whisper.cached_chunk(audio, Path(tmp) / "cache", identity, call)
            second = whisper.cached_chunk(audio, Path(tmp) / "cache", identity, call)
            self.assertEqual(first, second)
            self.assertEqual(calls["n"], 1)

    def test_successful_chunk_survives_partial_run_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunks = []
            for name, offset in (("a.mp3", 0.0), ("b.mp3", 10.0)):
                path = root / name
                path.write_bytes(name.encode())
                chunks.append((path, offset, 10.0))
            calls = {"a.mp3": 0, "b.mp3": 0}
            fail_b = {"value": True}
            def one(path):
                identity = {"engine": "groq", "model": "m", "version": "v", "language": None,
                            "extraction": "mp3", "start": 0, "end": 10}
                def provider():
                    calls[path.name] += 1
                    if path.name == "b.mp3" and fail_b["value"]:
                        raise acquisition.AcquisitionError("temporary_network", "retry exhausted")
                    return [{"start": 0.0, "end": 1.0, "text": path.name}]
                return whisper.cached_chunk(path, root / "cache", identity, provider)
            with self.assertRaises(whisper.PartialTranscription):
                whisper.transcribe_chunks(chunks, one)
            fail_b["value"] = False
            whisper.CHUNK_FAILURES.clear()
            result = whisper.transcribe_chunks(chunks, one)
            self.assertEqual([s["text"] for s in result], ["a.mp3", "b.mp3"])
            self.assertEqual(calls, {"a.mp3": 1, "b.mp3": 2})

    def test_installed_whisper_cli_json_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(whisper, "run_process", side_effect=_run_python_cli_fixture):
            root = Path(tmp)
            binary = root / "whisper-cli"
            binary.write_text(
                "import json,sys\n"
                "if '--help' in sys.argv:\n print('whisper.cpp help 1.2.3')\n raise SystemExit(0)\n"
                "base=sys.argv[sys.argv.index('-of')+1]\n"
                "json.dump(sys.argv,open(base+'.args.json','w'))\n"
                "json.dump({'transcription':[{'offsets':{'from':250,'to':1250},'text':' hello '}]},open(base+'.json','w'))\n",
                encoding="utf-8")
            model = root / "ggml-large-v3.bin"
            model.write_bytes(b"lmgg" + (51865).to_bytes(4, "little") + b"fixture")
            wav = root / "audio.wav"
            wav.write_bytes(b"RIFF" + b"x" * 100)
            with mock.patch.object(whisper, "_local_binary", return_value=binary), \
                    mock.patch.object(whisper, "extract_local_wav", return_value=wav), \
                    mock.patch.object(whisper, "audio_duration", return_value=2.0):
                segments = whisper._transcribe_local_video("video.mp4", root / "audio.mp3", model, "he", root / "cache")
            self.assertEqual(segments, [{"start": 0.25, "end": 1.25, "text": "hello"}])

    def test_local_cli_defaults_to_language_auto_and_fingerprints_binary(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(whisper, "run_process", side_effect=_run_python_cli_fixture):
            root = Path(tmp)
            binary = root / "whisper-cli"
            binary.write_text(
                "import json,sys\n"
                "if '--help' in sys.argv:\n print('bounded help')\n raise SystemExit(0)\n"
                "base=sys.argv[sys.argv.index('-of')+1]\njson.dump(sys.argv,open(base+'.args.json','w'))\n"
                "json.dump({'segments':[{'start':0,'end':1,'text':'ok'}]},open(base+'.json','w'))\n",
                encoding="utf-8")
            model = root / "ggml-large-v3.bin"
            model.write_bytes(b"lmgg" + (51865).to_bytes(4, "little") + b"fixture")
            wav = root / "audio.wav"
            wav.write_bytes(b"RIFF" + b"x" * 100)
            with mock.patch.object(whisper, "_local_binary", return_value=binary), \
                    mock.patch.object(whisper, "extract_local_wav", return_value=wav), \
                    mock.patch.object(whisper, "audio_duration", return_value=2.0):
                whisper._transcribe_local_video("video.mp4", root / "audio.mp3", model, None, root / "cache")
            bases = list(root.glob("whisper-local-*.args.json"))
            args = json.loads(bases[0].read_text(encoding="utf-8"))
            self.assertEqual(args[args.index("-l") + 1], "auto")
            self.assertNotEqual(whisper._binary_version(binary), whisper.canonical_hash([str(binary), "bounded help"]))

    def test_cache_rejects_timestamp_past_encoded_chunk_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            identity = {"engine": "groq", "model": "m", "version": "v", "language": None,
                        "extraction": "mp3", "start": 0.0, "end": 1.0}
            with self.assertRaisesRegex(acquisition.AcquisitionError, "duration"):
                whisper.cached_chunk(audio, Path(tmp) / "cache", identity,
                                     lambda: [{"start": 0.0, "end": 2.0, "text": "too long"}])

    def test_remote_extraction_uses_timed_shared_process_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "audio.mp3"
            def run(command, **kwargs):
                output.write_bytes(b"audio")
                return subprocess.CompletedProcess(command, 0, "", "")
            with mock.patch.object(whisper, "require_tools"), mock.patch.object(whisper, "run_process", side_effect=run) as process:
                whisper.extract_audio("video.mp4", output)
            self.assertEqual(process.call_args.kwargs["timeout"], acquisition.MEDIA_TIMEOUT)

    def test_configured_local_model_rejects_english_only_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-base.en.bin"
            model.write_bytes(b"model")
            with mock.patch.dict(os.environ, {"LOCAL_WHISPER_MODEL": str(model)}, clear=True):
                with self.assertRaisesRegex(acquisition.AcquisitionError, "multilingual"):
                    whisper.configured_local_model()


    def test_malformed_success_preserves_uncertainty_and_prevents_reupload(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            opener = mock.MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = b"not-json"
            with mock.patch.object(whisper, "build_opener", return_value=opener):
                for _ in range(2):
                    with self.assertRaises(acquisition.AcquisitionError) as failure:
                        whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio, work=Path(tmp) / "work")
                    self.assertTrue(failure.exception.uncertain)
            self.assertEqual(opener.open.call_count, 1)

    def test_validated_provider_response_survives_downstream_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"audio")
            opener = mock.MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"segments":[{"start":0,"end":1,"text":"hello"}]}'
            with mock.patch.object(whisper, "build_opener", return_value=opener):
                first = whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio, work=Path(tmp) / "work")
                second = whisper._post_whisper(whisper.GROQ_ENDPOINT, "key", "model", audio, work=Path(tmp) / "work")
            self.assertEqual(first, second)
            self.assertEqual(opener.open.call_count, 1)

    def test_disjoint_repeated_boundary_speech_is_preserved(self):
        first = [{"start": 9, "end": 9.3, "text": "Yes."}]
        later = [{"start": 10, "end": 10.3, "text": "Yes."}]
        merged = whisper.merge_segments(first, later, overlap_start=8, overlap_end=10.5)
        self.assertEqual(len(merged), 2)

    def test_renamed_english_model_is_rejected_by_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "renamed.bin"
            model.write_bytes(b"lmgg" + (51864).to_bytes(4, "little") + b"fixture")
            with self.assertRaisesRegex(acquisition.AcquisitionError, "multilingual"):
                whisper.validate_local_model(model)

    def test_actual_long_audio_is_rejected_before_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "audio.mp3"
            audio.write_bytes(b"audio")
            with mock.patch.object(whisper, "extract_audio", return_value=audio), \
                 mock.patch.object(whisper, "audio_duration", return_value=7201), \
                 mock.patch.object(whisper, "_transcribe_file") as upload:
                with self.assertRaises(acquisition.AcquisitionError) as stopped:
                    whisper.transcribe_video("source", audio, backend="openai", api_key="mock")
                self.assertEqual(stopped.exception.exit_code, 8)
                upload.assert_not_called()

if __name__ == "__main__":
    unittest.main()
