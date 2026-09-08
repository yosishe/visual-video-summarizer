"""Caption acquisition is bounded, cache-bound, and never rediscovers per track."""
from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import acquisition
import safety
import transcript


VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"
INFO = {
    "id": "abcdefghijk", "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
    "duration": 10, "language": "en", "subtitles": {
        "en": [{"ext": "vtt", "url": "https://captions.invalid/signed-one"}],
    }, "automatic_captions": {},
}


class CaptionAcquisitionTests(unittest.TestCase):
    def test_crlf_captions_survive_windows_text_writes_and_cache_reuse(self):
        content = VTT.replace("\n", "\r\n").encode("utf-8")
        temporary_file = tempfile.NamedTemporaryFile

        def windows_text_file(*args, **kwargs):
            # Reproduce Windows' default newline translation on every CI host.
            # An explicit newline policy in atomic_write must take precedence.
            if kwargs.get("mode") == "w":
                kwargs.setdefault("newline", "\r\n")
            return temporary_file(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(transcript, "_discover_info", return_value=INFO), \
                    mock.patch.object(transcript, "_fetch_caption_url", return_value=content) as fetch, \
                    mock.patch.object(safety.tempfile, "NamedTemporaryFile", side_effect=windows_text_file):
                first = transcript.fetch_captions("https://youtu.be/abcdefghijk", root / "one", None,
                                                  cache_dir=root / "cache")
                second = transcript.fetch_captions("https://youtu.be/abcdefghijk", root / "two", None,
                                                   cache_dir=root / "cache")
            expected = [{"start": 0.0, "end": 1.0, "text": "hello"}]
            self.assertEqual(first["segments"], expected)
            self.assertEqual(second["segments"], expected)
            self.assertTrue(second["cache_hit"])
            fetch.assert_called_once()
            cached_vtt = next((root / "cache" / "captions").glob("*.vtt"))
            self.assertEqual(cached_vtt.read_bytes(), content)

    def test_ranked_track_url_is_fetched_directly_after_one_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            discover = mock.Mock(return_value=INFO)
            fetch = mock.Mock(return_value=VTT.encode())
            with mock.patch.object(transcript, "_discover_info", discover), \
                    mock.patch.object(transcript, "_fetch_caption_url", fetch):
                result = transcript.fetch_captions(
                    "https://youtu.be/abcdefghijk", Path(tmp) / "run", None,
                    cache_dir=Path(tmp) / "cache")
            self.assertEqual(discover.call_count, 1)
            fetch.assert_called_once_with("https://captions.invalid/signed-one")
            self.assertEqual(result["track"]["key"], "en")
            self.assertEqual(result["segments"][0]["text"], "hello")

    def test_expired_signed_url_gets_one_explicit_inventory_refresh(self):
        refreshed = {**INFO, "subtitles": {"en": [{"ext": "vtt", "url": "https://captions.invalid/signed-two"}]}}
        fetch = mock.Mock(side_effect=[
            acquisition.AcquisitionError("expired_resource", "expired signed URL"),
            VTT.encode(),
        ])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(transcript, "_discover_info", side_effect=[INFO, refreshed]) as discover, \
                mock.patch.object(transcript, "_fetch_caption_url", fetch):
            result = transcript.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk",
                                               Path(tmp) / "run", None, cache_dir=Path(tmp) / "cache")
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(result["inventory_refreshed"])

    def test_rate_limit_is_typed_and_does_not_become_no_captions(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(transcript, "_discover_info", return_value=INFO), \
                mock.patch.object(transcript, "_fetch_caption_url",
                                  side_effect=acquisition.AcquisitionError("rate_limit", "limited")):
            with self.assertRaises(acquisition.AcquisitionError) as caught:
                transcript.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk",
                                          Path(tmp) / "run", None, cache_dir=Path(tmp) / "cache")
        self.assertEqual(caught.exception.category, "rate_limit")

    def test_caption_proxy_policy_denial_stops_without_retry_or_refresh(self):
        denied = transcript.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(transcript, "_discover_info", return_value=INFO) as discover, \
                mock.patch.object(transcript, "urlopen", side_effect=denied) as fetch, \
                mock.patch.object(acquisition.time, "sleep"):
            with self.assertRaises(acquisition.AcquisitionError) as caught:
                transcript.fetch_captions("https://youtu.be/abcdefghijk", Path(tmp) / "run", None)
        self.assertEqual(caught.exception.category, "environment_blocked")
        self.assertFalse(caught.exception.retryable)
        fetch.assert_called_once()
        discover.assert_called_once()

    def test_empty_valid_vtt_is_empty_response_not_absent_captions(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(transcript, "_discover_info", return_value=INFO), \
                mock.patch.object(transcript, "_fetch_caption_url", return_value=b"WEBVTT\n\n"):
            with self.assertRaises(acquisition.AcquisitionError) as caught:
                transcript.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk",
                                          Path(tmp) / "run", None, cache_dir=Path(tmp) / "cache")
            self.assertEqual(list((Path(tmp) / "cache" / "captions").glob("*.json")), [])
        self.assertEqual(caught.exception.category, "empty_response")

    def test_old_hash_bound_windows_corruption_is_reacquired_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            with mock.patch.object(transcript, "_discover_info", return_value=INFO), \
                    mock.patch.object(transcript, "_fetch_caption_url", return_value=VTT.encode()) as fetch:
                transcript.fetch_captions("https://youtu.be/abcdefghijk", root / "first", None, cache_dir=cache)
                caption = next((cache / "captions").glob("*.vtt"))
                receipt = caption.with_suffix(".json")
                data = json.loads(receipt.read_text())
                caption.write_bytes(VTT.replace("\n", "\r\r\n").encode())
                data["payload"]["file_sha256"] = acquisition.hash_file(caption)
                acquisition.write_cache(receipt, data["key"], data["payload"])
                second = transcript.fetch_captions("https://youtu.be/abcdefghijk", root / "second", None,
                                                  cache_dir=cache)
                third = transcript.fetch_captions("https://youtu.be/abcdefghijk", root / "third", None,
                                                 cache_dir=cache)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(second["segments"][0]["text"], "hello")
                self.assertTrue(third["cache_hit"])

    def test_inventory_schema_rejects_bad_duration_and_track_table(self):
        for bad in ({**INFO, "duration": float("nan")}, {**INFO, "subtitles": []},
                    {**INFO, "automatic_captions": {"en": {"url": "x"}}}):
            with self.subTest(bad=bad):
                with self.assertRaises(acquisition.AcquisitionError) as caught:
                    transcript._validate_inventory(bad)
                self.assertEqual(caught.exception.category, "malformed_response")

    def test_probe_uses_finite_process_deadline(self):
        result = __import__("subprocess").CompletedProcess([], 0,
            '{"format":{"duration":"1"},"streams":[{"codec_type":"audio"}]}', "")
        with mock.patch.object(transcript, "run_process", return_value=result) as run, \
                mock.patch.object(transcript, "require_tools"), \
                mock.patch.object(transcript, "run_text", side_effect=AssertionError("unbounded helper")):
            self.assertTrue(transcript.probe("video.mp4")["has_audio"])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)

    def test_canonical_alias_reuses_hash_verified_caption_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            discover = mock.Mock(return_value=INFO)
            fetch = mock.Mock(return_value=VTT.encode())
            with mock.patch.object(transcript, "_discover_info", discover), \
                    mock.patch.object(transcript, "_fetch_caption_url", fetch):
                transcript.fetch_captions("https://youtu.be/abcdefghijk", Path(tmp) / "one", None,
                                          cache_dir=cache)
                second = transcript.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk&t=10",
                                                   Path(tmp) / "two", None, cache_dir=cache)
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(second["cache_hit"])

    def test_discovery_never_adopts_a_stray_old_info_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            out.mkdir()
            (out / "video.info.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(transcript, "_run_ytdlp", return_value=0):
                with self.assertRaises(acquisition.AcquisitionError) as caught:
                    transcript._discover_info("https://www.youtube.com/watch?v=abcdefghijk", out,
                                              Path(tmp) / "cache")
            self.assertEqual(caught.exception.category, "malformed_response")

    def test_audio_download_is_single_fragment_bounded_and_receipted(self):
        calls = []

        def fake(args):
            calls.append(args)
            target = Path(args[args.index("-o") + 1].replace("%(ext)s", "m4a"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"audio-fixture")
            return 0

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(transcript, "_run_ytdlp", side_effect=fake):
            out, cache = Path(tmp) / "run", Path(tmp) / "cache"
            first = transcript.download_audio("https://youtu.be/abcdefghijk", out, cache_dir=cache)
            second = transcript.download_audio("https://www.youtube.com/watch?v=abcdefghijk", out,
                                               cache_dir=cache)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("-N", calls[0])
        self.assertNotIn("--ignore-errors", calls[0])
        self.assertEqual(calls[0][calls[0].index("-f") + 1], "ba/b")

    def test_audio_reuses_only_hash_validated_full_manifest_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "download"
            out.mkdir()
            media = Path(tmp) / "video.mp4"
            media.write_bytes(b"combined-media")
            manifest = {"schema_version": 2, "identity": {
                "source": "https://youtu.be/abcdefghijk", "sections": [], "exact_sections": False,
            }, "parts": [{"path": str(media), "sha256": acquisition.hash_file(media)}]}
            (out / "parts.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
            with mock.patch.object(transcript, "probe", return_value={"duration": 10, "has_audio": True}), \
                    mock.patch.object(transcript, "_run_ytdlp", side_effect=AssertionError("must reuse")):
                reused = transcript.download_audio("https://www.youtube.com/watch?v=abcdefghijk", out,
                                                   cache_dir=Path(tmp) / "cache")
        self.assertEqual(reused, media.resolve())


if __name__ == "__main__":
    unittest.main()
