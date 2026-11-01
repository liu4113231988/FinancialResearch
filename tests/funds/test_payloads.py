# -*- coding: utf-8 -*-
"""基金结果装载层的回归：残差尺度、披露滞后、择时措辞。

对应 TODO「全量功能/逻辑/设计复查（2026-11-01）」的 P0-1 / P0-2 / P0-4。
这些断言都自带最小复现输入，不依赖网络与 akshare。
"""
import unittest

import numpy as np
import pandas as pd

import market_research.funds.holdings as H
from market_research.funds.payloads import model_payload
from market_research.modeling.engine import ModelReport
from market_research.research.report import timing_comparison


def _model_report(idx, syn_ret, level=2.5):
    """构造一个最小可用的 ModelReport（300 个交易日）。"""
    weights = pd.Series({"sh000300": 0.35, "801120": 0.25})
    hist = pd.DataFrame({"sh000300": [0.35, 0.34], "801120": [0.25, 0.26]},
                        index=[idx[0], idx[-1]])
    return ModelReport(
        key="pcr", name="PCR", family="线性", desc="", interpretable=True,
        oos_r2=0.9, oos_te=0.05, oos_ic=0.9, win=0.5, beta=1.0,
        vol_ratio=1.0, fit_r2_mean=0.95, fit_te_mean=0.04,
        latest_weights=weights, weights_last_date=idx[-1],
        weights_hist=hist, syn_ret=syn_ret, extra={}, elapsed=0.1,
    )


class ResidualScaleTest(unittest.TestCase):
    def test_residual_is_normalized_like_curve(self):
        idx = pd.bdate_range("2021-01-04", periods=300)
        rng = np.random.default_rng(7)
        syn = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
        # 基金净值起点刻意设为 2.5：旧实现用未归一净值减从 1 起的基准，
        # 残差会整体抬到 ~2.5，图上全为正。
        nav = 2.5 * np.exp(syn.cumsum()) * np.exp(
            np.cumsum(rng.normal(0, 0.001, len(idx))))
        out = model_payload(_model_report(idx, syn, level=2.5), nav)

        self.assertAlmostEqual(out["curve"]["fund"][0], 1.0, places=6)
        self.assertAlmostEqual(out["curve"]["synth"][0], 1.0, places=6)
        vals = np.array([v for v in out["residual"]["value"] if v is not None])
        self.assertGreater(len(vals), 0)
        # 归一化后残差应围绕 0 波动，而不是停留在净值水平（~2.5）
        self.assertLess(abs(float(vals.mean())), 0.30)
        self.assertLess(float(np.abs(vals).max()), 1.0)


class DisclosureLagTest(unittest.TestCase):
    def test_statutory_deadlines(self):
        self.assertEqual(H.disclosure_lag_days("2024-06-30"), 62)   # 半年报 8/31
        self.assertEqual(H.disclosure_lag_days("2024-12-31"), 90)   # 年报次年 3/31
        self.assertEqual(H.disclosure_lag_days("2024-03-31"), 31)   # 季报（前十大）
        self.assertEqual(H.disclosure_lag_days("2024-09-30"), 31)
        # 无法识别的季末仍给兜底值，而不是抛异常
        self.assertEqual(H.disclosure_lag_days("2024-07-15"), H.DISCLOSURE_LAG_DAYS)
        self.assertEqual(H.disclosure_lag_days(None), H.DISCLOSURE_LAG_DAYS)


class HoldingBenchmarkCashTest(unittest.TestCase):
    def _hr(self, weight):
        idx = pd.bdate_range("2022-04-01", periods=10)
        panel = pd.DataFrame({"801120": np.full(len(idx), 1.0)}, index=idx)
        exposure = pd.DataFrame({"801120": [weight]},
                                index=[pd.Timestamp("2021-12-31")])
        return H.holding_benchmark(exposure, panel, cash_rate=0.015)

    def test_cash_leg_accrues(self):
        hr = self._hr(50.0)
        self.assertGreater(len(hr), 0)
        daily = (1.015 ** (1 / 252) - 1)
        # 50% 未覆盖 → 每日约半份货币收益，旧实现为 0
        self.assertAlmostEqual(float(hr.iloc[0]), 0.5 * daily, places=9)

    def test_full_exposure_has_no_cash_leg(self):
        hr = self._hr(100.0)
        self.assertAlmostEqual(float(hr.iloc[0]), 0.0, places=12)


class TimingComparisonTest(unittest.TestCase):
    def test_three_states(self):
        cls, text, tail = timing_comparison({"年化收益": 0.052, "夏普": 0.31})
        self.assertEqual(cls, "ok")
        self.assertIn("跑赢", text)
        self.assertNotIn("跑输", text)
        self.assertIn("不足以证明", tail)

        cls, text, _ = timing_comparison({"年化收益": -0.031, "夏普": -0.2})
        self.assertEqual(cls, "bad")
        self.assertIn("跑输", text)
        self.assertIn("-3.10%", text)

        cls, text, _ = timing_comparison({"年化收益": 0.0, "夏普": 0.0})
        self.assertEqual(cls, "")
        self.assertIn("持平", text)

    def test_missing_is_not_a_negative_conclusion(self):
        for tc in ({}, {"年化收益": float("nan"), "夏普": 0.1},
                   {"年化收益": None}):
            cls, text, tail = timing_comparison(tc)
            self.assertEqual(cls, "")
            self.assertIn("无法计算", text)
            self.assertNotIn("跑输", text)
            self.assertNotIn("跑赢", text)
            self.assertTrue(tail)


if __name__ == "__main__":
    unittest.main()
