#!/usr/bin/env python3
"""Bounded remote or installed whisper.cpp transcription."""
from __future__ import annotations

import io, json, math, mimetypes, os, re, shutil, ssl, sys, time, urllib.error, uuid
from pathlib import Path
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acquisition import (AcquisitionError, DISCOVERY_TIMEOUT, MEDIA_TIMEOUT, UPLOAD_TIMEOUT, canonical_hash,
    file_lock, hash_file, http_failure, read_cache, record_event, retry_call,
    run_process, write_cache)  # noqa: E402
from hostenv import require_tools  # noqa: E402
from safety import atomic_write  # noqa: E402

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"
MAX_AUDIO_BYTES = 24_000_000
MAX_MULTIPART_BYTES = 25_000_000
MAX_UPLOAD_BYTES = MAX_AUDIO_BYTES
MAX_CHUNK_SECONDS = 600.0
LOCAL_EXTRACTION = "pcm_s16le-16000hz-mono"
REMOTE_EXTRACTION = "mp3-64k-16000hz-mono"
FATAL_PROVIDER_CATEGORIES = {"authentication", "authorization", "quota_exceeded", "uncertain_upload"}
MAX_RESPONSE_BYTES = 10_000_000


def plan_chunks(total_seconds: float, total_bytes: int,
                max_bytes: int = MAX_AUDIO_BYTES) -> list[tuple[float, float]]:
    if not math.isfinite(total_seconds) or total_seconds < 0 or total_bytes < 0 or max_bytes <= 0:
        raise AcquisitionError("invalid_input", "Invalid audio duration or size")
    # Leave room for a two-second look-back at every boundary while keeping
    # each real checkpoint request at or below ten minutes.
    stride = MAX_CHUNK_SECONDS - 2.0
    count = max(1, math.ceil(total_bytes / max_bytes),
                math.ceil(total_seconds / stride) if total_seconds else 1)
    size = total_seconds / count
    return [(round(i * size, 3), round(total_seconds - i * size if i == count - 1 else size, 3))
            for i in range(count)]


def _dotenv_value(path: Path, name: str) -> str | None:
    if not path.is_file() or path.is_symlink(): return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]: value = value[1:-1]
                return value or None
    except OSError: pass
    return None


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None: candidates = tuple(x for x in candidates if x[1] == preferred)
    config = Path.home() / ".config" / "summarize-video" / ".env"
    for name, backend in candidates:
        value = (os.environ.get(name) or "").strip() or _dotenv_value(config, name)
        if value: return backend, value
    return None, None


def configured_local_model() -> Path | None:
    """Resolve LOCAL_WHISPER_MODEL only; never inspect credential files."""
    value = (os.environ.get("LOCAL_WHISPER_MODEL") or "").strip()
    return validate_local_model(Path(value).expanduser()) if value else None


def validate_local_model(path: Path) -> Path:
    if path.is_symlink():
        raise AcquisitionError("invalid_input", "Configured local Whisper model must not be a symlink")
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise AcquisitionError("dependency", "Configured local Whisper model is missing or invalid")
    name = resolved.name.lower()
    if re.search(r"(?:^|[.-])en(?:[.-]|$)", name):
        raise AcquisitionError("invalid_input", "A multilingual GGML Whisper model is required; .en models are incompatible")
    if resolved.suffix.lower() != ".bin":
        raise AcquisitionError("invalid_input", "Configured local model must be a GGML .bin model")
    try:
        with resolved.open("rb") as stream: header = stream.read(8)
    except OSError as exc: raise AcquisitionError("dependency", "Configured local Whisper model is unreadable") from exc
    magic = header[:4]
    if len(header) != 8 or magic not in {b"ggml", b"lmgg"}:
        raise AcquisitionError("invalid_input", "Configured local model does not have a recognized GGML header")
    n_vocab = int.from_bytes(header[4:8], "little" if magic == b"lmgg" else "big")
    if not 51865 <= n_vocab <= 100000:
        raise AcquisitionError("invalid_input", "A compatible multilingual Whisper vocabulary is required")
    return resolved


def _local_binary() -> Path:
    found = shutil.which("whisper-cli") or shutil.which("whisper.cpp")
    if not found: raise AcquisitionError("dependency", "whisper.cpp whisper-cli is not installed")
    return Path(found).resolve()


