from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import grab  # noqa: E402
import render as renderer  # noqa: E402
from candidates import PROFILES  # noqa: E402


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

    def _make_video(self, blur: int | None = None) -> None:
        """White+blue box (0–2 s), black (2–3.5 s), red result panel (3.5–6 s).

        With `blur`, the panel is boxblurred for its first 0.6 s and sharp for
        the remaining 1.9 s — the same picture at the 64×36 signature scale,
        measurably blurrier for blurdetect — to exercise grab-time refinement.
        """
        panel = (
            "drawbox=x=300:y=120:w=240:h=140:color=red:t=fill,"
            "drawbox=x=60:y=40:w=200:h=30:color=black:t=fill"
        )
        if blur is None:
            inputs = [
                "color=c=white:s=640x360:r=10:d=2",
                "color=c=black:s=640x360:r=10:d=1.5",
                "color=c=white:s=640x360:r=10:d=2.5",
            ]
            graph = (
                "[0:v]drawbox=x=80:y=80:w=160:h=100:color=blue:t=fill[a];"
                f"[2:v]{panel}[c];"
                "[a][1:v][c]concat=n=3:v=1:a=0,fps=10[out]"
            )
        else:
            inputs = [
                "color=c=white:s=640x360:r=10:d=2",
                "color=c=black:s=640x360:r=10:d=1.5",
                "color=c=white:s=640x360:r=10:d=0.6",
                "color=c=white:s=640x360:r=10:d=1.9",
            ]
            graph = (
                "[0:v]drawbox=x=80:y=80:w=160:h=100:color=blue:t=fill[a];"
                f"[2:v]{panel},boxblur={blur}[b];"
                f"[3:v]{panel}[c];"
                "[a][1:v][b][c]concat=n=4:v=1:a=0,fps=10[out]"
            )
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for source in inputs:
            command += ["-f", "lavfi", "-i", source]
        command += [
            "-filter_complex", graph, "-map", "[out]",
            "-c:v", "mpeg4", "-q:v", "2", str(self.video),
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
        # Hermetic: the fixtures are English; the user's ~/.config SUMMARY_LANG must not leak in.
        environment["SUMMARY_LANG"] = "en"
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
        self.assertEqual(manifest["tier"], "standard")
        self.assertIsNone(manifest["frames"][0]["refinement"])
        self.assertEqual(manifest["frames"][0]["triaged_t"], manifest["frames"][0]["actual_t"])

        # Adding an opening synthesis must not steal the image's prose anchor
        # or alter any existing chapter, selection, timestamp, or asset hash.
        self.assertNotIn("brief", manifest)
        summary["brief"] = {
            "synthesis": {"text": "A click reveals the completed result.", "seg_ids": ["seg_0001", "seg_0002"]},
            "main_points": [{"text": "The completed state is visible.", "seg_ids": ["seg_0002"]}],
            "takeaways": [{"text": "The result follows the click.", "seg_ids": ["seg_0001", "seg_0002"]}],
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        rerender = self._run(
            sys.executable, SCRIPTS / "render.py", "--work", work,
            "--summary", summary_path, "--selections", selection_path,
            "--assets-dir", assets_dir, "--out-dir", summary_dir,
        )
        self.assertEqual(rerender.returncode, 0, rerender.stderr + rerender.stdout)
        updated = json.loads((summary_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["brief"], summary["brief"])
        for key in ("chapters", "frames", "selections_sha256", "tier", "engine_version"):
            self.assertEqual(updated[key], manifest[key], key)
        self.assertEqual(updated["audit"]["stats"], manifest["audit"]["stats"])
        self.assertNotEqual(updated["summary_sha256"], manifest["summary_sha256"])
        updated_html = (summary_dir / "index.html").read_text(encoding="utf-8")
        chapter_html = lambda h: re.findall(r'<section class="chapter".*?</section>', h, re.S)
        self.assertEqual(chapter_html(updated_html), chapter_html(html_text))
        bundled = summary_dir.with_suffix(".html").read_text(encoding="utf-8")
        self.assertIn('class="brief"', bundled)
        self.assertIn('data:image/jpeg;base64,', bundled)

        # The actual CLI must stop on a new brief error, just like chapter errors.
        summary["brief"]["synthesis"]["text"] = "The result takes 999 seconds."
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        rejected = self._run(
            sys.executable, SCRIPTS / "render.py", "--work", work,
            "--summary", summary_path, "--selections", selection_path,
            "--assets-dir", assets_dir, "--out-dir", summary_dir,
        )
        self.assertEqual(rejected.returncode, 5, rejected.stderr)
        self.assertIn("brief/synthesis", rejected.stderr)
        self.assertEqual((summary_dir / "index.html").read_text(encoding="utf-8"), updated_html)

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
        self.assertEqual(payload["tier"], "high")
        self.assertEqual(payload["profile"]["refine"], "sharpness")
        self.assertLessEqual(payload["counts"]["final"], PROFILES["high"]["cap"])
        self.assertEqual(payload["coverage"]["targets"][0]["status"], "covered")
        self.assertIn("image_tokens_estimate", payload["cost"])
        self.assertIn("Tier:** high", run.stdout)

    def test_standard_tier_records_cost_and_profile(self) -> None:
        work = self._prepare_work("standard")
        run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json", "--tier", "standard", "--max-candidates", "4",
            "--profile-override", '{"coverage_min": 1}',
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["tier"], "standard")
        self.assertEqual(payload["mode"], "light")
        self.assertLessEqual(payload["counts"]["final"], 4)  # a hard ceiling now
        self.assertEqual(payload["cost"]["cpu"]["refine"], "none")
        self.assertEqual(payload["profile_override"], {"coverage_min": 1})
        self.assertEqual(len(payload["profile_sha256"]), 64)
        dropped = json.loads((work / "dropped.json").read_text(encoding="utf-8"))
        reasons = [row["reason"] for row in dropped]
        self.assertTrue(set(reasons) <= {"blank", "dedup", "cap"}, reasons)
        # dedup and cap drops map one-to-one onto the counts; blank rows also
        # include the recovery retries, so they are only bounded below.
        self.assertEqual(reasons.count("dedup"), payload["counts"]["dedup_dropped"])
        self.assertEqual(reasons.count("cap"), payload["counts"]["cap_dropped"])
        self.assertGreaterEqual(reasons.count("blank"), 0)
        self.assertTrue(all(row["kept_by_t"] is not None for row in dropped if row["reason"] == "dedup"))
        for candidate in payload["candidates"]:
            self.assertNotIn("text_chars", candidate["quality"])
            self.assertEqual((candidate["width"], candidate["height"]), (512, 288))

    def _blurred_candidate(self, work: Path) -> dict:
        """Run the high tier without dedup on the blur fixture and return the
        earliest non-blank ch02 candidate — a frame from the blurred stretch."""
        run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json", "--tier", "high", "--no-dedup",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        blurred = [
            candidate for candidate in payload["candidates"]
            if candidate["chapter_id"] == "ch02" and 3.5 <= candidate["actual_t"] < 4.1
            and not candidate["quality"]["blank"]
        ]
        self.assertTrue(blurred, [c["actual_t"] for c in payload["candidates"]])
        return min(blurred, key=lambda candidate: candidate["actual_t"])

    def _selection(self, chosen: dict, work: Path) -> Path:
        selections = [{
            "candidate_id": chosen["candidate_id"], "name": "ch02_result", "chapter_id": "ch02",
            "role": "evidence", "caption": "The red result panel is visible after the click.",
            "alt": "Red result panel on a white screen", "anchor_seg_ids": ["seg_0002"],
        }]
        path = work / "selections.json"
        path.write_text(json.dumps(selections), encoding="utf-8")
        return path

    def test_high_tier_refines_within_chapter_and_records_triaged_t(self) -> None:
        self._make_video(blur=3)
        work = self._prepare_work("high")
        chosen = self._blurred_candidate(work)
        selection_path = self._selection(chosen, work)
        summary_dir = self.root / "summary-fixture"
        assets_dir = summary_dir / "assets"
        grab_run = self._run(
            sys.executable, SCRIPTS / "grab.py", "--work", work,
            "--spec", selection_path, "--out-dir", assets_dir,
        )
        self.assertEqual(grab_run.returncode, 0, grab_run.stderr + grab_run.stdout)
        assets = json.loads((assets_dir / "assets-manifest.json").read_text(encoding="utf-8"))
        asset = assets["assets"][0]
        self.assertEqual(assets["refine"], "sharpness")
        self.assertTrue(asset["refinement"]["applied"], asset["refinement"])
        self.assertEqual(asset["triaged_t"], chosen["actual_t"])
        self.assertGreater(asset["actual_t"], asset["triaged_t"])
        self.assertGreaterEqual(asset["actual_t"], 4.1)  # inside the sharp stretch
        self.assertLess(asset["actual_t"], 6.0)
        self.assertLess(asset["refinement"]["blur_after"], asset["refinement"]["blur_before"] * 0.9)
        self.assertIn("refined +", grab_run.stdout)

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
        frame = manifest["frames"][0]
        self.assertEqual(manifest["tier"], "high")
        self.assertEqual(frame["actual_t"], asset["actual_t"])
        self.assertEqual(frame["triaged_t"], chosen["actual_t"])
        self.assertTrue(frame["refinement"]["applied"])
        html_text = (summary_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'data-time="{asset["actual_t"]:.3f}"', html_text)

    def test_refinement_falls_back_when_gate_fails(self) -> None:
        self._make_video(blur=3)
        work = self._prepare_work("fallback")
        chosen = self._blurred_candidate(work)
        selection_path = self._selection(chosen, work)
        assets_dir = self.root / "summary-fallback" / "assets"
        argv = ["grab", "--work", str(work), "--spec", str(selection_path), "--out-dir", str(assets_dir)]
        # First gate (the triaged frame) passes; the post-seek gate on the
        # refined frame fails → the baseline frame is written, run still green.
        with mock.patch.object(grab, "is_near_duplicate", side_effect=[True, False]), \
                mock.patch.object(sys, "argv", argv):
            self.assertEqual(grab.main(), 0)
        asset = json.loads((assets_dir / "assets-manifest.json").read_text(encoding="utf-8"))["assets"][0]
        self.assertFalse(asset["refinement"]["applied"])
        self.assertEqual(asset["refinement"]["fallback"], "gate-failed")
        self.assertEqual(asset["actual_t"], asset["triaged_t"])

    def test_grab_bad_selection_name_exits_2(self) -> None:
        work = self._prepare_work("badname")
        run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        chosen = payload["candidates"][0]
        selection_path = work / "selections.json"
        selection_path.write_text(json.dumps([{
            "candidate_id": chosen["candidate_id"], "name": "../escape", "chapter_id": chosen["chapter_id"],
            "role": "evidence", "caption": "c", "alt": "a", "anchor_seg_ids": ["seg_0002"],
        }]), encoding="utf-8")
        grab_run = self._run(
            sys.executable, SCRIPTS / "grab.py", "--work", work,
            "--spec", selection_path, "--out-dir", self.root / "summary-bad" / "assets",
        )
        self.assertEqual(grab_run.returncode, 2, grab_run.stdout + grab_run.stderr)
        self.assertIn("bad selection name", grab_run.stdout)

    @unittest.skipUnless(renderer._find_chrome() or renderer._find_weasyprint(), "no PDF engine")
    def test_render_pdf_writes_file(self) -> None:
        work = self._prepare_work("pdf")
        run = self._run(
            sys.executable, SCRIPTS / "candidates.py", self.video,
            "--work", work, "--transcript", work / "transcript.json",
            "--chapters", work / "chapters.json",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        chosen = max(
            (c for c in payload["candidates"] if "vt_result" in c["target_ids"]),
            key=lambda c: c["actual_t"],
        )
        selection_path = self._selection(chosen, work)
        summary_dir = self.root / "summary-pdf"
        assets_dir = summary_dir / "assets"
        grab_run = self._run(
            sys.executable, SCRIPTS / "grab.py", "--work", work,
            "--spec", selection_path, "--out-dir", assets_dir,
        )
        self.assertEqual(grab_run.returncode, 0, grab_run.stderr + grab_run.stdout)
        summary_path = work / "summary.json"
        summary_path.write_text(json.dumps({
            "overview": "PDF export check.",
            "chapters": [
                {"chapter_id": "ch01", "blocks": [{"text": "Intro.", "seg_ids": ["seg_0000"]}]},
                {"chapter_id": "ch02", "blocks": [{"text": "Result.", "seg_ids": ["seg_0001", "seg_0002"]}]},
            ],
        }), encoding="utf-8")
        render_run = self._run(
            sys.executable, SCRIPTS / "render.py", "--work", work,
            "--summary", summary_path, "--selections", selection_path,
            "--assets-dir", assets_dir, "--out-dir", summary_dir, "--pdf",
        )
        self.assertEqual(render_run.returncode, 0, render_run.stderr + render_run.stdout)
        pdf = self.root / "summary-pdf.pdf"
        self.assertTrue(pdf.exists())
        self.assertGreater(pdf.stat().st_size, 1000)
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        self.assertFalse((self.root / "summary-pdf.print.html").exists())
        self.assertIn("PDF: ", render_run.stdout)


if __name__ == "__main__":
    unittest.main()
