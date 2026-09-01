#!/usr/bin/env python3
"""Optional speech-to-text fallback using streamed stdlib HTTPS multipart uploads."""
from __future__ import annotations

import http.client
import json
import math
import mimetypes
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from frame_utils import probe_media


BACKENDS = {
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
        "key": "GROQ_API_KEY",
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
        "key": "OPENAI_API_KEY",
    },
}
MAX_AUDIO_BYTES = 23 * 1024 * 1024


def _config_value(path: Path, key_name: str) -> str | None:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == key_name:
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def load_api_key(preferred: str | None = None) -> tuple[str | None, str | None]:
    choices = [preferred] if preferred else ["groq", "openai"]
    config = Path.home() / ".config" / "summarize-video" / ".env"
    for backend in choices:
        if backend not in BACKENDS:
            continue
        key_name = BACKENDS[backend]["key"]
        value = os.environ.get(key_name) or _config_value(config, key_name)
        if value:
            return backend, value.strip()
    return None, None


def extract_audio(media: Path, output: Path) -> Path:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required for speech transcription")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        executable, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(media.resolve()), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "48k", str(output.resolve()),
    ], capture_output=True, text=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(result.stderr.strip() or "audio extraction produced no data")
    return output


def _chunk_plan(duration: float, size: int) -> list[tuple[float, float]]:
    count = max(1, math.ceil(size / MAX_AUDIO_BYTES))
    width = duration / count if duration > 0 else 0
    return [
        (round(index * width, 3), round(duration - index * width if index == count - 1 else width, 3))
        for index in range(count)
    ]


def _slice_audio(audio: Path, output_dir: Path, plan: list[tuple[float, float]]) -> list[tuple[Path, float]]:
    if len(plan) == 1:
        return [(audio, 0.0)]
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, (offset, duration) in enumerate(plan):
        output = output_dir / f"audio_{index:03d}.mp3"
        result = subprocess.run([
            executable, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{offset:.3f}", "-i", str(audio.resolve()), "-t", f"{duration:.3f}",
            "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
            str(output.resolve()),
        ], capture_output=True, text=True)
        if result.returncode != 0 or not output.exists():
            raise RuntimeError(result.stderr.strip() or f"failed to create audio chunk {index}")
        chunks.append((output, offset))
    return chunks


def _multipart_parts(model: str, audio: Path, boundary: str) -> tuple[bytes, bytes]:
    mime = mimetypes.guess_type(audio.name)[0] or "audio/mpeg"
    prefix = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"temperature\"\r\n\r\n0\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio.name}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    return prefix, suffix


def _upload(backend: str, api_key: str, audio: Path) -> dict:
    config = BACKENDS[backend]
    parsed = urlparse(config["endpoint"])
    boundary = "EvidenceBoundary" + secrets.token_hex(12)
    prefix, suffix = _multipart_parts(config["model"], audio, boundary)
    content_length = len(prefix) + audio.stat().st_size + len(suffix)
    last_error = ""
    for attempt in range(3):
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=300,
            context=ssl.create_default_context(),
        )
        try:
            connection.putrequest("POST", parsed.path)
            connection.putheader("Authorization", f"Bearer {api_key}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.putheader("User-Agent", "visual-video-summarizer/2 stdlib-http")
            connection.endheaders()
            connection.send(prefix)
            with audio.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    connection.send(block)
            connection.send(suffix)
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                return json.loads(body)
            last_error = f"HTTP {response.status}: {body[:400]}"
            if response.status not in {429, 500, 502, 503, 504}:
                break
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            connection.close()
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"speech-to-text request failed: {last_error}")


def _response_segments(payload: dict, offset: float) -> list[dict]:
    segments = []
    for row in payload.get("segments") or []:
        text = str(row.get("text") or "").strip()
        if text:
            segments.append({
                "start": round(float(row.get("start") or 0) + offset, 3),
                "end": round(float(row.get("end") or 0) + offset, 3),
                "text": text,
            })
    if not segments and str(payload.get("text") or "").strip():
        segments.append({"start": offset, "end": offset, "text": str(payload["text"]).strip()})
    return segments


def transcribe_media(
    media: Path,
    work: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    if not backend or not api_key:
        detected_backend, detected_key = load_api_key(backend)
        backend = backend or detected_backend
        api_key = api_key or detected_key
    if not backend or not api_key:
        raise RuntimeError(
            "no speech-to-text key; set GROQ_API_KEY or OPENAI_API_KEY in the environment "
            "or ~/.config/summarize-video/.env"
        )
    audio = extract_audio(media, work / "audio.mp3")
    duration = probe_media(audio)["duration"]
    chunks = _slice_audio(audio, work / "audio-chunks", _chunk_plan(duration, audio.stat().st_size))
    segments: list[dict] = []
    failures = 0
    for index, (chunk, offset) in enumerate(chunks):
        try:
            segments.extend(_response_segments(_upload(backend, api_key, chunk), offset))
        except RuntimeError as exc:
            failures += 1
            print(f"[vsum] speech chunk {index + 1}/{len(chunks)} failed: {exc}", file=sys.stderr)
    if failures == len(chunks) or not segments:
        raise RuntimeError("speech-to-text produced no usable transcript")
    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: speech_to_text.py <media-file>")
    rows, used_backend = transcribe_media(Path(sys.argv[1]), Path("speech-work"))
    print(json.dumps({"backend": used_backend, "segments": rows}, indent=2, ensure_ascii=False))
