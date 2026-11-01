# -*- coding: utf-8 -*-
"""分析窗口解析的回归（P2-19）：年数窗口必须锚定显式结束日。

全部为纯函数测试，不依赖网络与缓存。
"""
import os
import unittest
from unittest.mock import patch

import pandas as pd

import market_research.config as C
from market_research.validation.window import resolve_window


class ResolveWindowTests(unittest.TestCase):
    def test_years_with_explicit_end_is_anchored_at_end(self):
        # 修复前：起点按"今天"回推（2026-11-01 - 2 年 = 2024-11-01），
        # 晚于 end=2024-01-01，直接报"起始日需早于结束日"。
        self.assertEqual(resolve_window(years=2, end="2024-01-01"),
                         ("2022-01-01", "2024-01-01"))

    def test_leap_day_end_falls_back_to_feb_28(self):
        self.assertEqual(resolve_window(years=2, end="2024-02-29")[0],
                         "2022-02-28")

    def test_fractional_years_subtract_calendar_years_plus_days(self):
        s, e = resolve_window(years=2.5, end="2024-01-01")
        self.assertEqual(e, "2024-01-01")
        days = (pd.Timestamp(e) - pd.Timestamp(s)).days
        self.assertTrue(910 <= days <= 916, f"2.5 年 ≈ 913 天，实际 {days}")

    def test_explicit_start_beats_years_and_end(self):
        self.assertEqual(resolve_window(years=2, start="2021-06-01",
                                        end="2024-01-01")[0], "2021-06-01")

    def test_years_without_end_follows_rolling_start(self):
        self.assertEqual(resolve_window(years=2)[0], C._rolling_start(2))

    def test_env_locked_start_only_applies_without_explicit_end(self):
        with patch.dict(os.environ, {"FIL_START_DATE": "2021-01-01"}):
            self.assertEqual(resolve_window(years=2)[0], "2021-01-01")
            # 显式 end 是更具体的契约，优先于环境变量
            self.assertEqual(resolve_window(years=2, end="2024-01-01")[0],
                             "2022-01-01")

    def test_non_positive_years_is_rejected(self):
        for years in (0, -1):
            with self.subTest(years=years):
                with self.assertRaises(ValueError):
                    resolve_window(years=years, end="2024-01-01")


if __name__ == "__main__":
    unittest.main()
