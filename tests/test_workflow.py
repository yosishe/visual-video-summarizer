"""workflow.py: the controller decides what may run next, from the files on disk.

Fast and hermetic: every sibling script is replaced by a fake that writes the
artifact shape the real script writes (with the same input bindings), so these
tests exercise the state machine, staleness and resume logic — not ffmpeg.
"""
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

import gates  # noqa: E402
import workflow  # noqa: E402

TRANSCRIPT = {
    "schema_version": 2, "status": "ok", "source": "captions", "language": "en",
    "video": {"id": "vid", "title": "Fixture", "duration": 12.0, "url": "https://www.youtube.com/watch?v=vid"},
    "segments": [{"seg_id": f"seg_{i:04d}", "start": i * 2.0, "end": i * 2.0 + 2.0, "text": f"segment {i} value {i * 7}"}
                 for i in range(6)],
}
CHAPTERS = [
    {"chapter_id": "ch01", "title": "Intro", "start": 0.0, "end": 6.0, "needs_frames": False},
    {"chapter_id": "ch02", "title": "Demo", "start": 6.0, "end": 12.0, "needs_frames": True,
     "visual_targets": [{"target_id": "t1", "kind": "state", "seg_ids": ["seg_0004"], "why": "on screen"}]},
]
SELECTIONS = [{"candidate_id": "c_0000", "name": "demo", "chapter_id": "ch02", "role": "evidence",
               "caption": {"shows": "The demo panel.", "why": "proves the state."}, "alt": "demo panel",
               "anchor_seg_ids": ["seg_0004"]}]
SUMMARY = {"schema_version": 3, "lang": "en", "overview": "The video shows a demo.",
           "chapters": [{"chapter_id": "ch01", "blocks": [{"text": "Intro.", "seg_ids": ["seg_0000"]}]},
                        {"chapter_id": "ch02", "blocks": [{"text": "Demo value 28.", "seg_ids": ["seg_0004"]}]}]}


def _arg(command: list[str], flag: str) -> str | None:
    return command[command.index(flag) + 1] if flag in command else None


