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

ENGINE_VERSION = "1.10.0"

EXIT_UNRESOLVED = 9           # required visual coverage unresolved
EXIT_INVALID = 10             # a model-authored or upstream artifact is structurally invalid
EXIT_STALE = 11               # a downstream artifact was produced from different inputs
EXIT_INCOMPLETE = 12          # delivery verification failed
EXIT_SOURCE_UNAVAILABLE = 13  # the source could not be fetched (private, removed, blocked, unreadable)

# The request options each artifact records so a changed request is a stale artifact,
# not a silently adopted one.
TRANSCRIPT_OPTION_KEYS = ("whisper", "no_whisper", "langs", "wanted", "local_model")
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
HEALTH_MAX_EMPTY_TEXT = 0.10        # share of segments with blank text
HEALTH_MIN_SEGMENTS_PER_MIN = 2.0   # coarser than that, citations cannot point at a sentence
# flags that mean the transcript under-represents or misrepresents the speech → status "thin"
THIN_FLAGS = {"low_coverage", "large_gap", "sparse_segments", "empty_text", "translated", "chunks_failed"}
LEGACY_LANG_CODES = {"iw": "he", "ji": "yi", "in": "id"}

# visual probe: evidence for (or against) a no-visuals decision. A picture that
# holds still for PROBE_MIN_SETTLED_SAMPLES consecutive samples is "content"
# (a slide, a UI state, a drawn board, a photo); a talking head never holds
# still. The dominant still picture is the backdrop (cover image, main shot)
# and does not count. Enough still seconds beyond it contradict the decision.
PROBE_MIN_STILL_S = 90.0
PROBE_MIN_STILL_RATIO = 0.20
PROBE_MIN_SETTLED_SAMPLES = 2
PROBE_MAX_SPANS = 6


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


def _mmss(seconds: object) -> str:
    value = _num(seconds)
    if value is None:
        return "?"
    total = int(round(value))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes:02d}:{sec:02d}"


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


def _lang_base(code: object) -> str | None:
    text = str(code or "").strip().lower()
    if not text:
        return None
    base = text.split("-")[0].split("_")[0]
    return LEGACY_LANG_CODES.get(base, base)


def health_status(flags: list[str]) -> str:
    return "thin" if any(flag in THIN_FLAGS for flag in flags) else "ok"


def health_provenance(source: object, source_detail: object, language: object, video_language: object) -> dict:
    """Where the words came from, in one record: caption track or transcription
    backend, manual/automatic, original/translated, and whether the track's
    language is the video's."""
    detail = source_detail if isinstance(source_detail, dict) else {}
    kind = detail.get("kind")
    if not kind:
        text = str(source or "")
        kind = "captions" if text == "captions" else ("whisper" if text.startswith("whisper") else "none")
    track_language, video_base = _lang_base(language), _lang_base(video_language)
    return {
        "source": source, "kind": kind, "track": detail.get("track"), "backend": detail.get("backend"),
        "manual": detail.get("manual"), "original": detail.get("original"),
        "translated": bool(detail.get("translated", False)),
        "language": track_language, "video_language": video_base,
        "language_match": (track_language == video_base) if track_language and video_base else None,
    }


def health_summary(health: dict) -> str:
    """The one line every report prints for the transcript."""
    if not isinstance(health, dict):
        return "health unknown"
    provenance = health.get("provenance") if isinstance(health.get("provenance"), dict) else {}
    kind = provenance.get("kind") or "transcript"
    name = provenance.get("track") or provenance.get("backend") or provenance.get("language") or ""
    qualifiers = []
    if provenance.get("manual") is True:
        qualifiers.append("manual")
    elif provenance.get("manual") is False:
        qualifiers.append("auto")
    if provenance.get("original"):
        qualifiers.append("original")
    if provenance.get("translated"):
        qualifiers.append("machine-translated")
    if provenance.get("language_match") is False:
        qualifiers.append(f"video language {provenance.get('video_language')}")
    head = f"{kind} {name}".strip() + (f" ({', '.join(qualifiers)})" if qualifiers else "")
    coverage = health.get("coverage_ratio")
    coverage_text = f"coverage {coverage:.0%}" if isinstance(coverage, (int, float)) else "coverage n/a"
    status = health.get("status") or health_status(list(health.get("flags") or []))
    warnings = list(health.get("warnings") or [])
    tail = f"health {status}" + (f": {'; '.join(warnings[:3])}" if warnings else "")
    return f"{head} · {health.get('segments', 0)} segments · {coverage_text} · {tail}"


