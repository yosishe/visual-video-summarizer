from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acquisition  # noqa: E402
import doctor  # noqa: E402
import transcript  # noqa: E402
import whisper  # noqa: E402
import workflow  # noqa: E402


def model_file(root: Path, name: str = "ggml-large-v3-turbo.bin") -> Path:
    path = root / name
    path.write_bytes(b"lmgg" + (51865).to_bytes(4, "little") + b"fixture")
    return path


class LocalModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-model-config-")
        self.root = Path(self.temporary.name)
        self.config = self.root / "config" / "local-model.json"
        self.env = mock.patch.dict(os.environ, {"VSUM_LOCAL_MODEL_CONFIG": str(self.config)}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def test_configure_then_fresh_default_init_binds_registered_model(self):
        model = model_file(self.root)
        with mock.patch.object(whisper, "_local_binary", return_value=self.root / "whisper-cli"), \
                mock.patch("doctor.check", return_value={"ready": True, "checks": []}):
            self.assertEqual(workflow.main(["configure-local", "--local-model", str(model)]), 0)
            work = self.root / "work"
            self.assertEqual(workflow.main(["init", "https://www.youtube.com/watch?v=vid", "--work", str(work)]), 0)

        run = json.loads((work / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["request"]["whisper"], "local")
        self.assertEqual(run["request"]["local_model"], str(model.resolve()))
        self.assertEqual(run["request"]["local_model_source"], "registered")
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["model_path"], str(model.resolve()))

    def test_disabled_and_cloud_init_do_not_read_registered_config(self):
        with mock.patch.object(whisper, "resolve_local_model", side_effect=AssertionError("registry read")), \
                mock.patch("doctor.check", return_value={"ready": True, "checks": []}):
            for index, options in enumerate((["--no-whisper"], ["--whisper", "groq"])):
                work = self.root / f"work-{index}"
                self.assertEqual(workflow.main(["init", "https://www.youtube.com/watch?v=vid",
                                                "--work", str(work), *options]), 0)
                request = json.loads((work / "run.json").read_text(encoding="utf-8"))["request"]
                self.assertIsNone(request["local_model"])
                self.assertNotEqual(request["whisper"], "local")

    def test_explicit_then_environment_then_registry_precedence(self):
        registered = model_file(self.root, "ggml-registered.bin")
        environment = model_file(self.root, "ggml-environment.bin")
        explicit = model_file(self.root, "ggml-explicit.bin")
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"schema_version": 1, "model_path": str(registered)}), encoding="utf-8")

        with mock.patch.dict(os.environ, {"LOCAL_WHISPER_MODEL": str(environment)}, clear=False):
            self.assertEqual(whisper.resolve_local_model(explicit)[0], explicit.resolve())
            self.assertEqual(whisper.resolve_local_model()[0], environment.resolve())
        self.assertEqual(whisper.resolve_local_model()[0], registered.resolve())

    def test_invalid_registry_and_symlink_are_typed_failures(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("{interrupted", encoding="utf-8")
        with self.assertRaises(acquisition.AcquisitionError) as malformed:
            whisper.resolve_local_model()
        self.assertEqual(malformed.exception.category, "invalid_input")
        self.config.unlink()
        target = self.root / "target.json"
        target.write_text("{}", encoding="utf-8")
        self.config.symlink_to(target)
        with self.assertRaisesRegex(acquisition.AcquisitionError, "symlink"):
            whisper.resolve_local_model()

    def test_invalid_model_symlink_does_not_fall_back(self):
        target = model_file(self.root)
        linked = self.root / "ggml-linked.bin"
        linked.symlink_to(target)
        with mock.patch.dict(os.environ, {"LOCAL_WHISPER_MODEL": str(linked)}, clear=False), \
                self.assertRaises(acquisition.AcquisitionError) as failure:
            whisper.resolve_local_model()
        self.assertEqual(failure.exception.category, "invalid_input")
        self.assertIn("symlink", failure.exception.message)

    def test_registry_parent_symlink_is_rejected_without_creating_config_or_lock(self):
        actual = self.root / "actual"
        actual.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(actual, target_is_directory=True)
        config = linked / "local-model.json"
        model = model_file(self.root)
        with mock.patch.dict(os.environ, {"VSUM_LOCAL_MODEL_CONFIG": str(config)}, clear=False), \
                mock.patch.object(whisper, "_local_binary", return_value=self.root / "whisper-cli"), \
                self.assertRaises(acquisition.AcquisitionError) as failure:
            whisper.register_local_model(model)
        self.assertEqual(failure.exception.category, "invalid_input")
        self.assertIn("symlink", failure.exception.message)
        self.assertFalse((actual / "local-model.json").exists())
        self.assertFalse((actual / "local-model.json.lock").exists())
        (actual / "local-model.json").write_text(
            json.dumps({"schema_version": 1, "model_path": str(model)}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"VSUM_LOCAL_MODEL_CONFIG": str(config)}, clear=False), \
                self.assertRaises(acquisition.AcquisitionError) as read_failure:
            whisper.registered_local_model_path()
        self.assertEqual(read_failure.exception.category, "invalid_input")

    def test_interrupted_write_preserves_registry_and_busy_writer_stops(self):
        old_model = model_file(self.root, "ggml-old.bin")
        new_model = model_file(self.root, "ggml-new.bin")
        self.config.parent.mkdir(parents=True)
        original = json.dumps({"schema_version": 1, "model_path": str(old_model)})
        self.config.write_text(original, encoding="utf-8")
        common = mock.patch.multiple(whisper, _local_binary=mock.DEFAULT)
        with common as patched:
            patched["_local_binary"].return_value = self.root / "whisper-cli"
            with mock.patch.object(whisper, "atomic_write", side_effect=OSError("interrupted")), \
                    self.assertRaises(acquisition.AcquisitionError) as interrupted:
                whisper.register_local_model(new_model)
            self.assertEqual(interrupted.exception.category, "dependency")
            self.assertEqual(self.config.read_text(encoding="utf-8"), original)
            lock = self.config.with_suffix(self.config.suffix + ".lock")
            with acquisition.file_lock(lock), self.assertRaises(acquisition.AcquisitionError) as busy:
                whisper.register_local_model(new_model)
            self.assertEqual(busy.exception.category, "busy")

    def test_cloud_and_disabled_doctor_do_not_inspect_registry(self):
        with mock.patch.object(whisper, "local_model_candidate", side_effect=AssertionError("registry read")):
            for backend, disabled in (("groq", False), (None, True)):
                result = doctor.check(whisper_backend=backend, no_whisper=disabled)
                local = next(row for row in result["checks"] if row["name"] == "local transcription")
                self.assertEqual(local["model_status"], "not_inspected")

    def test_doctor_reports_unconfigured_without_reading_key_file(self):
        with mock.patch.object(whisper, "load_api_key", side_effect=AssertionError("credential read")), \
                mock.patch.object(doctor.shutil, "which", return_value=None):
            result = doctor.check()
        local = next(row for row in result["checks"] if row["name"] == "local transcription")
        self.assertEqual(local["model_source"], "unconfigured")
        self.assertEqual(local["model_status"], "unconfigured")

    def test_doctor_reports_missing_registered_path_without_claiming_no_model_exists(self):
        missing = self.root / "ggml-moved.bin"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"schema_version": 1, "model_path": str(missing)}), encoding="utf-8")
        result = doctor.check()
        local = next(row for row in result["checks"] if row["name"] == "local transcription")
        self.assertEqual(local["model_source"], "registered")
        self.assertEqual(local["model_path"], str(missing))
        self.assertEqual(local["model_status"], "missing")

    def test_missing_selected_model_fails_only_when_local_transcription_runs(self):
        missing = self.root / "ggml-missing.bin"
        with self.assertRaises(acquisition.AcquisitionError) as failure:
            whisper.transcribe_video("video.mp4", self.root / "audio.mp3", backend="local",
                                     local_model=missing)
        self.assertEqual(failure.exception.category, "dependency")

    def test_workflow_binding_matches_caption_transcript_with_symlink_leaf(self):
        target = model_file(self.root)
        linked = self.root / "ggml-linked.bin"
        linked.symlink_to(target)
        work = self.root / "work"
        source = "https://www.youtube.com/watch?v=vid123"
        with mock.patch("doctor.check", return_value={"ready": True, "checks": []}):
            self.assertEqual(workflow.main(["init", source, "--work", str(work), "--lang", "en",
                                            "--whisper", "local", "--local-model", str(linked)]), 0)

        info = {"id": "vid123", "title": "Fixture", "duration": 4, "language": "en",
                "subtitles": {"en": [{"ext": "vtt", "url": "https://example.invalid/en"}]},
                "automatic_captions": {}}
        vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:04.000\ncaption words\n"

        def fake_ytdlp(args):
            out_dir = Path(args[args.index("-o") + 1]).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            if "--write-info-json" in args:
                (out_dir / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
            elif "--write-subs" in args:
                (out_dir / "video.en.vtt").write_bytes(vtt)
            return 0

        run = workflow.load_run(work)
        bound = run["request"]["local_model"]
        with mock.patch.object(sys, "argv", ["transcript.py", source, "--work", str(work),
                                              "--whisper", "local", "--local-model", bound]), \
                mock.patch.object(transcript, "_run_ytdlp", side_effect=fake_ytdlp), \
                mock.patch.object(transcript, "_fetch_caption_url", return_value=vtt), \
                mock.patch.object(transcript, "transcribe_video") as inference:
            self.assertEqual(transcript.main(), 0)
        inference.assert_not_called()
        payload = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["inputs"]["local_model"], bound)
        stages = workflow.assess(work, workflow.load_run(work))
        self.assertEqual(stages["transcript"].status, "ok")


if __name__ == "__main__":
    unittest.main()
