"""workflow.py: the controller decides what may run next, from the files on disk.

Fast and hermetic: every sibling script is replaced by a fake that writes the
artifact shape the real script writes (with the same input bindings), so these
tests exercise the state machine, staleness and resume logic — not ffmpeg.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
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
SELECTIONS_HE = [dict(SELECTIONS[0], caption={"shows": "לוח ההדגמה.", "why": "מוכיח את המצב."}, alt="לוח ההדגמה")]
SUMMARY = {"schema_version": 3, "lang": "en", "overview": "The video shows a demo.",
           "chapters": [{"chapter_id": "ch01", "blocks": [{"text": "Intro.", "seg_ids": ["seg_0000"]}]},
                        {"chapter_id": "ch02", "blocks": [{"text": "Demo value 28.", "seg_ids": ["seg_0004"]}]}]}
READY_DOCTOR = {"ready": True, "platform": "test", "python_command": "python3",
                "checks": [{"name": name, "required": True, "available": True, "version": "fake 1.0"}
                           for name in ("Python", "ffmpeg", "ffprobe", "yt-dlp")]}
MISSING_FFMPEG_DOCTOR = {"ready": False, "platform": "test", "python_command": "python3",
                         "checks": [{"name": "Python", "required": True, "available": True, "version": "3.x"},
                                    {"name": "ffmpeg", "required": True, "available": False,
                                     "hint": "Install ffmpeg with your package manager, e.g. `x`, after the user approves."},
                                    {"name": "ffprobe", "required": True, "available": True, "version": "7"},
                                    {"name": "yt-dlp", "required": True, "available": True, "version": "2026"}]}


def _arg(command: list[str], flag: str) -> str | None:
    return command[command.index(flag) + 1] if flag in command else None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeScripts:
    """Writes what each real script writes, with the same bindings, and counts calls."""

    def __init__(self, *, captions: bool = True, audit_errors: int = 0, source_unavailable: bool = False,
                 probe: str | None = None):
        self.captions = captions
        self.audit_errors = audit_errors
        self.source_unavailable = source_unavailable
        self.probe = probe
        self.calls: list[str] = []
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], *, cwd=None) -> tuple[int, str, str]:
        script = Path(command[1]).name
        self.calls.append(script)
        self.commands.append(list(command))
        work = Path(_arg(command, "--work"))
        handler = getattr(self, script.replace(".py", ""))
        return handler(command, work)

    def transcript(self, command, work):
        payload = dict(TRANSCRIPT)
        payload["health"] = gates.transcript_health(payload["segments"], payload["video"]["duration"])
        payload["engine_version"] = gates.ENGINE_VERSION
        payload["source_identity"] = gates.canonical_source(command[2])
        payload["inputs"] = {"whisper": _arg(command, "--whisper"), "no_whisper": "--no-whisper" in command,
                             "langs": _arg(command, "--langs"), "wanted": _arg(command, "--wanted")}
        code = 0
        if self.source_unavailable:
            payload = {**payload, "status": "source_unavailable", "segments": [], "source": None,
                       "video": {"id": "video", "title": None, "duration": 0.0},
                       "source_detail": {"kind": "none", "yt_dlp_exit": 1,
                                         "reason": "yt-dlp could not fetch metadata (exit 1): ERROR: Video unavailable"}}
            code = 13
        elif not self.captions:
            payload = {**payload, "status": "no_transcript", "segments": [], "source": None,
                       "source_detail": {"kind": "none", "reason": "no caption track; cloud transcription not authorized"}}
            code = 6
        (work / "transcript.json").write_text(json.dumps(payload), encoding="utf-8")
        return code, "# transcript report\n", ""

    def candidates(self, command, work):
        transcript_path = Path(_arg(command, "--transcript"))
        chapters_path = Path(_arg(command, "--chapters"))
        chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
        visual = _arg(command, "--visual-content") or "illustrated"
        tier = _arg(command, "--tier") or "standard"
        needs = [c for c in chapters if c.get("needs_frames") is True]
        inputs = {"transcript_sha256": gates.sha256_file(transcript_path), "chapters_sha256": gates.sha256_file(chapters_path),
                  "cache_key": "cachekey", "visual_content": visual, "video_id": "vid",
                  "source_identity": gates.canonical_source(command[2]),
                  "visual_decided_by": _arg(command, "--decided-by") or "model",
                  "options": {"tier": tier, "sections": _arg(command, "--sections"),
                              "max_image_tokens": int(_arg(command, "--max-image-tokens")) if "--max-image-tokens" in command else None,
                              "allow_long": "--allow-long" in command}}
        if not needs:
            payload = {"schema_version": 2, "status": "no_visual_chapters", "inputs": inputs, "tier": tier,
                       "engine_version": gates.ENGINE_VERSION,
                       "candidates": [], "coverage": {"chapters": [{"chapter_id": c["chapter_id"], "status": "not-required"}
                                                                   for c in chapters], "targets": []},
                       "token_budget": {"mode": "individual"}}
            if self.probe:
                payload["visual_probe"] = {"verdict": self.probe, "reason": f"fake probe says {self.probe}",
                                           "non_talk_seconds": 200.0 if self.probe == "contradicts" else 0.0,
                                           "threshold_s": 90.0, "decided_by": inputs["visual_decided_by"],
                                           "non_talk_spans": [{"start": 60.0, "end": 260.0, "settled_s": 200.0,
                                                               "mode": "B", "mode_label": "static content",
                                                               "chapter_ids": ["ch02"]}] if self.probe == "contradicts" else []}
        else:
            payload = {"schema_version": 2, "status": "ok", "inputs": inputs, "tier": tier,
                       "engine_version": gates.ENGINE_VERSION,
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
        if payload.get("visual_probe", {}).get("verdict") == "contradicts" and inputs["visual_decided_by"] == "model":
            return 10, "# candidate frames report\n", "[vsum] the visual probe contradicts the no-visuals decision\n"
        return 0, "# candidate frames report\n", ""

    def shortlist(self, command, work):
        payload = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        ids = _arg(command, "--ids").split(",")
        payload["shortlist"] = {"requested_ids": ids, "width": 640, "image_tokens": 299 * len(ids),
                                "written": [{"candidate_id": i, "path": str(work / "shortlist" / f"{i}.jpg"),
                                             "actual_t": 7.0, "tokens": 299, "sha256": "f" * 64} for i in ids],
                                "failures": [], "candidates_sha256": gates.candidates_digest(payload)}
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
                           "verification": {"luma_mad": 0.41, "edge_mad": 0.62, "changed_ratio": 0.002,
                                            "thresholds": {"luma": 3.0, "edge": 4.0, "changed": 0.025}, "refined": False},
                           "full": {"path": str(full), "sha256": gates.sha256_file(full)},
                           "thumb": {"path": str(thumb), "sha256": gates.sha256_file(thumb)}})
        manifest = {"schema_version": 2, "engine_version": gates.ENGINE_VERSION, "assets": assets, "failures": [],
                    "duplicate_pairs": [], "selections_sha256": gates.canonical_sha256(selections),
                    "selections_binding_sha256": gates.selections_binding(selections),
                    "candidates_sha256": gates.candidates_digest(payload)}
        (out_dir / "assets-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return 0, "# grab report\n", ""

    def audit_summary(self, command, work):
        errors = [{"check": "number", "where": "ch02", "message": "x"}] * self.audit_errors
        candidates = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        selections_path = _arg(command, "--selections")
        inputs = {"summary_sha256": gates.sha256_file(Path(_arg(command, "--summary"))),
                  "selections_sha256": gates.sha256_file(Path(selections_path)) if selections_path else None,
                  "transcript_sha256": gates.sha256_file(work / "transcript.json"),
                  "chapters_sha256": gates.sha256_file(work / "chapters.json"),
                  "candidates_sha256": gates.candidates_digest(candidates), "lang": _arg(command, "--lang")}
        (work / "audit.json").write_text(json.dumps({"errors": errors, "reviews": [], "warnings": [], "stats": {},
                                                     "inputs": inputs}), encoding="utf-8")
        return (5 if errors else 0), "# summary audit\n", ""

    def render(self, command, work):
        out_dir = Path(_arg(command, "--out-dir"))
        out_dir.mkdir(parents=True, exist_ok=True)
        summary = json.loads(Path(_arg(command, "--summary")).read_text(encoding="utf-8"))
        selections = json.loads(Path(_arg(command, "--selections")).read_text(encoding="utf-8")) if "--selections" in command else []
        candidates = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        assets_manifest = Path(_arg(command, "--assets-dir")) / "assets-manifest.json" if "--assets-dir" in command else None
        text_only = _arg(command, "--output-mode") == "text-only"
        html = "<html><body>" + "".join('<img src="data:image/jpeg;base64,AAAA">' for _ in selections) + "</body></html>"
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        bundle = out_dir.parent / f"{out_dir.name}.html"
        bundle.write_text(html, encoding="utf-8")
        manifest = {
            "schema_version": 3, "engine_version": gates.ENGINE_VERSION,
            "summary_sha256": gates.canonical_sha256(summary),
            "selections_sha256": gates.canonical_sha256(selections),
            "transcript_sha256": gates.sha256_file(work / "transcript.json"),
            "chapters_sha256": gates.sha256_file(work / "chapters.json"),
            "candidates_sha256": gates.candidates_digest(candidates),
            "assets_manifest_sha256": gates.sha256_file(assets_manifest) if assets_manifest else None,
            "output_mode": _arg(command, "--output-mode"), "frames_count": len(selections),
            "lang": _arg(command, "--lang"), "tier": candidates.get("tier"),
            "visual_content": "none" if text_only else "illustrated",
            "bundle_sha256": gates.sha256_file(bundle), "pdf_sha256": None,
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return 0, "Rendered\n", ""


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-wf-")
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.fake = FakeScripts()
        self.patcher = mock.patch.object(workflow, "_invoke", self.fake)
        self.patcher.start()
        # The readiness check must not depend on the host (the fast CI job has no ffmpeg).
        self.doctor = mock.patch("doctor.check", return_value=dict(READY_DOCTOR))
        self.doctor.start()
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.doctor.stop()
        self.patcher.stop()
        self.temporary.cleanup()

    def wf(self, *argv: str) -> int:
        try:
            return workflow.main([*argv, "--work", str(self.work)]) or 0
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 1

    def wf_out(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.wf(*argv)
        return code, out.getvalue(), err.getvalue()

    def init(self, *extra: str) -> int:
        return self.wf("init", "https://www.youtube.com/watch?v=vid", "--lang", "en", *extra)

    def write(self, name: str, payload) -> None:
        (self.work / name).write_text(json.dumps(payload), encoding="utf-8")

    def run_json(self) -> dict:
        return json.loads((self.work / "run.json").read_text(encoding="utf-8"))

    def statuses(self) -> dict:
        return {name: record["status"] for name, record in self.run_json()["stages"].items()}

    def complete(self, *init_extra: str, selections=SELECTIONS, summary=SUMMARY) -> None:
        self.init(*init_extra)
        self.assertEqual(self.wf("run"), 0)
        self.write("chapters.json", CHAPTERS)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.wf("shortlist", "--ids", "c_0000,c_0001"), 0)
        self.write("selections.json", selections)
        self.assertEqual(self.wf("run"), 0)
        self.write("summary.json", summary)
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
        self.assertEqual([row["check"] for row in report["rows"][:3]], ["visual content", "source", "preflight"])
        self.assertIn('video vid "Fixture"', report["rows"][1]["evidence"])
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

    def test_missing_captions_explains_local_setup_before_cloud_choice(self):
        self.fake.captions = False
        self.init()
        self.assertEqual(self.wf("run"), 6)
        next_step = self.run_json()["blocker"]["next"]
        self.assertIn("--whisper local", next_step)
        self.assertIn("--local-model", next_step)
        self.assertLess(next_step.index("--whisper local"), next_step.index("--whisper groq|openai"))
        self.assertIn("approval", next_step)
        self.assertEqual(self.fake.calls, ["transcript.py"])

    def test_disabled_transcription_does_not_recommend_cloud_upload(self):
        self.fake.captions = False
        self.init("--no-whisper")
        self.assertEqual(self.wf("run"), 6)
        next_step = self.run_json()["blocker"]["next"]
        self.assertIn("disabled", next_step)
        self.assertNotIn("--whisper groq|openai", next_step)

    def test_proxy_policy_failure_recommends_environment_repair(self):
        self.init()
        self.wf("run")
        payload = json.loads((self.work / "transcript.json").read_text())
        payload.update(status="acquisition_failed", segments=[], source=None,
                       acquisition_error={"category": "environment_blocked", "exit_code": 14,
                                          "message": "Environment proxy denied source access"})
        self.write("transcript.json", payload)
        self.assertEqual(self.wf("run"), 14)
        next_step = self.run_json()["blocker"]["next"]
        self.assertIn("environment", next_step)
        self.assertIn("network policy", next_step)
        self.assertNotIn("another source", next_step)

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
        # the fake wrote no probe: the report says so instead of pretending
        self.assertTrue(any("no visual probe" in w for w in report["rows"][0]["warnings"]))

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

    # --- 1.8: durable bindings ------------------------------------------------------
    def test_init_force_with_a_different_source_is_refused(self):
        self.complete()
        code, _, err = self.wf_out("init", "https://www.youtube.com/watch?v=OTHERVIDEO1", "--lang", "en", "--force")
        self.assertEqual(code, 10)
        self.assertIn("fresh work directory", err)
        self.assertEqual(self.run_json()["source"]["raw"], "https://www.youtube.com/watch?v=vid")
        self.assertEqual(self.wf("verify"), 0)

    def test_foreign_transcript_identity_is_stale_and_refetched(self):
        self.work.mkdir()
        self.write("transcript.json", dict(TRANSCRIPT, source_identity="https://www.youtube.com/watch?v=OTHERVIDEO1"))
        self.init()
        self.assertEqual(self.wf("status"), 0)
        self.assertEqual(self.statuses()["transcript"], "stale")
        self.assertIn("different source", self.run_json()["stages"]["transcript"]["reason"])
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls, ["transcript.py"])
        self.assertEqual(self.statuses()["transcript"], "ok")

    def test_init_force_tier_change_marks_candidates_stale_and_reruns(self):
        self.complete()
        self.assertEqual(self.init("--force", "--tier", "high"), 0)
        self.assertEqual(self.wf("status"), 0)
        self.assertEqual(self.statuses()["candidates"], "stale")
        self.assertIn("tier", self.run_json()["stages"]["candidates"]["reason"])
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[-1], "candidates.py")
        self.assertIn("high", self.fake.commands[-1][self.fake.commands[-1].index("--tier") + 1])
        self.assertEqual(self.statuses()["candidates"], "ok")
        self.assertEqual(self.statuses()["shortlist"], "awaiting_model")

    def test_stale_verdict_is_durable_across_status_calls(self):
        self.complete()
        self.assertEqual(self.init("--force", "--tier", "high"), 0)
        for _ in range(3):
            self.assertEqual(self.wf("status"), 0)
            self.assertEqual(self.statuses()["candidates"], "stale")
        self.assertEqual(self.wf("next"), 0)
        self.assertEqual(self.statuses()["candidates"], "stale")

    def test_no_visuals_decision_with_needs_frames_chapter_is_invalid_and_revertable(self):
        self.init()
        self.wf("run")
        self.assertEqual(self.wf("decide", "no-visuals", "--reason", "talking head only, static camera, no slides"), 0)
        self.write("chapters.json", CHAPTERS)  # ch02 needs frames: the decision is contradicted
        self.assertEqual(self.wf("run"), 10)
        self.assertEqual(self.statuses()["chapters"], "invalid")
        self.assertTrue(any("decide illustrated" in e for e in self.run_json()["stages"]["chapters"]["errors"]))
        self.assertNotIn("candidates.py", self.fake.calls)
        self.assertEqual(self.wf("decide", "illustrated", "--reason", "chapter 2 shows the demo panel on screen"), 0)
        self.assertEqual(self.wf("run"), 0)
        self.assertIn("candidates.py", self.fake.calls)
        self.assertEqual(self.run_json()["visual_content"]["decision"], "illustrated")

    def test_engine_minor_upgrade_marks_deterministic_stages_stale(self):
        self.complete()
        before = gates.ENGINE_VERSION
        after = before.split(".")[0] + "." + str(int(before.split(".")[1]) + 1) + ".0"
        with mock.patch.object(gates, "ENGINE_VERSION", after), mock.patch.object(workflow, "ENGINE_VERSION", after):
            self.assertEqual(self.wf("status"), 0)
            run = self.run_json()
            self.assertEqual(run["engine_version"], after)
            self.assertTrue(run["history"][-1]["command"].startswith(f"engine {before} -> {after}"))
            self.assertEqual(run["stages"]["transcript"]["status"], "ok")
            self.assertTrue(any(after in w for w in run["stages"]["transcript"]["warnings"]))
            self.assertEqual(run["stages"]["candidates"]["status"], "stale")
            self.assertIn(after, run["stages"]["candidates"]["reason"])
            self.assertEqual(self.wf("verify"), 11)

    def test_bundle_hash_is_bound_and_a_foreign_bundle_fails_verify(self):
        self.complete()
        self.assertEqual(self.wf("verify"), 0)
        bundle = Path(self.run_json()["out_dir"]).parent / "summary-vid.html"
        bundle.write_text('<html><body><img src="data:image/jpeg;base64,FOREIGN"></body></html>', encoding="utf-8")
        self.assertEqual(self.wf("verify"), 11)
        self.assertEqual(self.statuses()["render"], "stale")
        self.assertIn("bundle", self.run_json()["stages"]["render"]["reason"])

    def test_whisper_option_change_reruns_transcript_without_retry(self):
        self.fake.captions = False
        self.init()
        self.assertEqual(self.wf("run"), 6)
        self.fake.captions = True
        self.assertEqual(self.init("--force", "--whisper", "groq"), 0)
        code, out, _ = self.wf_out("run")
        self.assertEqual(code, 0)
        self.assertNotIn("--retry", out)
        self.assertEqual(self.fake.calls, ["transcript.py", "transcript.py"])
        self.assertIn("groq", self.fake.commands[-1])
        self.assertEqual(self.statuses()["transcript"], "ok")

    def test_next_after_direct_shortlist_regenerates_report_and_names_workflow(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        self.wf("run")
        blocker = self.run_json()["blocker"]["next"]
        self.assertIn('workflow.py" shortlist', blocker)
        self.assertNotIn("shortlist.py", blocker)
        # an agent that ran shortlist.py by hand still gets the report the next step names
        self.fake([sys.executable, str(ROOT / "scripts" / "shortlist.py"), "--work", str(self.work), "--ids", "c_0000"])
        self.assertEqual(self.wf("next"), 0)
        report = self.work / "reports" / "shortlist.md"
        self.assertTrue(report.is_file())
        self.assertIn("c_0000", report.read_text(encoding="utf-8"))
        self.assertEqual(self.statuses()["shortlist"], "ok")

    def test_summary_lang_mismatch_is_invalid(self):
        self.init("--lang", "he")
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        self.wf("run")
        self.wf("shortlist", "--ids", "c_0000")
        self.write("selections.json", SELECTIONS_HE)
        self.assertEqual(self.wf("run"), 0)
        self.write("summary.json", SUMMARY)  # declares lang en
        self.assertEqual(self.wf("run"), 10)
        self.assertEqual(self.run_json()["blocker"]["stage"], "summary")
        self.assertIn("lang", self.run_json()["blocker"]["reason"])
        self.assertNotIn("audit_summary.py", self.fake.calls)

    def test_source_unavailable_is_exit_13_with_next_and_retry(self):
        self.fake.source_unavailable = True
        self.init()
        code, out, _ = self.wf_out("run")
        self.assertEqual(code, 13)
        run = self.run_json()
        self.assertEqual(run["blocker"]["exit_code"], 13)
        self.assertIn("cookies", run["blocker"]["next"])
        self.assertIn("fresh work directory", run["blocker"]["next"])
        self.assertEqual(self.wf("run"), 13)
        self.assertEqual(self.fake.calls, ["transcript.py"])
        self.assertEqual(self.wf("run", "--retry"), 13)
        self.assertEqual(len(self.fake.calls), 2)
        code, out, _ = self.wf_out("verify", "--json")
        self.assertEqual(code, 12)
        report = json.loads(out)
        source = next(row for row in report["rows"] if row["check"] == "source")
        self.assertEqual(source["status"], "FAIL")
        self.assertIn("Video unavailable", source["evidence"])

    def test_transcript_ok_fills_run_source_and_health(self):
        self.init()
        self.assertEqual(self.wf("run"), 0)
        run = self.run_json()
        self.assertEqual(run["source"]["video_id"], "vid")
        self.assertEqual(run["source"]["title"], "Fixture")
        self.assertEqual(run["source"]["duration"], 12.0)
        self.assertIsNotNone(run["transcript_health"])

    def test_preflight_blocks_run_with_install_hints(self):
        self.init()
        run = self.run_json()
        run["doctor"]["ready"] = False
        (self.work / "run.json").write_text(json.dumps(run), encoding="utf-8")
        with mock.patch("doctor.check", return_value=dict(MISSING_FFMPEG_DOCTOR)):
            code, out, _ = self.wf_out("run")
            self.assertEqual(code, 1)
            self.assertIn("NEXT (preflight, blocked)", out)
            self.assertIn("ffmpeg", out)
            self.assertIn("after the user approves", out)
            self.assertEqual(self.fake.calls, [])
            self.assertEqual(self.wf("verify"), 12)
            report = json.loads((self.work / "verify.json").read_text(encoding="utf-8"))
            preflight = next(row for row in report["rows"] if row["check"] == "preflight")
            self.assertEqual(preflight["status"], "FAIL")
            self.assertIn("ffmpeg", preflight["evidence"])
        # the tool is installed: the next run re-checks and proceeds
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls, ["transcript.py"])
        self.assertEqual(self.run_json()["doctor"]["checked_by"], "preflight")

    def test_run_prints_the_delivery_report_when_complete(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        self.wf("run")
        self.wf("shortlist", "--ids", "c_0000,c_0001")
        self.write("selections.json", SELECTIONS)
        self.wf("run")
        self.write("summary.json", SUMMARY)
        code, out, _ = self.wf_out("run")
        self.assertEqual(code, 0)
        self.assertIn("# delivery verification", out)
        self.assertIn("COMPLETE", out)
        self.assertIn("PASS render", out)
        self.assertTrue((self.work / "verify.json").is_file())

    def test_init_force_announces_a_kept_decision(self):
        self.init()
        self.wf("run")
        self.wf("decide", "no-visuals", "--reason", "talking head only, static camera, no slides")
        code, _, err = self.wf_out("init", "https://www.youtube.com/watch?v=vid", "--lang", "en", "--force")
        self.assertEqual(code, 0)
        self.assertIn("keeping", err)
        self.assertIn("talking head", err)
        self.assertEqual(self.run_json()["visual_content"]["decision"], "none")

    def test_focus_is_shown_in_next_and_next_json_prints_the_blocker(self):
        self.init("--focus", "the deployment part")
        self.wf("run")
        self.assertIn("the deployment part", self.run_json()["blocker"]["next"])
        code, out, _ = self.wf_out("next", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["stage"], "chapters")

    def test_verify_prints_the_recorded_pixel_gate(self):
        self.complete()
        code, out, _ = self.wf_out("verify", "--json")
        self.assertEqual(code, 0)
        grab = next(row for row in json.loads(out)["rows"] if row["check"] == "grab")
        self.assertIn("luma", grab["evidence"])

    def test_audit_binding_survives_two_status_calls_and_reruns(self):
        self.complete()
        edited = json.loads(json.dumps(SUMMARY))
        edited["overview"] = "The video shows a demo, edited."
        self.write("summary.json", edited)
        for _ in range(2):
            self.assertEqual(self.wf("status"), 0)
            self.assertEqual(self.statuses()["audit"], "stale")
            self.assertIn("summary.json", self.run_json()["stages"]["audit"]["reason"])
        calls_before = len(self.fake.calls)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[calls_before:], ["audit_summary.py", "render.py"])

    def test_interrupted_stage_is_rerun_not_adopted(self):
        self.init()
        self.wf("run")
        self.write("chapters.json", CHAPTERS)
        # simulate a crash mid-extraction: a running record and a half-written manifest
        run = self.run_json()
        run["stages"]["candidates"] = {"kind": "deterministic", "status": "running", "started_at": "x",
                                       "finished_at": None, "inputs": {}, "outputs": {}, "command": None,
                                       "exit_code": None, "binding": {"inputs": {"x": "y"}, "outputs": None}}
        (self.work / "run.json").write_text(json.dumps(run), encoding="utf-8")
        (self.work / "candidates.json").write_text('{"schema_version": 2, "status": "ok", "candid', encoding="utf-8")
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.calls[-1], "candidates.py")
        self.assertEqual(self.statuses()["candidates"], "ok")
        # a valid pool cut from another transcript is stale, not adopted
        pool = json.loads((self.work / "candidates.json").read_text(encoding="utf-8"))
        pool["inputs"]["transcript_sha256"] = "0" * 64
        self.write("candidates.json", pool)
        self.assertEqual(self.wf("status"), 0)
        self.assertEqual(self.statuses()["candidates"], "stale")

    def test_visual_probe_contradiction_blocks_until_user_override(self):
        self.fake.probe = "contradicts"
        self.init()
        self.wf("run")
        self.write("chapters.json", [dict(c, needs_frames=False, visual_targets=[]) for c in CHAPTERS])
        self.wf("decide", "no-visuals", "--reason", "talking head only, static camera, no slides")
        self.assertEqual(self.wf("run"), 10)
        self.assertEqual(self.run_json()["blocker"]["stage"], "candidates")
        self.assertIn("--decided-by", self.fake.commands[-1])
        self.assertEqual(self.fake.commands[-1][self.fake.commands[-1].index("--decided-by") + 1], "model")
        self.assertEqual(self.wf("decide", "no-visuals", "--by", "user", "--reason",
                                 "the user confirms the video shows nothing informative"), 0)
        self.assertEqual(self.wf("run"), 0)
        self.assertEqual(self.fake.commands[-1][self.fake.commands[-1].index("--decided-by") + 1], "user")
        self.assertEqual(self.statuses()["candidates"], "ok")
        self.write("summary.json", SUMMARY)
        self.assertEqual(self.wf("run"), 0)
        code, out, _ = self.wf_out("verify", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["visual_probe"]["verdict"], "contradicts")
        self.assertTrue(any("user override" in w for w in report["rows"][0]["warnings"]))


    def test_run_json_is_one_object_with_reference_and_reports(self):
        self.init()
        code, out, _ = self.wf_out("run", "--json")
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result["reference"], "references/chapters.md")
        self.assertEqual(result["stages"]["chapters"], "awaiting_model")
        self.assertTrue((Path(result["reports_dir"]) / "transcript.md").is_file())
        self.assertNotIn("# transcript report", out)

    def test_reconfiguration_preserves_omitted_options(self):
        self.init("--pdf", "--whisper", "openai", "--wanted", "he,en")
        self.wf("init", "https://www.youtube.com/watch?v=vid", "--force", "--tier", "high")
        request = self.run_json()["request"]
        self.assertEqual((request["lang"], request["pdf"], request["whisper"], request["wanted"]),
                         ("en", True, "openai", "he,en"))
        self.wf("init", "https://www.youtube.com/watch?v=vid", "--force", "--no-pdf")
        self.assertFalse(self.run_json()["request"]["pdf"])

    def test_failed_child_without_artifact_is_not_called_again(self):
        self.init()
        with mock.patch.object(workflow, "_invoke", return_value=(14, "", "rate limited")) as invoke:
            self.assertEqual(self.wf("run"), 14)
            self.assertEqual(self.wf("run"), 14)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(self.wf("run", "--retry"), 14)
            self.assertEqual(invoke.call_count, 2)

    def test_partial_transcript_requires_bound_user_acceptance(self):
        self.init()
        self.wf("run")
        partial = json.loads((self.work / "transcript.json").read_text())
        partial.update(status="partial", failed_chunks=[{"range": {"start_s": 4, "end_s": 6}, "status": "temporary_network"}])
        self.write("transcript.json", partial)
        self.assertEqual(self.wf("run"), 15)
        self.assertNotEqual(self.wf("verify"), 0)
        self.assertNotEqual(self.wf("decide", "partial-transcript", "--reason", "accept the known missing range"), 0)
        self.assertEqual(self.wf("decide", "partial-transcript", "--by", "user", "--reason", "accept the known missing range"), 0)
        self.assertEqual(self.wf("run"), 0)
        accepted = json.loads((self.work / "transcript.json").read_text())
        self.assertTrue(gates.partial_accepted(accepted))
        accepted["failed_chunks"][0]["range"]["end_s"] = 8
        self.write("transcript.json", accepted)
        self.assertEqual(self.wf("run"), 15)

    def test_completed_run_json_makes_zero_child_calls(self):
        self.complete()
        self.fake.calls.clear()
        code, out, _ = self.wf_out("run", "--json")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["blocker"])
        self.assertEqual(self.fake.calls, [])

    def test_uncertain_retry_requires_explicit_retry_flag(self):
        self.init()
        self.assertNotEqual(self.wf("run", "--retry-uncertain"), 0)
        self.assertEqual(self.fake.calls, [])


    def test_new_source_does_not_inherit_upload_authorization(self):
        self.init("--whisper", "groq")
        self.wf("init", "https://youtu.be/other", "--force")
        self.assertIsNone(self.run_json()["request"]["whisper"])

    def test_configured_local_request_has_stable_backend_binding(self):
        with mock.patch.dict(os.environ, {"LOCAL_WHISPER_MODEL": str(self.root / "ggml-small.bin")}):
            self.init()
            self.assertEqual(self.run_json()["request"]["whisper"], "local")
            self.wf("init", "https://www.youtube.com/watch?v=vid", "--force", "--whisper", "local")
            self.assertEqual(self.run_json()["request"]["local_model"], str((self.root / "ggml-small.bin").resolve()))
            self.assertEqual(self.run_json()["request"]["local_model"], str((self.root / "ggml-small.bin").resolve()))
        self.wf("init", "https://www.youtube.com/watch?v=vid", "--force", "--whisper", "openai")
        self.assertEqual(self.run_json()["request"]["whisper"], "openai")
        self.assertIsNone(self.run_json()["request"]["local_model"])

if __name__ == "__main__":
    unittest.main()