def _binary_version(binary: Path) -> str:
    """Fingerprint the executable plus bounded help; --version is not portable."""
    result = run_process([str(binary), "--help"], timeout=15)
    text = ((result.stdout or "") + "\n" + (result.stderr or ""))[:64_000]
    if result.returncode != 0 or ("usage" not in text.casefold() and "help" not in text.casefold()):
        raise AcquisitionError("dependency", "Unable to identify the installed whisper.cpp CLI")
    lines = text.splitlines()
    stable_help = "\n".join(lines[next((i for i, line in enumerate(lines) if "usage" in line.casefold()), 0):])
    return canonical_hash({"binary_sha256": hash_file(binary), "help_fingerprint": canonical_hash(stable_help)})


def extract_audio(video_path: str, out_path: Path) -> Path:
    require_tools("ffmpeg"); out_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
        str(Path(video_path).resolve()), "-vn", "-acodec", "libmp3lame", "-ar", "16000",
        "-ac", "1", "-b:a", "64k", str(out_path.resolve())], timeout=MEDIA_TIMEOUT)
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        raise AcquisitionError("invalid_input", "ffmpeg audio extraction failed or produced no audio")
    return out_path


def extract_local_wav(video_path: str, out_path: Path) -> Path:
    require_tools("ffmpeg"); out_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
        str(Path(video_path).resolve()), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out_path.resolve())], timeout=MEDIA_TIMEOUT)
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size <= 44:
        raise AcquisitionError("invalid_input", "Failed to extract 16 kHz 16-bit mono WAV audio")
    return out_path


def audio_duration(audio_path: Path) -> float:
    require_tools("ffprobe")
    result = run_process(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                          str(audio_path.resolve())], timeout=DISCOVERY_TIMEOUT)
    try: duration = float(json.loads(result.stdout or "{}")["format"]["duration"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise AcquisitionError("invalid_input", "Audio duration is unavailable") from exc
    if result.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise AcquisitionError("invalid_input", "Audio duration is unavailable")
    return duration


def split_audio(full_audio: Path, work_dir: Path, plan: list[tuple[float, float]]) -> list[tuple[Path, float, float]]:
    require_tools("ffmpeg"); work_dir.mkdir(parents=True, exist_ok=True)
    pending, chunks, index = list(plan), [], 0
    while pending:
        offset, duration = pending.pop(0)
        actual_offset = max(0.0, offset - (2.0 if offset else 0.0))
        duration += offset - actual_offset
        offset = actual_offset
        out = work_dir / f"chunk_{index:04d}.mp3"
        result = run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()), "-t", f"{duration:.3f}", "-vn", "-ar", "16000", "-ac", "1",
            "-b:a", "64k", str(out.resolve())], timeout=MEDIA_TIMEOUT)
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            raise AcquisitionError("invalid_input", f"Failed to create audio chunk {index + 1}")
        if out.stat().st_size > MAX_AUDIO_BYTES:
            out.unlink(missing_ok=True)
            if duration <= 1: raise AcquisitionError("invalid_input", "Audio chunk cannot fit the upload limit")
            half = duration / 2; pending[0:0] = [(offset, half), (offset + half, duration - half)]; continue
        chunks.append((out, offset, duration)); index += 1
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary, eol, stream = f"----WatchBoundary{uuid.uuid4().hex}", b"\r\n", io.BytesIO()
    for name, value in fields.items():
        stream.write(f"--{boundary}".encode() + eol)
        stream.write(f'Content-Disposition: form-data; name="{name}"'.encode() + eol + eol + str(value).encode() + eol)
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    stream.write(f"--{boundary}".encode() + eol)
    stream.write(f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode() + eol)
    stream.write(f"Content-Type: {mime}".encode() + eol + eol + file_path.read_bytes() + eol)
    stream.write(f"--{boundary}--".encode() + eol)
    return stream.getvalue(), boundary


class NoUploadRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Upload redirects are disabled", headers, None)


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path,
                  language: str | None = None, work: Path | None = None,
                  retry_uncertain: bool = False) -> dict:
    if endpoint not in {GROQ_ENDPOINT, OPENAI_ENDPOINT}:
        raise AcquisitionError("invalid_input", "Unapproved transcription endpoint")
    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size > MAX_AUDIO_BYTES:
        raise AcquisitionError("invalid_input", "Audio exceeds the 24,000,000-byte audio limit")
    work = Path(work) if work else audio_path.parent / ".upload-receipts"
    lock_key = canonical_hash([endpoint, model, language, hash_file(audio_path)])
    with file_lock(work / "upload-locks" / f"{lock_key}.lock"):
        return _post_whisper_locked(endpoint, api_key, model, audio_path, language, work, retry_uncertain)


