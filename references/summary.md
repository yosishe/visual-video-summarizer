# Writing `summary.json` (Step 6) and the audit

Write `<work>/summary.json` — the prose, with provenance. Every block cites the segments it synthesizes; a frame is inserted after the first block whose `seg_ids` overlap its `anchor_seg_ids`. Author it **chapters first, then overview, chapter key points, and the opening brief** (synthesize the completed explanation, not a first impression):

```json
{"schema_version": 3, "lang": "he", "source_language": "en",
 "overview": "הטענה של הסרטון במשפט אחד או שניים.",
 "brief": {
   "synthesis": {"text": "הטענה המרכזית, הסיבה לחשיבותה וההסתייגות הנחוצה להבנתה.", "seg_ids": ["seg_0084", "seg_0085"]},
   "main_points": [{"text": "הרעיון החשוב וההסבר התומך בו.", "seg_ids": ["seg_0084"]}],
   "takeaways": [{"text": "המסקנה הנתמכת בתמליל והתנאים שבהם היא חלה.", "seg_ids": ["seg_0085"]}]},
 "glossary": {"agent": "סוכן", "skill": "skill", "workflow": "זרימת עבודה"},
 "chapters": [{"chapter_id": "ch03", "title": "זרימת הייצוא",
   "blocks": [
     {"block_id": "ch03_b01", "kind": "prose", "text": "סינתזה מפורטת של מה שנאמר…", "seg_ids": ["seg_0084", "seg_0085"]},
     {"block_id": "ch03_b02", "kind": "code", "lang": "bash", "text": "npm run build", "seg_ids": ["seg_0086"]},
     {"block_id": "ch03_b03", "kind": "quote", "text": "Prompts are so late 2025.", "seg_ids": ["seg_0087"]}],
   "key_points": ["עובדה או מסקנה שאפשר לצטט"]}]}
```

`kind` is `prose` (default), `code` (rendered LTR in a `<pre>`), or `quote` (a verbatim line of the speaker, in the source language). Inside prose, `backticks` are the **only** markup: identifiers, commands, file names, UI strings, numbers with units and formulas go in backticks and the renderer isolates them left-to-right — never emit HTML, never bidi control characters. Write `lang` to match the requested language so the renderer and the audit agree with you.

## Opening brief (both output languages)

Include `brief` in newly authored summaries. It appears before the detailed chapters in the same HTML/PDF, in the selected document language. Older summaries without it remain valid. The JSON example above illustrates the shape, not a length requirement or text to reuse.

- **`synthesis`:** one short paragraph explaining the whole video's central argument and why it matters. Connect ideas across chapters; do not concatenate chapter summaries or merely list topics.
- **`main_points`:** usually 3–5 essential ideas, with the mechanism, reason, example, or limitation needed to understand each. Select by importance, not novelty alone.
- **`takeaways`:** usually 2–3 distinct conclusions to remember or apply, including their conditions. Conceptual lessons are valid; do not manufacture action items. Attribute speaker recommendations, and label a supported inference as a synthesis rather than something the speaker explicitly said.

Aim for **150–250 words total across all three parts**, excluding headings and timestamps. These are soft targets, not quotas: use fewer words or bullets when the source warrants it. Arrays may be empty; empty lists have no rendered heading. Do not shorten the detailed chapters to meet this brief's budget, or replace the existing overview and chapter key points.

Draft from the completed chapters, then verify against the original transcript, including its ending. Every item needs non-empty `text` and unique, existing `seg_ids` in transcript order. An item may cite distant chapters; items themselves can follow importance order. Cite the actual support for each claim, not an entire chapter as a substitute for checking it. The renderer derives compact source links from these IDs; never invent timestamps.

Before auditing, check that the brief answers: **What is the central claim? Why does it hold? What should the reader remember, and under what conditions?** Preserve qualifications, negations, quantities, trade-offs, and late corrections. Remove repeated ideas between main points and takeaways; add no external facts or unsupported advice.

## The audit (structural grounding, not semantic truth)

