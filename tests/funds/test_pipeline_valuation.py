# -*- coding: utf-8 -*-
"""P2-21：研究 CLI 的估值对照曲线必须遵守与 Web 回测一致的可用性判断。"""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.funds.pipeline as pipeline


def _panel():
    idx = pd.date_range("2020-01-02", periods=800, freq="B")
    rng = np.random.default_rng(11)
    index_nav = np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))
                       + rng.normal(0, 0.01, len(idx)))
    fund_nav = np.exp(np.cumsum(rng.normal(0, 0.011, len(idx)))
                      + 0.4 * rng.normal(0, 0.02, len(idx)))
    panel = pd.DataFrame({"__FUND__": fund_nav, "sh000300": index_nav},
                         index=idx)
    panel.attrs["data_quality"] = {"status": "ok", "reasons": []}
    return panel


def _fit(panel):
    idx = panel.index
    rebal = pd.date_range(idx[0], idx[-1], freq="MS")
    rng = np.random.default_rng(12)
    return {
        "weights": pd.DataFrame({"sh000300": 0.8}, index=rebal),
        "syn_ret": pd.Series(rng.normal(0, 0.008, len(idx)), index=idx),
        "diag": pd.DataFrame({"r2": 0.9, "te": 0.1}, index=rebal),
    }


class ValuationVariantTests(unittest.TestCase):
    def _run(self, pe_panel, attrs=None):
        panel = _panel()
        panel.attrs.update(attrs or {})
        fit = _fit(panel)
        with patch.object(pipeline.D, "load_panel", return_value=panel), \
             patch.object(pipeline.D, "load_pe_panel", return_value=pe_panel), \
             patch.object(pipeline.RBSA, "fit_rolling", return_value=fit), \
             patch.object(pipeline.audit, "record"):
            return pipeline.run_fund("110022", verbose=False)

    def test_unavailable_valuation_curve_is_not_generated(self):
        result = self._run(pd.DataFrame())
        self.assertNotIn("仅赛道估值通道", result["backtest"]["equity"].columns)
        self.assertTrue(result["valuation"]["excluded"])
        self.assertFalse(result["valuation"]["available"])
        self.assertIn("不可用", result["valuation"]["note"])
        # 其余曲线仍完整，且没有任何 NaN 污染
        self.assertFalse(result["backtest"]["equity"].isna().any().any())
        self.assertGreater(len(result["backtest"]["equity"]), 0)

    def test_available_valuation_curve_is_kept(self):
        panel = _panel()
        pe = pd.DataFrame(
            {"沪深300": np.linspace(0.1, 0.9, len(panel.index))},
            index=panel.index)
        result = self._run(pe)
        self.assertIn("仅赛道估值通道", result["backtest"]["equity"].columns)
        self.assertTrue(result["valuation"]["available"])
        self.assertFalse(result["valuation"]["excluded"])
        self.assertFalse(result["backtest"]["equity"].isna().any().any())


class NavBasisMetaTests(unittest.TestCase):
    """P1-20：研究 CLI 输出必须带净值口径，降级时带原因。"""

    def _run(self, attrs):
        panel = _panel()
        panel.attrs.update(attrs)
        fit = _fit(panel)
        result = None
        with patch.object(pipeline.D, "load_panel", return_value=panel), \
             patch.object(pipeline.D, "load_pe_panel", return_value=pd.DataFrame()), \
             patch.object(pipeline.RBSA, "fit_rolling", return_value=fit), \
             patch.object(pipeline.audit, "record"):
            result = pipeline.run_fund("110022", verbose=False)
        return result

    def test_total_basis_is_labelled(self):
        result = self._run({"nav_basis": "total"})
        self.assertEqual(result["meta"]["nav_basis"], "total")
        self.assertFalse(result["meta"]["nav_degraded"])
        self.assertIn("事后月度锚点校准", result["meta"]["data_basis"])

    def test_degraded_cum_basis_carries_reason(self):
        result = self._run({"nav_basis": "cum", "nav_degraded": True,
                            "nav_degraded_reason": "total 净值构造失败"})
        self.assertEqual(result["meta"]["nav_basis"], "cum")
        self.assertTrue(result["meta"]["nav_degraded"])
        self.assertEqual(result["meta"]["nav_degraded_reason"],
                         "total 净值构造失败")
        self.assertIn("不可按总回报解读", result["meta"]["data_basis"])


if __name__ == "__main__":
    unittest.main()
