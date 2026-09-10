#!/usr/bin/env python3
"""User-level setup for the tools the skill needs: yt-dlp, Pillow, ffmpeg and ffprobe.

    python bootstrap.py [--json] [--dry-run] [--system] [--local] [--skip-optional]

Everything lands in ONE user-owned directory (`hostenv.managed_home()`, override
with SUMMARIZE_VIDEO_HOME; delete it to remove every trace):

    bin/   ffmpeg, ffprobe (static builds, pinned commit + SHA-256 below) and a
           native `yt-dlp` launcher for the interpreter that ran this script
    site/<interpreter>/   yt-dlp and Pillow, installed with `pip --target`

No administrator rights, no system package manager, no shell profile edits and
no PATH changes outside the skill's own processes: `workflow.py` and
`doctor.py` prepend `bin/` and `site/` themselves (`hostenv.activate_managed_tools`).
`--system` is the only way this script touches the host (apt/dnf/apk/pacman,
Homebrew or winget for ffmpeg) and is never passed automatically.

Network: pypi.org / files.pythonhosted.org for the Python packages and
github.com (redirecting to its media host) for the ffmpeg archive. A download
whose size or SHA-256 differs from the pin is discarded, not installed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from hostenv import (  # noqa: E402
    activate_managed_tools, install_hint, interpreter_tag, managed_bin_dir, managed_home, managed_site_dir,
    platform_key, python_command, run_text, utf8_stdio,
)
from safety import atomic_write, sanitize_tool_output  # noqa: E402

# The static ffmpeg/ffprobe builds behind the `static-ffmpeg` PyPI project, pinned to
# one commit of their repository and to the SHA-256 of each archive (the values Git
# LFS records for that commit). Updating the pin means updating every row here.
FFMPEG_RELEASE = {
    "repository": "zackees/ffmpeg_bins",
    "commit": "df95abcb0ce6efff710dda5ef28a2f6f1dc21493",
    "version": "8.0",
    "archives": {
        "linux-x86_64": {"file": "linux.zip", "size": 142008975,
                         "sha256": "ca75b05e887c7a97676632f673031875847be83daa9794298fed9cef8cac14ad"},
        "linux-arm64": {"file": "linux_arm64.zip", "size": 131816005,
                        "sha256": "e03efe471c03b999f10988d5db62ae3bd94837463291b3c7755528b100e97d6f"},
        "darwin-x86_64": {"file": "darwin.zip", "size": 53079896,
                          "sha256": "70fd5b21cb37b6ea97c8b584cf76b3cc6a90179831c9c269811b9716c28605fb"},
        "darwin-arm64": {"file": "darwin_arm64.zip", "size": 41925556,
                         "sha256": "b2da44a8169c4d09a97db996250690c3346f72e4795521d23d3dbb1e72421207"},
        "win32-x86_64": {"file": "win32.zip", "size": 72065209,
                         "sha256": "92662c2241e93fe71b3f3a01e94a0b0dc8cfad726019f96b83bc109ce44c5d0b"},
    },
}
# Latest yt-dlp on purpose: YouTube changes faster than any pin would survive.
PIP_SPECS = {"yt-dlp": "yt-dlp", "pillow": "Pillow>=10"}
DOWNLOAD_SLACK = 1024 * 1024
MEMBER_LIMIT = 512 * 1024 * 1024
STEP_ORDER = ("ffmpeg", "yt-dlp", "pillow")
SYSTEM_FFMPEG = {
    "apt-get": (["apt-get", "update"], ["apt-get", "install", "-y", "ffmpeg"]),
    "dnf": (None, ["dnf", "install", "-y", "ffmpeg"]),
    "apk": (None, ["apk", "add", "ffmpeg"]),
    "pacman": (None, ["pacman", "-S", "--noconfirm", "ffmpeg"]),
}


class BootstrapError(SystemExit):
    """One setup step could not be completed; the message says which and why."""

    def __init__(self, step: str, message: str):
        self.step = step
        self.message = message
        super().__init__(1)

    def __str__(self) -> str:
        return f"{self.step}: {self.message}"


def _log(message: str) -> None:
    print(f"[setup] {message}", file=sys.stderr, flush=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- ffmpeg


def archive_key(machine: str | None = None) -> str | None:
    """The FFMPEG_RELEASE row for this platform and CPU, or None when there is none."""
    raw = (machine if machine is not None else platform.machine()).lower()
    if raw in ("arm64", "aarch64"):
        cpu = "arm64"
    elif raw in ("x86_64", "amd64", "x64", ""):
        cpu = "x86_64"
    else:
        return None
    key = f"{platform_key()}-{cpu}"
    return key if key in FFMPEG_RELEASE["archives"] else None


def ffmpeg_archive_url(entry: dict) -> str:
    release = FFMPEG_RELEASE
    return f"https://github.com/{release['repository']}/raw/{release['commit']}/v{release['version']}/{entry['file']}"


def fetch_bytes(url: str, expected_size: int, *, timeout: float = 60.0) -> bytes:
    """GET `url` and return at most `expected_size` + slack bytes; anything else is an error."""
    request = Request(url, headers={"User-Agent": "visual-video-summarizer-bootstrap"})
    limit = expected_size + DOWNLOAD_SLACK
    try:
        with urlopen(request, timeout=timeout) as response:
            buffer = io.BytesIO()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                buffer.write(chunk)
                if buffer.tell() > limit:
                    raise BootstrapError("ffmpeg", f"download exceeded the pinned size ({expected_size} bytes); discarded")
            return buffer.getvalue()
    except HTTPError as exc:
        raise BootstrapError("ffmpeg", f"download refused (HTTP {exc.code}) from github.com") from exc
    except (URLError, TimeoutError, OSError) as exc:
        detail = str(getattr(exc, "reason", exc)).lower()
        # urllib reports a refused CONNECT as URLError(OSError("Tunnel connection failed: 403 Forbidden")).
        if "tunnel connection failed" in detail or ("proxy" in detail and "403" in detail):
            raise BootstrapError("ffmpeg", "this environment's network policy blocks github.com (proxy 403); "
                                 "allow github.com and media.githubusercontent.com, use --system, or install "
                                 "ffmpeg with the package manager") from exc
        raise BootstrapError("ffmpeg", f"download failed: {sanitize_tool_output(str(exc), 160) or 'network error'}") from exc


def _executable_names() -> dict[str, str]:
    suffix = ".exe" if platform_key() == "win32" else ""
    return {"ffmpeg": f"ffmpeg{suffix}", "ffprobe": f"ffprobe{suffix}"}


def _version_line(path: Path) -> str:
    try:
        result = run_text([str(path), "-version"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError("ffmpeg", f"{path.name} did not run after extraction ({exc.__class__.__name__})") from exc
    if result.returncode != 0:
        raise BootstrapError("ffmpeg", f"{path.name} -version failed (exit {result.returncode})")
    return (result.stdout.splitlines() or ["version unavailable"])[0][:160]


def _place_executable(target: Path, data: bytes) -> None:
    if target.is_symlink():
        raise BootstrapError("ffmpeg", f"refusing to replace a symlink at {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        if platform_key() != "win32":
            temporary.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install_ffmpeg(bin_dir: Path | None = None, *, entry: dict | None = None,
                   fetch: Callable[[str, int], bytes] = fetch_bytes,
                   verify: Callable[[Path], str] = _version_line) -> dict:
    """Download the pinned archive, verify size and SHA-256, extract ffmpeg and ffprobe into `bin_dir`."""
    bin_dir = Path(bin_dir) if bin_dir else managed_bin_dir()
    if entry is None:
        key = archive_key()
        if key is None:
            raise BootstrapError("ffmpeg", f"no pinned static build for {platform_key()}/{platform.machine()}; "
                                 + install_hint("ffmpeg"))
        entry = FFMPEG_RELEASE["archives"][key]
    url = ffmpeg_archive_url(entry)
    _log(f"downloading ffmpeg {FFMPEG_RELEASE['version']} static build ({entry['size'] // (1024 * 1024)} MB) from github.com")
    data = fetch(url, int(entry["size"]))
    if len(data) != int(entry["size"]):
        raise BootstrapError("ffmpeg", f"download size {len(data)} differs from the pinned {entry['size']}; discarded")
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise BootstrapError("ffmpeg", "download SHA-256 differs from the pinned value; discarded, nothing installed")
    wanted = _executable_names()
    found: dict[str, zipfile.ZipInfo] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                name = Path(info.filename.replace("\\", "/")).name
                for tool, filename in wanted.items():
                    if name == filename and not info.is_dir() and tool not in found:
                        if info.file_size > MEMBER_LIMIT:
                            raise BootstrapError("ffmpeg", f"{filename} in the archive is implausibly large; refused")
                        found[tool] = info
            missing = sorted(set(wanted) - set(found))
            if missing:
                raise BootstrapError("ffmpeg", f"archive has no {', '.join(missing)}; nothing installed")
            for tool, info in found.items():
                _place_executable(bin_dir / wanted[tool], archive.read(info))
    except zipfile.BadZipFile as exc:
        raise BootstrapError("ffmpeg", "download is not a zip archive; nothing installed") from exc
    versions = {tool: verify(bin_dir / wanted[tool]) for tool in wanted}
    _log(f"ffmpeg ready in {bin_dir}: {versions['ffmpeg']}")
    return {"step": "ffmpeg", "method": "static-build", "url": url, "sha256": digest, "size": len(data),
            "bin_dir": str(bin_dir), "versions": versions, "installed_at": _now()}


def _privileged_prefix() -> list[str] | None:
    """`[]` when already root, `["sudo","-n"]` when sudo works without a prompt, None otherwise."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        return []
    if shutil.which("sudo"):
        try:
            if run_text(["sudo", "-n", "true"], timeout=15).returncode == 0:
                return ["sudo", "-n"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def install_ffmpeg_system(*, runner: Callable[..., subprocess.CompletedProcess] = run_text) -> dict:
    """`--system` only: the host package manager, when one runs without a password prompt."""
    key = platform_key()
    commands: list[list[str]] = []
    if key == "linux":
        manager = next((name for name in SYSTEM_FFMPEG if shutil.which(name)), None)
        if manager is None:
            raise BootstrapError("ffmpeg", "no supported package manager (apt-get, dnf, apk, pacman) on PATH")
        prefix = _privileged_prefix()
        if prefix is None:
            raise BootstrapError("ffmpeg", f"{manager} needs administrator rights; ask the user to run "
                                 f"`sudo {' '.join(SYSTEM_FFMPEG[manager][1])}`")
        update, install = SYSTEM_FFMPEG[manager]
        commands = [[*prefix, *cmd] for cmd in (update, install) if cmd]
    elif key == "darwin":
        if not shutil.which("brew"):
            raise BootstrapError("ffmpeg", "Homebrew is not installed; " + install_hint("ffmpeg"))
        commands = [["brew", "install", "ffmpeg"]]
    elif key == "win32":
        if not shutil.which("winget"):
            raise BootstrapError("ffmpeg", "winget is not available; " + install_hint("ffmpeg"))
        commands = [["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements",
                     "--accept-package-agreements", "--disable-interactivity"]]
    for command in commands:
        _log("system install: " + " ".join(command))
        try:
            result = runner(command, timeout=1800, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BootstrapError("ffmpeg", f"{command[0]} failed to run ({exc.__class__.__name__})") from exc
        if result.returncode != 0:
            raise BootstrapError("ffmpeg", f"{' '.join(command)} exited {result.returncode}: "
                                 + sanitize_tool_output(result.stderr or result.stdout, 240))
    return {"step": "ffmpeg", "method": "system-package", "commands": [" ".join(c) for c in commands],
            "installed_at": _now(), "note": "a new terminal may be needed before PATH shows the tool"}


# ----------------------------------------------------------------------------- Python packages


def _pip_available(runner: Callable[..., subprocess.CompletedProcess]) -> bool:
    try:
        return runner([sys.executable, "-m", "pip", "--version"], timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pip_install(specs: list[str], site_dir: Path | None = None, *,
                runner: Callable[..., subprocess.CompletedProcess] = run_text) -> dict:
    """`pip install --target` into the per-interpreter directory: no venv, no PEP 668 conflict, no root."""
    site_dir = Path(site_dir) if site_dir else managed_site_dir()
    step = "yt-dlp" if any(s.lower().startswith("yt-dlp") for s in specs) else "pillow"
    if not _pip_available(runner):
        raise BootstrapError(step, f"pip is not available for {sys.executable}; install pip for this Python "
                             "(python3-pip / `python -m ensurepip`) or install the tool with the package manager")
    site_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pip", "install", "--target", str(site_dir), "--upgrade",
               "--only-binary", ":all:", "--disable-pip-version-check", "--no-input",
               "--no-warn-script-location", *specs]
    _log("pip install --target " + str(site_dir) + " " + " ".join(specs))
    try:
        result = runner(command, timeout=900, env={**os.environ, "PIP_REQUIRE_VIRTUALENV": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(step, f"pip did not finish ({exc.__class__.__name__})") from exc
    if result.returncode != 0:
        raise BootstrapError(step, f"pip exited {result.returncode}: "
                             + sanitize_tool_output(result.stderr or result.stdout, 300))
    return {"step": step, "method": "pip --target", "specs": list(specs), "site_dir": str(site_dir),
            "interpreter": sys.executable, "installed_at": _now()}


LAUNCHER_BODY = '''# Generated by visual-video-summarizer/scripts/bootstrap.py — runs the user-level yt-dlp.
import sys
sys.path.insert(0, {site!r})
from yt_dlp import main
sys.exit(main())
'''


def _windows_launcher_stub() -> bytes | None:
    """The console-script launcher pip vendors (distlib), when this pip still ships it."""
    try:
        from importlib import resources
        package = resources.files("pip._vendor.distlib")
        name = "t64-arm.exe" if platform.machine().lower() in ("arm64", "aarch64") else "t64.exe"
        candidate = package / name
        return candidate.read_bytes() if candidate.is_file() else None
    except Exception:  # pragma: no cover - depends on the host's pip
        return None


def write_ytdlp_launcher(bin_dir: Path | None = None, site_dir: Path | None = None,
                         python: str | None = None) -> Path:
    """A `yt-dlp` on PATH that runs the pip --target install with the bootstrapping interpreter.

    POSIX: `bin/yt-dlp` (sh) → `bin/yt-dlp-launcher.py`. Windows: a native
    `yt-dlp.exe` (distlib launcher + shebang + zipped `__main__.py`), because
    `CreateProcess` resolves a bare `yt-dlp` to `.exe` only — a `.cmd` shim is
    never found by the stage scripts."""
    bin_dir = Path(bin_dir) if bin_dir else managed_bin_dir()
    site_dir = Path(site_dir) if site_dir else managed_site_dir()
    python = python or sys.executable
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = LAUNCHER_BODY.format(site=str(site_dir))
    if platform_key() == "win32":
        stub = _windows_launcher_stub()
        if stub is None:
            raise BootstrapError("yt-dlp", "this pip has no console-script launcher to build yt-dlp.exe; "
                                 "install yt-dlp with `winget install yt-dlp.yt-dlp` instead")
        shebang = b"#!" + (f'"{python}"' if " " in python else python).encode("utf-8") + b"\r\n"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("__main__.py", body)
        target = bin_dir / "yt-dlp.exe"
        _place_executable(target, stub + shebang + archive.getvalue())
        return target
    script = bin_dir / "yt-dlp-launcher.py"
    atomic_write(script, body)
    target = bin_dir / "yt-dlp"
    _place_executable(target, f'#!/bin/sh\nexec "{python}" "{script}" "$@"\n'.encode("utf-8"))
    return target


def install_ytdlp(bin_dir: Path | None = None, site_dir: Path | None = None, *,
                  runner: Callable[..., subprocess.CompletedProcess] = run_text) -> dict:
    receipt = pip_install([PIP_SPECS["yt-dlp"]], site_dir, runner=runner)
    launcher = write_ytdlp_launcher(bin_dir, site_dir)
    try:
        probe = runner([str(launcher), "--version"], timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError("yt-dlp", f"the installed yt-dlp did not run ({exc.__class__.__name__})") from exc
    if probe.returncode != 0:
        raise BootstrapError("yt-dlp", "the installed yt-dlp did not run: " + sanitize_tool_output(probe.stderr, 200))
    version = (probe.stdout.splitlines() or ["?"])[0].strip()[:40]
    _log(f"yt-dlp {version} ready as {launcher}")
    return {**receipt, "launcher": str(launcher), "version": version}


def install_pillow(site_dir: Path | None = None, *,
                   runner: Callable[..., subprocess.CompletedProcess] = run_text) -> dict:
    receipt = pip_install([PIP_SPECS["pillow"]], site_dir, runner=runner)
    _log("Pillow ready (contact sheets enabled)")
    return receipt


# ----------------------------------------------------------------------------- orchestration


def plan(snapshot: dict, *, skip_optional: bool = False) -> list[str]:
    """The steps a doctor snapshot calls for, in install order."""
    rows = {row.get("name"): row for row in snapshot.get("checks") or []}
    steps: list[str] = []
    if any(not rows.get(name, {}).get("available") for name in ("ffmpeg", "ffprobe")):
        steps.append("ffmpeg")
    ytdlp = rows.get("yt-dlp", {})
    if ytdlp and not ytdlp.get("available") and (ytdlp.get("required") or not skip_optional):
        steps.append("yt-dlp")
    if not skip_optional and rows.get("Pillow") is not None and not rows["Pillow"].get("available"):
        steps.append("pillow")
    return [step for step in STEP_ORDER if step in steps]


def _write_receipt(home: Path, entries: list[dict]) -> None:
    path = home / "receipt.json"
    previous: list = []
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8")).get("installs") or []
        except (OSError, ValueError, AttributeError):
            previous = []
    home.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"tool": "visual-video-summarizer bootstrap", "interpreter_tag": interpreter_tag(),
                                   "installs": [*previous, *entries][-40:]}, indent=2))


def run(*, local: bool = False, pdf: bool = False, system: bool = False, skip_optional: bool = False,
        dry_run: bool = False) -> dict:
    """Check, install what is missing, check again. Never raises for a failed step: `errors` lists them."""
    import doctor
    activate_managed_tools()
    before = doctor.check(local=local, pdf=pdf)
    steps = plan(before, skip_optional=skip_optional)
    home = managed_home()
    result = {"managed_home": str(home), "interpreter": sys.executable, "planned": steps, "installed": [],
              "errors": [], "dry_run": dry_run, "ready_before": bool(before.get("ready"))}
    if not steps:
        _log("every tool is already available; nothing to install")
        result.update(ready=bool(before.get("ready")), doctor=before)
        return result
    _log(f"installing for this user into {home}: {', '.join(steps)}")
    if dry_run:
        result.update(ready=bool(before.get("ready")), doctor=before)
        return result
    receipts: list[dict] = []
    for step in steps:
        try:
            if step == "ffmpeg":
                try:
                    receipts.append(install_ffmpeg())
                except BootstrapError as exc:
                    if not system:
                        raise
                    _log(f"user-level ffmpeg failed ({exc.message}); trying the system package manager (--system)")
                    receipts.append(install_ffmpeg_system())
            elif step == "yt-dlp":
                receipts.append(install_ytdlp())
            elif step == "pillow":
                receipts.append(install_pillow())
        except BootstrapError as exc:
            _log(f"{exc.step} not installed: {exc.message}")
            result["errors"].append({"step": exc.step, "message": exc.message})
    if receipts:
        _write_receipt(home, receipts)
    result["installed"] = receipts
    activate_managed_tools()
    after = doctor.check(local=local, pdf=pdf)
    result.update(ready=bool(after.get("ready")), doctor=after)
    _log("tools ready" if result["ready"] else "required tools are still missing — see the errors above")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable result on stdout")
    parser.add_argument("--dry-run", action="store_true", help="print what would be installed and exit")
    parser.add_argument("--system", action="store_true",
                        help="allow the host package manager for ffmpeg when the user-level download fails")
    parser.add_argument("--local", action="store_true", help="the source is a local recording (yt-dlp optional)")
    parser.add_argument("--pdf", action="store_true", help="also report the optional PDF engine")
    parser.add_argument("--skip-optional", action="store_true", help="required tools only (no Pillow)")
    args = parser.parse_args(argv)
    utf8_stdio()
    result = run(local=args.local, pdf=args.pdf, system=args.system, skip_optional=args.skip_optional,
                 dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Tools {'ready' if result['ready'] else 'NOT ready'} · user-level directory: {result['managed_home']}")
        for receipt in result["installed"]:
            print(f"- installed {receipt['step']} ({receipt['method']})")
        for error in result["errors"]:
            print(f"- {error['step']}: {error['message']}")
        if not result["ready"] and not result["errors"] and result["planned"]:
            print(f"- dry run: would install {', '.join(result['planned'])}")
        print(f"Re-check any time: {python_command()} scripts/doctor.py")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
