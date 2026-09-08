"""A fake `yt-dlp` for hermetic tests: answers the exact calls the scripts make.

Behaviour is driven by environment variables so the test never touches the
network:
  VSUM_SHIM_FIXTURE_DIR  directory holding video.mp4 and captions.vtt
  VSUM_SHIM_MODE         captions (default) | no-captions | unavailable
  VSUM_SHIM_LOG          file that receives one JSON line per invocation (argv)
  VSUM_SHIM_DURATION     duration reported in video.info.json (seconds)

Dispatch on argv, mirroring transcript.py / candidates.py / doctor.py:
  --version                         → prints a version, exit 0
  --write-info-json (skip-download) → writes video.info.json next to the -o template
                                      (mode `unavailable`: yt-dlp's "Video unavailable"
                                      error on stderr, nothing written, exit 1)
  --write-subs                      → writes video.<key>.vtt when captions exist
  -f bv*…  (video download)         → copies the fixture mp4 to the -o template
  -f ba/bestaudio (audio for Whisper) → exit 1: the Whisper path must not be reached

`install(bin_dir)` puts the shim on PATH the way a real yt-dlp would be found:
a `#!/bin/sh` wrapper on POSIX, and on Windows a genuine `yt-dlp.exe` built
from the console-script launcher that pip vendors (`pip/_vendor/distlib/t64.exe`
+ shebang + a zip holding this file as `__main__.py`). A `.cmd` shim cannot
work there: `CreateProcess` resolves a bare name by appending `.exe` only, and
cmd.exe would mangle the `<` in the download format and the `^` in
`--sub-langs`. Building a native exe exercises exactly the production path
(bare `yt-dlp` resolved from PATH by `safety.ytdlp_command`).
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import sys
import zipfile
from pathlib import Path

UNAVAILABLE_MESSAGE = ("ERROR: [youtube] fixture: Video unavailable. This video is private "
                       "https://example.invalid/watch?v=fixture&token=SECRET-TOKEN-VALUE")


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
    if mode == "unavailable":
        print(UNAVAILABLE_MESSAGE, file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    if "--write-info-json" in argv:
        info = {"id": "fixture", "title": "Fixture video", "uploader": "tests",
                "duration": float(os.environ.get("VSUM_SHIM_DURATION", "6")),
                "webpage_url": "https://www.youtube.com/watch?v=fixture", "language": "en", "chapters": [],
                "subtitles": {}, "automatic_captions": {}}
        if mode == "captions":
            info["subtitles"] = {"en": [{"ext": "vtt", "url": os.environ.get("VSUM_SHIM_CAPTION_URL", "https://example.invalid/en")}]}
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


# ----------------------------------------------------------------------------- installer


def _windows_launcher() -> bytes:
    """The console-script launcher pip vendors (distlib's t64.exe); SkipTest when absent."""
    import unittest
    try:
        from importlib import resources
        package = resources.files("pip._vendor.distlib")
        name = "t64-arm.exe" if platform.machine().lower() in ("arm64", "aarch64") else "t64.exe"
        return (package / name).read_bytes()
    except Exception as exc:  # pragma: no cover - depends on the host's pip
        raise unittest.SkipTest(
            f"no console-script launcher available to build a native yt-dlp.exe shim ({exc}); "
            "fallback: setuptools' cli-64.exe with a sibling -script.py") from exc


def install(bin_dir: Path) -> dict[str, str]:
    """Install the shim as `yt-dlp` in `bin_dir`; returns the environment fragment to prepend."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        python = sys.executable
        shebang = b"#!" + (f'"{python}"' if " " in python else python).encode("utf-8") + b"\r\n"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("__main__.py", Path(__file__).read_bytes())
        (bin_dir / "yt-dlp.exe").write_bytes(_windows_launcher() + shebang + archive.getvalue())
    else:
        wrapper = bin_dir / "yt-dlp"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
