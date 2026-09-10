"""Host-neutral environment helpers: install hints, executable discovery, fonts, UTF-8 stdio.

Nothing here installs, downloads or changes settings. The functions only look at
the running platform so that error messages and discovery paths are correct on
Linux, macOS and Windows alike. The one directory this module knows about is the
user-level tool directory that `bootstrap.py` fills (`managed_home()`); the
helpers here only make its contents visible to the running process.
"""
from __future__ import annotations

import os
import functools
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

MANAGED_HOME_ENV = "SUMMARIZE_VIDEO_HOME"

TOOL_PACKAGES = {
    "ffmpeg": {"darwin": "brew install ffmpeg", "linux": "apt install ffmpeg (or your distribution's package)",
               "win32": "winget install Gyan.FFmpeg (or: choco install ffmpeg / scoop install ffmpeg)"},
    "ffprobe": {"darwin": "brew install ffmpeg", "linux": "apt install ffmpeg (ffprobe ships with it)",
                "win32": "winget install Gyan.FFmpeg (ffprobe ships with it; or choco/scoop install ffmpeg)"},
    "yt-dlp": {"darwin": "brew install yt-dlp", "linux": "pipx install yt-dlp (or the official release)",
               "win32": "winget install yt-dlp.yt-dlp (or: choco install yt-dlp / scoop install yt-dlp / pipx install yt-dlp)"},
}


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def install_hint(tool: str) -> str:
    """One platform-appropriate sentence naming the two ways to get the tool.

    The first is the user-level setup in this repository (`scripts/bootstrap.py`
    installs into `managed_home()` without administrator rights; `workflow.py`
    runs it automatically). The second is the host's package manager, which
    changes the system and therefore still needs the user's approval."""
    table = TOOL_PACKAGES.get(tool, {})
    suggestion = table.get(platform_key())
    setup = (f"run `{python_command()} \"{Path(__file__).resolve().parent / 'bootstrap.py'}\"` "
             "(user-level, no admin rights)")
    if suggestion:
        return (f"Install {tool}: {setup}, or with your package manager, e.g. `{suggestion}`, "
                "after the user approves.")
    return f"Install {tool}: {setup}, or from its official release after the user approves."


def python_command() -> str:
    """The interpreter name to print in reports (never executed by this module)."""
    return "python" if platform_key() == "win32" else "python3"


# ----------------------------------------------------------------------------- managed tools


def managed_home() -> Path:
    """The one user-owned directory `bootstrap.py` writes to; delete it to remove everything.

    `SUMMARIZE_VIDEO_HOME` overrides the default (`%LOCALAPPDATA%\\summarize-video` on
    Windows, `$XDG_CACHE_HOME/summarize-video` or `~/.cache/summarize-video` elsewhere).
    Nothing is created by asking for the path."""
    override = os.environ.get(MANAGED_HOME_ENV)
    if override:
        return Path(override).expanduser()
    if platform_key() == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "summarize-video"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "summarize-video"


def managed_bin_dir() -> Path:
    """Executables the bootstrap placed for this user: ffmpeg, ffprobe and the yt-dlp launcher."""
    return managed_home() / "bin"


def interpreter_tag() -> str:
    """One directory name per interpreter build, so a Python upgrade never loads a stale compiled wheel."""
    tag = getattr(sys.implementation, "cache_tag", None) or f"python-{sys.version_info[0]}{sys.version_info[1]}"
    platform = sysconfig.get_platform().replace(".", "_").replace("-", "_")
    return f"{tag}-{platform}"


def managed_site_dir() -> Path:
    """Python packages the bootstrap installed for this exact interpreter (yt-dlp, Pillow)."""
    return managed_home() / "site" / interpreter_tag()


def _prepend_path(env: dict, key: str, entry: str) -> None:
    current = env.get(key, "")
    parts = [p for p in current.split(os.pathsep) if p] if current else []
    if entry not in parts:
        env[key] = os.pathsep.join([entry, *parts])


def activate_managed_tools(env: dict | None = None) -> dict:
    """Make the user-level tools visible: managed `bin` on PATH, managed `site` on PYTHONPATH/sys.path.

    Only directories that already exist are added, nothing is installed and no
    network is touched. Without an `env` the running process is updated
    (`os.environ` and `sys.path`); with one, that mapping is updated and
    returned, which is how `child_env` passes the tools on to the stage scripts."""
    target = os.environ if env is None else env
    bin_dir, site = managed_bin_dir(), managed_site_dir()
    if bin_dir.is_dir():
        _prepend_path(target, "PATH", str(bin_dir))
    if site.is_dir():
        _prepend_path(target, "PYTHONPATH", str(site))
        if env is None and str(site) not in sys.path:
            # After the interpreter's own packages: a system Pillow keeps winning in-process.
            sys.path.append(str(site))
    return target


def missing_tools(*names: str) -> list[str]:
    """The subset of `names` that PATH does not resolve (no version check, no network).

    The user-level tool directory is activated first, so a stage script started
    on its own finds the same ffmpeg the workflow controller found."""
    activate_managed_tools()
    return [name for name in names if shutil.which(name) is None]


def require_tools(*names: str) -> None:
    """Refuse to start when a required executable is absent, naming it with its install hint.

    Exit 1 (a tool problem), never a traceback: every entry point calls this
    before any media command so a missing ffmpeg at the grab stage is a one-line
    stop instead of a FileNotFoundError deep inside a subprocess call."""
    missing = missing_tools(*names)
    if missing:
        raise SystemExit("Missing required tool(s): " + ", ".join(missing) + ". "
                         + " ".join(install_hint(name) for name in missing))


def run_text(command: list[str], *, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with captured output decoded as UTF-8 (replacement on stray bytes).

    ffmpeg/ffprobe/yt-dlp print file names, titles and Hebrew captions; decoding
    them with the console locale (cp1252 on a default Windows console) raised
    UnicodeDecodeError in the middle of a stage. The output is a str exactly as
    before, so callers parse it unchanged."""
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout or 600, **kwargs)


@functools.lru_cache(maxsize=8)
def javascript_runtime(path_env: str) -> dict | None:
    """Installed supported runtimes only; no package or remote component setup."""
    for name, minimum in (("deno", (2, 3, 0)), ("node", (22, 0, 0))):
        binary = shutil.which(name, path=path_env)
        if not binary:
            continue
        try:
            result = run_text([binary, "--version"], timeout=10)
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout or "")
            if result.returncode == 0 and match and tuple(map(int, match.groups())) >= minimum:
                return {"name": name, "path": binary, "version": match.group(0)}
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


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
    activate_managed_tools(env)
    if extra:
        env.update(extra)
    return env
