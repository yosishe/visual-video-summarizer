"""Bounded acquisition, receipts and cache primitives. No credential discovery."""
from __future__ import annotations

import contextlib
import datetime as dt
import email.utils
import hashlib
import json
import math
import os
import random
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, TypeVar

from safety import atomic_write, sanitize_tool_output

MAX_ATTEMPTS = 3
WAIT_BUDGET = 60.0
DISCOVERY_TIMEOUT = 120
MEDIA_TIMEOUT = 1800
UPLOAD_TIMEOUT = 300
EXIT_ACQUISITION = 14
EXIT_PARTIAL = 15
T = TypeVar("T")


class AcquisitionError(SystemExit):
    """A safe, typed failure; SystemExit compatibility with the stage CLIs."""

    def __init__(self, category: str, message: str, *, retryable: bool = False,
                 retry_after: float | None = None, uncertain: bool = False):
        self.category = category
        self.message = sanitize_tool_output(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.uncertain = uncertain
        self.attempts = 0
        self.next_retry_at: float | None = None
        self.exit_code = 13 if category == "source_unavailable" else EXIT_ACQUISITION
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {"category": self.category, "message": self.message, "retryable": self.retryable,
                "exit_code": self.exit_code,
                "outcome": "deferred" if self.next_retry_at else "uncertain" if self.uncertain else "failed",
                "uncertain": self.uncertain, "attempts": self.attempts,
                "next_retry_at": self.next_retry_at, "retry_after": self.retry_after}


def retry_after(headers, *, now: float | None = None) -> float | None:
    """RFC 9110 seconds/HTTP-date, plus the provider millisecond extension."""
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    value = headers.get("retry-after")
    try:
        if "retry-after-ms" in headers:
            delay = float(headers["retry-after-ms"]) / 1000
        elif value is None:
            return None
        else:
            try:
                delay = float(value)
            except ValueError:
                parsed = email.utils.parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                delay = parsed.timestamp() - (time.time() if now is None else now)
        return max(0.0, delay) if math.isfinite(delay) else None
    except (TypeError, ValueError, OverflowError):
        return None


def http_failure(status: int, headers=None, body: bytes | str = b"") -> AcquisitionError:
    """Inspect only recognized provider codes; never echo a provider body."""
    code = kind = ""
    try:
        payload = json.loads(body[:65536])
        detail = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(detail, dict):
            code, kind = str(detail.get("code") or "").lower(), str(detail.get("type") or "").lower()
    except (ValueError, TypeError, UnicodeError):
        pass
    quota = ("quota", "billing", "credit", "spend", "usage_limit")
    if status == 429 and any(term in code or term in kind for term in quota):
        return AcquisitionError("quota_exceeded", "Provider quota or billing limit reached (HTTP 429)")
    if status == 429:
        return AcquisitionError("rate_limit", "Provider rate limit (HTTP 429)", retryable=True,
                                retry_after=retry_after(headers))
    if status == 401:
        return AcquisitionError("authentication", "Authentication failed (HTTP 401)")
    if status == 403:
        return AcquisitionError("authorization", "Access was refused (HTTP 403)")
    if status in (404, 410):
        return AcquisitionError("source_unavailable", f"Resource unavailable (HTTP {status})")
    if status == 408 or 500 <= status <= 599:
        return AcquisitionError("temporary_network", f"Temporary upstream failure (HTTP {status})", retryable=True,
                                retry_after=retry_after(headers))
    if status == 413:
        return AcquisitionError("invalid_input", "Upload exceeds the provider limit (HTTP 413)")
    return AcquisitionError("invalid_input", f"Request rejected (HTTP {status})")


def proxy_policy_failure(detail: object) -> AcquisitionError | None:
    """Recognize an explicit proxy denial without exposing exception contents."""
    value = str(detail).lower()
    if ("proxy" in value or "tunnel connection failed" in value) and any(
            marker in value for marker in ("403", "denied", "blocked by", "network policy")):
        return AcquisitionError("environment_blocked", "This environment's network proxy denied source access")
    return None


def classify_tool_failure(stderr: str, returncode: int = 1) -> AcquisitionError:
    """Conservative recognition of downloader diagnostics; unknown errors stop."""
    value = str(stderr).lower()
    denied = proxy_policy_failure(stderr)
    if denied is not None:
        return denied
    if "429" in value or "too many requests" in value:
        return AcquisitionError("rate_limit", "Source rate limit; retry only after cooldown", retryable=True,
                                retry_after=60.0)
    if any(s in value for s in ("sign in", "login required", "po token", "po_token", "captcha", "403",
                                "not a bot", "blocked")):
        return AcquisitionError("access_restricted", "Source access restricted; do not switch downloaders or bypass it")
    if any(s in value for s in ("private video", "video unavailable", "video has been removed", "not available",
                                "404", "410", "geo-restricted", "country")):
        return AcquisitionError("source_unavailable", "Source unavailable, private, removed or region restricted")
    if any(s in value for s in ("timed out", "timeout", "connection reset", "temporary failure", "503", "502", "504",
                                "unable to resolve", "name or service not known")):
        return AcquisitionError("temporary_network", "Temporary source/network failure", retryable=True)
    if any(s in value for s in ("unsupported url", "no suitable extractor")):
        return AcquisitionError("unsupported_resource", "Unsupported source")
    if any(s in value for s in ("no such option", "unrecognized option", "javascript runtime", "ejs")):
        return AcquisitionError("dependency", "Downloader capability unavailable; review the installed tool")
    return AcquisitionError("unknown", f"Acquisition tool failed (exit {returncode}); inspect the local stage report")


@contextlib.contextmanager
def file_lock(path: Path):
    """Nonblocking OS lock; automatically released on process exit/crash."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise AcquisitionError("invalid_input", "Lock path must not be a symlink")
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AcquisitionError("busy", "Another process owns this work or cache entry; resume after it finishes") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_cache(path: Path, key: str) -> object | None:
    try:
        if Path(path).is_symlink():
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or data.get("key") != key:
            return None
        if canonical_hash(data["payload"]) != data.get("sha256"):
            return None
        return data["payload"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def write_cache(path: Path, key: str, payload: object) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(Path(path), json.dumps({"schema_version": 1, "key": key, "sha256": canonical_hash(payload),
                                       "payload": payload}, ensure_ascii=False, allow_nan=False))


def record_event(work: Path | None, operation: str, provider: str, **fields) -> None:
    if work is None:
        return
    log_path = Path(os.environ["VSUM_OPERATION_LOG"]) if os.environ.get("VSUM_OPERATION_LOG") else Path(work) / "operations.jsonl"
    work = log_path.parent
    work.mkdir(parents=True, exist_ok=True)
    # Only these scalar counters/status fields may enter telemetry.
    allowed = {"attempt", "category", "elapsed_s", "cache_hit", "next_retry_at", "tool_invocations", "api_attempts", "http_attempts"}
    row = {"at": time.time(), "operation": operation, "provider": provider,
           **{k: v for k, v in fields.items() if k in allowed}}
    with file_lock(work / ".operations.lock"):
        path = log_path
        if path.is_symlink():
            raise AcquisitionError("invalid_input", "Telemetry path must not be a symlink")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, allow_nan=False) + "\n")


def retry_call(operation: str, call: Callable[[], T], *, provider: str, work: Path | None = None,
               sleep=None, monotonic=None) -> T:
    """One owner for retries. A cooldown receipt survives resume/compaction."""
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    started, waited = monotonic(), 0.0
    cooldown = Path(work) / "cooldowns" / (canonical_hash([provider, operation]) + ".json") if work else None
    if cooldown and cooldown.exists():
        try:
            until = float(json.loads(cooldown.read_text(encoding="utf-8"))["next_retry_at"])
        except (OSError, ValueError, KeyError, TypeError):
            until = 0
        if until > time.time():
            error = AcquisitionError("rate_limit", "Provider cooldown is still active", retry_after=until - time.time())
            error.next_retry_at = until
            raise error
    for attempt in range(1, MAX_ATTEMPTS + 1):
        record_event(work, operation, provider, attempt=attempt, category="started",
                     http_attempts=int(provider in ("youtube-captions", "groq", "openai")),
                     api_attempts=int(provider in ("groq", "openai")),
                     tool_invocations=int(provider in ("yt-dlp", "whisper.cpp")))
        try:
            value = call()
        except AcquisitionError as error:
            error.attempts = attempt
            record_event(work, operation, provider, attempt=attempt, category=error.category,
                         elapsed_s=round(monotonic() - started, 4))
            if not error.retryable or error.uncertain:
                raise
            delay = error.retry_after if error.retry_after is not None else random.uniform(1, 2 ** attempt)
            if attempt == MAX_ATTEMPTS or waited + delay > WAIT_BUDGET:
                if error.category == "rate_limit" or error.retry_after is not None:
                    error.next_retry_at = time.time() + max(delay, 1)
                    if cooldown:
                        cooldown.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write(cooldown, json.dumps({"next_retry_at": error.next_retry_at}))
                    record_event(work, operation, provider, category="deferred", next_retry_at=error.next_retry_at)
                raise
            sleep(delay)
            waited += delay
        else:
            record_event(work, operation, provider, attempt=attempt, category="ok",
                         elapsed_s=round(monotonic() - started, 4))
            return value
    raise AssertionError("unreachable")


def _kill_descendants(pid: int) -> None:
    """A nested helper may create a separate session. Stop descendants first.

    Read only PID/PPID, never command lines/environment. Kill in reverse tree
    order immediately, before ancestors can exit and orphan the tracked leaves.
    Windows uses taskkill /T instead.
    """
    try:
        listing = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=5)
        children: dict[int, list[int]] = {}
        for line in listing.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2:
                child, parent = map(int, fields)
                children.setdefault(parent, []).append(child)
        descendants = []
        pending = list(children.get(pid, []))
        while pending:
            child = pending.pop()
            descendants.append(child)
            pending.extend(children.get(child, []))
        for child in reversed(descendants):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass  # The owning process group is still terminated below.


def run_process(command: list[str], *, timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """Capture finite subprocesses and terminate their children on timeout/cancel."""
    options = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace")
    options.update(kwargs)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **options)
    except OSError as exc:
        raise AcquisitionError("dependency", "Unable to start the required executable") from exc
    previous_handler = None
    handler_installed = False
    # Controller children also use this helper. Forward termination through
    # nested process sessions before escalating to SIGKILL.
    if os.name != "nt":
        try:
            previous_handler = signal.getsignal(signal.SIGTERM)
            def cancelled(_signum, _frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, cancelled)
            handler_installed = True
        except ValueError:
            pass  # Python only permits signal handlers in the main thread.
    try:
        out, err = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            _kill_descendants(process.pid)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            process.communicate(timeout=3)
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise AcquisitionError("temporary_network", "Operation exceeded its deadline", retryable=True) from exc
    finally:
        if handler_installed:
            signal.signal(signal.SIGTERM, previous_handler)
    return subprocess.CompletedProcess(command, process.returncode, out, err)
