import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquisition as a


class PolicyTests(unittest.TestCase):
    def test_permanent_auth_stops_after_one_call(self):
        call = mock.Mock(side_effect=a.http_failure(401))
        with self.assertRaises(a.AcquisitionError):
            a.retry_call("transcribe", call, provider="openai", sleep=mock.Mock())
        self.assertEqual(call.call_count, 1)

    def test_three_total_transient_attempts_without_nested_retries(self):
        call = mock.Mock(side_effect=a.http_failure(503))
        sleep = mock.Mock()
        with self.assertRaises(a.AcquisitionError) as caught:
            a.retry_call("transcribe", call, provider="groq", sleep=sleep)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_provider_quota_is_not_rate_limit(self):
        err = a.http_failure(429, body=b'{"error":{"type":"insufficient_quota","message":"SECRET"}}')
        self.assertEqual(err.category, "quota_exceeded")
        self.assertFalse(err.retryable)
        self.assertNotIn("SECRET", str(err))

    def test_retry_after_seconds_dates_zero_and_invalid(self):
        now = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(a.retry_after({"Retry-After": "Tue, 08 Sep 2026 00:00:10 GMT"}, now=now), 10)
        self.assertEqual(a.retry_after({"Retry-After": "0"}), 0)
        self.assertEqual(a.retry_after({"Retry-After-Ms": "500"}), .5)
        for value in ("NaN", "inf", "nonsense"):
            self.assertIsNone(a.retry_after({"Retry-After": value}))

    def test_long_delay_defers_and_survives_resume_without_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            call = mock.Mock(side_effect=a.http_failure(429, {"Retry-After": "120"}))
            sleep = mock.Mock()
            with self.assertRaises(a.AcquisitionError) as caught:
                a.retry_call("transcribe", call, provider="groq", work=work, sleep=sleep)
            self.assertIsNotNone(caught.exception.next_retry_at)
            with self.assertRaises(a.AcquisitionError):
                a.retry_call("transcribe", call, provider="groq", work=work, sleep=sleep)
            self.assertEqual(call.call_count, 1)
            sleep.assert_not_called()

    def test_uncertain_upload_is_not_repeated(self):
        call = mock.Mock(side_effect=a.AcquisitionError("outcome_unknown", "Response lost", uncertain=True,
                                                       retryable=True))
        with self.assertRaises(a.AcquisitionError):
            a.retry_call("transcribe", call, provider="openai")
        self.assertEqual(call.call_count, 1)

    def test_rate_limited_captions_are_not_absent_captions(self):
        self.assertEqual(a.classify_tool_failure("HTTP Error 429").category, "rate_limit")
        self.assertEqual(a.classify_tool_failure("PO Token needed").category, "access_restricted")
        self.assertFalse(a.classify_tool_failure("strange failure").retryable)

    def test_proxy_policy_denial_is_distinct_from_youtube_access_restriction(self):
        for message in ("Tunnel connection failed: 403 Forbidden",
                        "HTTPS proxy denied access by network policy"):
            error = a.classify_tool_failure(message)
            self.assertEqual(error.category, "environment_blocked")
            self.assertFalse(error.retryable)
        self.assertEqual(a.classify_tool_failure("HTTP Error 403: Forbidden").category, "access_restricted")

    def test_success_and_failure_attempt_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            call = mock.Mock(side_effect=[a.http_failure(503), {"ok": True}])
            self.assertEqual(a.retry_call("transcribe", call, provider="groq", work=Path(tmp), sleep=mock.Mock()),
                             {"ok": True})
            rows = [json.loads(line) for line in (Path(tmp) / "operations.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row.get("api_attempts", 0) for row in rows), 2)


class CacheTests(unittest.TestCase):
    def test_hash_bound_cache_rejects_modified_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            a.write_cache(path, "key", {"segments": [1]})
            self.assertEqual(a.read_cache(path, "key"), {"segments": [1]})
            self.assertIsNone(a.read_cache(path, "other"))
            data = json.loads(path.read_text())
            data["payload"]["segments"] = [2]
            path.write_text(json.dumps(data))
            self.assertIsNone(a.read_cache(path, "key"))

    def test_competing_writer_refused_and_lock_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            with a.file_lock(path):
                with self.assertRaises(a.AcquisitionError):
                    with a.file_lock(path):
                        self.fail("second writer acquired lock")
            with a.file_lock(path):
                pass

    def test_timed_out_process_is_terminated(self):
        with self.assertRaises(a.AcquisitionError):
            a.run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=.1)


    @unittest.skipIf(os.name == "nt", "Windows uses native taskkill /T")
    def test_nested_timeout_kills_separate_session_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "leaf.pid"
            leaf = ("import os, signal, time; from pathlib import Path; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(120)")
            child = ("import sys; sys.path.insert(0, " + repr(str(Path(a.__file__).parent)) + "); "
                     "import acquisition; acquisition.run_process([sys.executable, '-c', " + repr(leaf) + "], timeout=90)")
            try:
                with self.assertRaises(a.AcquisitionError):
                    a.run_process([sys.executable, "-c", child], timeout=1)
                self.assertTrue(pid_file.is_file())
                pid = int(pid_file.read_text())
                result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
                self.assertTrue(result.returncode != 0 or result.stdout.strip().startswith("Z"), result.stdout)
            finally:
                if pid_file.is_file():
                    try: os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError: pass

if __name__ == "__main__":
    unittest.main()
