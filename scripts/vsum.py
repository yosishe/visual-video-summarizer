#!/usr/bin/env python3
"""summarize-video: a video link or file → illustrated study notes. Run `prepare`, write notes.md, run `render`."""
import argparse
import json
import shutil
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit("Stopped: summarize-video needs Python 3.10 or newer (this is " + sys.version.split()[0] + ").")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import render  # noqa: E402
import source  # noqa: E402
import transcript  # noqa: E402
from common import VsumError, fmt_time  # noqa: E402

SCRIPT = Path(__file__).resolve()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _transcript(args, meta: dict, info: dict, work: Path) -> tuple[list, dict]:
    if args.captions:
        return transcript.caption_file(Path(args.captions).expanduser())
    if meta["kind"] == "file":
        side = transcript.sidecar(Path(meta["path"]))
        if side:
            return transcript.caption_file(side)
    else:
        found = transcript.fetch_captions(meta["url"], info, work)
        if found:
            return found
    print("No captions: transcribing locally with whisper (about a tenth of the video's length)…", flush=True)
    return transcript.transcribe(source.media(meta, work), work, args.lang_audio or meta.get("language"),
                                 meta["duration"])


def _frames(args, meta: dict, work: Path) -> list[dict]:
    try:
        import frames
    except ImportError:
        raise VsumError("Pillow is not installed (install: brew install pillow, or pip install pillow in the "
                        "Python that runs this script), then run again.") from None
    video = source.media(meta, work)
    meta["duration"] = meta["duration"] or source.duration(video)
    if not source.has_video(video):
        print("No picture track: the summary will have no frames.")
        return []
    print("Scanning the video for screens that held still…", flush=True)
    cap = args.max_frames or frames.default_cap(meta["duration"])
    picked = frames.extract(video, frames.pick(frames.scan(video), cap), work / "frames")
    frames.sheets(picked, work / "frames", work / "sheets")
    return picked


def _request(args, meta: dict) -> dict:
    """What this run was asked for; a work folder serves one source, and changed options redo their stage."""
    if meta["kind"] == "file":
        identity = f"{meta['path']} ({Path(meta['path']).stat().st_size} bytes)"
    else:
        identity = meta["url"]
    captions = str(Path(args.captions).expanduser().resolve()) if args.captions else None
    return {"source": identity, "captions": captions, "lang_audio": args.lang_audio,
            "max_frames": args.max_frames, "no_frames": args.no_frames}


def prepare(args) -> int:
    meta, info = source.probe(args.source)
    work = Path(args.out or f"vsum-{meta['id']}").resolve()
    request = meta["request"] = _request(args, meta)
    saved = _load(work / "source.json") or {}
    if work.is_dir() and any(work.iterdir()) and not saved:
        raise VsumError(f"{work} is not a summarize-video folder and is not empty; choose a new or empty --out.")
    before = saved.get("request") or {}
    if saved and before.get("source", request["source"]) != request["source"]:
        raise VsumError(f"{work} holds another video (\"{saved.get('title')}\"); choose another --out.")
    if args.fresh:
        for name in ("transcript.md", "frames.json"):
            (work / name).unlink(missing_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    print(f"Work folder: {work}", flush=True)
    same = [before.get(k) == request[k] for k in ("captions", "lang_audio", "max_frames", "no_frames")]
    if (work / "transcript.md").is_file() and saved.get("transcript") and all(same[:2]):
        meta["transcript"] = saved["transcript"]
        meta["duration"] = meta["duration"] or saved.get("duration") or 0
        print("Transcript: reusing transcript.md")
    else:
        segments, meta["transcript"] = _transcript(args, meta, info, work)
        (work / "transcript.md").write_text(transcript.markdown(meta, segments), encoding="utf-8")
        print(f"Transcript: {transcript.describe(meta['transcript'])}", flush=True)
    _save(work / "source.json", meta)  # a later failure keeps the transcript
    picked = _load(work / "frames.json") if all(same[2:]) else None
    if picked is not None:
        print(f"Frames: reusing {len(picked)} candidates")
    else:
        for name in ("frames", "sheets"):
            shutil.rmtree(work / name, ignore_errors=True)
        picked = [] if args.no_frames else _frames(args, meta, work)
        _save(work / "frames.json", picked)
    _save(work / "source.json", meta)
    sheets = sorted((work / "sheets").glob("sheet-*.jpg"))
    words = next((line.split(": ")[1] for line in (work / "transcript.md").read_text(encoding="utf-8").splitlines()
                  if line.startswith("- Words: ")), "?")
    look = (f"2. Look at every contact sheet in one message: {', '.join(str(p) for p in sheets)}\n"
            f"   (open a single {work / 'frames'}/Fnn.jpg only to read small text)" if sheets
            else "2. There are no frames to look at.")
    print(f"\nReady: \"{meta['title']}\" ({fmt_time(meta['duration'])}), {transcript.describe(meta['transcript'])}, "
          f"{len(picked)} candidate frames.\n\nNext:\n1. Read {work / 'transcript.md'} ({words} words) to the end.\n"
          f"{look}\n3. Write {work / 'notes.md'} (format and rules: SKILL.md).\n"
          f"4. Run: python3 \"{SCRIPT}\" render \"{work}\"")
    return 0


def render_cmd(args) -> int:
    work = Path(args.work).resolve()
    meta = _load(work / "source.json")
    if not meta:
        raise VsumError(f"{work} is not a prepared folder; run prepare first.")
    notes = work / "notes.md"
    if not notes.is_file():
        raise VsumError(f"write {notes} first (format: SKILL.md), then render again.")
    page, errors, warnings = render.build(notes.read_text(encoding="utf-8"), meta,
                                          _load(work / "frames.json") or [], work / "frames")
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"notes.md {error}")
        return 1
    out = work / "summary.html"
    out.write_text(page, encoding="utf-8")
    print(f"Summary: {out}")
    if args.pdf:
        pdf = work / "summary.pdf"
        if not render.browser():
            print("PDF: no Chrome, Chromium or Edge found; the HTML is complete.")
        else:
            print(f"PDF: {pdf}" if render.pdf(out, pdf) else "PDF: the browser could not print it; the HTML is complete.")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="vsum", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="transcript, candidate frames and contact sheets")
    p.add_argument("source", help="a YouTube or other video URL, or a local video or audio file")
    p.add_argument("--out", help="work folder (default: ./vsum-<id>)")
    p.add_argument("--captions", help="a .vtt or .srt file to use as the transcript")
    p.add_argument("--lang-audio", help="spoken language for whisper, e.g. he or en (default: detected)")
    p.add_argument("--max-frames", type=int, help="most candidate frames (default: 30 per 10 minutes, 48 to 160)")
    p.add_argument("--no-frames", action="store_true", help="text only: no frames (a video without captions is still downloaded for its audio)")
    p.add_argument("--fresh", action="store_true", help="redo every step instead of reusing earlier results")
    r = sub.add_parser("render", help="notes.md → summary.html")
    r.add_argument("work", help="the folder prepare created")
    r.add_argument("--pdf", action="store_true", help="also print summary.pdf with Chrome, Chromium or Edge")
    args = parser.parse_args(argv)
    try:
        return prepare(args) if args.command == "prepare" else render_cmd(args)
    except VsumError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