def transcript_health(segments: list[dict], duration: float | None, *, source: object = None,
                      source_detail: object = None, language: object = None,
                      video_language: object = None) -> dict:
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
    sorted_rows = sorted(rows)
    span = (sorted_rows[-1][1] - sorted_rows[0][0]) if sorted_rows else 0.0
    blank = sum(1 for _, _, text in rows if not text.strip())
    empty_ratio = round(blank / len(rows), 3) if rows else 0.0
    reference_span = duration if duration > 0 else span
    per_minute = round(len(rows) / (reference_span / 60.0), 2) if reference_span >= 60.0 else None
    provenance = health_provenance(source, source_detail, language, video_language)
    warnings: list[str] = []
    flags: list[str] = []

    def flag(code: str, message: str | None) -> None:
        flags.append(code)
        if message:
            warnings.append(message)

    if coverage is not None and coverage < HEALTH_MIN_COVERAGE:
        flag("low_coverage", f"captions cover {coverage:.0%} of the {duration:.0f} s video")
    if largest_gap > HEALTH_MAX_GAP_S:
        flag("large_gap", f"largest uncaptioned gap is {largest_gap:.0f} s")
    if repetition > HEALTH_MAX_REPETITION:
        flag("repetition", f"{repetition:.0%} of segments repeat a recent segment")
    if wpm is not None and rows and not (HEALTH_WPM_RANGE[0] <= wpm <= HEALTH_WPM_RANGE[1]):
        flag("wpm", f"{wpm:.0f} words per minute is outside the plausible range")
    if rows and empty_ratio > HEALTH_MAX_EMPTY_TEXT:
        flag("empty_text", f"{empty_ratio:.0%} of segments have no text")
    if per_minute is not None and per_minute < HEALTH_MIN_SEGMENTS_PER_MIN:
        flag("sparse_segments", f"{per_minute:.1f} segments per minute: citations will be coarser than 30 s")
    if rows and duration <= 0:
        flag("no_duration", "video duration unknown; coverage cannot be computed")
    if provenance["translated"]:
        flag("translated", "the caption track is a machine translation (forced with --langs); "
                           "the summary is grounded in translated text")
    if provenance["language_match"] is False:
        flag("language_mismatch", (f"the {provenance['language']} track is not in the video's language "
                                   f"({provenance['video_language']})") if provenance.get("manual") is not True else None)
    if non_positive:
        flag("non_positive", f"{non_positive} segment(s) have end <= start")
    if beyond:
        flag("beyond_duration", f"{beyond} segment(s) start after the video ends")
    if not monotonic:
        flag("not_monotonic", "segments are not in chronological order")
    return {
        "segments": len(rows), "words": words, "covered_seconds": round(covered, 3),
        "coverage_ratio": coverage, "largest_gap_s": round(largest_gap, 3), "gaps_over_30s": gaps_over_30,
        "monotonic": monotonic, "non_positive": non_positive, "beyond_duration": beyond,
        "repetition_ratio": round(repetition, 3), "wpm": round(wpm, 1) if wpm is not None else None,
        "empty_text_ratio": empty_ratio, "segments_per_minute": per_minute, "span_seconds": round(span, 3),
        "provenance": provenance, "flags": flags, "status": health_status(flags),
        "warnings": warnings,
    }


def partial_fingerprint(payload: dict) -> str:
    """Acceptance applies only to these exact words, source, settings and gaps."""
    return canonical_sha256({key: payload.get(key) for key in
                             ("source_identity", "inputs", "segments", "failed_chunks")})


