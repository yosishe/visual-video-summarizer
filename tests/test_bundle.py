from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bundle import bundle  # noqa: E402


class BundleTests(unittest.TestCase):
    def _summary(self, root: Path) -> Path:
        summary = root / "summary-demo"
        assets = summary / "assets"
        assets.mkdir(parents=True)
        (summary / "manifest.json").write_text("{}", encoding="utf-8")
        (assets / "frame-thumb.jpg").write_bytes(b"small")
        (assets / "frame-full.jpg").write_bytes(b"verified-full")
        (summary / "index.html").write_text(
            '<figure><a href="assets/frame-full.jpg">'
            '<img src="assets/frame-thumb.jpg" alt="frame"></a></figure>',
            encoding="utf-8",
        )
        return summary

    def test_bundle_embeds_full_asset_and_removes_sidecar_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = self._summary(Path(temporary))
            output = bundle(summary, None)
            html = output.read_text(encoding="utf-8")
            expected = base64.b64encode(b"verified-full").decode("ascii")
            self.assertIn(f"data:image/jpeg;base64,{expected}", html)
            self.assertNotIn("assets/", html)
            self.assertEqual(output, (summary.parent / "summary-demo.html").resolve())

    def test_bundle_requires_render_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = self._summary(Path(temporary))
            (summary / "manifest.json").unlink()
            with self.assertRaisesRegex(SystemExit, "No manifest.json"):
                bundle(summary, None)

    def test_bundle_rejects_asset_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = self._summary(Path(temporary))
            (summary / "outside.jpg").write_bytes(b"outside")
            (summary / "index.html").write_text(
                '<img src="assets/../outside.jpg" alt="unsafe">',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "Unsafe asset path"):
                bundle(summary, None)

    def test_bundle_never_overwrites_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = self._summary(Path(temporary))
            with self.assertRaisesRegex(SystemExit, "index.html"):
                bundle(summary, summary / "index.html")


if __name__ == "__main__":
    unittest.main()
