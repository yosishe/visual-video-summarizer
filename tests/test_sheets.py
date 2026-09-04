"""Contact sheets: chronological tiles, burned ids, one sentinel, token cost."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sheets  # noqa: E402


def _candidates(n: int, root: Path) -> list[dict]:
    rows = []
    for i in range(n):
        path = root / f"c_{i:04d}.jpg"
        if sheets.Image is not None:
            sheets.Image.new("RGB", (512, 288), (10 * i % 255, 120, 200)).save(path)
        else:
            path.write_bytes(b"")
        rows.append({"candidate_id": f"c_{i:04d}", "actual_t": float(n - i) * 3.0, "path": str(path),
                     "width": 512, "height": 288})
    return rows


class PlanTests(unittest.TestCase):
    def test_plan_is_chronological_with_one_sentinel_per_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = sheets.plan_sheets(_candidates(40, Path(tmp)), tiles_per_sheet=16)
        self.assertEqual(len(plan), 3)                      # 15 real tiles per sheet
        for sheet in plan:
            self.assertLessEqual(len(sheet["tiles"]), 16)
            sentinels = [t for t in sheet["tiles"] if t.get("sentinel")]
            self.assertEqual(len(sentinels), 1)
            self.assertNotEqual(sentinels[0]["index"], 0)   # top-left carries a real candidate
            real = [t for t in sheet["tiles"] if not t.get("sentinel")]
            times = [t["actual_t"] for t in real]
            self.assertEqual(times, sorted(times))
            self.assertEqual([t["index"] for t in sheet["tiles"]], list(range(len(sheet["tiles"]))))
        self.assertTrue(plan[0]["sentinel_id"].startswith("x_"))

    def test_token_formula(self):
        self.assertEqual(sheets.image_tokens(1280, 792), 46 * 29)
        self.assertEqual(sheets.image_tokens(512, 288), 209)


@unittest.skipUnless(sheets.Image is not None, "PIL required")
class RenderTests(unittest.TestCase):
    def test_build_writes_sheets_and_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "candidates").mkdir()
            rows = _candidates(20, work / "candidates")
            (work / "candidates.json").write_text(json.dumps({"candidates": rows}))
            block = sheets.build_sheets(work, tiles_per_sheet=16, tile_width=320)
            self.assertEqual(block["status"], "ok")
            self.assertEqual(len(block["sheets"]), 2)
            first = block["sheets"][0]
            self.assertEqual((first["width"], first["height"]), (1280, 4 * (180 + sheets.BAR_H)))
            self.assertEqual(first["tokens"], sheets.image_tokens(1280, 792))
            self.assertTrue(Path(first["path"]).exists())
            self.assertLess(block["image_tokens"], block["individual_tokens"])
            payload = json.loads((work / "candidates.json").read_text())
            self.assertEqual(payload["sheets"]["sheets"][0]["sheet_id"], "sheet_00")
            # the sheet carries no candidate paths (ids are the only key)
            self.assertNotIn("path", payload["sheets"]["sheets"][0]["tiles"][0])


if __name__ == "__main__":
    unittest.main()
