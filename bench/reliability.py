#!/usr/bin/env python3
"""Deterministic, offline reliability comparison for acquisition behavior.

The harness uses real module functions with only network, sleep, and provider
boundaries mocked.  It archives the baseline commit and copies the frozen
six-video scorer corpus; it never downloads media, invokes ASR/models, or
writes into either source snapshot.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
from pathlib import Path
from unittest import mock

RUN_NAME = "2026-09-04-v15-high"
FIXTURE_DATE = "Tue, 08 Sep 2026 00:00:10 GMT"
FIXTURE_NOW = 1788825600.0


def category(exc: BaseException) -> str:
    return str(getattr(exc, "category", type(exc).__name__))


def worker(root: Path) -> dict:
    sys.path.insert(0, str(root / "scripts"))
    import transcript  # type: ignore
    import whisper  # type: ignore
    try:
        import acquisition  # type: ignore
    except ImportError:
        acquisition = None

    scenarios: dict[str, dict] = {}
    started = time.perf_counter()

    # Reproduce successful discovery followed by HTTP 429 on the selected
    # caption resource.  The baseline delegates that fetch to yt-dlp; current
    # code fetches the selected inventory URL directly.
    info = {"id": "abcdefghijk", "title": "Fixture", "duration": 12.0, "language": "en",
            "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "subtitles": {"en": [{"url": "https://captions.invalid/en.vtt", "ext": "vtt"}]},
            "automatic_captions": {}}
    metadata_attempts = caption_http_attempts = 0
    def baseline_caption_run(args):
        nonlocal metadata_attempts, caption_http_attempts
        if "--write-info-json" in args:
            metadata_attempts += 1
            template = Path(args[args.index("-o") + 1])
            (template.parent / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
            return 0
        caption_http_attempts += 1
        transcript.YTDLP_LAST["stderr"] = "HTTP Error 429: Too Many Requests"
        return 1
    def current_discovery(*_args, **_kwargs):
        nonlocal metadata_attempts
        metadata_attempts += 1
        return info
    def current_caption_url(_url):
        nonlocal caption_http_attempts
        caption_http_attempts += 1
        raise acquisition.http_failure(429, {"Retry-After": "120"})
    caption_stack = contextlib.ExitStack()
    if acquisition:
        caption_stack.enter_context(mock.patch.object(transcript, "_discover_info", current_discovery))
        caption_stack.enter_context(mock.patch.object(transcript, "_fetch_caption_url", current_caption_url))
        caption_stack.enter_context(mock.patch.object(acquisition.time, "sleep"))
    else:
        caption_stack.enter_context(mock.patch.object(transcript, "_run_ytdlp", baseline_caption_run))
    with tempfile.TemporaryDirectory() as tmp, caption_stack:
        caught = None
        try:
            kwargs = {"cache_dir": Path(tmp) / "cache"} if acquisition else {}
            result = transcript.fetch_captions("https://www.youtube.com/watch?v=abcdefghijk",
                                               Path(tmp) / "run", None, **kwargs)
            outcome = "unavailable" if result.get("unavailable") else "returned"
        except BaseException as exc:
            caught, outcome = exc, "raised"
    scenarios["caption_429_no_automatic_asr"] = {
        "measurement": "measured", "metadata_tool_attempts": metadata_attempts,
        "caption_http_attempts": caption_http_attempts,
        "classification": category(caught) if caught else outcome,
        "note": "valid inventory followed by selected-caption HTTP 429",
    }

    # Exercise the CLI policy with a provider explicitly selected.  All media
    # and ASR boundaries remain mocks: counters show whether fallback activates.
    with tempfile.TemporaryDirectory() as tmp:
        media_calls = asr_calls = key_calls = 0
        work = Path(tmp) / "work"
        fake_media = Path(tmp) / "audio.mp3"
        fake_media.write_bytes(b"fixture")
        def media(*_args, **_kwargs):
            nonlocal media_calls
            media_calls += 1
            return fake_media
        def asr(*_args, **_kwargs):
            nonlocal asr_calls
            asr_calls += 1
            return ([{"start": 0.0, "end": 1.0, "text": "fixture"}], "groq")
        def key(_provider):
            nonlocal key_calls
            key_calls += 1
            return ("groq", "fixture-key")
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(transcript, "download_audio", media))
        stack.enter_context(mock.patch.object(transcript, "transcribe_video", asr))
        stack.enter_context(mock.patch.object(transcript, "load_api_key", key))
        if acquisition:
            stack.enter_context(mock.patch.object(transcript, "_discover_info", return_value=info))
            stack.enter_context(mock.patch.object(transcript, "_fetch_caption_url",
                                                  side_effect=acquisition.http_failure(429, {"Retry-After": "120"})))
            stack.enter_context(mock.patch.object(acquisition.time, "sleep"))
        else:
            stack.enter_context(mock.patch.object(transcript, "_run_ytdlp", baseline_caption_run))
        argv = ["transcript.py", info["webpage_url"], "--work", str(work), "--whisper", "groq"]
        with stack, mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli_rc = transcript.main()
            except BaseException as exc:
                cli_rc = getattr(exc, "code", None)
        payload = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    scenarios["caption_429_pipeline_fallback"] = {
        "measurement": "measured", "explicit_provider": "groq", "cli_returncode": cli_rc,
        "api_key_boundary_calls": key_calls, "media_boundary_calls": media_calls,
        "asr_boundary_calls": asr_calls, "fallback_activated": bool(media_calls or asr_calls),
        "recorded_status": payload.get("status"),
        "recorded_failure_category": (payload.get("acquisition_error") or {}).get("category"),
    }

    # A provider-wide 401 must stop four planned chunks after the first call.
    auth_calls = 0
    chunks = [(Path(chr(97 + i)), float(i * 10)) for i in range(4)]
    def auth(_path):
        nonlocal auth_calls
        auth_calls += 1
        if acquisition:
            raise acquisition.AcquisitionError("authentication", "fixture HTTP 401")
        raise SystemExit("Whisper request failed: HTTP 401")
    caught = None
    try:
        whisper.CHUNK_FAILURES.clear()
        whisper.transcribe_chunks(chunks, auth)
    except BaseException as exc:
        caught = exc
    scenarios["permanent_401_four_chunks"] = {
        "measurement": "measured", "planned_chunks": 4, "provider_calls": auth_calls,
        "stopped_after_first": auth_calls == 1, "classification": category(caught) if caught else "returned",
    }

    # Exercise the real upload/retry owner with a mocked HTTP opener and sleep.
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "fixture.mp3"
        audio.write_bytes(b"audio-fixture")
        opener = mock.MagicMock()
        max_attempts = acquisition.MAX_ATTEMPTS if acquisition else whisper.MAX_ATTEMPTS
        opener.open.side_effect = [
            urllib.error.HTTPError(whisper.GROQ_ENDPOINT, 503, "fixture", {}, io.BytesIO(b"fixture"))
            for _ in range(int(max_attempts))
        ]
        caught = None
        sleep_target = acquisition.time if acquisition else whisper.time
        with mock.patch.object(whisper, "build_opener", return_value=opener), mock.patch.object(sleep_target, "sleep"):
            try:
                whisper._post_whisper(whisper.GROQ_ENDPOINT, "fixture-key", whisper.GROQ_MODEL, audio)
            except BaseException as exc:
                caught = exc
        scenarios["transient_5xx_bounded_attempts"] = {
            "measurement": "measured", "provider_calls": opener.open.call_count,
            "bounded_at_three": opener.open.call_count == 3,
            "classification": category(caught) if caught else "returned",
        }

    headers = {"Retry-After": FIXTURE_DATE}
    if acquisition:
        delay = acquisition.retry_after(headers, now=FIXTURE_NOW)
    else:
        error = urllib.error.HTTPError("https://fixture.invalid", 429, "fixture", headers, None)
        delay = whisper._retry_after(error)
    scenarios["retry_after_http_date"] = {
        "measurement": "measured", "header": FIXTURE_DATE, "fixed_now_epoch": FIXTURE_NOW,
        "parsed_delay_s": delay, "date_supported": delay == 10,
    }

    if hasattr(whisper, "cached_chunk"):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "chunk.mp3"
            audio.write_bytes(b"chunk-fixture")
            calls = 0
            def provider():
                nonlocal calls
                calls += 1
                return [{"start": 0.0, "end": 1.0, "text": "cached"}]
            identity = {"engine": "groq", "model": "fixture", "version": "1", "language": "en",
                        "extraction": "mp3", "start": 0.0, "end": 10.0}
            whisper.cached_chunk(audio, Path(tmp) / "cache", identity, provider)
            after_first = calls
            whisper.cached_chunk(audio, Path(tmp) / "cache", identity, provider)
            after_resume = calls
        scenarios["chunk_cache_resume"] = {
            "measurement": "measured", "first_run_provider_calls": after_first,
            "resume_provider_calls": after_resume - after_first, "cache_hit": after_resume == after_first,
        }
    else:
        scenarios["chunk_cache_resume"] = {
            "measurement": "measured_capability_absence", "chunk_cache_supported": False,
            "resume_provider_calls": None, "cache_hit": False,
            "note": "baseline controller completion caching is outside this chunk-cache fixture",
        }

    malformed = {"text": "words without timestamps"}
    caught = None
    try:
        value = whisper._segments_from_response(malformed)
    except BaseException as exc:
        caught, value = exc, None
    scenarios["malformed_provider_response"] = {
        "measurement": "measured", "rejected": caught is not None,
        "classification": category(caught) if caught else "accepted",
        "returned_segments": value,
    }

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "fixture.mp3"
        audio.write_bytes(b"audio-fixture")
        opener = mock.MagicMock()
        opener.open.side_effect = TimeoutError("fixture response lost")
        sleep_target = acquisition.time if acquisition else whisper.time
        errors = []
        with mock.patch.object(whisper, "build_opener", return_value=opener), mock.patch.object(sleep_target, "sleep"):
            for _ in range(2):
                try:
                    args = (whisper.GROQ_ENDPOINT, "fixture-key", whisper.GROQ_MODEL, audio)
                    if acquisition:
                        whisper._post_whisper(*args, work=Path(tmp) / "work")
                    else:
                        whisper._post_whisper(*args)
                except BaseException as exc:
                    errors.append(category(exc))
        scenarios["unknown_upload_outcome"] = {
            "measurement": "measured", "two_invocations_provider_calls": opener.open.call_count,
            "classifications": errors, "second_invocation_zero_calls": opener.open.call_count == 1,
        }

    return {"scenarios": scenarios, "elapsed_harness_s": round(time.perf_counter() - started, 6)}


def git(repo: Path, *args: str, binary: bool = False):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=not binary).stdout


def identity(repo: Path, baseline: str) -> dict:
    sha = git(repo, "rev-parse", "HEAD").strip()
    status = git(repo, "status", "--porcelain=v1").splitlines()
    digest = hashlib.sha256(git(repo, "diff", "--binary", "HEAD", binary=True)).digest()
    untracked = sorted(line[3:] for line in status if line.startswith("?? "))
    h = hashlib.sha256(digest)
    for name in untracked:
        path = repo / name
        if path.is_file():
            h.update(name.encode()); h.update(path.read_bytes())
    return {"baseline_requested": baseline, "head_sha": sha, "dirty": bool(status),
            "dirty_fingerprint_sha256": h.hexdigest() if status else None,
            "status_entry_count": len(status)}


def archive(repo: Path, revision: str, destination: Path) -> str:
    sha = git(repo, "rev-parse", f"{revision}^{{commit}}").strip()
    payload = git(repo, "archive", sha, binary=True)
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        tar.extractall(destination, filter="data")
    return sha


def run_worker(script: Path, root: Path) -> dict:
    proc = subprocess.run([sys.executable, str(script), "--worker", str(root)], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return json.loads(proc.stdout)


def scorer_replay(snapshot: Path, corpus_root: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="reliability-score-") as tmp:
        root = Path(tmp)
        run = root / "bench" / "runs" / RUN_NAME
        annotations = root / "bench" / "annotations"
        shutil.copytree(corpus_root / "bench" / "runs" / RUN_NAME, run)
        shutil.copytree(corpus_root / "bench" / "annotations", annotations)
        score = root / "bench" / "score.py"
        score.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / "bench" / "score.py", score)
        started = time.perf_counter()
        proc = subprocess.run([sys.executable, str(score), "--run", str(run), "--allow-draft"], cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        elapsed = round(time.perf_counter() - started, 6)
        scores = json.loads((run / "scores.json").read_text(encoding="utf-8")) if proc.returncode == 0 else None
    canonical = json.dumps(scores, sort_keys=True, allow_nan=True, separators=(",", ":")) if scores is not None else None
    return {"measurement": "measured_frozen_corpus_replay", "returncode": proc.returncode,
            "video_count": len(scores) if isinstance(scores, list) else None,
            "scores_sha256": hashlib.sha256(canonical.encode()).hexdigest() if canonical else None,
            "elapsed_harness_s": elapsed, "stderr": proc.stderr[-500:]}


def skill_size(snapshot: Path) -> dict:
    raw = (snapshot / "SKILL.md").read_bytes()
    text = raw.decode("utf-8")
    return {"measurement": "measured_static_file", "utf8_bytes": len(raw),
            "unicode_characters": len(text),
            "estimated_tokens_characters_div_4": round(len(text) / 4, 2),
            "estimate_method": "Unicode characters divided by 4; not tokenizer output"}


def render_md(report: dict) -> str:
    b, c = report["snapshots"]["baseline"], report["snapshots"]["current"]
    lines = ["# Reliability benchmark", "",
             "Deterministic offline decision benchmark. All reliability values below are measured from real functions with mocked network/provider/sleep boundaries. No media, ASR, model, or network call ran.", "",
             f"- Baseline: `{report['identity']['baseline_sha']}`",
             f"- Current: `{report['identity']['current']['head_sha']}` with dirty fingerprint `{report['identity']['current']['dirty_fingerprint_sha256']}`",
             f"- Harness latency: baseline `{b['elapsed_harness_s']:.6f}s`, current `{c['elapsed_harness_s']:.6f}s` (measurement overhead only; not media wall-clock or API billing)", "",
             "| Scenario | Baseline measured | Current measured |", "|---|---:|---:|"]
    labels = {
        "caption_429_no_automatic_asr": lambda x: (f"{x['classification']}; metadata {x['metadata_tool_attempts']}, "
                                                         f"caption HTTP {x['caption_http_attempts']}"),
        "caption_429_pipeline_fallback": lambda x: (f"media {x['media_boundary_calls']}, ASR {x['asr_boundary_calls']}; "
                                                        f"status {x['recorded_status']}"),
        "permanent_401_four_chunks": lambda x: f"provider calls {x['provider_calls']}/4",
        "transient_5xx_bounded_attempts": lambda x: f"provider calls {x['provider_calls']}",
        "retry_after_http_date": lambda x: (f"delay {x['parsed_delay_s']}s" if x['parsed_delay_s'] is not None
                                             else "HTTP-date unsupported"),
        "chunk_cache_resume": lambda x: ("chunk cache unsupported" if x.get('chunk_cache_supported') is False
                                           else f"resume calls {x['resume_provider_calls']}"),
        "malformed_provider_response": lambda x: "rejected" if x['rejected'] else "accepted",
        "unknown_upload_outcome": lambda x: f"calls across two invocations {x['two_invocations_provider_calls']}",
    }
    for name, fmt in labels.items():
        lines.append(f"| `{name}` | {fmt(b['scenarios'][name])} | {fmt(c['scenarios'][name])} |")
    q = report["quality_replay"]
    sizes = report["skill_size"]
    lines += ["", "## Frozen six-video corpus", "",
              f"The committed `{RUN_NAME}` scorer corpus and human annotations were replayed in temporary copies. This is a scorer regression check, not a new download, ASR, frame extraction, or human-quality evaluation.", "",
              f"- Baseline: {q['baseline']['video_count']} videos, score hash `{q['baseline']['scores_sha256']}`, rc {q['baseline']['returncode']}",
              f"- Current: {q['current']['video_count']} videos, score hash `{q['current']['scores_sha256']}`, rc {q['current']['returncode']}",
              f"- Unchanged scorer output: `{str(q['unchanged']).lower()}`", "",
              "## Static skill size", "",
              "Exact file measurements and a rough characters/4 estimate. The estimate is not tokenizer output or measured model usage.", "",
              "| Snapshot | UTF-8 bytes (measured) | Unicode characters (measured) | Characters/4 (estimated) |",
              "|---|---:|---:|---:|",
              f"| Baseline | {sizes['baseline']['utf8_bytes']} | {sizes['baseline']['unicode_characters']} | {sizes['baseline']['estimated_tokens_characters_div_4']} |",
              f"| Current | {sizes['current']['utf8_bytes']} | {sizes['current']['unicode_characters']} | {sizes['current']['estimated_tokens_characters_div_4']} |", "",
              "## Interpretation and limits", "",
              f"Decision assertions: `{str(report['decision']['success']).lower()}` ({sum(report['decision']['assertions'].values())}/{len(report['decision']['assertions'])} pass).", "",
              "Measured: metadata and selected-caption attempts, CLI fallback activation, provider calls, retry attempts, classifications, chunk-cache reuse, malformed-response rejection, HTTP-date parsing, and frozen-corpus score identity. The redesign preserves the selected-caption 429 as a rate-limit failure and does not start media/ASR fallback, stops provider-wide 401 failures after one chunk, bounds 5xx retries at three, resumes cached chunks with zero calls, parses HTTP-date Retry-After, rejects untimed responses, and prevents an automatic repeat after an unknown upload outcome.", "",
              "Inferred: these call reductions should reduce wasted provider work and duplicate-upload risk. Dollar cost is `null`: no provider pricing, media duration, encoded bytes, or live billing data were measured.", "",
              "Unavailable here: live source behavior, real media wall-clock, API billing, ASR quality, model quality, and new frame correctness. The quality result only proves identical scoring over the frozen committed artifacts.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="b52d268")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker.resolve()), sort_keys=True))
        return 0
    repo = Path(__file__).resolve().parents[1]
    output = (args.output_root.resolve() if args.output_root else repo.parents[1] / "outputs")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reliability-baseline-") as tmp:
        baseline_root = Path(tmp)
        baseline_sha = archive(repo, args.baseline, baseline_root)
        baseline = run_worker(Path(__file__).resolve(), baseline_root)
        current = run_worker(Path(__file__).resolve(), repo)
        quality_baseline = scorer_replay(baseline_root, repo)
        quality_current = scorer_replay(repo, repo)
        size_baseline = skill_size(baseline_root)
        size_current = skill_size(repo)
    assertions = {
        "caption_fixture_discovery_then_selected_resource": all(
            snap["scenarios"]["caption_429_no_automatic_asr"]["metadata_tool_attempts"] == 1 and
            snap["scenarios"]["caption_429_no_automatic_asr"]["caption_http_attempts"] == 1
            for snap in (baseline, current)),
        "baseline_caption_429_classification_lost": baseline["scenarios"]["caption_429_no_automatic_asr"]["classification"] == "returned",
        "baseline_caption_429_fallback_activated": baseline["scenarios"]["caption_429_pipeline_fallback"]["fallback_activated"] is True,
        "caption_429_classified_rate_limit": current["scenarios"]["caption_429_no_automatic_asr"]["classification"] == "rate_limit",
        "caption_429_no_media_fallback": current["scenarios"]["caption_429_pipeline_fallback"]["media_boundary_calls"] == 0,
        "caption_429_no_asr_fallback": current["scenarios"]["caption_429_pipeline_fallback"]["asr_boundary_calls"] == 0,
        "permanent_401_stops_after_first_chunk": current["scenarios"]["permanent_401_four_chunks"]["provider_calls"] == 1,
        "transient_5xx_capped_at_three": current["scenarios"]["transient_5xx_bounded_attempts"]["provider_calls"] == 3,
        "retry_after_http_date_supported": current["scenarios"]["retry_after_http_date"]["date_supported"] is True,
        "chunk_cache_resume_zero_calls": current["scenarios"]["chunk_cache_resume"]["resume_provider_calls"] == 0,
        "malformed_response_rejected": current["scenarios"]["malformed_provider_response"]["rejected"] is True,
        "unknown_upload_not_repeated": current["scenarios"]["unknown_upload_outcome"]["second_invocation_zero_calls"] is True,
        "frozen_six_video_scores_unchanged": quality_baseline["video_count"] == quality_current["video_count"] == 6 and
                                             quality_baseline["scores_sha256"] == quality_current["scores_sha256"],
    }
    report = {
        "schema_version": 1,
        "benchmark": "deterministic_offline_reliability_decision",
        "identity": {"baseline_sha": baseline_sha, "current": identity(repo, args.baseline)},
        "fixture": {"run": RUN_NAME, "retry_after_date": FIXTURE_DATE, "fixed_now_epoch": FIXTURE_NOW,
                    "network_calls": 0, "media_downloads": 0, "asr_calls": 0, "model_calls": 0},
        "snapshots": {"baseline": baseline, "current": current},
        "decision": {"success": all(assertions.values()), "assertions": assertions},
        "cost": {"dollar_cost_usd": None, "status": "unavailable_not_measured",
                 "inference": "fewer repeated calls should reduce wasted provider work"},
        "quality_replay": {"baseline": quality_baseline, "current": quality_current,
                           "unchanged": quality_baseline["returncode"] == quality_current["returncode"] == 0 and
                                        quality_baseline["scores_sha256"] == quality_current["scores_sha256"]},
        "skill_size": {"baseline": size_baseline, "current": size_current},
        "labels": {"measured": "fixture observation", "inferred": "conceptual implication only"},
        "limitations": ["harness latency is not media wall-clock or API billing",
                        "frozen corpus replay is not a new download, ASR run, frame extraction, or quality study",
                        "no dollar cost claim is supported by this offline fixture"],
    }
    json_path, md_path = output / "reliability-benchmark.json", output / "reliability-benchmark.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path),
                      "quality_unchanged": report["quality_replay"]["unchanged"],
                      "decision_success": report["decision"]["success"]}, sort_keys=True))
    return 0 if report["decision"]["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
