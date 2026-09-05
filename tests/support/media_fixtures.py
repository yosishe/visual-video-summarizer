"""Shared media fixtures and process helpers for the CLI-level tests.

Everything here is synthesized locally with ffmpeg (no network, no checked-in
media) and shared by `test_reliability_integration.py` (stage-boundary cases)
and `test_workflow_e2e.py` (the controller loop through a fake yt-dlp), so the
two modules cannot drift apart.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

CAPTIONS = "WEBVTT\n\n" + "".join(
    f"00:00:{i * 2:02d}.000 --> 00:00:{i * 2 + 2:02d}.000\nsegment {i} shows the counter at value {i * 7}\n\n"
    for i in range(6))


def transcript_payload() -> dict:
    return {"schema_version": 2, "status": "ok", "source": "captions", "language": "en",
            "video": {"id": "fixture", "title": "Fixture", "duration": 12.0, "is_url": False},
            "segments": [{"seg_id": f"seg_{i:04d}", "start": i * 2.0, "end": i * 2.0 + 2.0,
                          "text": f"segment {i} shows the counter at value {i * 7}"} for i in range(6)]}


def chapters(visual_second: bool = True) -> list[dict]:
    rows = [{"chapter_id": "ch01", "title": "Pattern", "start": 0.0, "end": 6.0, "needs_frames": True,
             "visual_targets": [{"target_id": "t_pattern", "kind": "state", "seg_ids": ["seg_0001"], "why": "pattern"}]},
            {"chapter_id": "ch02", "title": "Black", "start": 6.0, "end": 12.0, "needs_frames": visual_second}]
    return rows


def synthesize_fixture_video(path: Path) -> None:
    """0–6 s: a moving test pattern (visual); 6–12 s: pure black (no usable frame)."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=6",
        "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=10:duration=6",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def synthesize_slides_video(path: Path, slide_seconds: float = 25.0) -> None:
    """Four still, distinct 'slides' back to back (a slideshow): the picture holds
    for `slide_seconds` each, so the visual probe sees settled content."""
    boxes = [
        "drawbox=x=80:y=60:w=480:h=240:color=blue@1:t=fill",
        "drawbox=x=0:y=150:w=640:h=60:color=white@1:t=fill",
        "drawbox=x=200:y=40:w=240:h=280:color=red@1:t=fill",
        "drawbox=x=40:y=40:w=560:h=280:color=gray@1:t=20",
    ]
    backgrounds = ["white", "black", "0x202020", "0xE0E0E0"]
    inputs: list[str] = []
    filters: list[str] = []
    for index, (background, box) in enumerate(zip(backgrounds, boxes)):
        inputs += ["-f", "lavfi", "-i", f"color=c={background}:size=640x360:rate=10:duration={slide_seconds}"]
        filters.append(f"[{index}:v]{box}[s{index}]")
    concat = "".join(f"[s{index}]" for index in range(len(boxes))) + f"concat=n={len(boxes)}:v=1:a=0[v]"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(filters) + ";" + concat, "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def run_script(root: Path, *arguments, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a script as the agent would: cwd = the task root, output decoded as UTF-8.

    Deliberately does NOT inject PYTHONUTF8: the scripts must survive the host
    locale on their own (they reconfigure their stdio; captured child output is
    decoded through hostenv.run_text)."""
    environment = os.environ.copy()
    environment["PYTHONPYCACHEPREFIX"] = str(root / "pycache")
    environment["SUMMARY_LANG"] = "en"
    if env:
        environment.update(env)
    return subprocess.run([str(a) for a in arguments], cwd=str(root), env=environment,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def python() -> str:
    return sys.executable
