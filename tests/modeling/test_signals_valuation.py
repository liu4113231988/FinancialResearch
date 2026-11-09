# -*- coding: utf-8 -*-
"""P2-21：估值通道不可用时必须从融合分母剔除，而不是填中性 0。"""
import unittest

import numpy as np
import pandas as pd

from market_research.modeling.signals import SignalEngine, build_decision


def _inputs(n=500, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    syn = pd.Series(np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))), index=idx)
    nav = syn * np.exp(rng.normal(0.0, 0.004, n))
    weights = pd.DataFrame({"sh000300": np.ones(n)}, index=idx)
    return nav, syn, weights, idx


def _neutral_pe(idx):
    return pd.DataFrame({"沪深300": np.full(len(idx), 0.5)}, index=idx)


class ValuationUnavailableTest(unittest.TestCase):
    def test_empty_panel_returns_nan_not_neutral_zero(self):
        _nav, syn, weights, idx = _inputs()
        v = SignalEngine().valuation_channel(weights, None, syn.index)
        self.assertFalse(v["val_score"].notna().any())
        meta = v.attrs["valuation"]
        self.assertFalse(meta["available"])
        self.assertTrue(meta["excluded"])
        self.assertEqual(meta["weight"], 0.0)
        self.assertIn("未参与融合", meta["note"])

    def test_compute_excludes_channel_and_renormalizes(self):
        nav, syn, weights, idx = _inputs()
        engine = SignalEngine()
        cfg = engine.cfg
        wt, wv, wl = cfg["w_trend"], cfg["w_dev"], cfg["w_val_pe"]

        off = engine.compute(nav, syn, weights, pe_pct=None)
        neutral = engine.compute(nav, syn, weights, pe_pct=_neutral_pe(idx))

        self.assertTrue(off["val_weight_effective"].eq(0.0).all())
        # 通道不可用 → 分母只剩趋势/偏离；真实中性 → 估值仍占 wl。
        # 因而"不可用"版本不能被压向 0.5，|score| 应严格更大。
        row = off.index[-1]
        self.assertGreater(abs(float(off.loc[row, "score"])),
                           abs(float(neutral.loc[row, "score"])) + 1e-9)
        expected = (wt * float(off.loc[row, "trend_score"])
                    + wv * float(off.loc[row, "dev_score"])) / (wt + wv)
        self.assertAlmostEqual(float(off.loc[row, "score"]), expected, places=12)
        self.assertGreater(wl, 0)

    def test_all_nan_key_column_counts_as_unavailable(self):
        _nav, syn, weights, idx = _inputs()
        pe = pd.DataFrame({"沪深300": np.full(len(idx), np.nan)}, index=idx)
        v = SignalEngine().valuation_channel(weights, pe, syn.index)
        self.assertFalse(v.attrs["valuation"]["available"])
        self.assertFalse(v["val_score"].notna().any())


class DecisionReliabilityTest(unittest.TestCase):
    def test_unreliable_days_do_not_count_toward_exit_confirmation(self):
        idx = pd.bdate_range("2024-01-01", periods=11)
        health = pd.DataFrame({
            "health_score": [-1.0] * len(idx),
            "fit_reliable": [0.0] * 9 + [1.0, 1.0],
        }, index=idx)
        decision, trades = build_decision(health, {
            "health_exit": -0.5, "health_enter": -0.2, "health_confirm": 2})
        self.assertEqual([t["date"] for t in trades], [idx[-1]])
        self.assertEqual(decision.iloc[-2], 1)
        self.assertEqual(decision.iloc[-1], 0)

    def test_nan_health_pauses_but_does_not_reset_confirmation(self):
        idx = pd.bdate_range("2024-01-01", periods=4)
        health = pd.DataFrame({
            "health_score": [-1.0, -1.0, np.nan, -1.0],
            "fit_reliable": [1.0] * 4,
        }, index=idx)
        _decision, trades = build_decision(health, {
            "health_exit": -0.5, "health_enter": -0.2, "health_confirm": 2})
        self.assertEqual([t["date"] for t in trades], [idx[1]])


if __name__ == "__main__":
    unittest.main()
