"""The controller loop end to end, with a real ffmpeg and a fake yt-dlp on PATH.

`workflow.py` is driven from a YouTube-shaped URL exactly as an agent drives it
(init → run … → verify) on every operating system the fast CI job covers: the
shim is installed as a native executable, so the production path — a bare
`yt-dlp` resolved from PATH by `safety.ytdlp_command` — is what runs. The shim
log proves every downloader call carried the safety flags and that the Whisper
upload path was never reached.
"""
from __future__ import annotations

import json
import functools
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent / "support"
sys.path.insert(0, str(SUPPORT))

from media_fixtures import (  # noqa: E402
    SCRIPTS,
    CAPTIONS,
    chapters,
    run_script,
    synthesize_fixture_video,
)

sys.path.insert(0, str(SCRIPTS))
from safety import YTDLP_FLAGS  # noqa: E402

import ytdlp_shim  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class WorkflowEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.class_temporary = tempfile.TemporaryDirectory(prefix="vsum-e2e-", ignore_cleanup_errors=True)
        cls.fixtures = Path(cls.class_temporary.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.video = cls.fixtures / "video.mp4"
        synthesize_fixture_video(cls.video)
        (cls.fixtures / "captions.vtt").write_text(CAPTIONS, encoding="utf-8")
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                         functools.partial(Handler, directory=str(cls.fixtures)))
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)
        cls.class_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-e2e-case-", ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"

    def tearDown(self):
        self.temporary.cleanup()

    # --- helpers -----------------------------------------------------------------
    def _env(self, mode: str = "captions") -> dict:
        env = ytdlp_shim.install(self.root / "bin")
        env.update({"VSUM_SHIM_FIXTURE_DIR": str(self.fixtures), "VSUM_SHIM_MODE": mode,
                    "VSUM_SHIM_CAPTION_URL": f"http://127.0.0.1:{self.server.server_port}/captions.vtt",
                    "VSUM_SHIM_DURATION": "12", "VSUM_SHIM_LOG": str(self.root / "shim.log"),
                    "VSUM_LOCAL_MODEL_CONFIG": str(self.root / "unconfigured-local-model.json")})
        env.pop("LOCAL_WHISPER_MODEL", None)
        return env

    def _wf(self, env: dict, *argv):
        return run_script(self.root, sys.executable, SCRIPTS / "workflow.py", *argv, env=env)

    def _shim_calls(self) -> list[list[str]]:
        log = self.root / "shim.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def _select(self, manifest: dict) -> None:
        chosen = manifest["candidates"][0]
        selections = [{"candidate_id": chosen["candidate_id"], "name": "pattern", "chapter_id": chosen["chapter_id"],
                       "role": "evidence", "caption": {"shows": "The moving test pattern.", "why": "shows the state."},
                       "alt": "test pattern", "anchor_seg_ids": [chosen["seg_ids"][0]]}]
        (self.work / "selections.json").write_text(json.dumps(selections), encoding="utf-8")

    def _summarize(self) -> None:
        summary = {"schema_version": 3, "lang": "en", "overview": "A test pattern then black.",
                   "chapters": [{"chapter_id": "ch01", "blocks": [{"text": "The counter shows value 7.",
                                                                   "seg_ids": ["seg_0000", "seg_0001"]}]},
                                {"chapter_id": "ch02", "blocks": [{"text": "Then the screen is black.",
                                                                   "seg_ids": ["seg_0003"]}]}]}
        (self.work / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    # --- the loop ---------------------------------------------------------------------
    def test_workflow_end_to_end_with_shim_ytdlp(self):
        env = self._env()
        url = "https://www.youtube.com/watch?v=fixture"
        if os.name == "nt":
            found = shutil.which("yt-dlp", path=env["PATH"]) or ""
            self.assertTrue(found.lower().endswith("yt-dlp.exe"), found)
        self.assertEqual(self._wf(env, "init", url, "--work", self.work, "--lang", "en").returncode, 0)
        run = json.loads((self.work / "run.json").read_text(encoding="utf-8"))
        ytdlp_row = next(row for row in run["doctor"]["checks"] if row["name"] == "yt-dlp")
        self.assertEqual(ytdlp_row.get("version"), "2026.09.01")  # the doctor saw the same binary the stages use
        first = self._wf(env, "run", "--work", self.work)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("NEXT (chapters", first.stdout)
        transcript = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(transcript["status"], "ok")
        self.assertEqual(transcript["source_detail"]["track"], "en")
        self.assertFalse(transcript["source_detail"]["translated"])
        (self.work / "chapters.json").write_text(json.dumps(chapters(False)), encoding="utf-8")
        second = self._wf(env, "run", "--work", self.work)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("NEXT (shortlist", second.stdout)
        manifest = json.loads((self.work / "candidates.json").read_text(encoding="utf-8"))
        ids = manifest["candidates"][0]["candidate_id"]
        self.assertEqual(self._wf(env, "shortlist", "--work", self.work, "--ids", ids).returncode, 0)
        third = self._wf(env, "run", "--work", self.work)
        self.assertIn("NEXT (selections", third.stdout)
        self._select(manifest)
        fourth = self._wf(env, "run", "--work", self.work)
        self.assertEqual(fourth.returncode, 0, fourth.stderr)
        self.assertIn("NEXT (summary", fourth.stdout)
        self._summarize()
        fifth = self._wf(env, "run", "--work", self.work)
        self.assertEqual(fifth.returncode, 0, fifth.stderr)
        verify = self._wf(env, "verify", "--work", self.work, "--json")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        report = json.loads(verify.stdout)
        self.assertTrue(report["complete"])
        self.assertTrue(Path(report["deliverable"]).is_file())
        # every yt-dlp call carried the safety flags and the Whisper path was never reached
        calls = self._shim_calls()
        self.assertTrue(calls)
        for call in calls:
            if "--version" in call:
                continue
            for flag in YTDLP_FLAGS:
                self.assertIn(flag, call)
            self.assertNotIn("ba/bestaudio", call)
        # the reports survive for a compacted agent
        self.assertTrue((self.work / "reports" / "candidates.md").is_file())

    def test_workflow_source_unavailable_is_exit_13_without_a_retry_loop(self):
        env = self._env("unavailable")
        self._wf(env, "init", "https://www.youtube.com/watch?v=fixture", "--work", self.work, "--lang", "en")
        result = self._wf(env, "run", "--work", self.work)
        self.assertEqual(result.returncode, 13, result.stderr)
        transcript = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(transcript["status"], "source_unavailable")
        self.assertIn("Video unavailable", transcript["source_detail"]["reason"])
        self.assertNotIn("SECRET-TOKEN", (self.work / "transcript.json").read_text(encoding="utf-8"))
        run = json.loads((self.work / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["blocker"]["exit_code"], 13)
        self.assertIn("cookies", run["blocker"]["next"])
        # a second run does not retry on its own, and nothing was uploaded
        self.assertEqual(self._wf(env, "run", "--work", self.work).returncode, 13)
        calls = [call for call in self._shim_calls() if "--version" not in call]
        self.assertEqual(len(calls), 1)
        verify = self._wf(env, "verify", "--work", self.work, "--json")
        self.assertEqual(verify.returncode, 12)
        source = next(row for row in json.loads(verify.stdout)["rows"] if row["check"] == "source")
        self.assertEqual(source["status"], "FAIL")

    def test_workflow_no_visuals_decision_is_probed_and_reaches_verify(self):
        env = self._env()
        url = "https://www.youtube.com/watch?v=fixture"
        self.assertEqual(self._wf(env, "init", url, "--work", self.work, "--lang", "en").returncode, 0)
        self.assertEqual(self._wf(env, "run", "--work", self.work).returncode, 0)
        rows = [dict(c, needs_frames=False, visual_targets=[]) for c in chapters()]
        (self.work / "chapters.json").write_text(json.dumps(rows), encoding="utf-8")
        self.assertEqual(self._wf(env, "run", "--work", self.work).returncode, 10)   # illustrated intent
        decide = self._wf(env, "decide", "no-visuals", "--work", self.work, "--reason",
                          "a moving test pattern and a black screen show nothing informative")
        self.assertEqual(decide.returncode, 0, decide.stderr)
        result = self._wf(env, "run", "--work", self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NEXT (summary", result.stdout)
        run = json.loads((self.work / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["visual_probe"]["verdict"], "supports")
        self.assertTrue(any("-f" in call for call in self._shim_calls()))   # the video was fetched for the probe
        self._summarize()
        self.assertEqual(self._wf(env, "run", "--work", self.work).returncode, 0)
        verify = self._wf(env, "verify", "--work", self.work, "--json")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        report = json.loads(verify.stdout)
        self.assertIn("probe: supports", report["rows"][0]["evidence"])
        self.assertEqual(report["rows"][0]["warnings"], [])

    def test_workflow_no_captions_stops_with_exit_6_and_blocker(self):
        env = self._env("no-captions")
        self._wf(env, "init", "https://www.youtube.com/watch?v=fixture", "--work", self.work, "--lang", "en")
        result = self._wf(env, "run", "--work", self.work)
        self.assertEqual(result.returncode, 6, result.stderr)
        run = json.loads((self.work / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["blocker"]["stage"], "transcript")
        self.assertEqual(run["blocker"]["exit_code"], 6)
        transcript = json.loads((self.work / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(transcript["status"], "no_transcript")
        self.assertIn("not authorized", transcript["source_detail"]["reason"])
        verify = self._wf(env, "verify", "--work", self.work)
        self.assertEqual(verify.returncode, 12)


if __name__ == "__main__":
    unittest.main()
