"""The release string lives in one place and the changelog knows about it."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gates  # noqa: E402


class ReleaseTests(unittest.TestCase):
    def test_skill_metadata_version_matches_the_engine(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        front = text.split("---", 2)[1]
        match = re.search(r'^\s*version:\s*"([^"]+)"', front, re.M)
        self.assertIsNotNone(match, "SKILL.md frontmatter has no metadata.version")
        self.assertEqual(match.group(1), gates.ENGINE_VERSION)

    def test_changelog_has_an_entry_for_the_engine_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {gates.ENGINE_VERSION} —", changelog)

    def test_engine_version_is_a_release_string(self):
        self.assertRegex(gates.ENGINE_VERSION, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
