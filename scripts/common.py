"""Helpers every stage shares: tool checks, running tools, time labels."""
import shutil
import subprocess

INSTALL = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "yt-dlp": "brew install yt-dlp",
    "whisper-cli": "brew install whisper-cpp",
}


class VsumError(Exception):
    """Something the user can fix; the message says what and how."""


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        hint = INSTALL.get(tool)
        raise VsumError(f"{tool} is not installed" + (f" (install: {hint})" if hint else "") + ", then run again.")
    return path


def run(cmd: list[str], what: str, binary: bool = False) -> subprocess.CompletedProcess:
    """Run a tool; a failure becomes a VsumError carrying the tool's own last error line."""
    need(cmd[0])
    if binary:
        proc = subprocess.run(cmd, capture_output=True)
        err = proc.stderr.decode("utf-8", "replace")
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        err = proc.stderr
    if proc.returncode != 0:
        lines = [line.strip() for line in (err or "").splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR")] or lines
        raise VsumError(f"{what} failed: {errors[-1] if errors else f'exit code {proc.returncode}'}")
    return proc


def fmt_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_time(label: str) -> int:
    total = 0
    for part in label.split(":"):
        total = total * 60 + int(part)
    return total
