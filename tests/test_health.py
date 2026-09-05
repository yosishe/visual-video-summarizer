"""Transcript health (1.8): the signals a summary's limitations are stated from."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gates  # noqa: E402


def segments(count: int, step: float = 2.0, text: str = "words spoken here") -> list[dict]:
    return [{"seg_id": f"seg_{i:04d}", "start": i * step, "end": i * step + step, "text": f"{text} {i}"}
            for i in range(count)]


class HealthTests(unittest.TestCase):
    def test_empty_text_and_sparse_segments_make_a_thin_transcript(self):
        rows = segments(5, step=120.0)
        for row in rows[:3]:
            row["text"] = "  "
        health = gates.transcript_health(rows, 600.0)
        self.assertEqual(health["empty_text_ratio"], 0.6)
        self.assertEqual(health["segments_per_minute"], 0.5)
        self.assertIn("empty_text", health["flags"])
        self.assertIn("sparse_segments", health["flags"])
        self.assertEqual(health["status"], "thin")
        dense = gates.transcript_health(segments(300), 600.0)
        self.assertEqual(dense["status"], "ok")
        self.assertEqual(dense["flags"], [])

    def test_missing_duration_is_a_warning_not_silence(self):
        health = gates.transcript_health(segments(30), None)
        self.assertIsNone(health["coverage_ratio"])
        self.assertIn("no_duration", health["flags"])
        self.assertTrue(any("duration" in w for w in health["warnings"]))
        self.assertEqual(health["span_seconds"], 60.0)

    def test_provenance_and_language_rules(self):
        auto_wrong = gates.transcript_health(segments(300), 600.0, source="captions",
                                             source_detail={"kind": "captions", "track": "en", "manual": False},
                                             language="en", video_language="he")
        self.assertFalse(auto_wrong["provenance"]["language_match"])
        self.assertIn("language_mismatch", auto_wrong["flags"])
        self.assertTrue(any("not in the video's language" in w for w in auto_wrong["warnings"]))
        self.assertEqual(auto_wrong["status"], "ok")  # a smell, not thinness
        manual = gates.transcript_health(segments(300), 600.0, source="captions",
                                         source_detail={"kind": "captions", "track": "en", "manual": True},
                                         language="en", video_language="iw")
        self.assertFalse(manual["provenance"]["language_match"])
        self.assertIn("language_mismatch", manual["flags"])
        self.assertFalse(any("language" in w for w in manual["warnings"]))  # a human translation: recorded, not warned
        same = gates.transcript_health(segments(300), 600.0, language="he", video_language="iw")
        self.assertTrue(same["provenance"]["language_match"])
        translated = gates.transcript_health(segments(300), 600.0, source="captions",
                                             source_detail={"kind": "captions", "track": "en-de", "translated": True})
        self.assertIn("translated", translated["flags"])
        self.assertEqual(translated["status"], "thin")
        whisper = gates.transcript_health(segments(300), 600.0, source="whisper (groq)",
                                          source_detail={"kind": "whisper", "backend": "groq"})
        self.assertEqual(whisper["provenance"]["kind"], "whisper")

    def test_validate_transcript_upgrades_a_1_7_health_record_and_keeps_its_notes(self):
        payload = {"status": "ok", "source": "captions", "language": "en",
                   "video": {"id": "v", "duration": 600.0, "language": "en"}, "segments": segments(300),
                   "health": {"segments": 300, "warnings": ["caption cues were re-sorted into time order"],
                              "whisper": {"backend": "groq", "chunks_failed": 0}}}
        result = gates.validate_transcript(payload)
        health = result.info["health"]
        self.assertEqual(health["status"], "ok")
        self.assertIn("provenance", health)
        self.assertEqual(health["whisper"]["backend"], "groq")
        self.assertIn("caption cues were re-sorted into time order", health["warnings"])
        self.assertIn("caption cues were re-sorted into time order", result.warnings)

    def test_health_summary_is_one_line(self):
        health = gates.transcript_health(segments(300), 600.0, source="captions",
                                         source_detail={"kind": "captions", "track": "en", "manual": True,
                                                        "original": True}, language="en", video_language="en")
        line = gates.health_summary(health)
        self.assertNotIn("\n", line)
        self.assertIn("captions en (manual, original)", line)
        self.assertIn("300 segments", line)
        self.assertIn("coverage 100%", line)
        self.assertIn("health ok", line)
        thin = gates.transcript_health(segments(10, step=2.0), 600.0)
        self.assertIn("health thin", gates.health_summary(thin))
        self.assertEqual(gates.health_summary(None), "health unknown")


if __name__ == "__main__":
    unittest.main()