def _post_whisper_locked(endpoint: str, api_key: str, model: str, audio_path: Path,
                         language: str | None, work: Path, retry_uncertain: bool) -> dict:
    if endpoint not in {GROQ_ENDPOINT, OPENAI_ENDPOINT}:
        raise AcquisitionError("invalid_input", "Unapproved transcription endpoint")
    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size > MAX_AUDIO_BYTES:
        raise AcquisitionError("invalid_input", "Audio exceeds the 24,000,000-byte audio limit")
    fields = {"model": model, "response_format": "verbose_json", "temperature": "0"}
    if language: fields["language"] = language
    body, boundary = _build_multipart(fields, audio_path)
    if len(body) > MAX_MULTIPART_BYTES:
        raise AcquisitionError("invalid_input", "Upload multipart body exceeds the 25,000,000-byte limit")
    provider = "groq" if endpoint == GROQ_ENDPOINT else "openai"
    opkey = canonical_hash([endpoint, model, language, hash_file(audio_path)])
    response_path = work / "provider-responses" / f"{opkey}.json"
    cached = read_cache(response_path, opkey)
    if isinstance(cached, dict):
        _segments_from_response(cached)
        record_event(work, "whisper-upload", provider, cache_hit=True)
        return cached
    pending = Path(work) / "uncertain" / f"{opkey}.json" if work else None
    if pending and pending.exists():
        if not retry_uncertain:
            raise AcquisitionError("uncertain_upload", "A prior upload may have succeeded; use --retry-uncertain explicitly", uncertain=True)
        pending.unlink(missing_ok=True)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}",
               "User-Agent": "summarize-video-skill/2.0 (python-urllib)"}
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoUploadRedirects())

    def send() -> dict:
        if pending:
            pending.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(pending, json.dumps({"state": "request_started", "key": opkey}))
        try:
            with opener.open(Request(endpoint, data=body, headers=headers, method="POST"), timeout=UPLOAD_TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if pending: pending.unlink(missing_ok=True)
            try: error_body = exc.read(65536)
            except Exception: error_body = b""
            finally: exc.close()
            raise http_failure(exc.code, exc.headers, error_body) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            raise AcquisitionError("uncertain_upload", "Upload response was lost; retry requires explicit authorization", uncertain=True) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AcquisitionError("invalid_response", "Whisper response body exceeds the safety limit", uncertain=True)
        try: data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise AcquisitionError("invalid_response", "Whisper returned a non-JSON response", uncertain=True) from exc
        try:
            normalized = {"segments": _segments_from_response(data)}
            if isinstance(data.get("language"), str): normalized["language"] = data["language"]
            write_cache(response_path, opkey, normalized)
        except (AcquisitionError, OSError) as exc:
            raise AcquisitionError("invalid_response", "Whisper response could not be validated and checkpointed", uncertain=True) from exc
        if pending: pending.unlink(missing_ok=True)
        return normalized
    return retry_call("whisper-upload", send, provider=provider, work=work)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    from acquisition import retry_after
    return retry_after(exc.headers)


def _segments_from_response(data: dict, *, max_duration: float | None = None) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise AcquisitionError("invalid_response", "Whisper response has no timed segments")
    output = []
    for value in data["segments"]:
        if not isinstance(value, dict): raise AcquisitionError("invalid_response", "Whisper returned a malformed segment")
        try: start, end, text = float(value["start"]), float(value["end"]), value["text"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AcquisitionError("invalid_response", "Whisper returned invalid timestamps") from exc
        if not isinstance(text, str) or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise AcquisitionError("invalid_response", "Whisper returned invalid timestamps or text")
        if max_duration is not None and end > max_duration + 1e-6:
            raise AcquisitionError("invalid_response", "Whisper timestamp exceeds the encoded chunk duration")
        if text.strip(): output.append({"start": round(start, 3), "end": round(end, 3), "text": text.strip()})
    return output


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    return [{"start": round(s["start"] + offset_seconds, 3), "end": round(s["end"] + offset_seconds, 3),
             "text": s["text"]} for s in segments]


def _tokens(text: str) -> list[str]: return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def merge_segments(existing: list[dict], incoming: list[dict], *, overlap_start: float, overlap_end: float) -> list[dict]:
    result, prior = list(existing), [s for s in existing if s["end"] >= overlap_start and s["start"] <= overlap_end]
    for segment in incoming:
        duplicate = False
        if segment["start"] <= overlap_end and segment["end"] >= overlap_start:
            current = _tokens(segment["text"])
            for old in prior:
                if min(segment["end"], old["end"]) <= max(segment["start"], old["start"]):
                    continue  # Repeated words at distinct times are distinct speech.
                previous = _tokens(old["text"])
                if current and (current == previous or (len(current) >= 2 and len(previous) >= len(current) and
                    any(previous[i:i+len(current)] == current for i in range(len(previous)-len(current)+1)))):
                    duplicate = True; break
        if not duplicate: result.append(segment)
    return sorted(result, key=lambda s: (s["start"], s["end"]))


CHUNK_FAILURES: list[dict] = []
DETECTED_LANGUAGE: dict[str, str | None] = {"value": None}


class PartialTranscription(SystemExit):
    def __init__(self, segments: list[dict], failures: list[dict]):
        self.segments, self.failures = segments, failures
        super().__init__("Transcription is partial; one or more bounded audio ranges failed")


def transcribe_chunks(chunks, transcribe_one) -> list[dict]:
    chunk_list = list(chunks)
    segments, failures, previous_end = [], [], 0.0
    legacy_unbounded = bool(chunk_list) and all(len(item) == 2 for item in chunk_list)
    for index, item in enumerate(chunk_list):
        path, offset = item[0], float(item[1]); duration = float(item[2]) if len(item) > 2 else None
        try:
            local = transcribe_one(path)
            if not local: raise AcquisitionError("empty_response", "Whisper returned no timed speech for this audio range")
        except AcquisitionError as exc:
            end = offset + duration if duration is not None else offset
            failure = ({"index": index, "offset_s": round(offset, 3), "error": str(exc)[:200]}
                       if legacy_unbounded else
                       {"index": index, "offset_s": round(offset, 3), "range": {"start_s": round(offset, 3), "end_s": round(end, 3)},
                        "status": exc.category, "error": str(exc)[:200]})
            failures.append(failure); CHUNK_FAILURES.append(failure)
            if exc.category in FATAL_PROVIDER_CATEGORIES or exc.uncertain or not legacy_unbounded:
                if not legacy_unbounded:
                    for later_index, later in enumerate(chunk_list[index + 1:], index + 1):
                        later_offset, later_duration = float(later[1]), float(later[2])
                        unattempted = {"index": later_index, "offset_s": round(later_offset, 3),
                            "range": {"start_s": round(later_offset, 3), "end_s": round(later_offset + later_duration, 3)},
                            "status": "not_attempted", "error": "Not attempted after transcription paused"}
                        failures.append(unattempted); CHUNK_FAILURES.append(unattempted)
                    if segments:
                        raise PartialTranscription(segments, failures) from exc
                raise
            continue
        except SystemExit as exc:
            end = offset + duration if duration is not None else offset
            failure = ({"index": index, "offset_s": round(offset, 3), "error": str(exc)[:200]}
                       if legacy_unbounded else
                       {"index": index, "offset_s": round(offset, 3), "range": {"start_s": round(offset, 3), "end_s": round(end, 3)},
                        "status": "unknown", "error": str(exc)[:200]})
            failures.append(failure); CHUNK_FAILURES.append(failure); continue
        shifted = shift_segments(local, offset)
        segments = merge_segments(segments, shifted, overlap_start=offset, overlap_end=previous_end) if segments and offset < previous_end else segments + shifted
        previous_end = max(previous_end, offset + duration if duration is not None else max(s["end"] for s in shifted))
    if failures and not legacy_unbounded:
        if not segments: raise AcquisitionError("transcription_failed", "Whisper failed on every audio chunk")
        raise PartialTranscription(segments, failures)
    if failures and not segments:
        raise AcquisitionError("transcription_failed", "Whisper failed on every audio chunk")
    return segments


def cached_chunk(audio_path: Path, cache_dir: Path, identity: dict, transcribe) -> list[dict]:
    full = {**identity, "source_audio_sha256": hash_file(audio_path)}; key = canonical_hash(full)
    receipt = Path(cache_dir) / "whisper" / f"{key}.json"
    try: max_duration = float(identity["end"]) - float(identity["start"])
    except (KeyError, TypeError, ValueError):
        raise AcquisitionError("invalid_input", "Chunk cache identity requires finite bounds")
    if not math.isfinite(max_duration) or max_duration <= 0:
        raise AcquisitionError("invalid_input", "Chunk cache identity requires finite bounds")
    with file_lock(receipt.with_suffix(".lock")):
        cached = read_cache(receipt, key)
        if isinstance(cached, dict) and cached.get("identity") == full:
            segments = _segments_from_response({"segments": cached.get("segments")}, max_duration=max_duration)
            record_event(cache_dir, "whisper-chunk", str(identity.get("engine")), cache_hit=True); return segments
        segments = _segments_from_response({"segments": transcribe()}, max_duration=max_duration)
        write_cache(receipt, key, {"identity": full, "segments": segments})
        record_event(cache_dir, "whisper-chunk", str(identity.get("engine")), cache_hit=False); return segments


def _transcribe_file(backend: str, api_key: str, audio_path: Path, language: str | None = None,
                     *, work: Path | None = None, retry_uncertain: bool = False) -> list[dict]:
    endpoint, model = ((GROQ_ENDPOINT, GROQ_MODEL) if backend == "groq" else (OPENAI_ENDPOINT, OPENAI_MODEL))
    response = _post_whisper(endpoint, api_key, model, audio_path, language, work, retry_uncertain)
    detected = response.get("language")
    if isinstance(detected, str) and detected and not DETECTED_LANGUAGE["value"]: DETECTED_LANGUAGE["value"] = detected
    return _segments_from_response(response)


def _local_json_segments(data: dict) -> list[dict]:
    values = data.get("transcription") if isinstance(data, dict) else None
    if not isinstance(values, list): return _segments_from_response(data)
    normalized = []
    for item in values:
        offsets = item.get("offsets", {}) if isinstance(item, dict) else {}
        try:
            normalized.append({"start": float(offsets.get("from")) / 1000, "end": float(offsets.get("to")) / 1000,
                               "text": item.get("text") if isinstance(item, dict) else None})
        except (TypeError, ValueError) as exc:
            raise AcquisitionError("invalid_response", "whisper.cpp returned invalid timestamp offsets") from exc
    return _segments_from_response({"segments": normalized})


def _split_local_wav(full_audio: Path, work_dir: Path,
                     plan: list[tuple[float, float]]) -> list[tuple[Path, float, float]]:
    require_tools("ffmpeg"); work_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, (offset, duration) in enumerate(plan):
        actual_offset = max(0.0, offset - (2.0 if offset else 0.0))
        duration += offset - actual_offset
        out = work_dir / f"local_{index:04d}.wav"
        result = run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{actual_offset:.3f}",
            "-i", str(full_audio), "-t", f"{duration:.3f}", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out)], timeout=MEDIA_TIMEOUT)
        if result.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
            raise AcquisitionError("invalid_input", f"Failed to create local audio chunk {index + 1}")
        chunks.append((out, actual_offset, duration))
    return chunks