def partial_accepted(payload: dict) -> bool:
    acceptance = payload.get("partial_acceptance")
    return bool(isinstance(acceptance, dict) and acceptance.get("by") == "user"
                and str(acceptance.get("reason") or "").strip()
                and acceptance.get("fingerprint") == partial_fingerprint(payload))


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
    if status == "partial" and partial_accepted(payload):
        result.warnings.append("PARTIAL transcript: the user accepted the recorded missing ranges")
    elif status is not None and status != "ok":
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
    # Always recompute, then let a stored (1.8) record win: a 1.7 file gains
    # status/flags/provenance without losing its own notes (whisper chunks, re-sorts).
    fresh = transcript_health(segments, video.get("duration"), source=payload.get("source"),
                              source_detail=payload.get("source_detail"), language=payload.get("language"),
                              video_language=video.get("language"))
    stored = payload.get("health") if isinstance(payload.get("health"), dict) else None
    if stored:
        health = {**fresh, **stored}
        stored_warnings = list(stored.get("warnings") or [])
        health["warnings"] = stored_warnings + [w for w in fresh["warnings"] if w not in stored_warnings]
        stored_flags = list(stored.get("flags") or [])
        health["flags"] = stored_flags + [f for f in fresh["flags"] if f not in stored_flags]
        health["status"] = health_status(health["flags"]) if "status" not in stored else stored["status"]
        health.setdefault("provenance", fresh["provenance"])
    else:
        health = fresh
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


# ----------------------------------------------------------------------------- visual probe


def _probe_record(verdict: str, reason: str, **extra) -> dict:
    record = {"verdict": verdict, "reason": reason, "scanned_seconds": 0.0, "fps": None,
              "modes": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}, "backdrop": None, "distinct_still_pictures": 0,
              "non_talk_seconds": 0.0, "non_talk_ratio": 0.0, "threshold_s": None, "non_talk_spans": [],
              "method": "settled seconds (<=0.4 % of masked signature pixels changing between samples) of "
                        "distinct still pictures outside the dominant still picture"}
    record.update(extra)
    return record


