"""Host-neutral environment helpers: install hints, executable discovery, fonts, UTF-8 stdio.

Nothing here installs, downloads or changes settings. The functions only look at
the running platform so that error messages and discovery paths are correct on
Linux, macOS and Windows alike.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

TOOL_PACKAGES = {
    "ffmpeg": {"darwin": "brew install ffmpeg", "linux": "apt install ffmpeg (or your distribution's package)",
               "win32": "winget install Gyan.FFmpeg"},
    "ffprobe": {"darwin": "brew install ffmpeg", "linux": "apt install ffmpeg (ffprobe ships with it)",
                "win32": "winget install Gyan.FFmpeg (ffprobe ships with it)"},
    "yt-dlp": {"darwin": "brew install yt-dlp", "linux": "pipx install yt-dlp (or the official release)",
               "win32": "winget install yt-dlp.yt-dlp"},
}


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def install_hint(tool: str) -> str:
    """One platform-appropriate sentence; the agent must still ask before installing."""
    table = TOOL_PACKAGES.get(tool, {})
    suggestion = table.get(platform_key())
    if suggestion:
        return f"Install {tool} with your package manager, e.g. `{suggestion}`, after the user approves."
    return f"Install {tool} from its official release after the user approves."


def python_command() -> str:
    """The interpreter name to print in reports (never executed by this module)."""
    return "python" if platform_key() == "win32" else "python3"


def _windows_program_dirs() -> list[Path]:
    dirs = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            dirs.append(Path(value))
    return dirs


def chrome_candidates() -> list[str]:
    """Absolute paths and PATH names, in preference order, for a headless-capable browser."""
    candidates: list[str] = []
    if platform_key() == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif platform_key() == "win32":
        for base in _windows_program_dirs():
            candidates.append(str(base / "Google" / "Chrome" / "Application" / "chrome.exe"))
            candidates.append(str(base / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    candidates += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"]
    return candidates


def find_chrome() -> str | None:
    explicit = os.environ.get("CHROME_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    for candidate in chrome_candidates():
        path = Path(candidate)
        if path.is_absolute():
            if path.is_file():
                return str(path)
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def mono_font_candidates() -> list[str]:
    """Monospace TrueType fonts that PIL can open, most likely first for this platform."""
    common = ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "NotoSansMono-Regular.ttf"]
    if platform_key() == "darwin":
        return ["Menlo.ttc", "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf", *common]
    if platform_key() == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return ["consola.ttf", str(Path(windir) / "Fonts" / "consola.ttf"), "cour.ttf",
                str(Path(windir) / "Fonts" / "cour.ttf"), *common]
    return [*common, "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf", "Menlo.ttc"]


def utf8_stdio() -> None:
    """Reports contain Hebrew and symbols; a cp1252 console must not crash the script."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env
