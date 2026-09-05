"""Stage gates: the repository decides whether the workflow may advance.

Every function here is pure (stdlib only, no ffmpeg, no network) so the same
checks run inside each script, inside `workflow.py`, and on every platform.
A gate returns a `GateResult`; scripts turn errors into `GateError` (exit 10) or
`StaleError` (exit 11) at their boundary. Models author content; these gates
say whether that content is structurally allowed to move on.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

ENGINE_VERSION = "1.8.0"

EXIT_UNRESOLVED = 9           # required visual coverage unresolved
EXIT_INVALID = 10             # a model-authored or upstream artifact is structurally invalid
EXIT_STALE = 11               # a downstream artifact was produced from different inputs
EXIT_INCOMPLETE = 12          # delivery verification failed
EXIT_SOURCE_UNAVAILABLE = 13  # the source could not be fetched (private, removed, blocked, unreadable)

# The request options each artifact records so a changed request is a stale artifact,
# not a silently adopted one.
TRANSCRIPT_OPTION_KEYS = ("whisper", "no_whisper", "langs", "wanted")
CANDIDATE_OPTION_KEYS = ("tier", "sections", "max_image_tokens", "allow_long")

TARGET_KINDS = {"state", "action_result", "diagram", "slide"}
ROLES = {"evidence", "illustration"}
NOVELTY = {"new_state", "build_stage", "reprise"}
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")
HEBREW_RE = re.compile(r"[֐-׿]")
URL_RE = re.compile(r"^https?://", re.I)
MAX_SELECTIONS = 20
MAX_PER_CHAPTER = 3

# transcript-health thresholds (warnings, never errors: captions are evidence, not a quota)
HEALTH_MIN_COVERAGE = 0.6
HEALTH_MAX_GAP_S = 120.0
HEALTH_MAX_REPETITION = 0.3
HEALTH_WPM_RANGE = (40.0, 260.0)


class GateError(SystemExit):
    """Structural invalidity. Prints the message and exits with EXIT_INVALID."""

    exit_code = EXIT_INVALID

    def __init__(self, message: str, *, quiet: bool = False):
        self.message = message
        if not quiet:
            print(f"[vsum] {message}", file=sys.stderr)
        super().__init__(message)   # str(exc) is the message …
        self.code = self.exit_code  # … and the process exit status is the code


class StaleError(GateError):
    """A later artifact no longer matches the inputs it was made from."""

    exit_code = EXIT_STALE


class UnresolvedError(GateError):
    """A chapter or target that needs visual evidence has none."""

    exit_code = EXIT_UNRESOLVED


@dataclass
class GateResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self, prefix: str, *, stale: bool = False) -> None:
        if self.errors:
            shown = "; ".join(self.errors[:8])
            more = f" (+{len(self.errors) - 8} more)" if len(self.errors) > 8 else ""
            cls = StaleError if stale else GateError
            raise cls(f"{prefix}: {shown}{more}")

    def print_warnings(self, prefix: str) -> None:
        for warning in self.warnings:
            print(f"[vsum] warning: {prefix}: {warning}", file=sys.stderr)


# ----------------------------------------------------------------------------- hashing


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


RECEIPT_KEYS = ("shortlist", "shortlist_history")


def candidates_digest(payload: dict) -> str:
    """Identity of a candidate pool: the manifest minus the triage receipts written after it."""
    trimmed = {key: value for key, value in payload.items() if key not in RECEIPT_KEYS}
    return canonical_sha256(trimmed)


def selections_binding(selections: list) -> str:
    """The part of selections.json that decides which pixels grab writes:
    candidate id, asset name and crop. Captions and anchors can change without
    a re-grab (render re-reads them), so they are not part of the binding."""
    rows = []
    for selection in selections if isinstance(selections, list) else []:
        if isinstance(selection, dict):
            rows.append({"candidate_id": str(selection.get("candidate_id") or ""),
                         "name": str(selection.get("name") or ""),
                         "crop": selection.get("crop")})
    return canonical_sha256(rows)


def is_url(source: str) -> bool:
    return bool(URL_RE.match(str(source)))


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                 "youtube-nocookie.com", "www.youtube-nocookie.com"}
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def canonical_source(source: object) -> object:
    """One spelling per video: `youtu.be/<id>`, `/shorts/<id>`, `/live/<id>`, `/embed/<id>`,
    the mobile/music/nocookie hosts and tracking parameters (`t=`, `si=`, `list=`,
    `feature=`) all collapse to `https://www.youtube.com/watch?v=<id>`; other URLs
    only lose their fragment; anything that is not a URL is returned unchanged."""
    if not isinstance(source, str) or not is_url(source):
        return source
    parts = urlsplit(source.strip())
    host = (parts.hostname or "").lower()
    video_id = None
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0]
    elif host in YOUTUBE_HOSTS:
        segments = [segment for segment in parts.path.split("/") if segment]
        if segments and segments[0] in ("shorts", "live", "embed", "v") and len(segments) > 1:
            video_id = segments[1]
        elif segments and segments[0] == "watch":
            video_id = (parse_qs(parts.query).get("v") or [None])[0]
    if video_id and YOUTUBE_ID_RE.match(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def source_identity(source: str) -> dict | str:
    """What makes a source *this* source: the canonical URL, or the local path with size and mtime."""
    if is_url(source):
        return canonical_source(str(source))
    local = Path(source).expanduser().resolve()
    stat = local.stat()
    return {"path": str(local), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def source_key(identity: object) -> str | None:
    """The part of an identity that names the source (URL or path), without size/mtime."""
    if isinstance(identity, dict):
        return str(identity.get("path")) if identity.get("path") else None
    if isinstance(identity, str):
        return str(canonical_source(identity))
    return None


def identity_matches(recorded: object, expected: object) -> bool:
    """True when both identities name the same source (or either is unknown)."""
    if recorded is None or expected is None:
        return True
    if isinstance(recorded, dict) or isinstance(expected, dict):
        if not (isinstance(recorded, dict) and isinstance(expected, dict)):
            return False
        return all(recorded.get(key) == expected.get(key) for key in ("path", "size", "mtime_ns"))
    return canonical_source(str(recorded)) == canonical_source(str(expected))


def describe_identity(identity: object) -> str:
    if isinstance(identity, dict):
        return f"{identity.get('path')} ({identity.get('size')} B, mtime {identity.get('mtime_ns')})"
    return str(identity)


def _version_tuple(version: object) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def engine_drift(recorded: object, current: str | None = None) -> tuple[str, str | None]:
    """How far an artifact's engine version is from the running one:
    `same` | `patch` | `minor` | `major` | `unknown`, with a message for the report.
    The running version is read at call time so tests can patch ENGINE_VERSION."""
    current = current or ENGINE_VERSION
    old, new = _version_tuple(recorded), _version_tuple(current)
    if old is None or new is None:
        return "unknown", f"no engine version recorded (this is {current})"
    if old == new:
        return "same", None
    if old[0] != new[0]:
        return "major", f"produced by engine {recorded}; this is {current} (major difference)"
    if old[1] != new[1]:
        return "minor", f"produced by engine {recorded}; this is {current} — re-run the stage"
    return "patch", f"produced by engine {recorded}; this is {current} (patch difference, kept)"


def _norm_option(value: object) -> object:
    """None, False and "" all mean 'not requested'."""
    return None if value in (None, False, "") else value


def option_diffs(recorded: dict, expected: dict) -> list[str]:
    """Human-readable `key: recorded -> expected` for every option that differs."""
    diffs = []
    for key, wanted in expected.items():
        if _norm_option(recorded.get(key)) != _norm_option(wanted):
            diffs.append(f"{key}: {recorded.get(key)!r} -> {wanted!r}")
    return diffs


def load_json(path: Path | str, label: str | None = None) -> object:
    path = Path(path)
    label = label or path.name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"{label} not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"{label} unreadable: {path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"{label} is not valid JSON: {path} ({exc})") from exc


# ----------------------------------------------------------------------------- transcript


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def transcript_health(segments: list[dict], duration: float | None) -> dict:
    """Deterministic quality signals. Warnings only: a thin transcript is a fact to report."""
    rows = []
    for segment in segments:
        start, end = _num(segment.get("start")), _num(segment.get("end"))
        if start is None or end is None:
            continue
        rows.append((start, max(start, end), str(segment.get("text") or "")))
    duration = _num(duration) or 0.0
    words = sum(len(text.split()) for _, _, text in rows)
    covered = 0.0
    largest_gap = 0.0
    gaps_over_30 = 0
    previous_end = 0.0
    non_positive = sum(1 for start, end, _ in rows if end <= start)
    monotonic = all(rows[index][0] <= rows[index + 1][0] for index in range(len(rows) - 1))
    for start, end, _ in sorted(rows):
        gap = start - previous_end
        if gap > largest_gap:
            largest_gap = gap
        if gap > 30:
            gaps_over_30 += 1
        covered += max(0.0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    if duration and duration - previous_end > largest_gap:
        largest_gap = duration - previous_end
    beyond = sum(1 for start, _, _ in rows if duration and start > duration + 2)
    repeated = 0
    recent: list[str] = []
    for _, _, text in rows:
        normalized = " ".join(text.lower().split())
        if normalized and normalized in recent:
            repeated += 1
        recent = (recent + [normalized])[-5:]
    repetition = repeated / len(rows) if rows else 0.0
    minutes = (covered or duration) / 60.0
    wpm = words / minutes if minutes > 0 else None
    coverage = round(covered / duration, 3) if duration > 0 else None
    warnings: list[str] = []
    if coverage is not None and coverage < HEALTH_MIN_COVERAGE:
        warnings.append(f"captions cover {coverage:.0%} of the {duration:.0f} s video")
    if largest_gap > HEALTH_MAX_GAP_S:
        warnings.append(f"largest uncaptioned gap is {largest_gap:.0f} s")
    if repetition > HEALTH_MAX_REPETITION:
        warnings.append(f"{repetition:.0%} of segments repeat a recent segment")
    if wpm is not None and rows and not (HEALTH_WPM_RANGE[0] <= wpm <= HEALTH_WPM_RANGE[1]):
        warnings.append(f"{wpm:.0f} words per minute is outside the plausible range")
    if non_positive:
        warnings.append(f"{non_positive} segment(s) have end <= start")
    if beyond:
        warnings.append(f"{beyond} segment(s) start after the video ends")
    if not monotonic:
        warnings.append("segments are not in chronological order")
    return {
        "segments": len(rows), "words": words, "covered_seconds": round(covered, 3),
        "coverage_ratio": coverage, "largest_gap_s": round(largest_gap, 3), "gaps_over_30s": gaps_over_30,
        "monotonic": monotonic, "non_positive": non_positive, "beyond_duration": beyond,
        "repetition_ratio": round(repetition, 3), "wpm": round(wpm, 1) if wpm is not None else None,
        "warnings": warnings,
    }


def validate_transcript(payload: object, *, expected_identity: object = None,
                        expected_options: dict | None = None) -> GateResult:
    """Structure, status and — when the caller says what it expects — provenance:
    a transcript fetched for another source or under other transcription options
    is `stale` (info["stale"]), so the controller re-runs the stage instead of
    adopting it."""
    result = GateResult()
    if not isinstance(payload, dict):
        result.errors.append("transcript.json must be a JSON object")
        return result
    stale: list[str] = []
    if expected_identity is not None:
        recorded_identity = payload.get("source_identity")
        if recorded_identity is None:
            result.warnings.append("transcript.json predates source binding (no source_identity)")
        elif not identity_matches(recorded_identity, expected_identity):
            stale.append(f"transcript.json was fetched for a different source "
                         f"({describe_identity(recorded_identity)}, not {describe_identity(expected_identity)})")
    if expected_options is not None:
        recorded_options = payload.get("inputs")
        if not isinstance(recorded_options, dict):
            result.warnings.append("transcript.json predates option binding (no inputs block)")
        else:
            diffs = option_diffs(recorded_options, expected_options)
            if diffs:
                stale.append("transcript.json was produced with different transcription options: " + ", ".join(diffs))
    result.info["stale"] = stale
    result.errors.extend(stale)
    status = payload.get("status")
    if status is not None and status != "ok":
        detail = ""
        source_detail = payload.get("source_detail") or {}
        if isinstance(source_detail, dict) and source_detail.get("reason"):
            detail = f" ({source_detail['reason']})"
        result.errors.append(f"transcript status is {status!r}{detail}; run transcript.py again or choose a transcription option")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        result.errors.append("transcript.json must contain a segments array")
        return result
    if not segments:
        result.errors.append("transcript has zero segments (no usable captions or transcription)")
        return result
    seen: set[str] = set()
    previous_start = None
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            result.errors.append(f"segment {index} is not an object")
            continue
        seg_id = segment.get("seg_id")
        if not isinstance(seg_id, str) or not seg_id:
            result.errors.append(f"segment {index} has no seg_id")
        elif seg_id in seen:
            result.errors.append(f"duplicate seg_id {seg_id}")
        else:
            seen.add(seg_id)
        start, end = _num(segment.get("start")), _num(segment.get("end"))
        if start is None or end is None:
            result.errors.append(f"segment {seg_id or index} has non-numeric start/end")
            continue
        if end < start:
            result.errors.append(f"segment {seg_id or index} ends before it starts")
        if previous_start is not None and start < previous_start:
            result.errors.append(f"segment {seg_id or index} is out of chronological order")
        previous_start = start
        if not isinstance(segment.get("text"), str):
            result.errors.append(f"segment {seg_id or index} has no text")
    video = payload.get("video") if isinstance(payload.get("video"), dict) else {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else None
    if health is None:
        health = transcript_health(segments, video.get("duration"))
    result.info["health"] = health
    result.info["video_id"] = video.get("id")
    result.warnings.extend(health.get("warnings", []))
    if not payload.get("source"):
        result.warnings.append("transcript has no source provenance")
    return result


# ----------------------------------------------------------------------------- chapters


def _segment_ids(transcript: dict | None) -> set[str]:
    if not isinstance(transcript, dict):
        return set()
    return {str(segment.get("seg_id")) for segment in transcript.get("segments", []) if isinstance(segment, dict)}


def validate_chapters(raw: object, transcript: dict | None, duration: float | None = None, *,
                      visual_decision: str = "illustrated") -> GateResult:
    result = GateResult()
    if not isinstance(raw, list):
        result.errors.append("chapters.json must be an array of chapters")
        return result
    if not raw:
        result.errors.append("chapters.json is empty; author at least one chapter")
        return result
    known = _segment_ids(transcript)
    ids: set[str] = set()
    target_ids: set[str] = set()
    previous_end: float | None = None
    previous_start: float | None = None
    needs_any = False
    duration = _num(duration) or 0.0
    for index, chapter in enumerate(raw):
        label = f"chapter {index}"
        if not isinstance(chapter, dict):
            result.errors.append(f"{label} is not an object")
            continue
        chapter_id = chapter.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            result.errors.append(f"{label} has no chapter_id")
        else:
            label = chapter_id
            if chapter_id in ids:
                result.errors.append(f"duplicate chapter_id {chapter_id}")
            ids.add(chapter_id)
        if not str(chapter.get("title") or "").strip():
            result.warnings.append(f"{label} has no title")
        start, end = _num(chapter.get("start")), _num(chapter.get("end"))
        if start is None or end is None:
            result.errors.append(f"{label}: start and end must be numbers")
        else:
            if start < 0:
                result.errors.append(f"{label}: start is negative")
            if end <= start:
                result.errors.append(f"{label}: end must be after start")
            if previous_start is not None and start < previous_start:
                result.errors.append(f"{label}: chapters must be in chronological order")
            if previous_end is not None and start < previous_end - 1e-6:
                result.errors.append(f"{label}: overlaps the previous chapter")
            if previous_end is not None and start - previous_end > 30:
                result.warnings.append(f"{label}: {start - previous_end:.0f} s gap before this chapter")
            if index == 0 and start > 30:
                result.warnings.append(f"{label}: the first chapter starts at {start:.0f} s")
            previous_start, previous_end = start, max(end, previous_end or 0.0)
            if index == len(raw) - 1 and duration and end > duration + 1.0:
                result.errors.append(f"{label}: ends at {end:.0f} s, after the video ({duration:.0f} s)")
        needs = chapter.get("needs_frames", "MISSING")
        if needs is True:
            needs_any = True
        elif needs is not False:
            shown = "missing" if needs == "MISSING" else json.dumps(needs)
            result.errors.append(f"{label}: needs_frames must be true or false (got {shown})")
        targets = chapter.get("visual_targets")
        if targets is None:
            targets = []
        if not isinstance(targets, list):
            result.errors.append(f"{label}: visual_targets must be an array")
            targets = []
        if len(targets) > 3:
            result.warnings.append(f"{label}: {len(targets)} targets (usually at most 2)")
        for target_index, target in enumerate(targets):
            tlabel = f"{label} target {target_index}"
            if not isinstance(target, dict):
                result.errors.append(f"{tlabel} is not an object")
                continue
            target_id = target.get("target_id")
            if isinstance(target_id, str) and target_id:
                tlabel = target_id
                if target_id in target_ids:
                    result.errors.append(f"duplicate target_id {target_id}")
                target_ids.add(target_id)
            kind = target.get("kind", "state")
            if kind not in TARGET_KINDS:
                result.errors.append(f"{tlabel}: unsupported kind {kind!r}")
            seg_ids = target.get("seg_ids", [])
            if isinstance(seg_ids, str):
                result.errors.append(f"{tlabel}: seg_ids must be an array, not a string")
                seg_ids = []
            elif not isinstance(seg_ids, list):
                result.errors.append(f"{tlabel}: seg_ids must be an array")
                seg_ids = []
            refs = [str(value) for value in seg_ids]
            for key in ("seg_id", "action_seg_id"):
                if target.get(key):
                    refs.append(str(target[key]))
            if known:
                missing = [ref for ref in refs if ref not in known]
                if missing:
                    result.errors.append(f"{tlabel}: cites segments that are not in the transcript: {missing}")
            explicit_time = any(target.get(key) is not None for key in ("t", "anchor_t", "window"))
            if not refs and not explicit_time:
                result.errors.append(f"{tlabel}: no seg_ids (or anchor_t/window) — the engine cannot place it")
            if kind == "action_result" and not target.get("action_seg_id") and not seg_ids:
                result.warnings.append(f"{tlabel}: action_result without action_seg_id")
            if needs is False and refs:
                result.warnings.append(f"{tlabel}: target inside a chapter with needs_frames false is ignored")
    count = len(raw)
    if count < 3 or count > 20:
        result.warnings.append(f"{count} chapters (a typical talk has 5–12)")
    if visual_decision == "none" and needs_any:
        needing = [str(c.get("chapter_id")) for c in raw if isinstance(c, dict) and c.get("needs_frames") is True]
        result.errors.append(
            f"{', '.join(needing)} need frames (needs_frames: true), but a no-visuals decision is recorded — "
            "set needs_frames false if the screen truly shows nothing there, or revert the decision with "
            "`workflow.py decide illustrated --reason \"...\"`"
        )
    if visual_decision == "illustrated" and not needs_any and not result.errors:
        result.errors.append(
            "no chapter has needs_frames: true, but the request is an illustrated summary — "
            "either mark the chapters that show something on screen, or record an explicit no-visuals "
            "decision with `workflow.py decide no-visuals --reason \"...\"` if the video truly has no informative visuals"
        )
    result.info["needs_frames_chapters"] = sum(1 for c in raw if isinstance(c, dict) and c.get("needs_frames") is True)
    result.info["targets"] = len(target_ids) or sum(
        len(c.get("visual_targets") or []) for c in raw if isinstance(c, dict))
    return result


# ----------------------------------------------------------------------------- candidates


def validate_candidates(payload: object, *, transcript_sha: str | None = None, chapters_sha: str | None = None,
                        visual_decision: str = "illustrated", allow_unresolved: bool = False,
                        expected_identity: object = None, expected_video_id: object = None,
                        expected_options: dict | None = None) -> GateResult:
    result = GateResult()
    if not isinstance(payload, dict):
        result.errors.append("candidates.json must be a JSON object")
        return result
    status = payload.get("status", "ok")
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    stale = []
    if transcript_sha and inputs.get("transcript_sha256") and inputs["transcript_sha256"] != transcript_sha:
        stale.append("transcript.json changed since candidates were extracted")
    if chapters_sha and inputs.get("chapters_sha256") and inputs["chapters_sha256"] != chapters_sha:
        stale.append("chapters.json changed since candidates were extracted")
    if expected_identity is not None and inputs.get("source_identity") is not None \
            and not identity_matches(inputs["source_identity"], expected_identity):
        stale.append(f"candidates.json was cut from a different source "
                     f"({describe_identity(inputs['source_identity'])}, not {describe_identity(expected_identity)})")
    if expected_video_id and inputs.get("video_id") and str(inputs["video_id"]) != str(expected_video_id):
        stale.append(f"candidates.json belongs to video {inputs['video_id']}, not {expected_video_id}")
    if expected_options is not None:
        wanted_tier, recorded_tier = expected_options.get("tier"), payload.get("tier")
        if wanted_tier and recorded_tier and str(recorded_tier) != str(wanted_tier):
            stale.append(f"candidates.json was extracted at tier {recorded_tier} but the request is tier {wanted_tier}")
        other = {key: value for key, value in expected_options.items() if key != "tier"}
        recorded_options = inputs.get("options") if isinstance(inputs.get("options"), dict) else None
        if recorded_options is None:
            if inputs and other:
                result.warnings.append("candidates.json predates option binding (no inputs.options block)")
        else:
            diffs = option_diffs(recorded_options, other)
            if diffs:
                stale.append("candidates.json was extracted with different options: " + ", ".join(diffs))
    result.info["stale"] = stale
    result.errors.extend(stale)
    if not inputs:
        result.warnings.append("candidates.json predates input binding (no inputs block); staleness cannot be checked")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        result.errors.append("candidates.json has no candidates array")
        return result
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    unresolved_chapters = [row.get("chapter_id") for row in coverage.get("chapters", [])
                           if isinstance(row, dict) and row.get("status") == "unresolved"]
    unresolved_targets = [row.get("target_id") for row in coverage.get("targets", [])
                          if isinstance(row, dict) and row.get("status") == "unresolved"]
    result.info["unresolved_chapters"] = unresolved_chapters
    result.info["unresolved_targets"] = unresolved_targets
    result.info["candidates"] = len(candidates)
    if status == "no_visual_chapters":
        if visual_decision == "illustrated":
            result.errors.append("candidate extraction was skipped because no chapter needs frames, "
                                 "but the request is an illustrated summary")
        return result
    if status not in ("ok", "unresolved"):
        result.errors.append(f"candidates.json status is {status!r}")
    if unresolved_chapters or unresolved_targets:
        message = "unresolved visual coverage: " + ", ".join(
            [*(f"chapter {c}" for c in unresolved_chapters), *(f"target {t}" for t in unresolved_targets)])
        if allow_unresolved:
            result.warnings.append(message)
        else:
            result.errors.append(message)
    if not candidates and visual_decision == "illustrated":
        result.errors.append("zero candidate frames were extracted for an illustrated summary")
    return result


# ----------------------------------------------------------------------------- selections


def caption_fields(selection: dict) -> dict:
    caption = selection.get("caption")
    if isinstance(caption, dict):
        return {"shows": str(caption.get("shows") or "").strip(), "why": str(caption.get("why") or "").strip(),
                "look_at": str(caption.get("look_at") or "").strip()}
    return {"shows": str(caption or "").strip(), "why": "", "look_at": ""}


def validate_selections(selections: object, candidates_payload: dict | None, *, lang: str = "en",
                        shortlist_receipt: dict | None = None, require_non_empty: bool = True) -> GateResult:
    result = GateResult()
    if not isinstance(selections, list):
        result.errors.append("selections.json must be an array")
        return result
    if not selections:
        if require_non_empty:
            result.errors.append("selections.json is empty; an illustrated summary needs at least one verified frame")
        return result
    if len(selections) > MAX_SELECTIONS:
        result.errors.append(f"{len(selections)} selections exceed the HTML frame budget of {MAX_SELECTIONS}")
    candidates = {}
    if isinstance(candidates_payload, dict):
        candidates = {str(c.get("candidate_id")): c for c in candidates_payload.get("candidates", [])
                      if isinstance(c, dict)}
    shortlisted = None
    if isinstance(shortlist_receipt, dict):
        shortlisted = {str(row.get("candidate_id")) for row in shortlist_receipt.get("written", [])
                       if isinstance(row, dict)}
    seen: set[str] = set()
    names: set[str] = set()
    per_chapter: dict[str, int] = {}
    for index, selection in enumerate(selections):
        label = f"selection {index}"
        if not isinstance(selection, dict):
            result.errors.append(f"{label} is not an object")
            continue
        candidate_id = str(selection.get("candidate_id") or "")
        if not candidate_id:
            result.errors.append(f"{label}: candidate_id is required (never a timestamp)")
            continue
        label = candidate_id
        if candidate_id in seen:
            result.errors.append(f"{label}: selected twice")
        seen.add(candidate_id)
        candidate = candidates.get(candidate_id)
        if candidates and candidate is None:
            result.errors.append(f"{label}: not a candidate id in candidates.json")
        chapter_id = str(selection.get("chapter_id") or "")
        if candidate is not None:
            if chapter_id and chapter_id != str(candidate.get("chapter_id")):
                result.errors.append(f"{label}: chapter_id {chapter_id} differs from the candidate's {candidate.get('chapter_id')}")
            chapter_id = chapter_id or str(candidate.get("chapter_id") or "")
        if not chapter_id:
            result.errors.append(f"{label}: chapter_id is required")
        per_chapter[chapter_id] = per_chapter.get(chapter_id, 0) + 1
        name = str(selection.get("name") or "").strip()
        if not SAFE_NAME_RE.fullmatch(name):
            result.errors.append(f"{label}: name must be letters, digits, _ or - (got {name!r})")
        elif name in names:
            result.errors.append(f"{label}: name {name!r} is used twice")
        names.add(name)
        if selection.get("role") not in ROLES:
            result.errors.append(f"{label}: role must be one of {sorted(ROLES)}")
        novelty = selection.get("novelty", "new_state")
        if novelty not in NOVELTY:
            result.errors.append(f"{label}: novelty must be one of {sorted(NOVELTY)}")
        caption = caption_fields(selection)
        if not caption["shows"]:
            result.errors.append(f"{label}: caption.shows is required")
        elif lang == "he" and not HEBREW_RE.search(caption["shows"]):
            result.errors.append(f"{label}: Hebrew document — caption.shows must be Hebrew")
        if novelty == "build_stage" and not caption["why"]:
            result.errors.append(f"{label}: a build_stage frame must say in caption.why what the stage adds")
        alt = str(selection.get("alt") or "").strip()
        if not alt:
            result.errors.append(f"{label}: alt is required")
        elif len(alt) > 160:
            result.errors.append(f"{label}: alt text over 160 characters")
        anchors = selection.get("anchor_seg_ids")
        if not isinstance(anchors, list) or not anchors:
            result.errors.append(f"{label}: anchor_seg_ids must be a non-empty array")
        elif candidate is not None:
            allowed = set(map(str, candidate.get("seg_ids", []))) | set(map(str, candidate.get("aligned_seg_ids", [])))
            if allowed and not allowed.intersection(map(str, anchors)):
                result.errors.append(f"{label}: anchor_seg_ids do not overlap the candidate's provenance")
        crop = selection.get("crop")
        if crop is not None and not CROP_RE.match(str(crop)):
            result.errors.append(f"{label}: crop must be w:h:x:y")
        if shortlisted is not None and candidate_id not in shortlisted:
            result.warnings.append(f"{label}: was not in the verified shortlist (chosen from a contact-sheet tile only)")
    overfull = [chapter for chapter, count in per_chapter.items() if count > MAX_PER_CHAPTER]
    if overfull:
        result.errors.append("more than 3 frames in chapter(s): " + ", ".join(overfull))
    result.info["selections"] = len(selections)
    return result


# ----------------------------------------------------------------------------- assets


def validate_assets(assets_payload: object, selections: list | None = None, *, selections_sha: str | None = None,
                    candidates_sha: str | None = None, check_files: bool = True) -> GateResult:
    result = GateResult()
    if not isinstance(assets_payload, dict):
        result.errors.append("assets-manifest.json must be a JSON object")
        return result
    if assets_payload.get("failures"):
        result.errors.append(f"grab reported extraction failures: {assets_payload['failures'][:3]}")
    if assets_payload.get("duplicate_pairs"):
        result.errors.append("grab reported hard-duplicate selections")
    stale = []
    recorded_sel = assets_payload.get("selections_sha256")
    recorded_binding = assets_payload.get("selections_binding_sha256")
    recorded_cand = assets_payload.get("candidates_sha256")
    if recorded_binding and selections is not None:
        if recorded_binding != selections_binding(selections):
            stale.append("the selected frames (ids, names or crops) changed after the assets were grabbed; re-run grab.py")
    elif selections_sha and recorded_sel and recorded_sel != selections_sha:
        stale.append("selections.json changed after the assets were grabbed; re-run grab.py")
    if candidates_sha and recorded_cand and recorded_cand != candidates_sha:
        stale.append("candidates.json changed after the assets were grabbed; re-run grab.py")
    if not recorded_sel or not recorded_cand:
        result.warnings.append("assets-manifest.json predates input binding (no selections/candidates hash)")
    result.info["stale"] = stale
    result.errors.extend(stale)
    assets = assets_payload.get("assets")
    if not isinstance(assets, list):
        result.errors.append("assets-manifest.json has no assets array")
        return result
    by_id = {str(asset.get("candidate_id")): asset for asset in assets if isinstance(asset, dict)}
    if selections is not None:
        for selection in selections:
            if isinstance(selection, dict):
                candidate_id = str(selection.get("candidate_id") or "")
                if candidate_id not in by_id:
                    result.errors.append(f"{candidate_id}: no grabbed asset for this selection")
    # The recorded pixel gate (1.8): grab.py stores the measured deltas next to
    # the thresholds it applied, so the proof travels with the asset.
    worst: dict[str, float] = {}
    for candidate_id, asset in by_id.items():
        verification = asset.get("verification")
        if not isinstance(verification, dict):
            continue
        thresholds = verification.get("thresholds") if isinstance(verification.get("thresholds"), dict) else {}
        for metric, key in (("luma_mad", "luma"), ("edge_mad", "edge"), ("changed_ratio", "changed")):
            value, limit = _num(verification.get(metric)), _num(thresholds.get(key))
            if value is None:
                continue
            worst[metric] = max(worst.get(metric, 0.0), value)
            if limit is not None and value > limit:
                result.errors.append(f"{candidate_id}: recorded pixel gate failed ({metric} {value:.3g} > {limit:.3g})")
    result.info["verification_worst"] = worst
    if check_files:
        for candidate_id, asset in by_id.items():
            for variant in ("full", "thumb"):
                record = asset.get(variant) if isinstance(asset.get(variant), dict) else {}
                path = record.get("path")
                if not path:
                    result.errors.append(f"{candidate_id}: asset record has no {variant} path")
                    continue
                if not Path(path).is_file():
                    result.errors.append(f"{candidate_id}: missing asset file {Path(path).name}")
                    continue
                expected = record.get("sha256")
                if not expected:
                    result.errors.append(f"{candidate_id}: {variant} asset has no recorded sha256")
                elif sha256_file(path) != expected:
                    result.errors.append(f"{candidate_id}: {Path(path).name} does not match its recorded sha256")
    result.info["assets"] = len(by_id)
    return result


# ----------------------------------------------------------------------------- summary and manifest

MANIFEST_HASH_KEYS = ("summary_sha256", "selections_sha256", "transcript_sha256", "chapters_sha256",
                      "candidates_sha256", "assets_manifest_sha256")


def validate_summary(summary: object, *, lang: str | None = None) -> GateResult:
    """Shape only (the audit judges the content) plus the declared language."""
    result = GateResult()
    if not isinstance(summary, dict) or not isinstance(summary.get("chapters"), list) or not summary.get("overview"):
        result.errors.append("summary.json must be an object with overview and chapters")
        return result
    declared = summary.get("lang")
    if lang and declared and str(declared).lower() != str(lang).lower():
        result.errors.append(f"summary.json declares lang {declared!r} but the request is {lang!r} — "
                             f"rewrite it in {lang} (or `init --force --lang {declared}`)")
    elif lang and not declared:
        result.warnings.append(f"summary.json does not declare lang; the request is {lang}")
    result.info["chapters"] = len(summary["chapters"])
    return result


def validate_manifest(manifest: object, *, expected: dict, bundle_sha: str | None = None,
                      pdf_sha: str | None = None) -> GateResult:
    """Is the rendered document the one made from the current inputs? Every
    mismatch is `stale` (re-render), never invalid: the inputs are fine, the
    document is old."""
    result = GateResult()
    if not isinstance(manifest, dict):
        result.errors.append("manifest.json must be a JSON object")
        return result
    stale: list[str] = []
    if "frames_count" not in manifest:
        stale.append("manifest.json predates the workflow (no bindings); render again")
    for key in MANIFEST_HASH_KEYS:
        recorded, current = manifest.get(key), expected.get(key)
        if recorded and current and recorded != current:
            stale.append(f"{key} changed since the last render")
    for key, label in (("output_mode", "output mode"), ("lang", "language"), ("tier", "tier"),
                       ("visual_content", "visual-content decision")):
        recorded, current = manifest.get(key), expected.get(key)
        if recorded is not None and current is not None and str(recorded) != str(current):
            stale.append(f"the {label} changed since the last render ({recorded} -> {current})")
    if bundle_sha is not None:
        recorded_bundle = manifest.get("bundle_sha256")
        if not recorded_bundle:
            result.warnings.append("manifest.json predates bundle binding (no bundle_sha256)")
        elif recorded_bundle != bundle_sha:
            stale.append("the single-file bundle does not match manifest.json (rebuilt or foreign): render again")
    if pdf_sha is not None and manifest.get("pdf_sha256") and manifest["pdf_sha256"] != pdf_sha:
        stale.append("the PDF does not match manifest.json: render again")
    result.info["stale"] = stale
    result.errors.extend(stale)
    result.info["frames_count"] = manifest.get("frames_count")
    result.info["bundle_bound"] = bool(manifest.get("bundle_sha256"))
    return result
