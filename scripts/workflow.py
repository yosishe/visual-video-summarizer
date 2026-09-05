#!/usr/bin/env python3
"""The canonical workflow controller: the repository decides what may run next.

    python workflow.py init <source> --work W [--lang he|en] [--tier standard|high] [--pdf] ...
    python workflow.py run --work W          # runs deterministic stages until a model-authored file is needed
    python workflow.py next --work W         # exactly what to author or run next
    python workflow.py status --work W       # every stage, recomputed from the files on disk
    python workflow.py validate <stage> --work W
    python workflow.py decide no-visuals --work W --reason "..."
    python workflow.py shortlist --work W --ids c_0003,...
    python workflow.py verify --work W       # objective proof of what completed (exit 0 only when complete)

The controller shells out to the existing scripts (transcript.py, candidates.py,
shortlist.py, grab.py, audit_summary.py, render.py) and never re-implements a
stage. It keeps `<work>/run.json`: the request, the visual-content decision,
every stage's status, exit code, input and output hashes, counts and the
current blocker. Statuses are recomputed from the artifacts and their recorded
input hashes on every call, so a stage whose inputs changed is `stale` and
re-runs; a model-authored artifact is gated before anything downstream runs.

Exit codes: 0 = done or waiting for a model-authored file (see NEXT);
6/7/8/9 = the failing stage's own code; 10 = an artifact is invalid;
11 = a stale binding; 12 = delivery incomplete (verify).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from gates import (  # noqa: E402
    ENGINE_VERSION,
    EXIT_INCOMPLETE,
    EXIT_INVALID,
    EXIT_STALE,
    EXIT_UNRESOLVED,
    GateError,
    GateResult,
    candidates_digest,
    canonical_sha256,
    is_url,
    load_json,
    selections_binding,
    sha256_file,
    source_identity,
    validate_assets,
    validate_candidates,
    validate_chapters,
    validate_selections,
    validate_transcript,
)
from hostenv import child_env, python_command, utf8_stdio  # noqa: E402
from safety import atomic_write, validate_generated_html  # noqa: E402

RUN_SCHEMA = 1
STAGES = ("transcript", "chapters", "candidates", "shortlist", "selections", "grab", "summary", "audit", "render")
KINDS = {
    "transcript": "deterministic", "chapters": "model", "candidates": "deterministic", "shortlist": "receipt",
    "selections": "model", "grab": "deterministic", "summary": "model", "audit": "deterministic",
    "render": "deterministic",
}
VISUAL_STAGES = ("shortlist", "selections", "grab")
REFERENCES = {
    "chapters": "references/chapters.md",
    "selections": "references/triage.md",
    "summary": "references/summary.md",
    "failures": "references/failures.md",
}
SKILL_DIR = SCRIPT_DIR.parent
HISTORY_LIMIT = 50


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _json_or_none(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


# ----------------------------------------------------------------------------- run.json


def run_path(work: Path) -> Path:
    return work / "run.json"


def load_run(work: Path) -> dict:
    path = run_path(work)
    if not path.is_file():
        raise GateError(f"{path} not found — start with `workflow.py init <source> --work {work}`")
    run = load_json(path, "run.json")
    if not isinstance(run, dict) or run.get("schema_version") != RUN_SCHEMA:
        raise GateError("run.json has an unknown schema; re-run init --force")
    return run


def save_run(work: Path, run: dict) -> None:
    run["updated_at"] = _now()
    atomic_write(run_path(work), json.dumps(run, indent=2, ensure_ascii=False) + "\n")


def record_history(run: dict, command: str, stage: str | None, exit_code: int | None) -> None:
    history = list(run.get("history") or [])
    history.append({"at": _now(), "command": command, "stage": stage, "exit_code": exit_code})
    run["history"] = history[-HISTORY_LIMIT:]


def _env_lang() -> str | None:
    try:
        from render import _env_lang as render_env_lang  # noqa: WPS433 — lazy: render imports audit etc.
        return render_env_lang()
    except Exception:  # pragma: no cover - render unavailable only in broken installs
        return None


# ----------------------------------------------------------------------------- paths and inputs


class Paths:
    def __init__(self, work: Path, run: dict):
        self.work = work
        self.transcript = work / "transcript.json"
        self.chapters = work / "chapters.json"
        self.candidates = work / "candidates.json"
        self.selections = work / "selections.json"
        self.summary = work / "summary.json"
        self.audit = work / "audit.json"
        self.reports = work / "reports"
        out = run.get("out_dir")
        self.out_dir = Path(out) if out else None

    @property
    def assets_dir(self) -> Path | None:
        return self.out_dir / "assets" if self.out_dir else None

    @property
    def assets_manifest(self) -> Path | None:
        return self.assets_dir / "assets-manifest.json" if self.assets_dir else None

    @property
    def manifest(self) -> Path | None:
        return self.out_dir / "manifest.json" if self.out_dir else None

    @property
    def bundle(self) -> Path | None:
        return self.out_dir.parent / f"{self.out_dir.name}.html" if self.out_dir else None

    @property
    def pdf(self) -> Path | None:
        return self.out_dir.parent / f"{self.out_dir.name}.pdf" if self.out_dir else None


def decision(run: dict) -> str:
    return (run.get("visual_content") or {}).get("decision") or "illustrated"


def request(run: dict) -> dict:
    return run.get("request") or {}


def resolve_out_dir(run: dict, video_id: str | None) -> None:
    """The deliverable directory is `summary-<video id>` beside where init ran."""
    if run.get("out_dir"):
        return
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(video_id or "video"))[:80] or "video"
    run["out_dir"] = str(Path(run.get("cwd") or Path.cwd()) / f"summary-{safe}")


# ----------------------------------------------------------------------------- stage assessment


class Stage:
    def __init__(self, name: str):
        self.name = name
        self.kind = KINDS[name]
        self.status = "pending"
        self.reason = ""
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.inputs: dict = {}
        self.outputs: dict = {}
        self.next: str | None = None
        self.exit_code: int | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "status": self.status, "reason": self.reason, "errors": self.errors,
                "warnings": self.warnings, "inputs": self.inputs, "outputs": self.outputs, "next": self.next,
                "exit_code": self.exit_code}


def _recorded(run: dict, name: str) -> dict:
    return (run.get("stages") or {}).get(name) or {}


def _inputs_changed(run: dict, stage: Stage) -> bool:
    recorded = _recorded(run, stage.name).get("inputs")
    return bool(recorded) and recorded != stage.inputs


def assess(work: Path, run: dict) -> dict[str, Stage]:
    """Recompute every stage from the files on disk. Never trusts a stored status alone."""
    req = request(run)
    paths = Paths(work, run)
    visual = decision(run)
    stages = {name: Stage(name) for name in STAGES}
    blocked_after: str | None = None

    def block(stage: Stage, status: str, reason: str, *, exit_code: int | None = None, next_step: str | None = None):
        nonlocal blocked_after
        stage.status, stage.reason, stage.exit_code, stage.next = status, reason, exit_code, next_step
        if blocked_after is None:
            blocked_after = stage.name

    # transcript ------------------------------------------------------------
    stage = stages["transcript"]
    stage.inputs = {
        "source": canonical_sha256(run.get("source", {}).get("identity")),
        "request": canonical_sha256({k: req.get(k) for k in ("whisper", "no_whisper", "langs", "wanted")}),
    }
    transcript = _json_or_none(paths.transcript)
    if transcript is None:
        block(stage, "pending", "transcript.json not written yet")
    else:
        result = validate_transcript(transcript)
        stage.warnings = result.warnings
        stage.outputs = {"transcript.json": _sha(paths.transcript)}
        if isinstance(transcript, dict) and transcript.get("status") == "no_transcript":
            reason = (transcript.get("source_detail") or {}).get("reason") or "no usable transcript"
            block(stage, "failed", reason, exit_code=6, next_step=(
                "There is no frames-only path. Options: ask the user to authorize cloud transcription "
                "(`init --force --whisper groq|openai` — audio is uploaded to that provider), force a caption track "
                "with `--langs`, or choose another source."))
        elif result.errors:
            block(stage, "invalid", "; ".join(result.errors), exit_code=EXIT_INVALID,
                  next_step="Re-run the transcript stage (`workflow.py run --retry`).")
        elif _inputs_changed(run, stage):
            block(stage, "stale", "the source or transcription options changed since transcript.json was written")
        else:
            stage.status = "ok"
            stage.reason = f"{result.info['health']['segments']} segments; health warnings: {len(result.warnings)}"
            resolve_out_dir(run, result.info.get("video_id"))
            paths = Paths(work, run)

    # chapters --------------------------------------------------------------
    stage = stages["chapters"]
    stage.inputs = {"transcript.json": stage.outputs.get("transcript.json") or _sha(paths.transcript)}
    chapters = _json_or_none(paths.chapters)
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif chapters is None:
        block(stage, "awaiting_model", "chapters.json is not written yet", next_step=(
            f"Author {paths.chapters} from the transcript (report: {paths.reports / 'transcript.md'} or "
            f"{work / 'transcript.txt'}). Contract: {REFERENCES['chapters']}. Mark needs_frames true only for "
            "chapters that show something on screen; reference segment ids, never seconds."))
    else:
        duration = float(((transcript or {}).get("video") or {}).get("duration") or 0) if isinstance(transcript, dict) else 0
        result = validate_chapters(chapters, transcript if isinstance(transcript, dict) else None, duration,
                                   visual_decision=visual)
        stage.warnings = result.warnings
        stage.outputs = {"chapters.json": _sha(paths.chapters)}
        if result.errors:
            stage.errors = result.errors
            block(stage, "invalid", f"{len(result.errors)} error(s)", exit_code=EXIT_INVALID,
                  next_step=f"Fix {paths.chapters}: " + "; ".join(result.errors[:5]))
        else:
            stage.status, stage.reason = "ok", (
                f"{len(chapters)} chapters, {result.info.get('needs_frames_chapters', 0)} need frames, "
                f"{result.info.get('targets', 0)} targets")

    # candidates ------------------------------------------------------------
    stage = stages["candidates"]
    stage.inputs = {
        "transcript.json": _sha(paths.transcript), "chapters.json": _sha(paths.chapters),
        "options": canonical_sha256({k: req.get(k) for k in ("tier", "sections", "max_image_tokens", "allow_long")}
                                    | {"visual_content": visual}),
    }
    candidates = _json_or_none(paths.candidates)
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif candidates is None:
        block(stage, "pending", "candidates.json not written yet")
    else:
        result = validate_candidates(candidates, transcript_sha=stage.inputs["transcript.json"],
                                     chapters_sha=stage.inputs["chapters.json"], visual_decision=visual)
        stage.warnings = result.warnings
        stage.outputs = {"candidates.json": candidates_digest(candidates) if isinstance(candidates, dict) else None}
        if result.info.get("stale") or _inputs_changed(run, stage):
            block(stage, "stale", "; ".join(result.info.get("stale") or ["extraction options changed"]))
        elif isinstance(candidates, dict) and candidates.get("status") == "unresolved":
            block(stage, "failed", "unresolved visual coverage: " + ", ".join(
                [*result.info.get("unresolved_chapters", []), *result.info.get("unresolved_targets", [])]),
                exit_code=EXIT_UNRESOLVED, next_step=(
                    f"Fix {paths.chapters} (a target inside the window, or needs_frames false with a reason) and "
                    "run again, or re-run with --tier high (init --force --tier high)."))
        elif result.errors:
            stage.errors = result.errors
            block(stage, "invalid", "; ".join(result.errors), exit_code=EXIT_INVALID,
                  next_step="Re-run the candidate stage; if the pool is empty by design, record "
                            "`workflow.py decide no-visuals --reason ...` first.")
        else:
            stage.status = "ok"
            status = candidates.get("status", "ok") if isinstance(candidates, dict) else "ok"
            stage.reason = (f"{result.info.get('candidates', 0)} candidates" if status == "ok"
                            else "no visual chapters (explicit decision)")

    # shortlist (receipt) -----------------------------------------------------
    stage = stages["shortlist"]
    stage.inputs = {"candidates.json": stages["candidates"].outputs.get("candidates.json")}
    receipt = candidates.get("shortlist") if isinstance(candidates, dict) else None
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif visual == "none":
        stage.status, stage.reason = "skipped", "no visuals by explicit decision"
    elif isinstance(candidates, dict) and (candidates.get("token_budget") or {}).get("mode") == "individual":
        stage.status, stage.reason = "skipped", "contact sheets unavailable: candidates are read individually"
    elif isinstance(receipt, dict) and receipt.get("candidates_sha256") == stage.inputs["candidates.json"]:
        stage.status = "ok"
        stage.outputs = {"written": len(receipt.get("written") or [])}
        stage.reason = f"{len(receipt.get('written') or [])} frames re-decoded and verified"
        if receipt.get("failures"):
            stage.warnings = [f"shortlist failures: {receipt['failures'][:3]}"]
    else:
        block(stage, "awaiting_model", "no shortlist receipt for this candidate pool", next_step=(
            f"Read ALL contact sheets listed in {paths.reports / 'candidates.md'} in one message, decide keep/drop "
            "by burned-in id, then run: "
            f"{python_command()} \"{SCRIPT_DIR / 'shortlist.py'}\" --work \"{work}\" --ids <kept ids>"))

    # selections (model) ------------------------------------------------------
    stage = stages["selections"]
    stage.inputs = {"candidates.json": stages["candidates"].outputs.get("candidates.json")}
    selections = _json_or_none(paths.selections)
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif visual == "none":
        stage.status, stage.reason = "skipped", "no visuals by explicit decision"
    elif selections is None:
        block(stage, "awaiting_model", "selections.json is not written yet", next_step=(
            f"Read the shortlist frames listed in {paths.reports / 'shortlist.md'} in one message and write "
            f"{paths.selections} by candidate_id (contract: {REFERENCES['selections']})."))
    else:
        result = validate_selections(selections, candidates if isinstance(candidates, dict) else None,
                                     lang=req.get("lang") or "he", shortlist_receipt=receipt)
        stage.warnings = result.warnings
        stage.outputs = {"selections.json": _sha(paths.selections), "binding": selections_binding(selections)}
        if result.errors:
            stage.errors = result.errors
            block(stage, "invalid", f"{len(result.errors)} error(s)", exit_code=EXIT_INVALID,
                  next_step=f"Fix {paths.selections}: " + "; ".join(result.errors[:5]))
        else:
            stage.status, stage.reason = "ok", f"{len(selections)} frames selected"

    # grab ------------------------------------------------------------------
    stage = stages["grab"]
    stage.inputs = {"selections": stages["selections"].outputs.get("binding"),
                    "candidates.json": stages["candidates"].outputs.get("candidates.json")}
    assets_payload = _json_or_none(paths.assets_manifest) if paths.assets_manifest else None
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif visual == "none":
        stage.status, stage.reason = "skipped", "no visuals by explicit decision"
    elif assets_payload is None:
        block(stage, "pending", "assets not grabbed yet")
    else:
        result = validate_assets(assets_payload, selections if isinstance(selections, list) else [],
                                 selections_sha=canonical_sha256(selections),
                                 candidates_sha=stage.inputs["candidates.json"])
        stage.warnings = result.warnings
        stage.outputs = {"assets-manifest.json": _sha(paths.assets_manifest)}
        if result.info.get("stale"):
            block(stage, "stale", "; ".join(result.info["stale"]))
        elif result.errors:
            stage.errors = result.errors
            block(stage, "failed", "; ".join(result.errors[:3]), exit_code=2,
                  next_step="Fix the named selection (see grab's report) and run again.")
        else:
            stage.status, stage.reason = "ok", f"{result.info.get('assets', 0)} verified assets"

    # summary (model) ---------------------------------------------------------
    stage = stages["summary"]
    stage.inputs = {"transcript.json": _sha(paths.transcript), "chapters.json": _sha(paths.chapters),
                    "selections.json": _sha(paths.selections)}
    summary = _json_or_none(paths.summary)
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif summary is None:
        block(stage, "awaiting_model", "summary.json is not written yet", next_step=(
            f"Write {paths.summary} — chapters first, then overview, key points and the opening brief; "
            f"every block cites the segments it synthesizes (contract: {REFERENCES['summary']}). "
            f"Language: {req.get('lang') or 'he'}."))
    elif not isinstance(summary, dict) or not isinstance(summary.get("chapters"), list) or not summary.get("overview"):
        block(stage, "invalid", "summary.json must be an object with overview and chapters", exit_code=EXIT_INVALID,
              next_step=f"Fix {paths.summary} (contract: {REFERENCES['summary']}).")
    else:
        stage.outputs = {"summary.json": _sha(paths.summary)}
        stage.status, stage.reason = "ok", f"{len(summary['chapters'])} chapters written"

    # audit -----------------------------------------------------------------
    stage = stages["audit"]
    stage.inputs = {"summary.json": _sha(paths.summary), "selections.json": _sha(paths.selections),
                    "transcript.json": _sha(paths.transcript), "chapters.json": _sha(paths.chapters),
                    "candidates.json": stages["candidates"].outputs.get("candidates.json"),
                    "lang": req.get("lang")}
    audit = _json_or_none(paths.audit)
    recorded = _recorded(run, "audit")
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif audit is None or recorded.get("inputs") != stage.inputs:
        block(stage, "pending" if audit is None else "stale", "audit not run for the current summary")
    else:
        errors = len((audit or {}).get("errors") or [])
        stage.outputs = {"audit.json": _sha(paths.audit)}
        if errors:
            block(stage, "failed", f"{errors} audit error(s)", exit_code=5, next_step=(
                f"Fix every error listed in {paths.audit} (numbers, identifiers and segment references must come "
                f"from the cited segments) and run again."))
        else:
            reviews = len((audit or {}).get("reviews") or [])
            stage.status, stage.reason = "ok", f"0 errors, {reviews} reviews"

    # render ----------------------------------------------------------------
    stage = stages["render"]
    output_mode = "text-only" if visual == "none" else "illustrated"
    stage.inputs = {**stages["audit"].inputs,
                    "assets-manifest.json": _sha(paths.assets_manifest) if paths.assets_manifest else None,
                    "pdf": bool(req.get("pdf")), "output_mode": output_mode}
    manifest = _json_or_none(paths.manifest) if paths.manifest else None
    if blocked_after:
        stage.status, stage.reason = "blocked", f"waiting for {blocked_after}"
    elif manifest is None:
        block(stage, "pending", "not rendered yet")
    else:
        mismatches = []
        for key, current in (("summary_sha256", canonical_sha256(summary)), ("selections_sha256", canonical_sha256(selections or [])),
                             ("transcript_sha256", stage.inputs["transcript.json"]),
                             ("chapters_sha256", stage.inputs["chapters.json"]),
                             ("candidates_sha256", stage.inputs["candidates.json"]),
                             ("assets_manifest_sha256", stage.inputs["assets-manifest.json"])):
            recorded_value = manifest.get(key) if isinstance(manifest, dict) else None
            if recorded_value and current and recorded_value != current:
                mismatches.append(key)
        if not isinstance(manifest, dict) or "frames_count" not in manifest:
            block(stage, "stale", "manifest.json predates the workflow (no bindings); render again")
        elif mismatches or manifest.get("output_mode") != output_mode:
            block(stage, "stale", "inputs changed since the last render: " + ", ".join(mismatches or ["output mode"]))
        elif not (paths.bundle and paths.bundle.is_file()):
            block(stage, "stale", "the single-file bundle is missing")
        elif req.get("pdf") and not (paths.pdf and paths.pdf.is_file()):
            block(stage, "failed", "PDF requested but not produced", exit_code=4,
                  next_step="Install Google Chrome/Edge or WeasyPrint (ask the user), or deliver the HTML without PDF.")
        else:
            stage.outputs = {"manifest.json": _sha(paths.manifest), "bundle": _sha(paths.bundle)}
            stage.status, stage.reason = "ok", f"{manifest.get('frames_count')} frames, {output_mode}"

    return stages


# ----------------------------------------------------------------------------- execution


def _invoke(command: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a sibling script; tests patch this. Output is captured and echoed."""
    completed = subprocess.run(command, cwd=str(cwd) if cwd else None, env=child_env(),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode, completed.stdout, completed.stderr


def _script(name: str) -> str:
    return str(SCRIPT_DIR / name)


def stage_command(name: str, work: Path, run: dict) -> list[str]:
    req = request(run)
    paths = Paths(work, run)
    py = sys.executable
    source = str(run["source"]["raw"])
    if name == "transcript":
        cmd = [py, _script("transcript.py"), source, "--work", str(work)]
        if req.get("whisper"):
            cmd += ["--whisper", req["whisper"]]
        if req.get("no_whisper"):
            cmd.append("--no-whisper")
        if req.get("langs"):
            cmd += ["--langs", req["langs"]]
        if req.get("wanted"):
            cmd += ["--wanted", req["wanted"]]
        return cmd
    if name == "candidates":
        cmd = [py, _script("candidates.py"), source, "--work", str(work), "--transcript", str(paths.transcript),
               "--chapters", str(paths.chapters), "--tier", req.get("tier") or "standard",
               "--visual-content", decision(run)]
        if req.get("sections"):
            cmd += ["--sections", req["sections"]]
        if req.get("allow_long"):
            cmd.append("--allow-long")
        if req.get("max_image_tokens"):
            cmd += ["--max-image-tokens", str(req["max_image_tokens"])]
        return cmd
    if name == "grab":
        return [py, _script("grab.py"), "--work", str(work), "--spec", str(paths.selections),
                "--out-dir", str(paths.assets_dir)]
    if name == "audit":
        cmd = [py, _script("audit_summary.py"), "--work", str(work), "--summary", str(paths.summary),
               "--lang", req.get("lang") or "he"]
        if decision(run) != "none":
            cmd += ["--selections", str(paths.selections)]
        return cmd
    if name == "render":
        cmd = [py, _script("render.py"), "--work", str(work), "--summary", str(paths.summary),
               "--out-dir", str(paths.out_dir), "--lang", req.get("lang") or "he"]
        if decision(run) == "none":
            cmd += ["--output-mode", "text-only"]
        else:
            cmd += ["--output-mode", "illustrated", "--selections", str(paths.selections),
                    "--assets-dir", str(paths.assets_dir)]
        if req.get("pdf"):
            cmd.append("--pdf")
        return cmd
    raise ValueError(name)


def execute_stage(name: str, work: Path, run: dict, stage: Stage) -> int:
    paths = Paths(work, run)
    paths.reports.mkdir(parents=True, exist_ok=True)
    command = stage_command(name, work, run)
    record = {"kind": KINDS[name], "status": "running", "started_at": _now(), "finished_at": None,
              "inputs": stage.inputs, "outputs": {}, "command": command, "exit_code": None}
    run.setdefault("stages", {})[name] = record
    save_run(work, run)
    print(f"[workflow] {name}: running {Path(command[1]).name}", file=sys.stderr)
    code, out, err = _invoke(command, cwd=work)
    (paths.reports / f"{name}.md").write_text(out, encoding="utf-8")
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    if out.strip():
        print(out.rstrip())
    record.update({"finished_at": _now(), "exit_code": code, "status": "ok" if code == 0 else "failed"})
    record_history(run, "run", name, code)
    return code


def _print_next(stage: Stage) -> None:
    print()
    print(f"NEXT ({stage.name}, {stage.status}): {stage.reason}")
    if stage.next:
        print(f"  {stage.next}")
    if stage.errors:
        for error in stage.errors[:12]:
            print(f"  - {error}")
    if stage.warnings:
        for warning in stage.warnings[:6]:
            print(f"  warning: {warning}")


def persist(work: Path, run: dict, stages: dict[str, Stage]) -> None:
    stored = run.setdefault("stages", {})
    for name, stage in stages.items():
        record = stored.get(name) or {}
        record.update(stage.as_dict())
        record.setdefault("started_at", None)
        record.setdefault("finished_at", None)
        record.setdefault("command", None)
        stored[name] = record
    run["counts"] = collect_counts(work, run)
    blocker = next((s for s in stages.values() if s.status in ("failed", "invalid", "stale", "awaiting_model")), None)
    run["blocker"] = None if blocker is None else {
        "stage": blocker.name, "status": blocker.status, "exit_code": blocker.exit_code,
        "reason": blocker.reason, "next": blocker.next}
    save_run(work, run)


def collect_counts(work: Path, run: dict) -> dict:
    paths = Paths(work, run)
    transcript = _json_or_none(paths.transcript)
    chapters = _json_or_none(paths.chapters)
    candidates = _json_or_none(paths.candidates)
    selections = _json_or_none(paths.selections)
    audit = _json_or_none(paths.audit)
    manifest = _json_or_none(paths.manifest) if paths.manifest else None
    counts = {
        "segments": len((transcript or {}).get("segments") or []) if isinstance(transcript, dict) else 0,
        "chapters": len(chapters) if isinstance(chapters, list) else 0,
        "needs_frames_chapters": sum(1 for c in chapters if isinstance(c, dict) and c.get("needs_frames") is True)
        if isinstance(chapters, list) else 0,
        "targets": sum(len(c.get("visual_targets") or []) for c in chapters if isinstance(c, dict))
        if isinstance(chapters, list) else 0,
        "candidates": len((candidates or {}).get("candidates") or []) if isinstance(candidates, dict) else 0,
        "unresolved": 0, "sheets": 0, "shortlist_written": 0,
        "selections": len(selections) if isinstance(selections, list) else 0,
        "frames_rendered": (manifest or {}).get("frames_count") if isinstance(manifest, dict) else None,
        "audit_errors": len((audit or {}).get("errors") or []) if isinstance(audit, dict) else None,
        "audit_reviews": len((audit or {}).get("reviews") or []) if isinstance(audit, dict) else None,
    }
    if isinstance(candidates, dict):
        coverage = candidates.get("coverage") or {}
        counts["unresolved"] = sum(1 for row in [*coverage.get("chapters", []), *coverage.get("targets", [])]
                                   if isinstance(row, dict) and row.get("status") == "unresolved")
        counts["sheets"] = len(((candidates.get("sheets") or {}).get("sheets")) or [])
        counts["shortlist_written"] = len(((candidates.get("shortlist") or {}).get("written")) or [])
        if isinstance(transcript, dict):
            run["transcript_health"] = transcript.get("health")
    return counts


# ----------------------------------------------------------------------------- commands


def cmd_init(args) -> int:
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    if run_path(work).exists() and not args.force:
        raise GateError(f"{run_path(work)} already exists — use `run`/`status`, or `init --force` to reset the stage "
                        "records (the files are kept)")
    source = str(args.source)
    kind = "url" if is_url(source) else "file"
    if kind == "file" and not Path(source).expanduser().is_file():
        raise GateError(f"source file not found: {source}")
    try:
        identity = source_identity(source)
    except OSError as exc:
        raise GateError(f"cannot read source: {exc}") from exc
    lang = (args.lang or _env_lang() or "he").lower()
    if lang not in ("he", "en"):
        raise GateError(f"unsupported language {lang!r}; use he or en")
    previous = _json_or_none(run_path(work)) if args.force else None
    run = {
        "schema_version": RUN_SCHEMA, "engine_version": ENGINE_VERSION,
        "created_at": (previous or {}).get("created_at") or _now(), "updated_at": _now(),
        "skill_dir": str(SKILL_DIR), "work_dir": str(work), "cwd": str(Path.cwd()),
        "out_dir": str(Path(args.out_dir).expanduser().resolve()) if args.out_dir else None,
        "source": {"raw": source, "kind": kind, "identity": identity, "video_id": None, "title": None, "duration": None},
        "request": {"lang": lang, "tier": args.tier, "pdf": bool(args.pdf), "whisper": args.whisper,
                    "no_whisper": bool(args.no_whisper), "langs": args.langs, "wanted": args.wanted,
                    "output_mode": args.output_mode, "sections": args.sections, "allow_long": bool(args.allow_long),
                    "max_image_tokens": args.max_image_tokens, "focus": args.focus},
        "visual_content": {"decision": "none" if args.output_mode == "text-only" else "illustrated",
                           "reason": "user requested a text-only summary" if args.output_mode == "text-only" else None,
                           "decided_by": "init", "decided_at": _now()},
        "doctor": None, "stages": {}, "transcript_health": None, "counts": {}, "blocker": None, "history": [],
    }
    if args.force and previous and previous.get("visual_content", {}).get("decided_by") == "model":
        run["visual_content"] = previous["visual_content"]
    try:
        import doctor as doctor_module
        run["doctor"] = {**doctor_module.check(local=(kind == "file"), pdf=bool(args.pdf)), "captured_at": _now()}
        if not run["doctor"].get("ready"):
            missing = [row["name"] for row in run["doctor"]["checks"] if row["required"] and not row["available"]]
            print(f"[workflow] warning: missing required tools: {', '.join(missing)} — ask the user before installing",
                  file=sys.stderr)
    except Exception as exc:  # pragma: no cover - doctor is read-only; never block init on it
        run["doctor"] = {"ready": None, "error": str(exc)[:200], "captured_at": _now()}
    record_history(run, "init", None, 0)
    save_run(work, run)
    print(f"Initialized {run_path(work)} (lang={lang}, tier={args.tier}, pdf={bool(args.pdf)}, "
          f"visual content: {run['visual_content']['decision']})")
    print(f"Next: {python_command()} \"{Path(__file__).resolve()}\" run --work \"{work}\"")
    return 0


def cmd_run(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    until = args.until
    for _ in range(len(STAGES) + 2):  # each iteration executes at most one stage
        stages = assess(work, run)
        persist(work, run, stages)
        for name in STAGES:
            stage = stages[name]
            if stage.status in ("ok", "skipped"):
                if name == until:
                    print(f"[workflow] reached --until {until}")
                    return 0
                continue
            if stage.status == "blocked":
                continue
            if stage.kind == "model" or (stage.kind == "receipt" and stage.status == "awaiting_model"):
                if stage.status == "awaiting_model":
                    _print_next(stage)
                    return 0
                _print_next(stage)
                return stage.exit_code or EXIT_INVALID
            # deterministic stage
            if stage.status == "failed" and not args.retry and not _inputs_changed(run, stage):
                _print_next(stage)
                print("  (inputs unchanged; pass --retry to run it again)")
                return stage.exit_code or 1
            if stage.status == "invalid" and not args.retry:
                _print_next(stage)
                return stage.exit_code or EXIT_INVALID
            if stage.status == "stale" and not _stale_is_rerunnable(stage):
                _print_next(stage)
                return EXIT_STALE
            code = execute_stage(name, work, run, stage)
            save_run(work, run)
            if code != 0:
                fresh = assess(work, run)
                persist(work, run, fresh)
                _print_next(fresh[name])
                return code
            break  # re-assess from the top after every executed stage
        else:
            stages = assess(work, run)
            persist(work, run, stages)
            if all(s.status in ("ok", "skipped") for s in stages.values()):
                print("[workflow] every stage is complete — run `verify` for the delivery report")
                return 0
    return 0


def _stale_is_rerunnable(stage: Stage) -> bool:
    return stage.kind in ("deterministic",)


def cmd_status(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    stages = assess(work, run)
    persist(work, run, stages)
    if args.json:
        print(json.dumps({"stages": {n: s.as_dict() for n, s in stages.items()}, "counts": run.get("counts"),
                          "visual_content": run.get("visual_content"), "blocker": run.get("blocker"),
                          "out_dir": run.get("out_dir")}, indent=2, ensure_ascii=False))
        return 0
    print(f"# workflow status — {run['source']['raw']}")
    print(f"- lang {request(run).get('lang')} · tier {request(run).get('tier')} · pdf {request(run).get('pdf')} · "
          f"visual content: {decision(run)} ({(run.get('visual_content') or {}).get('reason') or 'default'})")
    print()
    print("| stage | kind | status | detail |")
    print("|---|---|---|---|")
    for name, stage in stages.items():
        print(f"| {name} | {stage.kind} | {stage.status} | {stage.reason} |")
    blocker = run.get("blocker")
    if blocker:
        print()
        print(f"Blocker: {blocker['stage']} ({blocker['status']}) — {blocker['reason']}")
        if blocker.get("next"):
            print(f"Next: {blocker['next']}")
    return 0


def cmd_next(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    stages = assess(work, run)
    persist(work, run, stages)
    for stage in stages.values():
        if stage.status in ("ok", "skipped", "blocked"):
            continue
        _print_next(stage)
        if stage.status in ("pending", "stale"):
            print(f"  Run: {python_command()} \"{Path(__file__).resolve()}\" run --work \"{work}\"")
        return 0
    print("NEXT: nothing — every stage is complete; run `verify`.")
    return 0


def cmd_validate(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    stages = assess(work, run)
    persist(work, run, stages)
    stage = stages[args.stage]
    _print_next(stage) if stage.status not in ("ok", "skipped") else print(f"{args.stage}: {stage.status} — {stage.reason}")
    if stage.errors or stage.status in ("invalid", "failed"):
        return stage.exit_code or EXIT_INVALID
    if stage.status == "stale":
        return EXIT_STALE
    return 0


def cmd_decide(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    if args.what != "no-visuals":
        raise GateError("the only decision is `no-visuals`")
    reason = " ".join(str(args.reason or "").split())
    if len(reason) < 20:
        raise GateError("--reason must explain in at least 20 characters why this video has no informative visuals "
                        "(e.g. 'talking head interview, static camera, no slides or screen content')")
    run["visual_content"] = {"decision": "none", "reason": reason, "decided_by": args.by, "decided_at": _now()}
    record_history(run, "decide no-visuals", None, 0)
    save_run(work, run)
    print(f"Recorded: visual content = none ({args.by}): {reason}")
    print("Downstream stages will run in text-only mode; this decision is printed first in the verify report.")
    return 0


def cmd_shortlist(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    paths = Paths(work, run)
    paths.reports.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, _script("shortlist.py"), "--work", str(work), "--ids", args.ids]
    if args.width:
        command += ["--width", str(args.width)]
    code, out, err = _invoke(command, cwd=work)
    (paths.reports / "shortlist.md").write_text(out, encoding="utf-8")
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    if out.strip():
        print(out.rstrip())
    record_history(run, "shortlist", "shortlist", code)
    stages = assess(work, run)
    persist(work, run, stages)
    return code


def verify_report(work: Path, run: dict) -> tuple[list[dict], int]:
    stages = assess(work, run)
    persist(work, run, stages)
    paths = Paths(work, run)
    rows: list[dict] = []
    visual = run.get("visual_content") or {}
    rows.append({"check": "visual content", "status": "PASS",
                 "evidence": f"{visual.get('decision')} — {visual.get('reason') or 'illustrated (default request)'} "
                             f"(decided by {visual.get('decided_by')})"})
    for name in STAGES:
        stage = stages[name]
        if stage.status == "ok":
            status = "PASS"
        elif stage.status == "skipped":
            status = "SKIP"
        else:
            status = "FAIL"
        rows.append({"check": name, "status": status, "evidence": stage.reason or stage.status,
                     "stage_status": stage.status, "warnings": stage.warnings})
    # deliverable checks beyond the stage graph
    if stages["render"].status == "ok" and paths.bundle:
        try:
            html = paths.bundle.read_text(encoding="utf-8")
            validate_generated_html(html)
            frames = (_json_or_none(paths.manifest) or {}).get("frames_count") if paths.manifest else None
            embedded = html.count("data:image/")
            ok = frames is None or embedded >= frames
            rows.append({"check": "bundle", "status": "PASS" if ok else "FAIL",
                         "evidence": f"{paths.bundle.name}: static HTML validated, {embedded} embedded images for "
                                     f"{frames} frames"})
        except SystemExit as exc:
            rows.append({"check": "bundle", "status": "FAIL", "evidence": str(exc)})
        if request(run).get("pdf") and paths.pdf and paths.pdf.is_file():
            head = paths.pdf.read_bytes()[:5]
            rows.append({"check": "pdf", "status": "PASS" if head.startswith(b"%PDF") else "FAIL",
                         "evidence": f"{paths.pdf.name} ({paths.pdf.stat().st_size // 1024} KB)"})
    failures = [row for row in rows if row["status"] == "FAIL"]
    stale_only = failures and all(row.get("stage_status") == "stale" for row in failures)
    code = 0 if not failures else (EXIT_STALE if stale_only else EXIT_INCOMPLETE)
    return rows, code


def cmd_verify(args) -> int:
    work = Path(args.work).expanduser().resolve()
    run = load_run(work)
    rows, code = verify_report(work, run)
    paths = Paths(work, run)
    report = {"verified_at": _now(), "complete": code == 0, "exit_code": code, "rows": rows,
              "deliverable": str(paths.bundle) if paths.bundle else None,
              "pdf": str(paths.pdf) if request(run).get("pdf") and paths.pdf else None,
              "counts": run.get("counts"), "visual_content": run.get("visual_content")}
    atomic_write(work / "verify.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return code
    print("# delivery verification")
    print()
    for row in rows:
        print(f"- {row['status']:4} {row['check']}: {row['evidence']}")
        for warning in row.get("warnings") or []:
            print(f"       warning: {warning}")
    print()
    if code == 0:
        print(f"COMPLETE — deliverable: {paths.bundle}" + (f" and {paths.pdf}" if report["pdf"] else ""))
    else:
        blocker = run.get("blocker") or {}
        print(f"INCOMPLETE (exit {code}) — {blocker.get('stage')}: {blocker.get('reason')}")
        if blocker.get("next"):
            print(f"Next: {blocker['next']}")
    print(f"Report written to {work / 'verify.json'}")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="record the request and create <work>/run.json")
    init.add_argument("source", help="YouTube URL or local recording")
    init.add_argument("--work", required=True)
    init.add_argument("--lang", choices=("he", "en"), default=None, help="default: SUMMARY_LANG, else he")
    init.add_argument("--tier", choices=("standard", "high"), default="standard")
    init.add_argument("--pdf", action="store_true")
    init.add_argument("--whisper", choices=("groq", "openai"), default=None,
                      help="explicit consent to upload audio to this provider when captions are missing")
    init.add_argument("--no-whisper", action="store_true")
    init.add_argument("--langs", default=None, help="force a yt-dlp caption pattern")
    init.add_argument("--wanted", default=None)
    init.add_argument("--output-mode", choices=("illustrated", "text-only"), default="illustrated")
    init.add_argument("--sections", default=None)
    init.add_argument("--allow-long", action="store_true")
    init.add_argument("--max-image-tokens", type=int, default=None)
    init.add_argument("--out-dir", default=None, help="default: ./summary-<video id>")
    init.add_argument("--focus", default=None, help="free-text focus notes from the user")
    init.add_argument("--force", action="store_true", help="reset stage records of an existing run.json")
    init.set_defaults(func=cmd_init)

    run = sub.add_parser("run", help="execute deterministic stages until a model-authored file is needed")
    run.add_argument("--work", required=True)
    run.add_argument("--until", choices=STAGES, default=None)
    run.add_argument("--retry", action="store_true", help="re-run a failed stage whose inputs did not change")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    for name, func in (("status", cmd_status), ("next", cmd_next)):
        cmd = sub.add_parser(name)
        cmd.add_argument("--work", required=True)
        cmd.add_argument("--json", action="store_true")
        cmd.set_defaults(func=func)

    validate = sub.add_parser("validate", help="gate one stage's artifact and print its errors")
    validate.add_argument("stage", choices=STAGES)
    validate.add_argument("--work", required=True)
    validate.set_defaults(func=cmd_validate)

    decide = sub.add_parser("decide", help="record an explicit no-visuals decision")
    decide.add_argument("what", choices=("no-visuals",))
    decide.add_argument("--work", required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--by", choices=("model", "user"), default="model")
    decide.set_defaults(func=cmd_decide)

    shortlist = sub.add_parser("shortlist", help="run shortlist.py and record the stage")
    shortlist.add_argument("--work", required=True)
    shortlist.add_argument("--ids", required=True)
    shortlist.add_argument("--width", type=int, default=None)
    shortlist.set_defaults(func=cmd_shortlist)

    verify = sub.add_parser("verify", help="objective delivery report; exit 0 only when complete")
    verify.add_argument("--work", required=True)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