def probe_verdict(states: list[dict], scanned_seconds: object, *, fps: object, chapters: list[dict] | None = None) -> dict:
    """Pure verdict over a whole-video state scan (states.scan_video with no chapters).

    `contradicts` when the still pictures beyond the backdrop add up to at least
    min(PROBE_MIN_STILL_S, PROBE_MIN_STILL_RATIO × scanned) seconds; `supports`
    otherwise; `unavailable` when nothing was scanned. The mode timeline is
    reported for audit but does not decide (a seated interview reads as
    "dynamic UI" to the mode classifier; stillness is what separates it)."""
    scanned = _num(scanned_seconds) or 0.0
    rate = _num(fps) or 1.0
    rows = [s for s in states or [] if isinstance(s, dict)]
    if scanned <= 0 or not rows:
        return _probe_record("unavailable", "nothing was scanned", fps=rate)
    modes = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    for state in rows:
        start, end = _num(state.get("start")), _num(state.get("end"))
        if start is not None and end is not None and state.get("mode") in modes:
            modes[state["mode"]] += max(0.0, end - start)
    still = [s for s in rows if int(_num(s.get("settled_samples")) or 0) >= PROBE_MIN_SETTLED_SAMPLES]
    families: dict[str, dict] = {}
    for state in still:
        key = str(state.get("family_id") or state.get("state_id"))
        entry = families.setdefault(key, {"settled_s": 0.0, "build": False, "mode": state.get("mode")})
        entry["settled_s"] += int(_num(state.get("settled_samples")) or 0) / rate
        entry["build"] = entry["build"] or bool((state.get("build") or {}).get("is_build"))
    non_build = [key for key, entry in families.items() if not entry["build"]]
    backdrop_key = max(non_build, key=lambda key: families[key]["settled_s"]) if non_build else None
    backdrop = None
    if backdrop_key is not None:
        backdrop = {"family": backdrop_key, "settled_s": round(families[backdrop_key]["settled_s"], 1),
                    "mode": families[backdrop_key]["mode"]}
    non_talk = sum(entry["settled_s"] for key, entry in families.items() if key != backdrop_key)
    threshold = min(PROBE_MIN_STILL_S, PROBE_MIN_STILL_RATIO * scanned)

    def chapter_ids(start: float, end: float) -> list[str]:
        ids = []
        for chapter in chapters or []:
            if not isinstance(chapter, dict):
                continue
            c0, c1 = _num(chapter.get("start")), _num(chapter.get("end"))
            if c0 is not None and c1 is not None and c0 < end and c1 > start:
                ids.append(str(chapter.get("chapter_id")))
        return ids

    beyond = [s for s in still if str(s.get("family_id") or s.get("state_id")) != backdrop_key]
    beyond.sort(key=lambda s: -(int(_num(s.get("settled_samples")) or 0)))
    spans = []
    for state in beyond[:PROBE_MAX_SPANS]:
        start, end = float(_num(state.get("start")) or 0.0), float(_num(state.get("end")) or 0.0)
        spans.append({"start": start, "end": end, "settled_s": round(int(_num(state.get("settled_samples")) or 0) / rate, 1),
                      "mode": state.get("mode"), "mode_label": state.get("mode_label"),
                      "chapter_ids": chapter_ids(start, end)})
    verdict = "contradicts" if non_talk >= threshold else "supports"
    reason = (f"{non_talk:.0f} s of still on-screen content beyond the main picture "
              f"(threshold {threshold:.0f} s of {scanned:.0f} s scanned)")
    return _probe_record(verdict, reason, scanned_seconds=round(scanned, 1), fps=rate,
                         modes={key: round(value, 1) for key, value in modes.items()}, backdrop=backdrop,
                         distinct_still_pictures=len(families), non_talk_seconds=round(non_talk, 1),
                         non_talk_ratio=round(non_talk / scanned, 3) if scanned else 0.0,
                         threshold_s=round(threshold, 1), non_talk_spans=spans)


def probe_spans_text(probe: dict) -> str:
    return ", ".join(
        f"{row.get('mode_label') or row.get('mode')} at {_mmss(row.get('start'))}–{_mmss(row.get('end'))}"
        + (f" ({', '.join(row.get('chapter_ids') or [])})" if row.get("chapter_ids") else "")
        for row in (probe.get("non_talk_spans") or [])[:3] if isinstance(row, dict))


def probe_refusal(probe: dict) -> str:
    """The exit-10 message when a model's no-visuals decision is contradicted."""
    spans = probe_spans_text(probe)
    return (f"the visual probe contradicts the no-visuals decision: {spans or probe.get('reason')} — "
            f"{probe.get('reason')}. Mark those chapters needs_frames: true with a target inside the span and run "
            "again, or, only if the user confirms the video has no informative visuals, record the decision with "
            "`workflow.py decide no-visuals --by user --reason \"...\"`")


def probe_summary(probe: object) -> str:
    if not isinstance(probe, dict):
        return "no visual probe recorded"
    return f"{probe.get('verdict')} — {probe.get('reason')}"


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
        # The decision was probed against the video (1.8): a model decision the
        # probe contradicts is refused; a user decision is recorded with a warning.
        probe = payload.get("visual_probe") if isinstance(payload.get("visual_probe"), dict) else None
        by = inputs.get("visual_decided_by") or "model"
        result.info["visual_probe"] = probe
        if probe is None:
            if by != "init":
                result.warnings.append("no visual probe recorded for the no-visuals decision "
                                       "(pre-1.8 manifest); the decision stands unverified")
        elif probe.get("verdict") == "unavailable":
            result.warnings.append(f"visual probe unavailable ({probe.get('reason')}); "
                                   "the no-visuals decision stands unverified")
        elif probe.get("verdict") == "contradicts":
            if by == "user":
                result.warnings.append(f"visual probe contradicts the decision (user override): {probe.get('reason')}")
            else:
                result.errors.append(probe_refusal(probe))
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
