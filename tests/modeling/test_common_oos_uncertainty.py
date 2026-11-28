# -*- coding: utf-8 -*-
"""B2：共同样本外日期上的配对块自助区间。"""
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from market_research.modeling.engine import ModelReport, ModelZoo


def _reports(n=300, seed=3):
    idx = pd.bdate_range("2022-01-03", periods=n)
    rng = np.random.default_rng(seed)
    actual = pd.Series(rng.normal(0.0002, 0.01, n), index=idx)
    reports = {
        "perfect": SimpleNamespace(syn_ret=actual.copy(), oos_y=actual, extra={}),
        # 系统性偏差 + 少量噪声：误差逐日不同，不会退化成常数
        "noisy": SimpleNamespace(syn_ret=actual + 0.1
                                 + rng.normal(0, 0.01, n), oos_y=actual, extra={}),
        "clone": SimpleNamespace(syn_ret=actual.copy(), oos_y=actual, extra={}),
    }
    return reports, actual


class CommonOOSUncertaintyTests(unittest.TestCase):
    def test_reference_and_ties_never_claim_advantage(self):
        reports, _ = _reports()
        ModelZoo._attach_common_oos(reports)
        block = reports["clone"].extra["common_oos_uncertainty"]
        self.assertEqual(block["reference"], "perfect")
        self.assertAlmostEqual(block["point_estimate"], 0.0, places=12)
        self.assertIsNone(block["distinguishable"])

    def test_clearly_worse_model_is_distinguishable(self):
        reports, _ = _reports()
        ModelZoo._attach_common_oos(reports)
        block = reports["noisy"].extra["common_oos_uncertainty"]
        self.assertEqual(block["reference"], "perfect")
        self.assertGreater(block["point_estimate"], 0)
        self.assertIs(block["distinguishable"], False)
        estimated = [item for item in block["intervals"]
                     if item["status"] == "estimated"]
        self.assertTrue(estimated)
        self.assertTrue(all(item["lower"] > 0 for item in estimated))

    def test_short_common_sample_leaves_no_interval(self):
        reports, _ = _reports(n=15)
        ModelZoo._attach_common_oos(reports)
        for report in reports.values():
            self.assertIsNone(report.extra["common_oos_uncertainty"])

    def test_non_finite_difference_reports_no_conclusion(self):
        reports, actual = _reports()
        broken = SimpleNamespace(syn_ret=actual.copy(), oos_y=actual, extra={})
        broken.syn_ret.iloc[5] = np.nan
        ModelZoo._attach_common_oos({"perfect": reports["perfect"],
                                     "broken": broken})
        block = broken.extra["common_oos_uncertainty"]
        self.assertIsNone(block["point_estimate"])
        self.assertIsNone(block["distinguishable"])
        self.assertIn("非有限值", block["note"])

    def test_uncertainty_flows_into_model_payload(self):
        from market_research.funds.payloads import model_payload
        reports, actual = _reports()
        ModelZoo._attach_common_oos(reports)
        source = reports["noisy"]
        source.extra.update({"assessment_status": "sufficient", "oos_n": 300,
                             "rebalance_count": 10})
        rep = ModelReport(key="noisy", name="noisy", family="linear", desc="",
                          interpretable=True, oos_r2=0.5, oos_te=0.1,
                          oos_ic=0.9, win=0.55, beta=0.9, vol_ratio=1.0,
                          fit_r2_mean=0.7, fit_te_mean=0.1,
                          latest_weights=pd.Series(dtype=float),
                          weights_last_date=None, weights_hist=pd.DataFrame(),
                          syn_ret=source.syn_ret, extra=source.extra,
                          elapsed=0.0, fit_diag=pd.DataFrame(), oos_y=actual)
        nav = pd.Series(np.exp(np.cumsum(actual.values)), index=actual.index)
        assessment = model_payload(rep, nav)["assessment"]
        self.assertIn("common_oos_uncertainty", assessment)
        self.assertEqual(assessment["common_oos_uncertainty"]["reference"],
                         "perfect")
        self.assertIs(assessment["common_oos_uncertainty"]["distinguishable"],
                      False)


if __name__ == "__main__":
    unittest.main()
