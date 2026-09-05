"""Reliability regressions at the CLI boundary, with real ffmpeg.

Every case here reproduced a silent success on 1.6.0: missing or empty
transcript, an empty or all-talk chapters.json, a chapter that needs frames
but yields none, unknown segment references, a swapped download, stale
bindings, and a zero-frame "illustrated" render. The stage scripts are
invoked directly (the way the benchmark and a bypassing agent would call
them) so the gates are proven at the script boundary, not only through the
controller — the controller loop itself lives in `test_workflow_e2e.py`.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent / "support"
sys.path.insert(0, str(SUPPORT))

from media_fixtures import (  # noqa: E402
    SCRIPTS,
    chapters,
    run_script,
    synthesize_fixture_video,
    transcript_payload,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class ReliabilityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.class_temporary = tempfile.TemporaryDirectory(prefix="vsum-rel-", ignore_cleanup_errors=True)
        cls.fixtures = Path(cls.class_temporary.name) / "fixtures"
        cls.fixtures.mkdir()
        cls.video = cls.fixtures / "video.mp4"
        synthesize_fixture_video(cls.video)

    @classmethod
    def tearDownClass(cls):
        cls.class_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-rel-case-", ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self, *arguments, env: dict | None = None):
        return run_script(self.root, *arguments, env=env)

    def _work(self, name: str, *, transcript: dict | None = transcript_payload(), chapter_rows=None) -> Path:
        work = self.root / name
        work.mkdir()
        if transcript is not None:
            (work / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        if chapter_rows is not None:
            (work / "chapters.json").write_text(json.dumps(chapter_rows), encoding="utf-8")
        return work

    def _candidates(self, work: Path, *extra):
        return self._run(sys.executable, SCRIPTS / "candidates.py", self.video, "--work", work,
                         "--chapters", work / "chapters.json", *extra)

    # --- transcript boundary ---------------------------------------------------
    def test_missing_transcript_is_exit_10_and_nothing_is_downloaded(self):
        work = self._work("missing", transcript=None, chapter_rows=chapters(False))
        result = self._candidates(work)
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("transcript.json not found", result.stderr)
        self.assertFalse((work / "download").exists())
        self.assertFalse((work / "candidates.json").exists())

    def test_empty_and_failed_transcripts_are_exit_10(self):
        empty = dict(transcript_payload(), segments=[])
        failed = dict(transcript_payload(), status="no_transcript", segments=[])
        for label, payload in (("empty", empty), ("failed", failed)):
            with self.subTest(label=label):
                work = self._work(label, transcript=payload, chapter_rows=chapters(False))
                result = self._candidates(work)
                self.assertEqual(result.returncode, 10, result.stderr)
                self.assertFalse((work / "download").exists())

    # --- chapter boundary --------------------------------------------------------
    def test_empty_chapters_array_is_exit_10_without_download(self):
        work = self._work("emptych", chapter_rows=[])
        result = self._candidates(work)
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertFalse((work / "download").exists())

    def test_dangling_target_seg_ids_are_exit_10_naming_the_target(self):
        rows = chapters(False)
        rows[0]["visual_targets"][0]["seg_ids"] = ["seg_9999"]
        work = self._work("dangling", chapter_rows=rows)
        result = self._candidates(work)
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("t_pattern", result.stderr)
        self.assertIn("seg_9999", result.stderr)

    def test_all_false_needs_decision_and_writes_full_shape_manifest(self):
        rows = [dict(c, needs_frames=False, visual_targets=[]) for c in chapters()]
        work = self._work("allfalse", chapter_rows=rows)
        result = self._candidates(work)
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("no-visuals", result.stderr)
        self.assertFalse((work / "candidates.json").exists())
        decided = self._candidates(work, "--visual-content", "none")
        self.assertEqual(decided.returncode, 0, decided.stderr)
        self.assertIn("no_visual_chapters", decided.stdout)
        manifest = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "no_visual_chapters")
        for key in ("cost", "token_budget", "sheets", "coverage", "inputs", "profile_sha256", "counts"):
            self.assertIn(key, manifest)
        self.assertFalse((work / "download").exists())

    def test_needs_frames_chapter_without_a_frame_is_exit_9(self):
        work = self._work("black", chapter_rows=chapters(True))
        result = self._candidates(work)
        self.assertEqual(result.returncode, 9, result.stderr)
        manifest = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "unresolved")
        self.assertIn("ch02", result.stderr)
        allowed = self._candidates(work, "--allow-unresolved")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    # --- provenance, triage receipt, bindings ---------------------------------------
    def _extract(self, name: str = "happy") -> tuple[Path, dict]:
        work = self._work(name, chapter_rows=chapters(False))
        result = self._candidates(work)
        self.assertEqual(result.returncode, 0, result.stderr)
        return work, json.loads((work / "candidates.json").read_text(encoding="utf-8"))

    def _select(self, work: Path, manifest: dict) -> Path:
        chosen = manifest["candidates"][0]
        selections = [{"candidate_id": chosen["candidate_id"], "name": "pattern", "chapter_id": chosen["chapter_id"],
                       "role": "evidence", "caption": {"shows": "The moving test pattern.", "why": "shows the state."},
                       "alt": "test pattern", "anchor_seg_ids": [chosen["seg_ids"][0]]}]
        (work / "selections.json").write_text(json.dumps(selections), encoding="utf-8")
        return work / "selections.json"

    def _summarize(self, work: Path) -> Path:
        summary = {"schema_version": 3, "lang": "en", "overview": "A test pattern then black.",
                   "chapters": [{"chapter_id": "ch01", "blocks": [{"text": "The counter shows value 7.",
                                                                   "seg_ids": ["seg_0000", "seg_0001"]}]},
                                {"chapter_id": "ch02", "blocks": [{"text": "Then the screen is black.",
                                                                   "seg_ids": ["seg_0003"]}]}]}
        (work / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return work / "summary.json"

    def test_manifest_records_inputs_and_shortlist_receipt(self):
        work, manifest = self._extract()
        self.assertEqual(manifest["status"], "ok")
        inputs = manifest["inputs"]
        for key in ("transcript_sha256", "chapters_sha256", "cache_key", "video_id", "generated_at", "source_identity"):
            self.assertTrue(inputs.get(key), key)
        ids = ",".join(c["candidate_id"] for c in manifest["candidates"][:2])
        result = self._run(sys.executable, SCRIPTS / "shortlist.py", "--work", work, "--ids", ids)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((work / "candidates.json").read_text(encoding="utf-8"))["shortlist"]
        self.assertEqual(len(receipt["written"]), len(ids.split(",")))
        self.assertTrue(all(row["sha256"] for row in receipt["written"]))
        unknown = self._run(sys.executable, SCRIPTS / "shortlist.py", "--work", work, "--ids", "c_9999")
        self.assertEqual(unknown.returncode, 10, unknown.stderr)

    def test_grab_refuses_a_swapped_download_cache_exit_11(self):
        work, manifest = self._extract("swap")
        spec = self._select(work, manifest)
        parts = json.loads((work / "download" / "parts.json").read_text(encoding="utf-8"))
        parts["cache_key"] = "0" * 64
        (work / "download" / "parts.json").write_text(json.dumps(parts), encoding="utf-8")
        result = self._run(sys.executable, SCRIPTS / "grab.py", "--work", work, "--spec", spec,
                           "--out-dir", self.root / "out-swap" / "assets")
        self.assertEqual(result.returncode, 11, result.stderr)

    def test_render_bindings_zero_frames_and_text_only(self):
        work, manifest = self._extract("render")
        spec = self._select(work, manifest)
        self._summarize(work)
        out = self.root / "summary-fixture"
        grab = self._run(sys.executable, SCRIPTS / "grab.py", "--work", work, "--spec", spec, "--out-dir", out / "assets")
        self.assertEqual(grab.returncode, 0, grab.stderr)
        assets_manifest = json.loads((out / "assets" / "assets-manifest.json").read_text(encoding="utf-8"))
        for key in ("selections_sha256", "selections_binding_sha256", "candidates_sha256", "cache_key", "video_id"):
            self.assertTrue(assets_manifest.get(key), key)
        render = [sys.executable, SCRIPTS / "render.py", "--work", work, "--summary", work / "summary.json",
                  "--selections", spec, "--assets-dir", out / "assets", "--out-dir", out, "--lang", "en"]
        result = self._run(*render)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        for key in ("transcript_sha256", "chapters_sha256", "candidates_sha256", "assets_manifest_sha256",
                    "output_mode", "frames_count", "generated_at"):
            self.assertIn(key, rendered)
        self.assertEqual(rendered["frames_count"], 1)
        # re-render into the same directory is a resume, not a refusal
        self.assertEqual(self._run(*render).returncode, 0)
        # a foreign transcript can no longer be rendered against this pool
        payload = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
        payload["video"]["title"] = "Other video"
        (work / "transcript.json").write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self._run(*render).returncode, 11)
        (work / "transcript.json").write_text(json.dumps(transcript_payload()), encoding="utf-8")
        # zero selections under illustrated intent is exit 10
        (work / "empty.json").write_text("[]", encoding="utf-8")
        empty = self._run(sys.executable, SCRIPTS / "render.py", "--work", work, "--summary", work / "summary.json",
                          "--selections", work / "empty.json", "--assets-dir", out / "assets",
                          "--out-dir", self.root / "summary-empty", "--lang", "en")
        self.assertEqual(empty.returncode, 10, empty.stderr)
        # an explicit text-only delivery renders without assets and says so
        text = self._run(sys.executable, SCRIPTS / "render.py", "--work", work, "--summary", work / "summary.json",
                         "--out-dir", self.root / "summary-text", "--lang", "en", "--output-mode", "text-only")
        self.assertEqual(text.returncode, 0, text.stderr)
        text_manifest = json.loads((self.root / "summary-text" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(text_manifest["output_mode"], "text-only")
        self.assertEqual(text_manifest["frames_count"], 0)


if __name__ == "__main__":
    unittest.main()
