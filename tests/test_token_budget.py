"""Token safety: the image spend is bounded before the model looks at anything."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import candidates  # noqa: E402


class TokenBudgetTests(unittest.TestCase):
    def test_sheets_fix_the_floor_and_the_shortlist_absorbs_the_rest(self):
        plan = candidates.plan_token_budget(20_000, sheet_tokens=6_026, individual_tokens=13_376, shortlist_px=768)
        self.assertEqual(plan["mode"], "sheets")
        self.assertEqual(plan["shortlist_tokens_each"], 448)  # 768×432 → 28×16
        self.assertEqual(plan["shortlist_max"], 30)  # (20000-6026)//448 = 31 → capped at 30
        self.assertEqual(plan["planned"], 6_026 + 30 * 448)
        self.assertFalse(plan["over_budget"])

    def test_a_tight_budget_shrinks_the_shortlist(self):
        plan = candidates.plan_token_budget(9_000, sheet_tokens=6_026, individual_tokens=13_376, shortlist_px=640)
        self.assertEqual(plan["shortlist_tokens_each"], 299)
        self.assertEqual(plan["shortlist_max"], (9_000 - 6_026) // 299)
        self.assertFalse(plan["over_budget"])

    def test_sheets_that_do_not_fit_are_over_budget(self):
        plan = candidates.plan_token_budget(5_000, sheet_tokens=6_026, individual_tokens=13_376, shortlist_px=640)
        self.assertTrue(plan["over_budget"])
        self.assertEqual(plan["shortlist_max"], 0)

    def test_without_sheets_the_pool_read_is_the_spend(self):
        plan = candidates.plan_token_budget(12_000, sheet_tokens=None, individual_tokens=13_376, shortlist_px=640)
        self.assertEqual(plan["mode"], "individual")
        self.assertEqual(plan["planned"], 13_376)
        self.assertTrue(plan["over_budget"])

    def test_defaults_and_config_value(self):
        self.assertEqual(candidates.IMAGE_TOKEN_BUDGET["standard"], 12_000)
        self.assertEqual(candidates.IMAGE_TOKEN_BUDGET["high"], 20_000)
        with mock.patch.dict("os.environ", {"SUMMARY_MAX_IMAGE_TOKENS": " 8000 "}):
            self.assertEqual(candidates.config_value("SUMMARY_MAX_IMAGE_TOKENS"), "8000")
        with mock.patch.dict("os.environ", {}, clear=False), \
                mock.patch.object(candidates, "CONFIG_ENV", Path("/nonexistent/.env")):
            os_env = dict(candidates.os.environ)
            os_env.pop("SUMMARY_MAX_IMAGE_TOKENS", None)
            with mock.patch.dict("os.environ", os_env, clear=True):
                self.assertIsNone(candidates.config_value("SUMMARY_MAX_IMAGE_TOKENS"))

    def test_duration_guard_constants(self):
        self.assertEqual(candidates.MAX_DURATION_SECONDS, 7200)
        self.assertEqual(candidates.EXIT_TOO_LONG, 8)
        self.assertEqual(candidates.EXIT_OVER_BUDGET, 7)


if __name__ == "__main__":
    unittest.main()
