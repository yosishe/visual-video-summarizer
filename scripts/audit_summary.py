#!/usr/bin/env python3
"""Deterministic grounding audit of summary.json against the transcript.

    python3 audit_summary.py --work <work> --summary <work>/summary.json [--selections ...] [--lang he]

What can be checked without a model is checked hard: every number, every
backtick identifier / URL / path in a block must appear in the segments the
block cites; segment order and chapter ownership; Hebrew hygiene (no niqqud,
no bidi control characters, Hebrew prose that is actually Hebrew). Latin
proper names are checked *softly* (fuzzy against the cited segments, the
video's title/tags, or the glossary) because auto-captions misspell them
("Open Claw" for OpenClaw) — those land in `reviews`, never `errors`.

What this cannot do, stated plainly: detect a wrong paraphrase, a dropped
reasoning step, or a claim invented in ordinary Hebrew words. That is the
benchmark's summary checklist, not this script.

Exit 0 = no errors; 5 = errors present. Output: a Markdown report on stdout
and `<work>/audit.json` = {errors, reviews, warnings, stats}.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

HEBREW_RE = re.compile(r"[א-ת]")
LATIN_RE = re.compile(r"[A-Za-z]")
NIQQUD_RE = re.compile(r"[ְ-ׇ]")
BIDI_CONTROL_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")
DASH_RE = re.compile(r"[–—]")
NUMBER_RE = re.compile(r"(?<![A-Za-z_/.\-])\d[\d,.]*(?:%|k|K|x|×)?")
LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./_\-']*")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
URL_RE = re.compile(r"https?://\S+")
WORD_RE = re.compile(r"[\w'׳״]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    "hundred": "100", "thousand": "1000", "million": "1000000",
    "אפס": "0", "אחד": "1", "אחת": "1", "שניים": "2", "שתיים": "2", "שני": "2", "שתי": "2",
    "שלושה": "3", "שלוש": "3", "ארבעה": "4", "ארבע": "4", "חמישה": "5", "חמש": "5",
    "שישה": "6", "שש": "6", "שבעה": "7", "שבע": "7", "שמונה": "8", "תשעה": "9", "תשע": "9",
    "עשרה": "10", "עשר": "10", "עשרים": "20", "שלושים": "30", "מאה": "100", "אלף": "1000", "מיליון": "1000000",
}
NEGATION_SOURCE_RE = re.compile(r"\b(not|never|no|n't|without|cannot|can't|won't|don't|doesn't|isn't|aren't)\b", re.I)
NEGATION_TARGET_RE = re.compile(r"(?:^|\s)(לא|אין|אף פעם|בלי|אי אפשר|לעולם לא|איננו|אינה|אינם)(?=\s|$|[,.;:])")
MAX_SENTENCE_WORDS = 35
OWNERSHIP_TOLERANCE_S = 5.0
MAX_SEGMENTS_PER_BLOCK = 25
UNCITED_GAP_S = 60.0
WPM_RANGE = (50, 200)


def _normalize_number(token: str) -> str:
    token = token.replace(",", "").rstrip("%kKx×.")
    return token or "0"


def _numbers_in(text: str) -> list[str]:
    found = [_normalize_number(m.group(0)) for m in NUMBER_RE.finditer(text)]
    for word, digits in NUMBER_WORDS.items():
        if re.search(rf"(?<![\wא-ת]){re.escape(word)}(?![\wא-ת])", text, re.I):
            found.append(digits)
    return found


def _tokens(text: str) -> list[str]:
    return [m.group(0).casefold() for m in WORD_RE.finditer(text)]


def _fuzzy_in(term: str, haystack_tokens: list[str], joined: str, threshold: float = 0.8) -> bool:
    needle = re.sub(r"[\s\-_.]", "", term).casefold()
    if not needle:
        return True
    if needle in joined:
        return True
    for width in (1, 2, 3):
        for index in range(len(haystack_tokens) - width + 1):
            candidate = "".join(haystack_tokens[index:index + width])
            if difflib.SequenceMatcher(None, needle, candidate).ratio() >= threshold:
                return True
    return False


class Audit:
    def __init__(self) -> None:
        self.errors: list[dict] = []
        self.reviews: list[dict] = []
        self.warnings: list[dict] = []

    def add(self, level: str, check: str, where: str, message: str) -> None:
        getattr(self, level).append({"check": check, "where": where, "message": message})


def run_audit(
    transcript: dict,
    chapters: list[dict],
    summary: dict,
    *,
    selections: list[dict] | None = None,
    candidates: dict | None = None,
    info: dict | None = None,
    lang: str | None = None,
) -> dict:
    audit = Audit()
    lang = (lang or summary.get("lang") or "en").lower()
    segments = {str(s["seg_id"]): s for s in transcript.get("segments", [])}
    order = {seg_id: index for index, seg_id in enumerate(segments)}
    chapter_map = {c["chapter_id"]: c for c in chapters}
    glossary = summary.get("glossary") or {}
    authority_text = " ".join(
        str(v) for v in ((info or {}).get("title"), (info or {}).get("description"),
                         " ".join((info or {}).get("tags") or []), (info or {}).get("uploader"),
                         transcript.get("video", {}).get("title"), transcript.get("video", {}).get("uploader"))
        if v
    )
    authority_tokens = _tokens(authority_text) + [str(k) for k in glossary] + [str(v) for v in glossary.values()]
    authority_joined = "".join(authority_tokens).casefold()
    all_transcript_tokens = _tokens(" ".join(s["text"] for s in segments.values()))
    all_transcript_joined = "".join(all_transcript_tokens)
    cited: set[str] = set()
    total_words = 0
    all_text_parts: list[str] = [str(summary.get("overview") or "")]

    for chapter in summary.get("chapters", []):
        chapter_id = str(chapter.get("chapter_id"))
        chapter_window = chapter_map.get(chapter_id)
        previous_last = -1
        for index, block in enumerate(chapter.get("blocks", [])):
            where = f"{chapter_id}/{block.get('block_id') or index + 1}"
            text = str(block.get("text") or "")
            kind = str(block.get("kind") or "prose")
            all_text_parts.append(text)
            seg_ids = [str(s) for s in block.get("seg_ids", [])]
            cited_rows = [segments[s] for s in seg_ids if s in segments]
            cited.update(s for s in seg_ids if s in segments)
            cited_text = " ".join(r["text"] for r in cited_rows)
            cited_tokens = _tokens(cited_text)
            cited_joined = "".join(cited_tokens)
            total_words += len(text.split())

            # 6. ordering and ownership
            positions = [order[s] for s in seg_ids if s in order]
            if positions != sorted(positions):
                audit.add("errors", "order", where, "seg_ids are not in transcript order")
            if positions and positions[0] < previous_last:
                audit.add("errors", "order", where, "block cites segments earlier than the previous block")
            if positions:
                previous_last = positions[-1]
            if chapter_window:
                # A segment may straddle a chapter boundary, and a result
                # chapter legitimately cites the action sentence spoken just
                # before it (chapters are cut on topic, captions on breath).
                # Foreign = more than OWNERSHIP_TOLERANCE_S outside the window.
                for row in cited_rows:
                    if float(row["end"]) < float(chapter_window["start"]) - OWNERSHIP_TOLERANCE_S or \
                            float(row["start"]) > float(chapter_window["end"]) + OWNERSHIP_TOLERANCE_S:
                        audit.add("errors", "ownership", where,
                                  f"{row['seg_id']} lies outside chapter {chapter_id}")
                        break
            if len(seg_ids) > MAX_SEGMENTS_PER_BLOCK:
                audit.add("warnings", "density", where, f"block cites {len(seg_ids)} segments — under-detailed?")

            if kind == "code":
                continue

            # 1. numbers ("one"/"אחד" are articles as often as counts — not checked)
            cited_numbers = set(_numbers_in(cited_text))
            for number in set(_numbers_in(text)) - {"0", "1"}:
                if number not in cited_numbers:
                    audit.add("errors", "number", where,
                              f"number {number} is not in the cited segments")

            # 3. backtick identifiers, URLs, paths
            for term in BACKTICK_RE.findall(text) + URL_RE.findall(text):
                needle = re.sub(r"[\s\-_.]", "", term).casefold()
                if needle and needle not in cited_joined and needle not in authority_joined and \
                        needle not in all_transcript_joined:
                    audit.add("errors", "identifier", where, f"`{term}` appears nowhere in the transcript or metadata")

            # 2. Latin runs in Hebrew prose: fuzzy against cited, then authority, then the whole transcript
            if lang == "he":
                inner_backticks = set(BACKTICK_RE.findall(text))
                stripped = BACKTICK_RE.sub(" ", text)
                stripped = URL_RE.sub(" ", stripped)
                for run in set(LATIN_RUN_RE.findall(stripped)):
                    if len(run) < 3 or run in inner_backticks:
                        continue
                    if _fuzzy_in(run, cited_tokens, cited_joined):
                        continue
                    if _fuzzy_in(run, authority_tokens, authority_joined):
                        continue
                    if _fuzzy_in(run, all_transcript_tokens, all_transcript_joined):
                        audit.add("reviews", "entity", where,
                                  f"'{run}' is in the transcript but not in this block's cited segments")
                    else:
                        audit.add("errors", "entity", where, f"'{run}' appears nowhere in the transcript or metadata")

            # 4. negation parity
            if NEGATION_SOURCE_RE.search(cited_text) and not (
                NEGATION_TARGET_RE.search(text) or NEGATION_SOURCE_RE.search(text)
            ):
                audit.add("reviews", "negation", where, "cited segments negate something; the block does not")

            # 7. Hebrew hygiene
            if lang == "he":
                hebrew = len(HEBREW_RE.findall(text))
                latin = len(LATIN_RE.findall(BACKTICK_RE.sub("", text)))
                if kind == "prose" and not hebrew:
                    audit.add("errors", "hebrew", where, "block has no Hebrew letters")
                elif kind == "prose" and hebrew < 0.6 * (hebrew + latin):
                    audit.add("warnings", "hebrew", where, "less than 60 % Hebrew letters")
                if NIQQUD_RE.search(text):
                    audit.add("errors", "niqqud", where, "niqqud marks present")
                if BIDI_CONTROL_RE.search(text):
                    audit.add("errors", "bidi", where, "bidi control characters present — use backticks instead")
                if DASH_RE.search(text):
                    audit.add("warnings", "dash", where, "em/en dash — prefer a comma, colon or a new sentence")
                first = text.strip()[:1]
                if kind == "prose" and first and LATIN_RE.match(first):
                    audit.add("warnings", "leading-latin", where, "block opens with a Latin word")
            for sentence in SENTENCE_SPLIT_RE.split(text):
                if len(sentence.split()) > MAX_SENTENCE_WORDS:
                    audit.add("warnings", "sentence", where, f"sentence over {MAX_SENTENCE_WORDS} words")

        for point in chapter.get("key_points", []) or []:
            all_text_parts.append(str(point))
            if lang == "he" and str(point).strip()[:1] and LATIN_RE.match(str(point).strip()[:1]):
                audit.add("warnings", "leading-latin", f"{chapter_id}/key_points", "key point opens with a Latin word")

    # 5. coverage
    uncited_runs: list[tuple[float, float]] = []
    run_start = None
    for seg_id, row in segments.items():
        chapter = next((c for c in chapters if float(c["start"]) <= float(row["start"]) < float(c["end"])), None)
        if seg_id in cited or chapter is None:
            if run_start is not None:
                uncited_runs.append((run_start, float(row["start"])))
                run_start = None
            continue
        if run_start is None:
            run_start = float(row["start"])
    if run_start is not None and segments:
        uncited_runs.append((run_start, float(list(segments.values())[-1]["end"])))
    for start, end in uncited_runs:
        if end - start > UNCITED_GAP_S:
            audit.add("reviews", "coverage", f"{start:.0f}-{end:.0f}s",
                      f"{end - start:.0f} s of transcript are cited by no block")

    # 8. length budget
    duration_min = float(transcript.get("video", {}).get("duration") or 0) / 60
    if duration_min:
        wpm = total_words / duration_min
        if not (WPM_RANGE[0] <= wpm <= WPM_RANGE[1]):
            audit.add("warnings", "length", "summary", f"{wpm:.0f} words per video minute (expected {WPM_RANGE[0]}–{WPM_RANGE[1]})")

    # 9. glossary consistency: the declared form is the only form used
    all_text = "\n".join(all_text_parts)
    for term, form in glossary.items():
        if str(term) != str(form) and re.search(rf"(?<![\wא-ת]){re.escape(str(term))}(?![\wא-ת])", all_text):
            if HEBREW_RE.search(str(form)):
                audit.add("warnings", "glossary", "summary", f"'{term}' used although the glossary form is '{form}'")

    # 10. captions
    if selections and candidates:
        by_id = {c.get("candidate_id"): c for c in candidates.get("candidates", [])}
        for selection in selections:
            candidate = by_id.get(selection.get("candidate_id"))
            caption = selection.get("caption")
            parts = [caption] if isinstance(caption, str) else [
                v for v in (caption or {}).values() if isinstance(v, str)
            ]
            text = " ".join(parts)
            if candidate is None or not text:
                continue
            seg_text = " ".join(segments[s]["text"] for s in candidate.get("seg_ids", []) if s in segments)
            ocr_text = str(candidate.get("quality", {}).get("ocr_text") or "")
            haystack_tokens = _tokens(seg_text + " " + ocr_text)
            haystack_joined = "".join(haystack_tokens)
            for run in set(LATIN_RUN_RE.findall(BACKTICK_RE.sub(" ", text))) | set(BACKTICK_RE.findall(text)):
                if len(run) < 3:
                    continue
                if not (_fuzzy_in(run, haystack_tokens, haystack_joined) or _fuzzy_in(run, authority_tokens, authority_joined)
                        or _fuzzy_in(run, all_transcript_tokens, all_transcript_joined)):
                    audit.add("reviews", "caption", str(selection.get("candidate_id")),
                              f"'{run}' is not in the frame's segments, OCR text or metadata")
            if lang == "he" and not HEBREW_RE.search(text):
                audit.add("errors", "caption", str(selection.get("candidate_id")), "caption is not Hebrew")

    stats = {
        "lang": lang,
        "words": total_words,
        "cited_segments": len(cited),
        "segments": len(segments),
        "coverage": round(len(cited) / len(segments), 3) if segments else None,
        "hebrew_ratio": (
            round(len(HEBREW_RE.findall(all_text)) / max(1, len(HEBREW_RE.findall(all_text)) + len(LATIN_RE.findall(all_text))), 3)
        ),
    }
    return {"errors": audit.errors, "reviews": audit.reviews, "warnings": audit.warnings, "stats": stats}


def render_report(result: dict) -> str:
    lines = ["# summary audit", ""]
    stats = result["stats"]
    lines.append(f"- lang {stats['lang']} · {stats['words']} words · cited {stats['cited_segments']}/{stats['segments']} segments "
                 f"({(stats['coverage'] or 0) * 100:.0f} %) · Hebrew ratio {stats['hebrew_ratio']:.0%}")
    lines.append(f"- **{len(result['errors'])} errors**, {len(result['reviews'])} reviews, {len(result['warnings'])} warnings")
    for level in ("errors", "reviews", "warnings"):
        if result[level]:
            lines.append("")
            lines.append(f"## {level}")
            for row in result[level]:
                lines.append(f"- `{row['check']}` {row['where']}: {row['message']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--selections", default=None)
    parser.add_argument("--lang", default=None)
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve()
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    chapters = json.loads((work / "chapters.json").read_text(encoding="utf-8"))
    summary = json.loads(Path(args.summary).expanduser().read_text(encoding="utf-8"))
    selections = json.loads(Path(args.selections).expanduser().read_text(encoding="utf-8")) if args.selections else None
    candidates = None
    if (work / "candidates.json").exists():
        candidates = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
    info = None
    info_path = work / "download" / "video.info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {k: raw.get(k) for k in ("title", "description", "tags", "uploader")}
        except (OSError, json.JSONDecodeError):
            info = None
    result = run_audit(transcript, chapters, summary, selections=selections, candidates=candidates,
                       info=info, lang=args.lang)
    (work / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_report(result))
    return 5 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
