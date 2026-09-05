"""A fake `yt-dlp` for hermetic tests: answers the exact calls the scripts make.

Behaviour is driven by environment variables so the test never touches the
network:
  VSUM_SHIM_FIXTURE_DIR  directory holding video.mp4 and captions.vtt
  VSUM_SHIM_MODE         captions (default) | no-captions
  VSUM_SHIM_LOG          file that receives one JSON line per invocation (argv)

Dispatch on argv, mirroring transcript.py / candidates.py / doctor.py:
  --version                         → prints a version, exit 0
  --write-info-json (skip-download) → writes video.info.json next to the -o template
  --write-subs                      → writes video.<key>.vtt when captions exist
  -f bv*…  (video download)         → copies the fixture mp4 to the -o template
  -f ba/bestaudio (audio for Whisper) → exit 1: the Whisper path must not be reached
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def _template_dir(argv: list[str]) -> Path | None:
    if "-o" in argv:
        return Path(argv[argv.index("-o") + 1]).parent
    return None


def main(argv: list[str]) -> int:
    log = os.environ.get("VSUM_SHIM_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(argv) + "\n")
    fixtures = Path(os.environ.get("VSUM_SHIM_FIXTURE_DIR") or ".")
    mode = os.environ.get("VSUM_SHIM_MODE", "captions")
    if "--version" in argv:
        print("2026.09.01")
        return 0
    out_dir = _template_dir(argv)
    if out_dir is None:
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    if "--write-info-json" in argv:
        info = {"id": "fixture", "title": "Fixture video", "uploader": "tests",
                "duration": float(os.environ.get("VSUM_SHIM_DURATION", "6")),
                "webpage_url": "https://www.youtube.com/watch?v=fixture", "language": "en", "chapters": [],
                "subtitles": {}, "automatic_captions": {}}
        if mode == "captions":
            info["subtitles"] = {"en": [{"ext": "vtt", "url": "https://example.invalid/en"}]}
            info["automatic_captions"] = {
                "en-orig": [{"ext": "vtt", "url": "https://example.invalid/en-orig"}],
                "en-de": [{"ext": "vtt", "url": "https://example.invalid/en?tlang=de"}],
            }
        (out_dir / "video.info.json").write_text(json.dumps(info), encoding="utf-8")
        return 0
    if "--write-subs" in argv:
        if mode == "captions":
            pattern = argv[argv.index("--sub-langs") + 1] if "--sub-langs" in argv else "^en$"
            key = pattern.strip("^$").replace("\\", "") or "en"
            shutil.copyfile(fixtures / "captions.vtt", out_dir / f"video.{key}.vtt")
        return 0
    if "-f" in argv:
        fmt = argv[argv.index("-f") + 1]
        if fmt.startswith("ba"):
            print("shim: audio download refused (Whisper path must not be reached)", file=sys.stderr)
            return 1
        template = Path(argv[argv.index("-o") + 1])
        target = template.with_name(template.name.replace("%(ext)s", "mp4"))
        shutil.copyfile(fixtures / "video.mp4", target)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
