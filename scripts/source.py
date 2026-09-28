"""Where the video comes from: its metadata, and the media file that frames and whisper read."""
import json
import re
from pathlib import Path

from common import VsumError, need, run

YTDLP = ["yt-dlp", "--ignore-config", "--no-playlist"]
YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})")


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def youtube_id(url: str | None) -> str | None:
    match = YOUTUBE_ID.search(url or "")
    return match.group(1) if match else None


def slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text).strip("-")[:60] or "video"


def probe(text: str) -> tuple[dict, dict]:
    """(meta, info): meta is saved as source.json; info is yt-dlp's full record ({} for a file)."""
    if is_url(text):
        need("yt-dlp")
        info = json.loads(run(YTDLP + ["-J", text], "reading the video page").stdout)
        if info.get("_type") == "playlist":
            raise VsumError("this link is a playlist; give the link of one video from it.")
        if info.get("is_live"):
            raise VsumError("this is a live stream; summarize it once it has ended and been saved.")
        url = info.get("webpage_url") or text
        vid = youtube_id(url)
        meta = {"kind": "youtube" if vid else "url", "url": url, "path": None,
                "id": vid or slug(str(info.get("id") or info.get("title") or "video")),
                "title": info.get("title") or url, "duration": float(info.get("duration") or 0),
                "uploader": info.get("uploader") or info.get("channel"), "language": info.get("language"),
                "chapters": [{"t": float(c.get("start_time") or 0), "title": c.get("title") or ""}
                             for c in info.get("chapters") or []]}
        return meta, info
    path = Path(text).expanduser().resolve()
    if not path.is_file():
        raise VsumError(f"no such file: {path}")
    return {"kind": "file", "url": None, "path": str(path), "id": slug(path.stem), "title": path.stem,
            "duration": duration(path), "uploader": None, "language": None, "chapters": []}, {}


def _ffprobe(path: Path, *args: str) -> dict:
    proc = run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)], "reading the media file")
    return json.loads(proc.stdout or "{}")


def duration(path: Path) -> float:
    return float(_ffprobe(path, "-show_entries", "format=duration").get("format", {}).get("duration") or 0)


def has_video(path: Path) -> bool:
    """A real picture track; the cover art of an audio file does not count."""
    streams = _ffprobe(path, "-select_streams", "v", "-show_entries", "stream_disposition=attached_pic").get("streams")
    return any(not (s.get("disposition") or {}).get("attached_pic") for s in streams or [])


def media(meta: dict, work: Path) -> Path:
    """The file to read: the user's own, or one ≤720p download (H.264 preferred) with its audio."""
    if meta["kind"] == "file":
        return Path(meta["path"])

    def done() -> list[Path]:
        return [p for p in work.glob("video.*") if p.name.count(".") == 1]  # not video.f137.mp4 or .part

    if not done():
        run(YTDLP + ["-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b", "-S", "vcodec:h264,res:720,acodec:m4a",
                     "--merge-output-format", "mp4", "--no-progress", "-o", str(work / "video.%(ext)s"),
                     meta["url"]], "video download")
    files = done()
    if not files:
        raise VsumError("the video download finished without a file; run prepare again.")
    return files[0]
