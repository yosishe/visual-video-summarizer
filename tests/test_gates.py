"""Stage gates: the fail-closed rules that used to live only in SKILL.md prose."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gates  # noqa: E402
import hostenv  # noqa: E402


def transcript(count: int = 6, duration: float = 12.0) -> dict:
    return {
        "source": "captions", "status": "ok",
        "video": {"id": "vid", "duration": duration},
        "segments": [
            {"seg_id": f"seg_{i:04d}", "start": i * 2.0, "end": i * 2.0 + 2.0, "text": f"segment {i} says value {i * 7}"}
            for i in range(count)
        ],
    }


def chapters(needs: tuple[bool, ...] = (False, True), targets: bool = True) -> list[dict]:
    rows = []
    for index, flag in enumerate(needs):
        row = {"chapter_id": f"ch{index + 1:02d}", "title": f"Chapter {index + 1}",
               "start": index * 6.0, "end": index * 6.0 + 6.0, "needs_frames": flag}
        if flag and targets:
            row["visual_targets"] = [{"target_id": f"t{index}", "kind": "state",
                                      "seg_ids": [f"seg_{index * 3 + 1:04d}"], "why": "on screen"}]
        rows.append(row)
    return rows


def candidates_payload(*, status: str = "ok", n: int = 2, unresolved: bool = False, inputs: dict | None = None) -> dict:
    payload = {
        "schema_version": 2, "status": status, "tier": "standard",
        "candidates": [{"candidate_id": f"c_{i:04d}", "chapter_id": "ch02", "actual_t": 6.5 + i,
                        "seg_ids": ["seg_0003"], "aligned_seg_ids": ["seg_0003", "seg_0004"]} for i in range(n)],
        "coverage": {"chapters": [{"chapter_id": "ch01", "status": "not-required", "candidate_ids": []},
                                  {"chapter_id": "ch02", "status": "unresolved" if unresolved else "covered",
                                   "candidate_ids": []}],
                     "targets": []},
    }
    if inputs:
        payload["inputs"] = inputs
    return payload


def selection(**overrides) -> dict:
    row = {"candidate_id": "c_0000", "name": "ch02_state", "chapter_id": "ch02", "role": "evidence",
           "caption": {"shows": "The panel.", "why": "proves the state."}, "alt": "a panel",
           "anchor_seg_ids": ["seg_0003"]}
    row.update(overrides)
    return row


class TranscriptGateTests(unittest.TestCase):
    def test_empty_segments_is_error(self):
        result = gates.validate_transcript({"status": "ok", "segments": []})
        self.assertFalse(result.ok)
        self.assertIn("zero segments", result.errors[0])

    def test_no_transcript_status_is_error_even_with_legacy_shape(self):
        result = gates.validate_transcript({"status": "no_transcript", "segments": [], "source": None})
        self.assertTrue(any("no_transcript" in error for error in result.errors))

    def test_missing_file_is_gate_error_with_exit_10(self):
        with self.assertRaises(gates.GateError) as ctx:
            gates.load_json(Path(tempfile.gettempdir()) / "definitely-missing-vsum.json", "transcript.json")
        self.assertEqual(ctx.exception.code, 10)

    def test_legacy_transcript_without_status_is_accepted(self):
        payload = transcript()
        del payload["status"]
        self.assertTrue(gates.validate_transcript(payload).ok)

    def test_non_monotonic_and_negative_duration_are_errors(self):
        payload = transcript()
        payload["segments"][2]["start"] = 0.5
        payload["segments"][3]["end"] = 1.0
        result = gates.validate_transcript(payload)
        self.assertTrue(any("chronological" in error for error in result.errors))
        self.assertTrue(any("ends before" in error for error in result.errors))

    def test_health_reports_coverage_gaps_and_repetition(self):
        segments = [{"seg_id": "a", "start": 0, "end": 2, "text": "hello there"},
                    {"seg_id": "b", "start": 2, "end": 4, "text": "hello there"},
                    {"seg_id": "c", "start": 200, "end": 202, "text": "hello there"}]
        health = gates.transcript_health(segments, 600)
        self.assertEqual(health["segments"], 3)
        self.assertLess(health["coverage_ratio"], 0.05)
        self.assertGreater(health["largest_gap_s"], 190)
        self.assertGreater(health["repetition_ratio"], 0.5)
        self.assertTrue(any("cover" in w for w in health["warnings"]))
        self.assertTrue(any("gap" in w for w in health["warnings"]))
        self.assertTrue(any("repeat" in w for w in health["warnings"]))

    def test_health_is_attached_as_info_and_warnings(self):
        payload = transcript(count=2, duration=600)
        result = gates.validate_transcript(payload)
        self.assertTrue(result.ok)
        self.assertIn("health", result.info)
        self.assertTrue(result.warnings)


class ChapterGateTests(unittest.TestCase):
    def test_empty_array_is_error(self):
        result = gates.validate_chapters([], transcript())
        self.assertIn("empty", result.errors[0])

    def test_needs_frames_must_be_json_boolean(self):
        for value in ("MISSING", None, "false", 0, 1):
            with self.subTest(value=value):
                rows = chapters()
                if value == "MISSING":
                    del rows[1]["needs_frames"]
                else:
                    rows[1]["needs_frames"] = value
                result = gates.validate_chapters(rows, transcript())
                self.assertTrue(any("needs_frames must be true or false" in e for e in result.errors), result.errors)

    def test_dangling_seg_ids_are_listed_per_target(self):
        rows = chapters()
        rows[1]["visual_targets"][0]["seg_ids"] = ["seg_9999", "seg_0003"]
        rows[1]["visual_targets"][0]["action_seg_id"] = "seg_8888"
        result = gates.validate_chapters(rows, transcript())
        self.assertFalse(result.ok)
        self.assertTrue(any("t1" in e and "seg_9999" in e and "seg_8888" in e for e in result.errors), result.errors)

    def test_seg_ids_as_string_is_error_not_characters(self):
        rows = chapters()
        rows[1]["visual_targets"][0]["seg_ids"] = "seg_0003"
        result = gates.validate_chapters(rows, transcript())
        self.assertTrue(any("must be an array" in e for e in result.errors))

    def test_unsorted_and_overlapping_are_errors(self):
        rows = chapters((True, True))
        rows[1]["start"] = 2.0
        result = gates.validate_chapters(rows, transcript())
        self.assertTrue(any("overlaps" in e for e in result.errors))
        swapped = list(reversed(chapters((True, True))))
        result = gates.validate_chapters(swapped, transcript())
        self.assertTrue(any("chronological" in e for e in result.errors))

    def test_all_false_requires_an_explicit_no_visuals_decision(self):
        rows = chapters((False, False))
        illustrated = gates.validate_chapters(rows, transcript())
        self.assertTrue(any("no-visuals" in e for e in illustrated.errors), illustrated.errors)
        decided = gates.validate_chapters(rows, transcript(), visual_decision="none")
        self.assertTrue(decided.ok, decided.errors)

    def test_duplicate_ids_and_bad_kind(self):
        rows = chapters((True, True))
        rows[1]["chapter_id"] = "ch01"
        rows[1]["visual_targets"][0]["target_id"] = "t0"
        rows[1]["visual_targets"][0]["kind"] = "photo"
        result = gates.validate_chapters(rows, transcript())
        self.assertTrue(any("duplicate chapter_id" in e for e in result.errors))
        self.assertTrue(any("duplicate target_id" in e for e in result.errors))
        self.assertTrue(any("unsupported kind" in e for e in result.errors))

    def test_last_chapter_beyond_duration(self):
        rows = chapters()
        rows[1]["end"] = 100.0
        result = gates.validate_chapters(rows, transcript(), duration=12.0)
        self.assertTrue(any("after the video" in e for e in result.errors))

    def test_valid_chapters_pass_with_counts(self):
        result = gates.validate_chapters(chapters(), transcript(), duration=12.0)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.info["needs_frames_chapters"], 1)


class CandidateGateTests(unittest.TestCase):
    def test_digest_ignores_shortlist_receipt(self):
        payload = candidates_payload()
        before = gates.candidates_digest(payload)
        payload["shortlist"] = {"written": [{"candidate_id": "c_0000"}]}
        payload["shortlist_history"] = [{}]
        self.assertEqual(before, gates.candidates_digest(payload))
        payload["candidates"].pop()
        self.assertNotEqual(before, gates.candidates_digest(payload))

    def test_unresolved_required_fails_unless_allowed(self):
        payload = candidates_payload(unresolved=True)
        self.assertFalse(gates.validate_candidates(payload).ok)
        allowed = gates.validate_candidates(payload, allow_unresolved=True)
        self.assertTrue(allowed.ok)
        self.assertTrue(allowed.warnings)

    def test_zero_candidates_under_illustrated_intent_fails(self):
        payload = candidates_payload(n=0)
        self.assertFalse(gates.validate_candidates(payload).ok)
        self.assertTrue(gates.validate_candidates(payload, visual_decision="none").ok)

    def test_no_visual_chapters_status_needs_decision(self):
        payload = candidates_payload(status="no_visual_chapters", n=0)
        self.assertFalse(gates.validate_candidates(payload).ok)
        self.assertTrue(gates.validate_candidates(payload, visual_decision="none").ok)

    def test_input_hash_mismatch_is_stale(self):
        payload = candidates_payload(inputs={"transcript_sha256": "aaa", "chapters_sha256": "bbb"})
        result = gates.validate_candidates(payload, transcript_sha="aaa", chapters_sha="ccc")
        self.assertTrue(result.info["stale"])
        self.assertFalse(result.ok)
        fresh = gates.validate_candidates(payload, transcript_sha="aaa", chapters_sha="bbb")
        self.assertTrue(fresh.ok)

    def test_legacy_manifest_without_inputs_warns(self):
        result = gates.validate_candidates(candidates_payload(), transcript_sha="x", chapters_sha="y")
        self.assertTrue(result.ok)
        self.assertTrue(any("predates" in w for w in result.warnings))


class SelectionGateTests(unittest.TestCase):
    def test_empty_is_error_for_illustrated(self):
        self.assertFalse(gates.validate_selections([], candidates_payload()).ok)
        self.assertTrue(gates.validate_selections([], candidates_payload(), require_non_empty=False).ok)

    def test_unknown_duplicate_overfull_and_bad_name(self):
        rows = [selection(), selection(name="dup name!"), selection(candidate_id="c_9999", name="x9")]
        result = gates.validate_selections(rows, candidates_payload())
        self.assertTrue(any("selected twice" in e for e in result.errors))
        self.assertTrue(any("letters, digits" in e for e in result.errors))
        self.assertTrue(any("c_9999" in e for e in result.errors))
        too_many = [selection(candidate_id=f"c_{i:04d}", name=f"n{i}") for i in range(4)]
        result = gates.validate_selections(too_many, candidates_payload(n=4))
        self.assertTrue(any("more than 3 frames" in e for e in result.errors))

    def test_chapter_mismatch_and_anchor_provenance(self):
        result = gates.validate_selections([selection(chapter_id="ch01")], candidates_payload())
        self.assertTrue(any("differs" in e for e in result.errors))
        result = gates.validate_selections([selection(anchor_seg_ids=["seg_0000"])], candidates_payload())
        self.assertTrue(any("provenance" in e for e in result.errors))

    def test_hebrew_caption_required_when_he(self):
        result = gates.validate_selections([selection()], candidates_payload(), lang="he")
        self.assertTrue(any("Hebrew" in e for e in result.errors))
        hebrew = selection(caption={"shows": "הלוח המלא.", "why": "מוכיח."})
        self.assertTrue(gates.validate_selections([hebrew], candidates_payload(), lang="he").ok)

    def test_unshortlisted_is_a_warning(self):
        receipt = {"written": [{"candidate_id": "c_0001"}]}
        result = gates.validate_selections([selection()], candidates_payload(), shortlist_receipt=receipt)
        self.assertTrue(result.ok)
        self.assertTrue(any("shortlist" in w for w in result.warnings))

    def test_build_stage_needs_why(self):
        row = selection(novelty="build_stage", caption={"shows": "board"})
        result = gates.validate_selections([row], candidates_payload())
        self.assertTrue(any("build_stage" in e for e in result.errors))


class AssetGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-gates-")
        self.root = Path(self.temporary.name)
        self.full = self.root / "a-full.jpg"
        self.thumb = self.root / "a-thumb.jpg"
        self.full.write_bytes(b"full")
        self.thumb.write_bytes(b"thumb")

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, **extra) -> dict:
        payload = {"assets": [{"candidate_id": "c_0000",
                               "full": {"path": str(self.full), "sha256": gates.sha256_file(self.full)},
                               "thumb": {"path": str(self.thumb), "sha256": gates.sha256_file(self.thumb)}}],
                   "failures": [], "duplicate_pairs": []}
        payload.update(extra)
        return payload

    def test_bound_hashes_detect_stale_selections_and_candidates(self):
        payload = self.manifest(selections_sha256="s1", candidates_sha256="c1")
        self.assertTrue(gates.validate_assets(payload, [selection()], selections_sha="s1", candidates_sha="c1").ok)
        stale = gates.validate_assets(payload, [selection()], selections_sha="s2", candidates_sha="c1")
        self.assertTrue(stale.info["stale"])
        self.assertFalse(stale.ok)

    def test_file_hash_mismatch_and_missing_selection_asset(self):
        payload = self.manifest()
        self.full.write_bytes(b"tampered")
        result = gates.validate_assets(payload, [selection(), selection(candidate_id="c_0001", name="b")])
        self.assertTrue(any("does not match" in e for e in result.errors))
        self.assertTrue(any("c_0001" in e for e in result.errors))

    def test_missing_recorded_sha_is_an_error_not_a_skip(self):
        payload = self.manifest()
        del payload["assets"][0]["full"]["sha256"]
        result = gates.validate_assets(payload, [selection()])
        self.assertTrue(any("no recorded sha256" in e for e in result.errors))


class HostEnvTests(unittest.TestCase):
    def test_install_hint_is_platform_neutral(self):
        hint = hostenv.install_hint("ffmpeg")
        self.assertIn("ffmpeg", hint)
        self.assertIn("after the user approves", hint)
        if hostenv.platform_key() != "darwin":
            self.assertNotIn("brew", hint)

    def test_chrome_candidates_are_absolute_or_names(self):
        for candidate in hostenv.chrome_candidates():
            self.assertTrue(Path(candidate).is_absolute() or "/" not in candidate)

    def test_child_env_forces_utf8(self):
        env = hostenv.child_env({"X": "1"})
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["X"], "1")

    def test_gate_result_raise_prefixes_message(self):
        result = gates.GateResult(errors=["bad thing"])
        with self.assertRaises(gates.GateError) as ctx:
            result.raise_for_errors("chapters.json")
        self.assertEqual(ctx.exception.code, 10)
        self.assertIn("chapters.json: bad thing", ctx.exception.message)
        with self.assertRaises(gates.StaleError) as ctx:
            result.raise_for_errors("assets", stale=True)
        self.assertEqual(ctx.exception.code, 11)


if __name__ == "__main__":
    unittest.main()
