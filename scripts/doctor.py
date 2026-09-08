#!/usr/bin/env python3
"""Read-only readiness check. No installs, network requests, or key-file reads."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hostenv import find_chrome, install_hint, javascript_runtime, platform_key, python_command, run_text, utf8_stdio  # noqa: E402
from safety import YTDLP_FLAGS, ytdlp_command  # noqa: E402


def check(local: bool = False, pdf: bool = False, local_model: str | None = None) -> dict:
    rows = [{"name": "Python", "required": True, "available": sys.version_info >= (3, 10),
             "version": sys.version.split()[0]}]
    for name in ("ffmpeg", "ffprobe", "yt-dlp"):
        binary = shutil.which(name)
        row = {"name": name, "required": name != "yt-dlp" or not local,
               "available": bool(binary), "path": binary}
        if binary:
            command = ytdlp_command(["--version"]) if name == "yt-dlp" else [binary, "-version"]
            try:
                result = run_text(command, timeout=10)
                row["available"] = result.returncode == 0
                if result.returncode == 0:
                    row["version"] = (result.stdout.splitlines() or ["version unavailable"])[0][:160]
                else:
                    row["note"] = "Version/safety-flag check failed; update this dependency explicitly."
            except (OSError, subprocess.TimeoutExpired):
                row["available"] = False
                row["note"] = "Could not run the version check."
        if not row["available"]:
            # The platform-appropriate install command the agent proposes to the
            # user (SKILL.md §2); the doctor itself never installs anything.
            row["hint"] = install_hint(name)
        rows.append(row)
    chrome = find_chrome()
    weasy = shutil.which("weasyprint") or bool(importlib.util.find_spec("weasyprint"))
    rows.append({"name": "PDF engine", "required": False, "requested": pdf, "available": bool(chrome or weasy),
                 "path": chrome,
                 "note": "Installed engine detected; export confirms system libraries are usable." if chrome or weasy
                 else "Optional: install Chrome/Edge or WeasyPrint yourself if PDF is needed."})
    runtime = javascript_runtime(os.environ.get("PATH", ""))
    rows.append({"name": "YouTube JavaScript runtime", "required": False, "available": bool(runtime),
                 "path": (runtime or {}).get("path"), "version": (runtime or {}).get("version"),
                 "note": "Full YouTube support needs an installed compatible EJS component and supported JS runtime; "
                         "a runtime alone does not prove source access. No remote components are downloaded."})
    ejs = bool(importlib.util.find_spec("yt_dlp_ejs"))
    rows.append({"name": "yt-dlp EJS", "required": False, "available": ejs,
                 "note": "Detected in this Python environment only; packaged yt-dlp may bundle EJS separately. "
                         "Source acquisition reports a missing capability without bypassing restrictions."})
    model = local_model or os.environ.get("LOCAL_WHISPER_MODEL")
    binary = shutil.which("whisper-cli")
    model_path = Path(model).expanduser() if model else None
    valid_model = bool(model_path and model_path.is_file() and model_path.suffix == ".bin"
                       and ".en" not in model_path.name and model_path.stat().st_size > 1024 * 1024)
    rows.append({"name": "local transcription", "required": False, "available": bool(binary and valid_model),
                 "path": binary, "model_configured": bool(model),
                 "note": "Optional whisper.cpp needs whisper-cli and a configured compatible multilingual GGML model. "
                         "Model execution validates compatibility; no model download or credential read occurs here."})
    rows.append({"name": "Pillow", "required": False, "available": bool(importlib.util.find_spec("PIL")),
                 "note": "Optional: contact sheets need it; without it every candidate is read individually."})
    rows.append({"name": "workflow", "required": False,
                 "available": (Path(__file__).resolve().parent / "workflow.py").is_file(),
                 "note": "scripts/workflow.py is the canonical entry point (init → run → verify)."})
    config = Path.home() / ".config" / "summarize-video" / ".env"
    return {
        "ready": all(r["available"] for r in rows if r["required"]), "checks": rows,
        "platform": platform_key(), "python_command": python_command(),
        "cloud_transcription": "off unless --whisper groq|openai is explicitly selected",
        "model_privacy": "Your agent provider processes the transcript and selected images under its own settings.",
        "config_present": config.is_file(),
        "config_permissions_private": (config.stat().st_mode & 0o077 == 0) if config.is_file() and os.name == "posix" else None,
        "ytdlp_safety_flags": list(YTDLP_FLAGS),
        "scope": "Checks local executable versions and config-file metadata only; no install, upload, or credential read.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="yt-dlp is optional for a local source")
    parser.add_argument("--pdf", action="store_true", help="Check requested PDF support; HTML may proceed if unavailable")
    parser.add_argument("--local-model", default=None)
    parser.add_argument("--json", action="store_true", help="Machine-readable result")
    args = parser.parse_args()
    utf8_stdio()
    result = check(args.local, args.pdf, args.local_model)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Ready" if result["ready"] else "Missing required dependency")
        for row in result["checks"]:
            status = "OK" if row["available"] else ("MISSING" if row["required"] else "optional")
            print(f"- {row['name']}: {status}" + (f" ({row['version']})" if row.get("version") else ""))
            if row.get("note"):
                print(f"  {row['note']}")
            if row.get("hint") and (row["required"] or not row["available"]):
                print(f"  hint: {row['hint']}")
        print("Cloud transcription: " + result["cloud_transcription"])
        print(result["model_privacy"])
        if result["config_permissions_private"] is False:
            print("Config permissions: restrict ~/.config/summarize-video/.env to its owner (chmod 600).")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
