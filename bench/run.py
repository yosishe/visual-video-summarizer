#!/usr/bin/env python3
"""Benchmark runner — the deterministic stages of /summarize-video per profile.

The model stages (chapters, triage, summary) cannot run here; they run inside
Claude Code. This script does everything around them so a run is reproducible:

    python3 bench/run.py prepare    --profile v130-standard [--ids ID,...] [--run NAME]
        transcript.py per video into <run>/<id>/work; copies the shared
        bench/inputs/<id>/chapters.json when it exists (chapters are authored
        once per video and reused across profiles so engines are compared on
        the same targets).
    python3 bench/run.py candidates --profile v130-standard [--ids ...] [--run NAME]
        candidates.py with the profile's tier and --profile-override; records
        wall-clock in <run>/<id>/cost.json and copies candidates.json,
        dropped.json, chapters.json, transcript.json next to the work dir.
    python3 bench/run.py finish     --profile ... [--ids ...] [--run NAME]
        grab.py + render.py once <run>/<id>/work/{selections,summary}.json exist
        (written by the model); copies selections/summary/manifest.
    python3 bench/run.py import     --profile v130-high --id ISb0nrlNoKQ --from <work dir> [--render-dir <summary-<id> dir>] [--wall N]
        Imports an existing run's artifacts (e.g. the 1.3.0 release E2E) as a
        baseline without re-spending anything.

Run directories default to bench/runs/<YYYY-MM-DD>-<profile>.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
SKILL = BENCH.parent
SCRIPTS = SKILL / "scripts"

ARTIFACTS = ("candidates.json", "dropped.json", "chapters.json", "transcript.json",
             "selections.json", "summary.json", "triage-rejections.json", "audit.json")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(ids: list[str] | None) -> list[dict]:
    videos = load_json(BENCH / "manifest.json")["videos"]
    if ids:
        wanted = set(ids)
        videos = [v for v in videos if v["id"] in wanted]
        missing = wanted - {v["id"] for v in videos}
        if missing:
            raise SystemExit(f"unknown ids: {sorted(missing)}")
    return videos


def load_profile(name: str) -> dict:
    path = BENCH / "profiles" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"profile not found: {path}")
    return load_json(path)


def run_dir_for(args) -> Path:
    name = args.run or f"{dt.date.today().isoformat()}-{args.profile}"
    path = BENCH / "runs" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cmd(cmd: list[str], *, cwd: Path | None = None, report: Path | None = None) -> tuple[int, float, str]:
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    wall = time.monotonic() - started
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
    if report is not None:
        report.write_text(proc.stdout, encoding="utf-8")
    return proc.returncode, wall, proc.stderr


def copy_artifacts(work: Path, dest: Path) -> None:
    for name in ARTIFACTS:
        src = work / name
        if src.exists():
            shutil.copy2(src, dest / name)


def update_cost(dest: Path, **fields) -> None:
    path = dest / "cost.json"
    cost = load_json(path) if path.exists() else {}
    cost.update(fields)
    path.write_text(json.dumps(cost, indent=2) + "\n", encoding="utf-8")


def cmd_prepare(args) -> int:
    run_dir = run_dir_for(args)
    for video in load_manifest(args.ids):
        dest = run_dir / video["id"]
        work = dest / "work"
        work.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(SCRIPTS / "transcript.py"), video["url"], "--work", str(work),
               "--langs", video.get("langs", "en.*")]
        if args.no_whisper:
            cmd.append("--no-whisper")
        rc, wall, _ = run_cmd(cmd)
        update_cost(dest, transcript_wall_s=round(wall, 1), transcript_rc=rc)
        shared = BENCH / "inputs" / video["id"] / "chapters.json"
        if shared.exists():
            shutil.copy2(shared, work / "chapters.json")
            note = "chapters.json copied from bench/inputs"
        else:
            note = "NEXT: author work/chapters.json (model), then copy it to bench/inputs/<id>/ for reuse"
        print(f"[bench] {video['id']}: transcript rc={rc} ({wall:.0f}s) — {note}")
    return 0


def cmd_candidates(args) -> int:
    run_dir = run_dir_for(args)
    profile = load_profile(args.profile)
    for video in load_manifest(args.ids):
        dest = run_dir / video["id"]
        work = dest / "work"
        if not (work / "chapters.json").exists():
            print(f"[bench] {video['id']}: no work/chapters.json — run prepare and author chapters first", file=sys.stderr)
            continue
        cmd = [sys.executable, str(SCRIPTS / "candidates.py"), video["url"], "--work", str(work),
               "--chapters", str(work / "chapters.json"), "--tier", profile["tier"]]
        if profile.get("override"):
            cmd += ["--profile-override", json.dumps(profile["override"])]
        cmd += args.extra
        rc, wall, stderr = run_cmd(cmd, report=dest / "candidates-report.md")
        (dest / "candidates-stderr.txt").write_text(stderr, encoding="utf-8")
        copy_artifacts(work, dest)
        update_cost(dest, candidates_wall_s=round(wall, 1), candidates_rc=rc, profile=args.profile)
        print(f"[bench] {video['id']}: candidates rc={rc} ({wall:.0f}s) — NEXT: triage in Claude Code, write work/selections.json")
    return 0


def cmd_finish(args) -> int:
    run_dir = run_dir_for(args)
    for video in load_manifest(args.ids):
        dest = run_dir / video["id"]
        work = dest / "work"
        selections = work / "selections.json"
        summary = work / "summary.json"
        if not selections.exists():
            print(f"[bench] {video['id']}: no work/selections.json — skipped", file=sys.stderr)
            continue
        out_dir = dest / f"summary-{video['id']}"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        rc, wall, _ = run_cmd([sys.executable, str(SCRIPTS / "grab.py"), "--work", str(work),
                               "--spec", str(selections), "--out-dir", str(out_dir / "assets")])
        update_cost(dest, grab_wall_s=round(wall, 1), grab_rc=rc)
        if rc == 0 and summary.exists():
            cmd = [sys.executable, str(SCRIPTS / "render.py"), "--work", str(work), "--summary", str(summary),
                   "--selections", str(selections), "--assets-dir", str(out_dir / "assets"),
                   "--out-dir", str(out_dir)] + args.extra
            rc2, wall2, _ = run_cmd(cmd)
            update_cost(dest, render_wall_s=round(wall2, 1), render_rc=rc2)
            if (out_dir / "manifest.json").exists():
                shutil.copy2(out_dir / "manifest.json", dest / "manifest.json")
        copy_artifacts(work, dest)
        print(f"[bench] {video['id']}: finished (grab rc={rc})")
    return 0


def cmd_import(args) -> int:
    run_dir = run_dir_for(args)
    src = Path(args.src).expanduser().resolve()
    dest = run_dir / args.id
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ARTIFACTS:
        if (src / name).exists():
            shutil.copy2(src / name, dest / name)
            copied.append(name)
    if args.render_dir:
        manifest = Path(args.render_dir).expanduser().resolve() / "manifest.json"
        if manifest.exists():
            shutil.copy2(manifest, dest / "manifest.json")
            copied.append("manifest.json")
    update_cost(dest, imported_from=str(src), profile=args.profile,
                candidates_wall_s=args.wall, grab_wall_s=args.grab_wall)
    print(f"[bench] imported {args.id} into {dest}: {', '.join(copied)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "candidates", "finish"):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True)
        p.add_argument("--ids", default=None, help="Comma-separated video ids (default: all)")
        p.add_argument("--run", default=None, help="Run name (default: <date>-<profile>)")
        p.add_argument("--no-whisper", action="store_true")
        p.add_argument("extra", nargs="*", help="Extra args passed to the stage script (after --)")
    p = sub.add_parser("import")
    p.add_argument("--profile", required=True)
    p.add_argument("--run", default=None)
    p.add_argument("--id", required=True)
    p.add_argument("--from", dest="src", required=True, help="Work dir holding the artifacts")
    p.add_argument("--render-dir", default=None, help="Rendered summary-<id> dir (for manifest.json)")
    p.add_argument("--wall", type=float, default=None, help="candidates wall-clock seconds, if known")
    p.add_argument("--grab-wall", type=float, default=None)
    args = parser.parse_args()
    if getattr(args, "ids", None):
        args.ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    return {"prepare": cmd_prepare, "candidates": cmd_candidates, "finish": cmd_finish, "import": cmd_import}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