def _transcribe_local_video(video_path: str, audio_out: Path, model: Path,
                            language: str | None, cache_dir: Path | None, allow_long: bool = False) -> list[dict]:
    binary, wav = _local_binary(), extract_local_wav(video_path, audio_out.with_suffix(".wav"))
    version, model_fingerprint, duration = _binary_version(binary), hash_file(model), audio_duration(wav)
    _duration_guard(duration, allow_long)
    plan = plan_chunks(duration, 0)
    chunks = [(wav, 0.0, duration)] if len(plan) == 1 else _split_local_wav(wav, audio_out.parent / "local-chunks", plan)
    positions = {id(path): (offset, length) for path, offset, length in chunks}
    def transcribe_one(path: Path):
        offset, length = positions[id(path)]
        output_base = audio_out.parent / f"whisper-local-{canonical_hash([hash_file(path), offset])[:12]}"
        command = [str(binary), "-m", str(model), "-f", str(path), "-oj", "-of", str(output_base)]
        command += ["-l", language or "auto"]
        def invoke():
            output_json = output_base.with_suffix(".json")
            if output_json.is_symlink():
                raise AcquisitionError("invalid_input", "Refusing a symlinked whisper.cpp output")
            output_json.unlink(missing_ok=True)
            started = time.monotonic()
            record_event(cache_dir, "whisper-local", "whisper.cpp", category="started", tool_invocations=1)
            try:
                result = run_process(command, timeout=MEDIA_TIMEOUT)
            except AcquisitionError as exc:
                record_event(cache_dir, "whisper-local", "whisper.cpp", category=exc.category,
                             elapsed_s=round(time.monotonic() - started, 4))
                raise
            if result.returncode != 0:
                record_event(cache_dir, "whisper-local", "whisper.cpp", category="dependency",
                             elapsed_s=round(time.monotonic() - started, 4))
                raise AcquisitionError("dependency", "whisper.cpp transcription failed")
            try: data = json.loads(output_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                record_event(cache_dir, "whisper-local", "whisper.cpp", category="invalid_response",
                             elapsed_s=round(time.monotonic() - started, 4))
                raise AcquisitionError("invalid_response", "whisper.cpp produced no valid JSON timestamps") from exc
            segments = _local_json_segments(data)
            record_event(cache_dir, "whisper-local", "whisper.cpp", category="ok",
                         elapsed_s=round(time.monotonic() - started, 4))
            return segments
        identity = {"engine": "whisper.cpp", "model": model_fingerprint, "version": version, "language": language or "auto",
                    "extraction": LOCAL_EXTRACTION, "start": round(offset, 3), "end": round(offset + length, 3)}
        return cached_chunk(path, cache_dir, identity, invoke) if cache_dir else invoke()
    return transcribe_chunks(chunks, transcribe_one)


def _duration_guard(duration: float, allow_long: bool) -> None:
    if duration > 7200 and not allow_long:
        error = AcquisitionError("duration_limit", "Audio exceeds the 120-minute guard; explicit --allow-long required")
        error.exit_code = 8
        raise error


def transcribe_video(video_path: str, audio_out: Path, backend: str | None = None, api_key: str | None = None,
                     language: str | None = None, local_model: Path | str | None = None,
                     cache_dir: Path | None = None, retry_uncertain: bool = False,
                     allow_long: bool = False) -> tuple[list[dict], str]:
    DETECTED_LANGUAGE["value"] = None; CHUNK_FAILURES.clear()
    cache_dir = Path(cache_dir) if cache_dir is not None else Path(audio_out).parent / ".whisper-cache"
    if backend == "local" or local_model is not None:
        model = validate_local_model(Path(local_model).expanduser()) if local_model is not None else configured_local_model()
        if model is None: raise AcquisitionError("dependency", "Local Whisper requires LOCAL_WHISPER_MODEL or --local-model")
        segments = _transcribe_local_video(video_path, Path(audio_out), model, language, cache_dir, allow_long)
        if not segments: raise AcquisitionError("empty_response", "Local Whisper returned no timed speech")
        return segments, "local"
    if backend not in {"groq", "openai"}:
        raise AcquisitionError("invalid_input", "Explicit transcription provider required: groq, openai, or local")
    if api_key is None: _, api_key = load_api_key(backend)
    if not api_key: raise AcquisitionError("authentication", "No API key is available for the selected Whisper provider")
    audio_path = extract_audio(video_path, Path(audio_out)); duration = audio_duration(audio_path)
    _duration_guard(duration, allow_long)
    plan = plan_chunks(duration, audio_path.stat().st_size)
    chunks = [(audio_path, 0.0, duration)] if len(plan) == 1 and audio_path.stat().st_size <= MAX_AUDIO_BYTES else split_audio(audio_path, Path(audio_out).parent / "chunks", plan)
    model = GROQ_MODEL if backend == "groq" else OPENAI_MODEL
    position = {id(path): (offset, length) for path, offset, length in chunks}
    def callback(path: Path):
        offset, length = position[id(path)]
        identity = {"engine": backend, "model": model, "version": "openai-audio-v1", "language": language,
                    "extraction": REMOTE_EXTRACTION, "start": round(offset, 3), "end": round(offset + length, 3)}
        call = lambda: _transcribe_file(backend, api_key, path, language, work=cache_dir, retry_uncertain=retry_uncertain)
        return cached_chunk(path, cache_dir, identity, call) if cache_dir else call()
    segments = transcribe_chunks(chunks, callback)
    if not segments: raise AcquisitionError("empty_response", "Whisper returned no transcript segments")
    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2: raise SystemExit("usage: whisper.py <video-path> [audio-out.mp3] --backend groq|openai|local")
    video = sys.argv[1]; audio = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    selected = sys.argv[sys.argv.index("--backend") + 1] if "--backend" in sys.argv else None
    result, used = transcribe_video(video, audio, backend=selected)
    print(json.dumps({"backend": used, "segments": result}, ensure_ascii=False, indent=2))