`workflow.py run` executes `audit_summary.py` and stops with **exit 5** while errors remain (`<work>/audit.json`; `<work>/reports/audit.md`). Fix every `error`: a number, identifier or URL the cited segments do not contain; an unknown or missing segment reference; wrong segment order; a block outside its chapter (±5 s); niqqud; bidi controls; a non-Hebrew block in a Hebrew document; an empty transcript. `review` lines are judgement calls (a name that is in the transcript but not in this block's segments; a negation the block dropped; more than 60 s of transcript cited by no block; fewer than 15 % of segments cited at all) — decide each one. The deterministic audit checks references, numerical grounding and language hygiene; it cannot establish the truth of an ordinary-language paraphrase, a dropped reasoning step, or an invented claim made of ordinary words. That judgement is yours: re-read the transcript's ending before finishing, and keep corrections the speaker made late in the video.

Standalone: `python "<SKILL_DIR>/scripts/audit_summary.py" --work "<work>" --summary "<work>/summary.json" --selections "<work>/selections.json"`.

## כללי הסיכום בעברית (חלים כאשר `lang` הוא `he`)

**מבנה:** `overview` = משפט אחד או שניים שאומרים מה הסרטון **טוען**, לא על מה הוא "מדבר"; פותחים בעברית. כותרת פרק עד 8 מילים, שם או טענה, לא "הקדמה". בלוק = פסקה של 60 עד 140 מילים שמסכמת רעיון אחד; 2 עד 5 בלוקים לפרק; בלוק שמצטט יותר מ-25 מקטעים דחוס מדי. `key_points` = 2 עד 4 לפרק, כל אחת עובדה או מסקנה שאפשר לצטט, לא כותרת מחדש.

**חובה לשמור:** כל מספר, יחידה, אחוז, טווח וזמן שהדובר אומר, בספרות ("7 עד 10 skills", "כ-300 אלף", "כל 30 דקות"); שמות כלים, מוצרים, חברות, ממשקי API, פקודות, קבצים וכתובות בלטינית כפי שהם — ב-`backticks` כשהם מזהה טכני, בלי backticks כשהם שם מוצר בשטף המשפט; הדוגמאות שהדובר משתמש בהן כדי להוכיח טענה ושרשראות הנימוק ("כי", "ולכן", "אלא אם") — סיכום שמביא מסקנה בלי הסיבה שלה נכשל; הסתייגויות, שלילה ואי-ודאות במשמעותן המדויקת; הגדרות במילות הדובר (ציטוט קצר ב-`kind: "quote"` כשהניסוח עצמו חשוב).

**משמיטים:** ברכות, קריאה למנוי, ספונסרים, "כמו שאתם רואים", חזרות, דיבור על הסרטון עצמו, מילות מילוי; תיאור של מה שקורה על המסך כשיש לזה תמונה בסיכום — הכיתוב עושה את העבודה.

**סגנון:** עברית תקנית בלי ניקוד. משפט עד 25 מילים, רעיון אחד למשפט. בלי מקפים ארוכים — פסיק, נקודתיים או משפט חדש; טווחי מספרים "7 עד 10". מונח שיש לו עברית מקובלת בתעשייה נכתב בעברית (סוכן, שרת, זרימת עבודה, תמליל, פריסה); מונח שהעברית שלו אינה מקובלת נשאר באנגלית (skill, prompt, endpoint, token). לא מתעתקים שמות מוצרים (Notion, לא "נושן"). מונח שחוזר נכתב באותה צורה בכל הסיכום ונרשם ב-`glossary`. תחיליות לפני מילה לטינית עם מקף: ב-Notion, ה-API, ל-GitHub. בלי פנייה בגוף שני; הדובר מכונה בשמו או "הדובר"; ניסוח בלתי-אישי ("ניתן", "מומלץ", "הסרטון מציע"). כל משפט פותח בעברית, לא במונח לועזי — אם המונח הוא הנושא, מקדימים מילה: "הכלי OpenClaw…". לא מתרגמים את התמליל: מסכמים אותו — פחות מילים מהמקור, יותר מבנה. סרטון שמקורו בעברית מסוכם באותם כללים (בלי תרגום, אבל גם בלי הדבקה).

For `--lang en` write detailed English prose under the same structure: synthesize, don't paste the transcript; quote only the lines that matter.

## Rendering

Never hand-write the HTML. `workflow.py run` calls `render.py`, which runs the audit again, validates chapter ownership, segment provenance, budgets, coverage, duplicates and asset hashes, refuses stale inputs (exit 11), writes `manifest.json` (the source of truth, with the hashes of every input), a designed `index.html`, and — automatically — **`summary-<video-id>.html`: one self-contained file with every image embedded**. Hebrew documents come out right-to-left with a subset of the Heebo typeface embedded, English terms, code, timestamps and ranges isolated left-to-right. `--pdf` prints that file to `summary-<video-id>.pdf` via Google Chrome/Edge headless, or WeasyPrint when Chrome is absent (exit 4 = no engine: deliver the HTML). The directory stays as the editable source — change a caption or a block, run again.
