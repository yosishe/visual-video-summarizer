"""Small deterministic boundaries; these do not sandbox the host agent."""
from __future__ import annotations

import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

YTDLP_FLAGS = ("--ignore-config", "--no-plugin-dirs", "--no-exec",
               "--no-remote-components", "--no-playlist")
CSP = ("default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; "
       "form-action 'none'; frame-src 'none'; connect-src 'none'; "
       "img-src 'self' data:; font-src data:; style-src 'unsafe-inline'")


def ytdlp_command(args: list[str]) -> list[str]:
    return ["yt-dlp", *YTDLP_FLAGS, *args]


def atomic_write(path: Path, text: str) -> None:
    """Never follow a predictable temporary-file or destination symlink."""
    if path.is_symlink():
        raise SystemExit(f"Refusing output symlink: {path.name}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def asset_file(root: Path, filename: str) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or \
            any(c in filename for c in ("/", "\\", ":", "\x00")):
        raise SystemExit("Asset filename must be a basename")
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"Missing or symlinked asset: {filename}")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise SystemExit(f"Unsupported asset type: {filename}")
    if path.resolve().parent != root.resolve():
        raise SystemExit(f"Unsafe asset path: {filename}")
    return path.resolve()


def validate_generated_html(text: str) -> None:
    """Reject active content/remote resources in this renderer's static subset.

    This is an output check, not a general-purpose sanitizer for arbitrary pages.
    """
    class StaticHTML(HTMLParser):
        allowed = set("html head meta title style body header div h1 h2 h3 h4 h5 h6 "
                      "p nav ol ul li span a bdi b strong em code pre blockquote main "
                      "section footer figure img figcaption br".split())

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.in_style = False
            self.css = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.allowed:
                raise SystemExit(f"Active/unsupported HTML element: {tag}")
            for name, value in attrs:
                value = value or ""
                if name.startswith("on") or name in {"srcdoc", "srcset", "style", "background"}:
                    raise SystemExit(f"Active/unsupported HTML attribute: {name}")
                if tag == "meta" and name == "http-equiv" and value.lower() != "content-security-policy":
                    raise SystemExit("Only the generated CSP meta directive is allowed")
                if name in {"src", "href"} and value.startswith("assets/") and not re.fullmatch(
                        r"assets/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:jpg|jpeg|png|webp|gif)", value, re.I):
                    raise SystemExit("Unsafe asset path in generated HTML")
                if name == "src" and not (value.startswith("assets/") or re.fullmatch(
                        r"data:image/(?:jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+", value)):
                    raise SystemExit("Only embedded images or local assets are allowed")
                if name == "href" and not (value.startswith(("#", "assets/")) or
                        (urlsplit(value).scheme in {"https", "http"} and urlsplit(value).hostname)):
                    raise SystemExit("Unsupported link scheme in generated HTML")
            self.in_style = tag == "style"

        def handle_endtag(self, tag):
            if tag == "style":
                self.in_style = False

        def handle_data(self, data):
            if self.in_style:
                self.css.append(data)

    parser = StaticHTML()
    parser.feed(text)
    parser.close()
    css = "".join(parser.css)
    if "\\" in css or re.search(r"@import|/\*", css, re.I):
        raise SystemExit("Unsupported CSS in generated HTML")
    for match in re.finditer(r"url\((.*?)\)", css, re.I | re.S):
        value = match.group(1).strip().strip("\"'")
        if not re.fullmatch(r"data:font/woff;base64,[A-Za-z0-9+/=]+", value):
            raise SystemExit("External CSS resources are not allowed")