class FakeScripts:
    """Writes what each real script writes, with the same bindings, and counts calls."""

    def __init__(self, *, captions: bool = True, audit_errors: int = 0):
        self.captions = captions
        self.audit_errors = audit_errors
        self.calls: list[str] = []

    def __call__(self, command: list[str], *, cwd=None) -> tuple[int, str, str]:
        script = Path(command[1]).name
        self.calls.append(script)
        work = Path(_arg(command, "--work"))
        handler = getattr(self, script.replace(".py", ""))
        return handler(command, work)

    def transcript(self, command, work):
        payload = dict(TRANSCRIPT)
        if not self.captions:
            payload = {**payload, "status": "no_transcript", "segments": [], "source": None,
                       "source_detail": {"kind": "none", "reason": "no caption track; cloud transcription not authorized"}}
        (work / "transcript.json").write_text(json.dumps(payload), encoding="utf-8")
        return (0 if self.captions else 6), "# transcript report\n", ""

    def candidates(self, command, work):
        transcript_path = Path(_arg(command, "--transcript"))
        chapters_path = Path(_arg(command, "--chapters"))
        chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
        visual = _arg(command, "--visual-content") or "illustrated"
        needs = [c for c in chapters if c.get("needs_frames") is True]
        inputs = {"transcript_sha256": gates.sha256_file(transcript_path), "chapters_sha256": gates.sha256_file(chapters_path),
                  "cache_key": "cachekey", "visual_content": visual, "video_id": "vid"}
        if not needs:
            payload = {"schema_version": 2, "status": "no_visual_chapters", "inputs": inputs, "tier": "standard",
                       "candidates": [], "coverage": {"chapters": [{"chapter_id": c["chapter_id"], "status": "not-required"}
                                                                   for c in chapters], "targets": []},
                       "token_budget": {"mode": "individual"}}
        else:
            payload = {"schema_version": 2, "status": "ok", "inputs": inputs, "tier": "standard",
                       "candidates": [{"candidate_id": "c_0000", "chapter_id": "ch02", "actual_t": 7.0,
                                       "seg_ids": ["seg_0003", "seg_0004"], "aligned_seg_ids": ["seg_0004"],
                                       "path": str(work / "candidates" / "c_0000.jpg")},
                                      {"candidate_id": "c_0001", "chapter_id": "ch02", "actual_t": 10.0,
                                       "seg_ids": ["seg_0005"], "aligned_seg_ids": ["seg_0005"],
                                       "path": str(work / "candidates" / "c_0001.jpg")}],
                       "coverage": {"chapters": [{"chapter_id": "ch01", "status": "not-required"},
                                                 {"chapter_id": "ch02", "status": "covered"}],
                                    "targets": [{"target_id": "t1", "status": "covered"}]},
                       "token_budget": {"mode": "sheets", "shortlist_max": 30},
                       "sheets": {"status": "ok", "sheets": [{"sheet_id": "sheet_00"}]}}
        (work / "candidates.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0, "# candidate frames report\n", ""

    def shortlist(self, command, work):
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        ids = _arg(command, "--ids").split(",")
        payload["shortlist"] = {"requested_ids": ids, "written": [{"candidate_id": i} for i in ids], "failures": [],
                                "candidates_sha256": gates.candidates_digest(payload)}
        (work / "candidates.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0, "# shortlist\n", ""

    def grab(self, command, work):
        out_dir = Path(_arg(command, "--out-dir"))
        out_dir.mkdir(parents=True, exist_ok=True)
        selections = json.loads(Path(_arg(command, "--spec")).read_text(encoding="utf-8"))
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        assets = []
        for selection in selections:
            full = out_dir / f"{selection['name']}-full.jpg"
            thumb = out_dir / f"{selection['name']}-thumb.jpg"
            full.write_bytes(b"full" + selection["candidate_id"].encode())
            thumb.write_bytes(b"thumb" + selection["candidate_id"].encode())
            assets.append({"candidate_id": selection["candidate_id"], "actual_t": 7.0,
                           "full": {"path": str(full), "sha256": gates.sha256_file(full)},
                           "thumb": {"path": str(thumb), "sha256": gates.sha256_file(thumb)}})
        manifest = {"schema_version": 2, "assets": assets, "failures": [], "duplicate_pairs": [],
                    "selections_sha256": gates.canonical_sha256(selections),
                    "selections_binding_sha256": gates.selections_binding(selections),
                    "candidates_sha256": gates.candidates_digest(payload)}
        (out_dir / "assets-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return 0, "# grab report\n", ""

    def audit_summary(self, command, work):
        errors = [{"check": "number", "where": "ch02", "message": "x"}] * self.audit_errors
        (work / "audit.json").write_text(json.dumps({"errors": errors, "reviews": [], "warnings": [], "stats": {}}),
                                         encoding="utf-8")
        return (5 if errors else 0), "# summary audit\n", ""

    def render(self, command, work):
        out_dir = Path(_arg(command, "--out-dir"))
        out_dir.mkdir(parents=True, exist_ok=True)
        summary = json.loads(Path(_arg(command, "--summary")).read_text(encoding="utf-8"))
        selections = json.loads(Path(_arg(command, "--selections")).read_text(encoding="utf-8")) if "--selections" in command else []
        candidates = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        assets_manifest = Path(_arg(command, "--assets-dir")) / "assets-manifest.json" if "--assets-dir" in command else None
        manifest = {
            "schema_version": 3, "summary_sha256": gates.canonical_sha256(summary),
            "selections_sha256": gates.canonical_sha256(selections),
            "transcript_sha256": gates.sha256_file(work / "transcript.json"),
            "chapters_sha256": gates.sha256_file(work / "chapters.json"),
            "candidates_sha256": gates.candidates_digest(candidates),
            "assets_manifest_sha256": gates.sha256_file(assets_manifest) if assets_manifest else None,
            "output_mode": _arg(command, "--output-mode"), "frames_count": len(selections),
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        html = "<html><body>" + "".join('<img src="data:image/jpeg;base64,AAAA">' for _ in selections) + "</body></html>"
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        (out_dir.parent / f"{out_dir.name}.html").write_text(html, encoding="utf-8")
        return 0, "Rendered\n", ""


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-wf-")
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.fake = FakeScripts()
        self.patcher = mock.patch.object(workflow, "_invoke", self.fake)
        self.patcher.start()
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.patcher.stop()
        self.temporary.cleanup()

    def wf(self, *argv: str) -> int:
        try:
            return workflow.main([*argv, "--work", str(self.work)]) or 0
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 1

    def init(self, *extra: str) -> int:
        return self.wf("init", "https://www.youtube.com/watch?v=vid", "--lang", "en", *extra)

    def write(self, name: str, payload) -> None:
        (self.work / name).write_text(json.dumps(payload), encoding="utf-8")

    def run_json(self) -> dict:
        return json.loads((self.work / "run.json").read_text(encoding="utf-8"))

    def complete(self) -> None:
        self.init()
        self.assertEqual(self.wf("run"), 0)
        self.write("chapters.json", CHAPTERS)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.wf("shortlist", "--ids", "c_0000,c_0001"), 0)
        self.write("selections.json", SELECTIONS)
        self.assertEqual(self.wf("run"), 0)
        self.write("summary.json", SUMMARY)
        self.assertEqual(self.wf("run"), 0)

    # --- init ---------------------------------------------------------------
    def test_init_writes_run_json_and_refuses_existing(self):
        self.assertEqual(self.init(), 0)
        run = self.run_json()
        self.assertEqual(run["request"]["lang"], "en")
        self.assertEqual(run["visual_content"]["decision"], "illustrated")
        self.assertIn("doctor", run)
        self.assertEqual(self.init(), 10)
        self.assertEqual(self.init("--force", "--tier", "high"), 0)
        self.assertEqual(self.run_json()["request"]["tier"], "high")

    # --- the loop -------------------------------------------------------------
    def test_run_stops_at_chapters_with_next_and_exit_0(self):
        self.init()
        self.assertEqual(self.wf("run"), 0)
        run = self.run_json()
        self.assertEqual(run["stages"]["transcript"]["status"], "ok")
        self.assertEqual(run["stages"]["chapters"]["status"], "awaiting_model")
        self.assertEqual(run["blocker"]["stage"], "chapters")
        self.assertIn("references/chapters.md", run["blocker"]["next"])
        self.assertEqual(self.fake.calls, ["transcript.py"])

    def test_invalid_chapters_exit_10_and_nothing_downstream_runs(self):
        self.init()
        self.wf("run")
        bad = json.loads(json.dumps(CHAPTERS))
        bad[1]["visual_targets"][0]["seg_ids"] = ["seg_9999"]
        bad[0]["needs_frames"] = None
        self.write("chapters.json", bad)
        self.assertEqual(self.wf("run"), 10)
        run = self.run_json()
        self.assertEqual(run["stages"]["chapters"]["status"], "invalid")
        self.assertTrue(any("seg_9999" in e for e in run["stages"]["chapters"]["errors"]))
        self.assertNotIn("candidates.py", self.fake.calls)

    def test_full_loop_completes_and_verify_passes(self):
        self.complete()
        self.assertEqual(self.fake.calls, ["transcript.py", "candidates.py", "shortlist.py", "grab.py",
                                           "audit_summary.py", "render.py"])
        self.assertEqual(self.wf("verify"), 0)
        report = json.loads((self.work / "verify.json").read_text(encoding="utf-8"))
        self.assertTrue(report["complete"])
        self.assertEqual({row["status"] for row in report["rows"]}, {"PASS"})
        self.assertTrue(Path(report["deliverable"]).is_file())

    def test_resume_does_not_rerun_completed_stages(self):
        self.complete()
        before = list(self.fake.calls)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls, before)

    def test_editing_chapters_marks_candidates_and_downstream_stale_and_reruns(self):
        self.complete()
        edited = json.loads(json.dumps(CHAPTERS))
        edited[1]["title"] = "Demo (renamed)"
        self.write("chapters.json", edited)
        self.assertEqual(self.wf("status"), 0)
        run = self.run_json()
        self.assertEqual(run["stages"]["candidates"]["status"], "stale")
        self.assertEqual(run["stages"]["render"]["status"], "blocked")
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[-1], "candidates.py")
        run = self.run_json()
        self.assertEqual(run["stages"]["candidates"]["status"], "ok")
        # a fresh pool needs a fresh triage receipt, but the summary text survives
        self.assertEqual(run["stages"]["shortlist"]["status"], "awaiting_model")
        self.assertEqual(run["stages"]["summary"]["status"], "blocked")
        self.assertTrue((self.work / "summary.json").is_file())

    def test_caption_edit_reruns_audit_and_render_but_not_grab(self):
        self.complete()
        edited = json.loads(json.dumps(SELECTIONS))
        edited[0]["caption"]["shows"] = "The demo panel, edited."
        self.write("selections.json", edited)
        calls_before = len(self.fake.calls)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[calls_before:], ["audit_summary.py", "render.py"])

    def test_new_selection_reruns_grab(self):
        self.complete()
        edited = json.loads(json.dumps(SELECTIONS))
        edited[0]["candidate_id"], edited[0]["anchor_seg_ids"] = "c_0001", ["seg_0005"]
        self.write("selections.json", edited)
        calls_before = len(self.fake.calls)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[calls_before], "grab.py")

    def test_transcript_failure_records_blocker_and_propagates_exit_6(self):
        self.fake.captions = False
        self.init()
        self.assertEqual(self.wf("run"), 6)
        run = self.run_json()
        self.assertEqual(run["stages"]["transcript"]["status"], "failed")
        self.assertEqual(run["blocker"]["exit_code"], 6)
        self.assertIn("frames-only", run["blocker"]["next"])
        # unchanged inputs: no silent retry, same code
        self.assertEqual(self.wf("run"), 6)
        self.assertEqual(self.fake.calls, ["transcript.py"])
        self.assertEqual(self.wf("run", "--retry"), 6)
        self.assertEqual(self.fake.calls, ["transcript.py", "transcript.py"])

    def test_illustrated_intent_with_all_false_chapters_is_invalid(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", [dict(c, needs_frames=False, visual_targets=[]) for c in CHAPTERS])
        self.assertEqual(self.wf("run"), 10)
        self.assertTrue(any("no-visuals" in e for e in self.run_json()["stages"]["chapters"]["errors"]))

    def test_decide_no_visuals_skips_visual_stages_and_renders_text_only(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", [dict(c, needs_frames=False, visual_targets=[]) for c in CHAPTERS])
        self.assertEqual(self.wf("decide", "no-visuals", "--reason", "short"), 10)
        self.assertEqual(self.wf("decide", "no-visuals", "--reason", "talking head only, static camera, no slides"), 0)
        self.assertEqual(self.wf("run"), 0)
        run = self.run_json()
        self.assertEqual(run["stages"]["candidates"]["status"], "ok")
        for name in ("shortlist", "selections", "grab"):
            self.assertEqual(run["stages"][name]["status"], "skipped")
        self.write("summary.json", SUMMARY)
        self.assertEqual(self.wf("run"), 0)
        self.assertNotIn("grab.py", self.fake.calls)
        self.assertEqual(self.wf("verify"), 0)
        report = json.loads((self.work / "verify.json").read_text(encoding="utf-8"))
        self.assertEqual(report["visual_content"]["decision"], "none")
        self.assertEqual(report["rows"][0]["check"], "visual content")
        self.assertIn("talking head", report["rows"][0]["evidence"])

    def test_audit_errors_block_render_with_exit_5(self):
        self.fake.audit_errors = 2
        self.init()
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        self.wf("run")
        self.wf("shortlist", "--ids", "c_0000")
        self.write("selections.json", SELECTIONS)
        self.wf("run")
        self.write("summary.json", SUMMARY)
        self.assertEqual(self.wf("run"), 5)
        self.assertNotIn("render.py", self.fake.calls)
        self.assertEqual(self.run_json()["blocker"]["stage"], "audit")

    def test_verify_incomplete_exit_12_and_selection_without_receipt_warns(self):
        self.init()
        self.wf("run")
        self.assertEqual(self.wf("verify"), 12)
        report = json.loads((self.work / "verify.json").read_text(encoding="utf-8"))
        self.assertFalse(report["complete"])
        self.assertTrue(any(row["status"] == "FAIL" for row in report["rows"]))

    def test_next_names_reference_documents(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        self.wf("run")
        self.wf("shortlist", "--ids", "c_0000")
        self.assertEqual(self.wf("next"), 0)
        self.assertIn("references/triage.md", self.run_json()["blocker"]["next"])

    def test_pre_existing_valid_artifacts_are_adopted(self):
        """A work dir prepared by hand (the old manual route) resumes instead of restarting."""
        self.work.mkdir()
        self.write("transcript.json", TRANSCRIPT)
        self.write("chapters.json", CHAPTERS)
        self.init()
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls, ["candidates.py"])


if __name__ == "__main__":
    unittest.main()
