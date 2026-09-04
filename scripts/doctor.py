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

from safety import YTDLP_FLAGS, ytdlp_command


def check(local: bool = False, pdf: bool = False) -> dict:
    rows = [{"name": "Python", "required": True, "available": sys.version_info >= (3, 10),
             "version": sys.version.split()[0]}]
    for name in ("ffmpeg", "ffprobe", "yt-dlp"):
        binary = shutil.which(name)
        row = {"name": name, "required": name != "yt-dlp" or not local,
               "available": bool(binary), "path": binary}
        if binary:
            command = ytdlp_command(["--version"]) if name == "yt-dlp" else [binary, "-version"]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                row["available"] = result.returncode == 0
                if result.returncode == 0:
                    row["version"] = (result.stdout.splitlines() or ["version unavailable"])[0][:160]
                else:
                    row["note"] = "Version/safety-flag check failed; update this dependency explicitly."
            except (OSError, subprocess.TimeoutExpired):
                row["available"] = False
                row["note"] = "Could not run the version check."
        rows.append(row)
    chrome = next((str(p) for p in [
        Path(os.environ.get("CHROME_BIN") or "/nonexistent"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ] if p.is_file()), None)
    chrome = chrome or next((shutil.which(n) for n in
                            ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
                            if shutil.which(n)), None)
    weasy = shutil.which("weasyprint") or bool(importlib.util.find_spec("weasyprint"))
    rows.append({"name": "PDF engine", "required": pdf, "available": bool(chrome or weasy),
                 "note": "Installed engine detected; export confirms system libraries are usable." if chrome or weasy
                 else "Optional: install Chrome or WeasyPrint yourself if PDF is needed."})
    config = Path.home() / ".config" / "summarize-video" / ".env"
    return {
        "ready": all(r["available"] for r in rows if r["required"]), "checks": rows,
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
    parser.add_argument("--pdf", action="store_true", help="Require an installed PDF engine")
    parser.add_argument("--json", action="store_true", help="Machine-readable result")
    args = parser.parse_args()
    result = check(args.local, args.pdf)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Ready" if result["ready"] else "Missing required dependency")
        for row in result["checks"]:
            status = "OK" if row["available"] else ("MISSING" if row["required"] else "optional")
            print(f"- {row['name']}: {status}" + (f" ({row['version']})" if row.get("version") else ""))
            if row.get("note"):
                print(f"  {row['note']}")
        print("Cloud transcription: " + result["cloud_transcription"])
        print(result["model_privacy"])
        if result["config_permissions_private"] is False:
            print("Config permissions: restrict ~/.config/summarize-video/.env to its owner (chmod 600).")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
