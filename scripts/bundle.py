#!/usr/bin/env python3
"""Bundle a summary directory into ONE self-contained HTML file.

Takes the `summary-<id>/` directory (index.html + assets/) and emits a single
.html with every image embedded as a base64 data URI — open it with a double
click, mail it, drop it in a chat. No server, no sidecar folder.

Notes:
- Thumb references are swapped for their -full siblings, so the single file
  carries the crisp 1280px images (the thumb/full split only matters when a
  folder of files is served alongside the page).
- <a href="assets/..."> click-to-enlarge wrappers are unwrapped: browsers
  block top-level navigation to data: URIs, so a link would be dead weight.
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
}


def data_uri(path: Path) -> str:
    mime = MIME.get(path.suffix.lower())
    if mime is None:
        raise SystemExit(f"Unsupported image type for embedding: {path.name}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def bundle(summary_dir: Path, out: Path | None) -> Path:
    index = summary_dir / "index.html"
    if not index.exists():
        raise SystemExit(f"No index.html in {summary_dir}")
    html = index.read_text(encoding="utf-8")

    # 1. unwrap <a href="assets/..."> around images (data: links are blocked)
    html = re.sub(r'<a href="assets/[^"]+">\s*(<img[^>]*>)\s*</a>', r"\1", html)

    # 2. inline every local image; prefer the -full sibling over a -thumb
    missing: list[str] = []

    def repl(match: re.Match) -> str:
        rel = match.group(1)
        path = summary_dir / rel
        full = path.with_name(path.name.replace("-thumb.", "-full."))
        target = full if full.exists() else path
        if not target.exists():
            missing.append(rel)
            return match.group(0)
        return f'src="{data_uri(target)}"'

    html = re.sub(r'src="(assets/[^"]+)"', repl, html)
    if missing:
        raise SystemExit(f"Missing asset files: {missing}")

    leftovers = re.findall(r'(?:src|href)="assets/[^"]+"', html)
    if leftovers:
        raise SystemExit(f"Unresolved asset references remain: {leftovers[:5]}")

    out = out or summary_dir.parent / f"{summary_dir.name}.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="bundle",
        description="Emit a single self-contained HTML from a summary directory.",
    )
    ap.add_argument("summary_dir", help="The summary-<id>/ directory (index.html + assets/)")
    ap.add_argument("--out", default=None, help="Output file (default: <summary-dir>.html)")
    args = ap.parse_args()

    summary_dir = Path(args.summary_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve() if args.out else None
    result = bundle(summary_dir, out)
    size_mb = result.stat().st_size / (1024 * 1024)
    n_images = result.read_text(encoding="utf-8").count("data:image/")
    print(f"Bundled {n_images} images into `{result}` ({size_mb:.1f} MB) — "
          "self-contained, opens with a double click.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
