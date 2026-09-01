from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-test-")
        self.root = Path(self.temporary.name)
        self.video = self.root / "fixture.mp4"
        self._make_video()
        self.transcript = {
            "source": "fixture",
            "video": {
                "id": "fixture", "title": "Synthetic UI transition", "uploader": "tests",
                "url": str(self.video), "duration": 6.0, "is_url": False,
            },
            "segments": [
                {"seg_id": "seg_0000", "start": 0.0, "end": 2.4, "text": "Introduction."},
                {"seg_id": "seg_0001", "start": 2.4, "end": 3.0, "text": "Now I click to show the result."},
                {"seg_id": "seg_0002", "start": 3.0, "end": 5.8, "text": "The completed state is visible."},
            ],
        }
        self.chapters = [
            {"chapter_id": "ch01", "title": "Introduction", "start": 0.0, "end": 3.0, "needs_frames": False},
            {
                "chapter_id": "ch02", "title": "Result", "start": 3.0, "end": 6.0,
                "needs_frames": True,
                "visual_targets": [{
                    "target_id": "vt_result", "kind": "action_result",
                    "seg_ids": ["seg_0001", "seg_0002"], "why": "post-click result",
                }],
            },
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_video(self) -> None:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=white:s=640x360:r=10:d=2",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=10:d=1.5",
            "-f", "lavfi", "-i", "color=c=white:s=640x360:r=10:d=2.5",
            "-filter_complex",
            "[0:v]drawbox=x=80:y=80:w=160:h=100:color=blue:t=fill[a];"
            "[2:v]drawbox=x=300:y=120:w=240:h=140:color=red:t=fill[c];"
            "[a][1:v][c]concat=n=3:v=1:a=0,fps=10[out]",
            "-map", "[out]", "-c:v", "mpeg4", "-q:v", "2", str(self.video),
        ]
        subprocess.run(command, check=True)

    def _prepare_work(self, name: str) -> Path:
        work = self.root / name
        work.mkdir()
        (work / "transcript.json").write_text(json.dumps(self.transcript), encoding="utf-8")
        (work / "chapters.json").write_text(json.dumps(self.chapters), encoding="utf-8")
        return work

    def _run(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = str(self.root / "pycache")
        return subprocess.run(
            [str(argument) for argument in arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_light_pipeline_grab_and_renderer(self) -> None:
        work = self._prepare_work("light")
        candidates_run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json", "--mode", "light",
        )
        self.assertEqual(candidates_run.returncode, 0, candidates_run.stderr)
        candidate_payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        self.assertLessEqual(candidate_payload["counts"]["final"], 36)
        self.assertEqual(candidate_payload["coverage"]["chapters"][0]["status"], "not-required")
        self.assertEqual(candidate_payload["coverage"]["chapters"][1]["status"], "covered")
        self.assertEqual(candidate_payload["coverage"]["targets"][0]["status"], "covered")
        target_candidates = [
            candidate for candidate in candidate_payload["candidates"]
            if "vt_result" in candidate["target_ids"]
        ]
        self.assertTrue(target_candidates)
        self.assertTrue(all(3.0 <= candidate["actual_t"] < 6.0 for candidate in target_candidates))
        self.assertTrue(all(not candidate["quality"]["blank"] for candidate in target_candidates))
        self.assertTrue(any("recovered" in candidate["reasons"] for candidate in target_candidates))
        chosen = max(target_candidates, key=lambda candidate: candidate["actual_t"])

        selections = [{
            "candidate_id": chosen["candidate_id"], "name": "ch02_result", "chapter_id": "ch02",
            "role": "evidence", "caption": "The red result panel is visible after the click.",
            "alt": "Red result panel on a white screen", "anchor_seg_ids": ["seg_0002"],
        }]
        selection_path = work / "selections.json"
        selection_path.write_text(json.dumps(selections), encoding="utf-8")
        summary_dir = self.root / "summary-fixture"
        assets_dir = summary_dir / "assets"
        grab_run = self._run(
            sys.executable, SCRIPTS / "grab.py", "--work", work,
            "--spec", selection_path, "--out-dir", assets_dir,
        )
        self.assertEqual(grab_run.returncode, 0, grab_run.stderr + grab_run.stdout)

        summary = {
            "overview": "A synthetic example showing an action followed by a stable visual result.",
            "chapters": [
                {"chapter_id": "ch01", "title": "Introduction", "blocks": [
                    {"text": "The fixture opens with an introductory state.", "seg_ids": ["seg_0000"]}
                ]},
                {"chapter_id": "ch02", "title": "Result", "blocks": [
                    {"text": "After the click, the completed result replaces the transition.", "seg_ids": ["seg_0001", "seg_0002"]}
                ]},
            ],
        }
        summary_path = work / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        render_run = self._run(
            sys.executable, SCRIPTS / "render.py", "--work", work,
            "--summary", summary_path, "--selections", selection_path,
            "--assets-dir", assets_dir, "--out-dir", summary_dir,
        )
        self.assertEqual(render_run.returncode, 0, render_run.stderr + render_run.stdout)
        manifest = json.loads((summary_dir / "manifest.json").read_text(encoding="utf-8"))
        html_text = (summary_dir / "index.html").read_text(encoding="utf-8")
        self.assertEqual(manifest["frames"][0]["candidate_id"], chosen["candidate_id"])
        self.assertIn(f'data-candidate-id="{chosen["candidate_id"]}"', html_text)
        self.assertIn("assets/ch02_result-thumb.jpg", html_text)

    def test_advanced_mode_uses_adaptive_windowed_engine(self) -> None:
        work = self._prepare_work("advanced")
        run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json", "--mode", "advanced",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "advanced")
        self.assertLessEqual(payload["counts"]["final"], 60)
        self.assertEqual(payload["coverage"]["targets"][0]["status"], "covered")


if __name__ == "__main__":
    unittest.main()
