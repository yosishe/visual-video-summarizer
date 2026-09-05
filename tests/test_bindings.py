"""The 1.8 bindings: an artifact says what it was made for, and the gate compares.

Hermetic (stdlib only): source identity canonicalisation, request options,
engine drift, the no-visuals contradiction, the recorded pixel gate, the
manifest and summary validators.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gates  # noqa: E402

CANON = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def transcript(**overrides) -> dict:
    payload = {"schema_version": 2, "status": "ok", "source": "captions", "source_identity": CANON,
               "inputs": {"whisper": None, "no_whisper": False, "langs": None, "wanted": None},
               "video": {"id": "dQw4w9WgXcQ", "duration": 12.0},
               "segments": [{"seg_id": f"seg_{i:04d}", "start": i * 2.0, "end": i * 2.0 + 2.0, "text": f"t {i}"}
                            for i in range(6)]}
    payload.update(overrides)
    return payload


def chapters(flags: tuple[bool, ...] = (False, True)) -> list[dict]:
    rows = []
    for index, needs in enumerate(flags):
        rows.append({"chapter_id": f"ch{index + 1:02d}", "title": f"Chapter {index + 1}", "start": index * 6.0,
                     "end": index * 6.0 + 6.0, "needs_frames": needs,
                     "visual_targets": [{"target_id": f"t{index}", "kind": "state", "seg_ids": [f"seg_{index * 3:04d}"]}]
                     if needs else []})
    return rows


def candidates(**overrides) -> dict:
    payload = {"schema_version": 2, "status": "ok", "tier": "standard",
               "inputs": {"source_identity": CANON, "video_id": "dQw4w9WgXcQ", "transcript_sha256": "t" * 64,
                          "chapters_sha256": "c" * 64,
                          "options": {"tier": "standard", "sections": None, "max_image_tokens": None,
                                      "allow_long": False}},
               "candidates": [{"candidate_id": "c_0000", "chapter_id": "ch02"}],
               "coverage": {"chapters": [{"chapter_id": "ch02", "status": "covered"}], "targets": []}}
    payload.update(overrides)
    return payload


class CanonicalSourceTests(unittest.TestCase):
    def test_youtube_variants_collapse_to_one_spelling(self):
        for variant in ("https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?t=120",
                        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120s&feature=share",
                        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
                        "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ",
                        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?si=abc",
                        "https://music.youtube.com/watch?v=dQw4w9WgXcQ#fragment"):
            self.assertEqual(gates.canonical_source(variant), CANON, variant)
        self.assertTrue(gates.identity_matches("https://youtu.be/dQw4w9WgXcQ", CANON))
        self.assertFalse(gates.identity_matches("https://youtu.be/AAAAAAAAAAA", CANON))

    def test_other_urls_lose_only_their_fragment_and_files_compare_by_stat(self):
        self.assertEqual(gates.canonical_source("https://vimeo.com/123?x=1#frag"), "https://vimeo.com/123?x=1")
        self.assertEqual(gates.canonical_source("not a url"), "not a url")
        self.assertEqual(gates.canonical_source(None), None)
        local = {"path": "/tmp/a.mp4", "size": 10, "mtime_ns": 5}
        self.assertTrue(gates.identity_matches(local, dict(local)))
        self.assertFalse(gates.identity_matches(local, dict(local, size=11)))
        self.assertFalse(gates.identity_matches(local, CANON))
        self.assertTrue(gates.identity_matches(None, CANON))
        self.assertEqual(gates.source_key(local), "/tmp/a.mp4")
        self.assertEqual(gates.source_key("https://youtu.be/dQw4w9WgXcQ"), CANON)
        self.assertIn("/tmp/a.mp4", gates.describe_identity(local))


class EngineDriftTests(unittest.TestCase):
    def test_levels(self):
        with mock.patch.object(gates, "ENGINE_VERSION", "1.8.0"):
            self.assertEqual(gates.engine_drift("1.8.0")[0], "same")
            self.assertEqual(gates.engine_drift("1.8.3")[0], "patch")
            self.assertEqual(gates.engine_drift("1.7.0")[0], "minor")
            self.assertEqual(gates.engine_drift("2.0.0")[0], "major")
            self.assertEqual(gates.engine_drift(None)[0], "unknown")
            self.assertEqual(gates.engine_drift("garbage")[0], "unknown")
            self.assertIn("re-run", gates.engine_drift("1.7.0")[1])
        # the running version is read at call time, not at import time
        with mock.patch.object(gates, "ENGINE_VERSION", "9.9.9"):
            self.assertEqual(gates.engine_drift("1.8.0")[0], "major")


class TranscriptBindingTests(unittest.TestCase):
    def test_identity_and_option_mismatches_are_stale(self):
        result = gates.validate_transcript(transcript(), expected_identity="https://youtu.be/AAAAAAAAAAA")
        self.assertTrue(result.info["stale"])
        self.assertTrue(any("different source" in e for e in result.errors))
        result = gates.validate_transcript(transcript(), expected_identity="https://youtu.be/dQw4w9WgXcQ",
                                           expected_options={"whisper": "groq", "no_whisper": False,
                                                             "langs": None, "wanted": None})
        self.assertEqual(len(result.info["stale"]), 1)
        self.assertIn("whisper: None -> 'groq'", result.info["stale"][0])
        # None / False / "" all mean "not requested"
        result = gates.validate_transcript(transcript(inputs={"whisper": "", "no_whisper": None, "langs": None,
                                                              "wanted": ""}),
                                           expected_options={"whisper": None, "no_whisper": False, "langs": None,
                                                             "wanted": None})
        self.assertEqual(result.info["stale"], [])
        self.assertTrue(result.ok)

    def test_no_transcript_with_changed_options_is_stale_before_failed(self):
        failed = transcript(status="no_transcript", segments=[], source=None,
                            source_detail={"kind": "none", "reason": "no captions"})
        result = gates.validate_transcript(failed, expected_options={"whisper": "groq", "no_whisper": False,
                                                                     "langs": None, "wanted": None})
        self.assertTrue(result.info["stale"])  # re-run with the new option, not "pass --retry"

    def test_legacy_transcript_without_bindings_only_warns(self):
        legacy = transcript()
        legacy.pop("source_identity")
        legacy.pop("inputs")
        result = gates.validate_transcript(legacy, expected_identity=CANON,
                                           expected_options={"whisper": None, "no_whisper": False, "langs": None,
                                                             "wanted": None})
        self.assertTrue(result.ok)
        self.assertEqual(result.info["stale"], [])
        self.assertTrue(any("predates source binding" in w for w in result.warnings))
        self.assertTrue(any("predates option binding" in w for w in result.warnings))


class DecisionContradictionTests(unittest.TestCase):
    def test_needs_frames_true_under_a_no_visuals_decision_is_an_error(self):
        result = gates.validate_chapters(chapters((False, True)), transcript(), 12.0, visual_decision="none")
        self.assertFalse(result.ok)
        self.assertTrue(any("ch02" in e and "decide illustrated" in e for e in result.errors))
        result = gates.validate_chapters(chapters((False, False)), transcript(), 12.0, visual_decision="none")
        self.assertTrue(result.ok)
        result = gates.validate_chapters(chapters((False, True)), transcript(), 12.0, visual_decision="illustrated")
        self.assertTrue(result.ok)


class CandidateBindingTests(unittest.TestCase):
    def test_tier_options_identity_and_video_id(self):
        base = dict(transcript_sha="t" * 64, chapters_sha="c" * 64)
        result = gates.validate_candidates(candidates(), **base, expected_options={"tier": "high"})
        self.assertTrue(any("tier standard" in e and "tier high" in e for e in result.info["stale"]))
        result = gates.validate_candidates(candidates(), **base,
                                           expected_options={"tier": "standard", "allow_long": True})
        self.assertTrue(any("allow_long: False -> True" in e for e in result.info["stale"]))
        result = gates.validate_candidates(candidates(), **base, expected_identity="https://youtu.be/AAAAAAAAAAA")
        self.assertTrue(any("different source" in e for e in result.info["stale"]))
        result = gates.validate_candidates(candidates(), **base, expected_video_id="other")
        self.assertTrue(any("belongs to video" in e for e in result.info["stale"]))
        result = gates.validate_candidates(candidates(), **base, expected_identity="https://youtu.be/dQw4w9WgXcQ",
                                           expected_video_id="dQw4w9WgXcQ",
                                           expected_options={"tier": "standard", "sections": None,
                                                             "max_image_tokens": None, "allow_long": False})
        self.assertTrue(result.ok)

    def test_pool_without_options_block_warns_but_tier_is_still_compared(self):
        payload = candidates()
        payload["inputs"].pop("options")
        result = gates.validate_candidates(payload, expected_options={"tier": "high", "allow_long": False})
        self.assertTrue(any("tier" in e for e in result.info["stale"]))
        self.assertTrue(any("predates option binding" in w for w in result.warnings))


class AssetVerificationTests(unittest.TestCase):
    def test_recorded_pixel_gate_over_threshold_is_an_error(self):
        def payload(changed: float) -> dict:
            return {"assets": [{"candidate_id": "c_0000", "verification": {
                "luma_mad": 0.4, "edge_mad": 0.6, "changed_ratio": changed,
                "thresholds": {"luma": 3.0, "edge": 4.0, "changed": 0.025}}}],
                "failures": [], "duplicate_pairs": []}
        good = gates.validate_assets(payload(0.002), check_files=False)
        self.assertTrue(good.ok)
        self.assertEqual(good.info["verification_worst"]["changed_ratio"], 0.002)
        bad = gates.validate_assets(payload(0.031), check_files=False)
        self.assertTrue(any("recorded pixel gate failed" in e for e in bad.errors))
        legacy = gates.validate_assets({"assets": [{"candidate_id": "c_0000"}], "failures": [], "duplicate_pairs": []},
                                       check_files=False)
        self.assertTrue(legacy.ok)


class ManifestAndSummaryGateTests(unittest.TestCase):
    def test_manifest_binds_hashes_language_tier_and_bundle(self):
        manifest = {"frames_count": 1, "summary_sha256": "s", "lang": "he", "tier": "standard",
                    "output_mode": "illustrated", "visual_content": "illustrated", "bundle_sha256": "b" * 64}
        expected = {"summary_sha256": "s", "lang": "he", "tier": "standard", "output_mode": "illustrated",
                    "visual_content": "illustrated"}
        self.assertTrue(gates.validate_manifest(manifest, expected=expected, bundle_sha="b" * 64).ok)
        stale = gates.validate_manifest(manifest, expected=dict(expected, lang="en"), bundle_sha="b" * 64)
        self.assertTrue(any("language changed" in e for e in stale.info["stale"]))
        stale = gates.validate_manifest(manifest, expected=expected, bundle_sha="x" * 64)
        self.assertTrue(any("bundle does not match" in e for e in stale.info["stale"]))
        stale = gates.validate_manifest(manifest, expected=dict(expected, summary_sha256="other"))
        self.assertTrue(any("summary_sha256 changed" in e for e in stale.info["stale"]))
        legacy = gates.validate_manifest(dict(manifest, bundle_sha256=None), expected=expected, bundle_sha="b" * 64)
        self.assertTrue(legacy.ok)
        self.assertTrue(any("predates bundle binding" in w for w in legacy.warnings))
        self.assertFalse(legacy.info["bundle_bound"])
        old = gates.validate_manifest({"summary_sha256": "s"}, expected=expected)
        self.assertTrue(any("predates the workflow" in e for e in old.info["stale"]))

    def test_summary_shape_and_language(self):
        self.assertFalse(gates.validate_summary({"overview": "x"}).ok)
        self.assertFalse(gates.validate_summary([], lang="he").ok)
        ok = gates.validate_summary({"overview": "x", "chapters": [], "lang": "he"}, lang="he")
        self.assertTrue(ok.ok)
        mismatch = gates.validate_summary({"overview": "x", "chapters": [], "lang": "en"}, lang="he")
        self.assertTrue(any("declares lang 'en'" in e for e in mismatch.errors))
        undeclared = gates.validate_summary({"overview": "x", "chapters": []}, lang="he")
        self.assertTrue(undeclared.ok)
        self.assertTrue(undeclared.warnings)


if __name__ == "__main__":
    unittest.main()
