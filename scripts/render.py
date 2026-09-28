"""notes.md → one self-contained HTML page; a PDF through Chrome, Chromium or Edge when asked."""
import base64
import datetime
import html
import re
import shutil
import subprocess
from pathlib import Path

from common import fmt_time, parse_time

FONT = Path(__file__).resolve().parent / "fonts" / "FrankRuhlLibre-hebrew.woff2"
FIGURE = re.compile(r"^!\[F(\d+)\]\((.*)\)$")
TIME = re.compile(r"\[(\d{1,2}(?::\d{2}){1,2})\]")
LEAD_TIME = re.compile(r"^\[(\d{1,2}(?::\d{2}){1,2})\]\s*")
CODE = re.compile(r"(`[^`\n]+`)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
# A quoted run with Latin letters and no Hebrew ("Hello! How are you?"): isolated, or the bidi
# algorithm moves its closing punctuation and quote marks to the wrong side in a Hebrew line.
QUOTED_LATIN = re.compile(r'"(?=[^"\u0590-\u05FF]*[A-Za-z])[^"\u0590-\u05FF]*"')
URL = re.compile(r"https?://[^\s<>\"'\]]+")
HEBREW = re.compile(r"[֐-׿]")
LATIN = re.compile(r"[A-Za-z]")
NIQQUD = re.compile(r"[֑-ׇֽֿׁׂׅׄ]")
BIDI = re.compile(r"[‎‏‪-‮⁦-⁩]")
LISTS = {"ul": re.compile(r"^[-*]\s+"), "ol": re.compile(r"^\d+[.)]\s+"), "quote": re.compile(r"^>\s?")}
BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge"]

STRINGS = {
    "he": {"toc": "תוכן העניינים", "watch": "לצפייה בסרטון", "by": "מאת", "length": "אורך",
           "transcript": "מקור התמליל", "manual": "כתוביות ידניות", "auto": "כתוביות אוטומטיות",
           "whisper": "תמלול מקומי", "file": "קובץ כתוביות",
           "footer": "הופק בתאריך {date} באמצעות הכלי <bdi>summarize-video</bdi>, מתוך התמליל והפריימים של הסרטון."},
    "en": {"toc": "Contents", "watch": "Watch the video", "by": "by", "length": "Length",
           "transcript": "Transcript", "manual": "manual captions", "auto": "automatic captions",
           "whisper": "local transcription", "file": "subtitle file",
           "footer": "Written from the video's transcript and frames with summarize-video on {date}."},
}

# A printed course reader: ink on paper, one text column, figures wider than the text,
# one accent (the video's red) reserved for everything that jumps back to the source.
CSS = """
:root{--ink:#1c1d1f;--muted:#5d6068;--paper:#fbfbf9;--rule:#e4e3de;--wash:#f0efeb;--accent:#b3302a;
--measure:41rem;--wide:60rem}
*{box-sizing:border-box}
html{background:var(--paper);color:var(--ink);-webkit-text-size-adjust:100%}
body{margin:0;font:1.125rem/1.9 "Frank Ruhl Libre","Iowan Old Style","Palatino Linotype",Georgia,serif;
font-kerning:normal;text-rendering:optimizeLegibility}
.masthead,main,footer{max-inline-size:var(--measure);margin-inline:auto;padding-inline:1.25rem}
.masthead{padding-block:4.5rem 2rem;border-block-end:1px solid var(--rule)}
h1{font-size:clamp(2rem,5vw,2.75rem);line-height:1.2;font-weight:800;margin:0 0 1.25rem}
.meta{margin:0;color:var(--muted);font-size:.95rem;line-height:1.8}
.meta a{color:var(--accent)}
main{padding-block:1rem 2rem}
.brief{margin:2.5rem 0 0;padding-inline-start:1.25rem;border-inline-start:3px solid var(--accent);
font-size:1.25rem;line-height:1.85;color:var(--ink)}
.brief p{margin:0 0 .8rem}
.toc{margin-block:3rem 1rem;font-size:1rem;line-height:1.6}
.toc-title{margin:0 0 .6rem;font-weight:700;color:var(--muted)}
.toc ol{margin:0;padding-inline-start:1.4rem;columns:2 17rem;column-gap:2.5rem}
.toc li{break-inside:avoid;margin-block-end:.35rem}
.toc a{color:inherit;text-decoration:none}
.toc a:hover{text-decoration:underline}
.toc .t{color:var(--muted);font:.78rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h2{font-size:1.8rem;line-height:1.35;font-weight:800;margin:4.5rem 0 1rem}
h3{font-size:1.3rem;line-height:1.4;font-weight:700;margin:2.5rem 0 .6rem}
p{margin:0 0 1.15rem}
ul,ol{margin:0 0 1.25rem;padding-inline-start:1.4rem}
li{margin-block:.4rem}
blockquote{margin:1.6rem 0;padding-inline-start:1.1rem;border-inline-start:2px solid var(--rule);color:#35383d}
code{font:.84em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--wash);padding:.1em .35em;
border-radius:3px;unicode-bidi:isolate}
pre{direction:ltr;text-align:start;background:var(--wash);padding:1rem 1.2rem;border-radius:4px;overflow-x:auto;
font:.88rem/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre code{background:none;padding:0;font-size:inherit}
.ts{font:600 .76rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--accent);text-decoration:none;
border:1px solid currentColor;border-radius:3px;padding:.2em .38em;vertical-align:.15em;unicode-bidi:isolate;
white-space:nowrap}
a.ts:hover{background:var(--accent);color:var(--paper)}
h2 .ts{vertical-align:.35em}
figure{margin-block:2.4rem;margin-inline:calc(50% - min(var(--wide),100vw - 2.5rem) / 2)}
figure img{display:block;inline-size:100%;block-size:auto;border-radius:4px;background:var(--wash);cursor:zoom-in}
figcaption{max-inline-size:var(--measure);margin:.75rem auto 0;padding-inline:1.25rem;color:var(--muted);
font-size:.95rem;line-height:1.75}
footer{padding-block:2.5rem 4rem;color:var(--muted);font-size:.9rem;border-block-start:1px solid var(--rule)}
#zoom{position:fixed;inset:0;display:grid;place-items:center;padding:2vmin;background:rgb(18 19 21/.92);
cursor:zoom-out;z-index:9}
#zoom[hidden]{display:none}
#zoom img{max-inline-size:100%;max-block-size:100%;object-fit:contain}
@page{size:A4;margin:16mm 15mm}
@media print{html{background:#fff}body{font-size:11pt;line-height:1.75}.masthead{padding-block:0 1rem}
h2{break-after:avoid;margin-top:2.2rem}figure{break-inside:avoid;margin-inline:0}figcaption{padding-inline:0}
a{color:inherit}#zoom{display:none}}
"""

SCRIPT = """const z=document.getElementById('zoom');
document.addEventListener('click',e=>{if(e.target.matches('figure img')){z.firstElementChild.src=e.target.src;z.hidden=false}
else if(!z.hidden){z.hidden=true}});
document.addEventListener('keydown',e=>{if(e.key==='Escape')z.hidden=true});"""


def _block_start(line: str) -> bool:
    return line.startswith(("#", "```")) or bool(FIGURE.match(line)) or any(p.match(line) for p in LISTS.values())


def parse(text: str) -> list[dict]:
    """Blocks of the Markdown subset, each with the line it starts on."""
    lines = text.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        line, number = lines[i].strip(), i + 1
        if not line:
            i += 1
        elif line.startswith("```"):
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append({"kind": "code", "text": "\n".join(body), "line": number})
            i += 1
        elif FIGURE.match(line):
            num, caption = FIGURE.match(line).groups()
            blocks.append({"kind": "figure", "id": f"F{int(num):02d}", "text": caption.strip(), "line": number})
            i += 1
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            blocks.append({"kind": {1: "title", 2: "h2"}.get(level, "h3"), "text": line[level:].strip(), "line": number})
            i += 1
        else:
            kind = next((k for k, p in LISTS.items() if p.match(line)), None)
            if kind:
                items = []
                while i < len(lines) and LISTS[kind].match(lines[i].strip()):
                    items.append(LISTS[kind].sub("", lines[i].strip(), count=1))
                    i += 1
                    while (i < len(lines) and lines[i][:1] in (" ", "\t") and lines[i].strip()
                           and not _block_start(lines[i].strip())):
                        items[-1] += " " + lines[i].strip()
                        i += 1
                blocks.append({"kind": kind, "items": items, "line": number})
            else:
                body, i = [line], i + 1
                while i < len(lines) and lines[i].strip() and not _block_start(lines[i].strip()):
                    body.append(lines[i].strip())
                    i += 1
                blocks.append({"kind": "p", "text": " ".join(body), "line": number})
    return blocks


def stamp(label: str, link) -> str:
    target = link(parse_time(label)) if link else None
    if target:
        return f'<a class="ts" href="{html.escape(target)}" dir="ltr">{label}</a>'
    return f'<span class="ts" dir="ltr">{label}</span>'


def _url(match) -> str:
    url = match.group(0).rstrip(".,;:!?")
    return f'<a href="{url}" dir="ltr">{url}</a>{match.group(0)[len(url):]}'


def inline(text: str, link) -> str:
    """Escaped text with `code`, **bold**, bare URLs and [mm:ss] links to the source."""
    out = []
    for part in CODE.split(text):
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            out.append(f'<code dir="ltr">{html.escape(part[1:-1])}</code>')
            continue
        part = html.escape(part, quote=False)
        part = QUOTED_LATIN.sub(lambda m: f'<bdi dir="ltr">{m.group(0)}</bdi>', part)  # before any attribute exists
        part = BOLD.sub(r"<strong>\1</strong>", part)
        part = URL.sub(_url, part)
        out.append(TIME.sub(lambda m: stamp(m.group(1), link), part))
    return "".join(out)


def check(blocks: list[dict], times: dict, duration: float) -> tuple[list[str], list[str]]:
    errors, warnings, used = [], [], {}
    if not any(b["kind"] == "h2" for b in blocks):
        errors.append("line 1: no chapters; start each with a `## ` heading")
    for b in blocks:
        n = b["line"]
        if b["kind"] == "code":
            continue
        if b["kind"] == "figure":
            if b["id"] not in times:
                errors.append(f"line {n}: {b['id']} is not one of the frames (see frames.json)")
            if not b["text"]:
                errors.append(f"line {n}: {b['id']} needs a caption: ![{b['id']}](what it shows and why it is here)")
            if b["id"] in used:
                warnings.append(f"line {n}: {b['id']} is already used on line {used[b['id']]}")
            used.setdefault(b["id"], n)
        for text in b.get("items") or [b.get("text", "")]:
            for m in TIME.finditer(text):
                if duration and parse_time(m.group(1)) > duration + 5:
                    errors.append(f"line {n}: [{m.group(1)}] is after the end of the video ({fmt_time(duration)})")
            if NIQQUD.search(text):
                warnings.append(f"line {n}: niqqud; write Hebrew without vowel points")
    return errors, warnings


def _source_link(meta: dict):
    if meta.get("kind") == "youtube" and meta.get("id"):
        return lambda seconds: f"https://www.youtube.com/watch?v={meta['id']}&t={seconds}s"
    return None


def _meta_line(meta: dict, s: dict) -> str:
    bits = []
    if meta.get("url"):
        bits.append(f'<a href="{html.escape(meta["url"])}">{s["watch"]}</a>')
    bits.append(f'<bdi dir="auto">{html.escape(meta.get("title") or "")}</bdi>')
    if meta.get("uploader"):
        bits.append(f'{s["by"]}\u00a0<bdi dir="auto">{html.escape(meta["uploader"])}</bdi>')
    if meta.get("duration"):
        bits.append(f'{s["length"]}\u00a0<bdi dir="ltr">{fmt_time(meta["duration"])}</bdi>')
    t = meta.get("transcript") or {}
    how = {"captions": s["auto"] if t.get("auto") else s["manual"], "whisper": s["whisper"],
           "file": s["file"]}.get(t.get("method"))
    if how:
        bits.append(f'{s["transcript"]}: {how}')
    return " · ".join(bits)


def _body(blocks: list[dict], times: dict, folder: Path, link) -> tuple[list, list[tuple]]:
    """HTML parts in order (the table of contents is a placeholder list before the first chapter)."""
    parts, toc, toc_at, brief = [], [], None, False
    for b in blocks:
        kind = b["kind"]
        if kind == "h2":
            toc_at = len(parts) if toc_at is None else toc_at
            lead = LEAD_TIME.match(b["text"])
            name = b["text"][lead.end():] if lead else b["text"]
            toc.append((len(toc) + 1, name, lead.group(1) if lead else None))
            chip = stamp(lead.group(1), link) + " " if lead else ""  # a space: margins flip on dir="ltr" chips
            parts.append(f'<h2 id="s{len(toc)}">{chip}{inline(name, link)}</h2>')
        elif kind == "h3":
            parts.append(f"<h3>{inline(b['text'], link)}</h3>")
        elif kind == "p":
            parts.append(f"<p>{inline(b['text'], link)}</p>")
        elif kind in ("ul", "ol"):
            parts.append(f"<{kind}>" + "".join(f"<li>{inline(x, link)}</li>" for x in b["items"]) + f"</{kind}>")
        elif kind == "quote":
            paras, current = [], []
            for item in b["items"] + [""]:
                if item:
                    current.append(item)
                elif current:
                    paras.append(" ".join(current))
                    current = []
            opening = not toc and not brief
            brief = brief or opening
            parts.append(("<blockquote class=\"brief\">" if opening else "<blockquote>")
                         + "".join(f"<p>{inline(p, link)}</p>" for p in paras) + "</blockquote>")
        elif kind == "code":
            parts.append(f'<pre dir="ltr"><code>{html.escape(b["text"])}</code></pre>')
        elif kind == "figure":
            data = base64.b64encode((folder / f"{b['id']}.jpg").read_bytes()).decode()
            alt = html.escape(re.sub(r"[`*]", "", b["text"]))
            parts.append(f'<figure><img src="data:image/jpeg;base64,{data}" alt="{alt}"><figcaption>'
                         f'{stamp(fmt_time(times[b["id"]]), link)} {inline(b["text"], link)}</figcaption></figure>')
    if toc:
        parts.insert(toc_at, toc)
    return parts, toc


def build(notes: str, meta: dict, frames: list[dict], folder: Path) -> tuple[str, list[str], list[str]]:
    """(html, errors, warnings); html is empty while there are errors."""
    warnings = []
    if BIDI.search(notes):
        notes = BIDI.sub("", notes)
        warnings.append("removed invisible direction marks; the page isolates directions itself")
    blocks = parse(notes)
    times = {f["id"]: f["t"] for f in frames}
    errors, found = check(blocks, times, meta.get("duration") or 0)
    warnings += found
    missing = sorted({b["id"] for b in blocks if b["kind"] == "figure" and b["id"] in times
                      and not (folder / f"{b['id']}.jpg").is_file()})
    errors += [f"{fid}.jpg is missing from {folder}; run prepare again" for fid in missing]
    if errors:
        return "", errors, warnings
    hebrew, latin = len(HEBREW.findall(notes)), len(LATIN.findall(notes))
    lang = "he" if hebrew > 0.3 * (hebrew + latin) else "en"
    s, link = STRINGS[lang], _source_link(meta)
    title = next((b["text"] for b in blocks if b["kind"] == "title"), meta.get("title") or "")
    parts, toc = _body([b for b in blocks if b["kind"] != "title"], times, folder, link)
    if toc:
        items = "".join(f'<li><a href="#s{i}">{inline(name, None)}</a>'
                        + (f' <span class="t" dir="ltr">{t}</span>' if t else "") + "</li>" for i, name, t in toc)
        nav = f'<nav class="toc" aria-label="{s["toc"]}"><p class="toc-title">{s["toc"]}</p><ol>{items}</ol></nav>'
        parts = [nav if isinstance(p, list) else p for p in parts]
    today = datetime.date.today()
    date = today.strftime("%d.%m.%Y") if lang == "he" else today.isoformat()
    font = ""
    if FONT.is_file():
        data = base64.b64encode(FONT.read_bytes()).decode()
        font = ('@font-face{font-family:"Frank Ruhl Libre";font-weight:300 900;font-display:swap;'
                f'src:url(data:font/woff2;base64,{data}) format("woff2");'
                "unicode-range:U+0590-05FF,U+200C-2010,U+20AA,U+25CC,U+FB1D-FB4F}")
    direction = "rtl" if lang == "he" else "ltr"
    page = (f'<!doctype html>\n<html lang="{lang}" dir="{direction}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; '
            'font-src data:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'">\n'
            f"<title>{html.escape(title)}</title>\n<style>{font}{CSS}</style>\n</head>\n<body>\n"
            f'<header class="masthead"><h1 dir="auto">{inline(title, None)}</h1>'
            f'<p class="meta">{_meta_line(meta, s)}</p></header>\n<main>\n' + "\n".join(parts)
            + f'\n</main>\n<footer>{s["footer"].format(date=date)}</footer>\n'
            f'<div id="zoom" hidden><img alt=""></div>\n<script>{SCRIPT}</script>\n</body>\n</html>\n')
    return page, [], warnings


def browser() -> str | None:
    for name in BROWSERS:
        path = name if Path(name).is_file() else shutil.which(name)
        if path:
            return path
    return None


def pdf(page: Path, out: Path) -> bool:
    chrome = browser()
    if not chrome:
        return False
    out.unlink(missing_ok=True)
    # Headless mode already uses a throwaway profile; an explicit fresh --user-data-dir makes
    # Chrome 153 on macOS print the file and then never exit.
    try:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", page.resolve().as_uri()], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        pass  # the file may be complete even if the browser lingers
    return out.is_file() and out.stat().st_size > 0
