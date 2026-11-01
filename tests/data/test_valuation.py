# -*- coding: utf-8 -*-
"""估值/股息面板（csindex 取数）的回归：列名契约、分位口径与覆盖边界。

取数源统一为 `stock_zh_index_value_csindex`（中证官方，日频）；旧乐咕
`stock_index_pe_lg` 在当前 akshare 版本已缺失。全部离线 mock，不联网。
"""
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

import akshare as ak

import market_research.config as C
from market_research.data import storage
from market_research.data.providers import valuation
from market_research.data.providers.valuation import (
    CSINDEX_CODE, fetch_pe_panel, fetch_dividend_panel)
from market_research.modeling.signals import PE_SYMBOL, PE_FALLBACK


def _csindex_frame(code, pe_values):
    n = len(pe_values)
    return pd.DataFrame({
        "日期": pd.bdate_range("2020-01-01", periods=n),
        "指数代码": [code] * n,
        "指数中文全称": ["测试指数"] * n,
        "市盈率1": [v * 2 for v in pe_values],
        "市盈率2": pe_values,
        "股息率1": [2.0] * n,
        "股息率2": [1.5] * n,
    })


def _mock_csindex(pe_values):
    frame = _csindex_frame("000300", pe_values)

    def fake(symbol):
        if symbol == CSINDEX_CODE["沪深300"]:
            return frame
        raise RuntimeError(f"csindex 无此指数: {symbol}")
    return fake


class ValuationPanelTests(unittest.TestCase):
    def test_pe_columns_follow_pe_symbol_contract(self):
        # fetch_pe_panel 返回**原始 PE**（分位由 load_pe_panel 在累积后重算）。
        n = 250 * 9                          # 9 年
        vals = list(np.linspace(8.0, 30.0, n))
        with patch.object(ak, "stock_zh_index_value_csindex",
                          side_effect=_mock_csindex(vals)):
            df = fetch_pe_panel(["沪深300", "不存在的标的"], verbose=False)
        self.assertIn("沪深300", df.columns)
        self.assertNotIn("不存在的标的", df.columns)
        raw = df["沪深300"].dropna()
        # 原始 PE 不再被夹到 [0,1]；单调递增序列最后一期就是最大值
        self.assertAlmostEqual(float(raw.iloc[-1]), 30.0, places=10)
        self.assertAlmostEqual(float(raw.iloc[0]), 8.0, places=10)

    def test_dividend_panel_converts_percent_to_decimal(self):
        with patch.object(ak, "stock_zh_index_value_csindex",
                          side_effect=_mock_csindex([10.0] * 30)):
            df = fetch_dividend_panel(["沪深300"], verbose=False)
        # csindex 股息率2 以 % 为单位 → 年化小数
        self.assertAlmostEqual(float(df["沪深300"].iloc[-1]), 0.015, places=12)

    def test_csindex_covers_every_pe_symbol(self):
        need = set(PE_SYMBOL.values()) | {PE_FALLBACK}
        self.assertTrue(need <= set(CSINDEX_CODE), need - set(CSINDEX_CODE))

    def test_nonpositive_and_missing_columns(self):
        frame = _csindex_frame("000300", [10.0, -1.0, 0.0, 12.0])
        s = valuation._csindex_value(frame, "市盈率2")
        self.assertEqual([float(v) for v in s.values], [10.0, 12.0])
        with self.assertRaises(ValueError):
            valuation._csindex_value(frame, "不存在的列")


