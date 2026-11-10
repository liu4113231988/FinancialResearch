# -*- coding: utf-8 -*-
"""申万行业静态股息率建议值的离线契约测试（不联网、不改默认口径）。"""
import unittest

import pandas as pd

import market_research.config as C
from market_research.data.providers.industry_dividend import (
    DIVIDEND_COLUMN, normalize_snapshot, suggest_index_dividend)
from market_research.research.commands.holdings import _format_suggestion


def _snapshot():
    return pd.DataFrame({
        "行业代码": ["801010.SI", "801780.SI", "801080.SI",
                     "999999.SI", "801030.SI"],
        "行业名称": ["农林牧渔", "银行", "电子", "不在候选池", "基础化工"],
        DIVIDEND_COLUMN: [2.0, 5.0, 1.0, 3.0, -1.0],
    })


class IndustryDividendTests(unittest.TestCase):
    def test_normalize_maps_codes_converts_percent_and_filters(self):
        detail = normalize_snapshot(_snapshot())
        self.assertEqual(list(detail["行业代码"]), ["801010", "801780", "801080"])
        self.assertEqual(list(detail["股息率"]), [0.02, 0.05, 0.01])

    def test_suggestion_is_median_and_declares_no_backfill(self):
        s = suggest_index_dividend(_snapshot(), as_of="2026-11-10")
        self.assertTrue(s["available"])
        self.assertEqual(s["suggested"], 0.02)
        self.assertEqual(s["covered"], 3)
        self.assertEqual(s["expected"], len(C.SW_INDUSTRY))
        self.assertTrue(s["static_snapshot"])
        self.assertTrue(s["not_for_backfill"])
        self.assertTrue(s["default_unchanged"])
        self.assertEqual(s["source"], "akshare.sw_index_first_info")
        self.assertEqual(s["as_of"], "2026-11-10")

    def test_suggestion_does_not_mutate_default(self):
        before = C.RBSA["index_dividend_annual"]
        suggest_index_dividend(_snapshot(), as_of="2026-11-10")
        self.assertEqual(before, 0.0)
        self.assertEqual(C.RBSA["index_dividend_annual"], before)

    def test_missing_column_and_empty_snapshot(self):
        with self.assertRaisesRegex(ValueError, "缺少 静态股息率"):
            normalize_snapshot(pd.DataFrame({"行业代码": ["801010.SI"]}))
        empty = pd.DataFrame(columns=["行业代码", DIVIDEND_COLUMN])
        s = suggest_index_dividend(empty, as_of="2026-11-10")
        self.assertFalse(s["available"])
        self.assertIsNone(s["suggested"])

    def test_formatted_output_states_results_unchanged(self):
        s = suggest_index_dividend(_snapshot(), as_of="2026-11-10")
        lines = "\n".join(_format_suggestion(s, 0.0))
        self.assertIn("本次实际取值仍为 0.00%", lines)
        self.assertIn("结果不变", lines)
        self.assertIn("不可回填历史", lines)
        self.assertIn("P1-10", lines)

    def test_formatted_output_handles_unavailable(self):
        empty = pd.DataFrame(columns=["行业代码", DIVIDEND_COLUMN])
        lines = "\n".join(_format_suggestion(
            suggest_index_dividend(empty, as_of="2026-11-10"), 0.0))
        self.assertIn("本次实际取值仍为 0.00%", lines)
        self.assertIn("结果不变", lines)