class AccumulationTests(unittest.TestCase):
    """csindex 官方文件只留近一月 → 历史靠本地累积（P1-10 覆盖边界内）。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=C.ROOT / "output")
        p = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_accumulate_merges_history_and_new_month(self):
        from market_research.data import panels
        idx_old = pd.bdate_range("2025-01-01", periods=30)
        cached = pd.DataFrame({"沪深300": np.linspace(10.0, 12.0, 30)}, index=idx_old)
        storage.write("pe_panel", cached, {})
        # 新抓的"最近一个月"：与旧缓存重叠数日，重叠段被官方修订过
        idx_new = pd.bdate_range(idx_old[-1] - pd.Timedelta(days=2), periods=20)
        fresh = pd.DataFrame({"沪深300": np.linspace(12.0, 14.0, 20)}, index=idx_new)
        df = panels._accumulate_panel("pe_panel", lambda s, verbose=True: fresh,
                                      ["沪深300"], verbose=False)
        self.assertEqual(len(df), len(idx_old.union(idx_new)))
        # 重叠日期以新值为准（官方修订优先）；更早的历史完整保留
        self.assertAlmostEqual(float(df["沪深300"].loc[idx_new[0]]),
                               float(fresh["沪深300"].loc[idx_new[0]]))
        self.assertAlmostEqual(float(df["沪深300"].loc[idx_old[0]]),
                               float(cached["沪深300"].iloc[0]))

    def test_fetch_failure_keeps_cached_untouched(self):
        from market_research.data import panels
        idx = pd.bdate_range("2025-01-01", periods=5)
        cached = pd.DataFrame({"沪深300": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=idx)
        storage.write("div_panel", cached, {})
        def boom(syms, verbose=True):
            raise RuntimeError("offline")
        df = panels._accumulate_panel("div_panel", boom, ["沪深300"], verbose=False)
        pd.testing.assert_frame_equal(df, cached, check_freq=False)
        # 失败不回写：storage 里的缓存保持原样
        pd.testing.assert_frame_equal(storage.read("div_panel"), cached,
                                      check_freq=False)

    def test_load_pe_panel_computes_percentile_on_accumulated_raw(self):
        # 回归 P1-A：pe_panel 缓存存的是**原始 PE**，分位必须由 load_pe_panel
        # 在累积出完整历史后重算——否则短片段上算分位会得到全 NaN 并被永久冻结。
        from market_research.data import panels
        # 种子缓存：600 个交易日的单调递增原始 PE（> min_periods=250）
        idx = pd.bdate_range("2018-01-01", periods=600)
        raw = pd.DataFrame({"沪深300": np.linspace(8.0, 30.0, 600)}, index=idx)
        storage.write("pe_panel", raw, {})
        # 本次 fetch 无新数据 → 沿用缓存（模拟离线/未到同步时点）
        with patch("market_research.data.panels.fetch_pe_panel",
                   return_value=pd.DataFrame()):
            pct = panels.load_pe_panel(verbose=False)
        self.assertIn("沪深300", pct.columns)
        vals = pct["沪深300"].dropna()
        self.assertGreater(len(vals), 0)
        self.assertTrue(((vals >= 0) & (vals <= 1)).all())
        # 单调递增原始 PE → 最后一期处于历史最贵档，分位接近 1.0
        self.assertAlmostEqual(float(vals.iloc[-1]), 1.0, places=6)

    def test_load_pe_panel_short_history_yields_nan_not_crash(self):
        # 历史不足 min_periods 时输出 NaN（估值通道按无估值处理），不得抛异常
        from market_research.data import panels
        idx = pd.bdate_range("2025-12-01", periods=20)
        raw = pd.DataFrame({"沪深300": np.linspace(8.0, 12.0, 20)}, index=idx)
        storage.write("pe_panel", raw, {})
        with patch("market_research.data.panels.fetch_pe_panel",
                   return_value=pd.DataFrame()):
            pct = panels.load_pe_panel(verbose=False)
        self.assertIn("沪深300", pct.columns)
        self.assertTrue(pct["沪深300"].isna().all())


class ValuationSyncDailyTest(unittest.TestCase):
    def test_accumulate_valuation_runs_once_per_day(self):
        tmp = TemporaryDirectory(dir=C.ROOT / "output")
        p = patch.object(C, "DATA_DIR", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(tmp.cleanup)
        from market_research.tasks import sync
        import market_research.data.marketdata as D
        calls = []
        with patch.object(D, "load_pe_panel",
                          side_effect=lambda verbose=False: calls.append("pe")), \
             patch.object(D, "load_dividend_panel",
                          side_effect=lambda verbose=False: calls.append("div")):
            day = datetime(2026, 11, 1, 18, 40, tzinfo=sync.SHANGHAI)
            sync._accumulate_valuation(day)
            sync._accumulate_valuation(day)
        self.assertEqual(calls, ["pe", "div"])     # 同一天只累积一次


if __name__ == "__main__":
    unittest.main()
