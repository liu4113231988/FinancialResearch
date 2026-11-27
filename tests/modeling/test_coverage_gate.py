"""B2/P3-R：样本外覆盖与评估资格（只标注，不改变排名顺序）。"""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.modeling.engine import (MIN_OOS_SESSIONS, MIN_REFITS,
                                             ModelReport, ModelZoo)


def _panel(n=600, seed=11):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n)
    returns = rng.normal(0, 0.01, (n, 4))
    prices = np.exp(np.cumsum(returns, axis=0))
    frame = pd.DataFrame(prices,
                         columns=["sh000300", "801120", "801080", "000905"],
                         index=dates)
    frame["__FUND__"] = 100 * np.exp(
        np.cumsum(returns @ [0.3, 0.3, 0.2, 0.1]))
    return frame


def _cfg():
    return {**C.RBSA, "window": 100, "refit": 20, "min_obs": 60,
            "auto_equity": False, "rebal_mode": "linspace"}


class CoverageGateTests(unittest.TestCase):
    def _report(self, max_rebal):
        zoo = ModelZoo(_cfg())
        reports = zoo.run(_panel(), ["ridge"], workers=1, max_rebal=max_rebal)
        return zoo, reports["ridge"]

    def test_sufficient_coverage_is_observable(self):
        _, rep = self._report(max_rebal=5)
        self.assertGreaterEqual(rep.extra["oos_n"], MIN_OOS_SESSIONS)
        self.assertGreaterEqual(rep.extra["rebalance_count"], MIN_REFITS)
        self.assertEqual(rep.extra["assessment_status"], "sufficient")
        self.assertEqual(rep.extra["assessment_reasons"], [])
        self.assertIsNotNone(rep.extra["oos_start"])
        self.assertIsNotNone(rep.extra["oos_end"])

    def test_single_rebalance_is_marked_insufficient_not_failed(self):
        _, rep = self._report(max_rebal=1)
        self.assertTrue(np.isfinite(rep.oos_r2))
        self.assertEqual(rep.extra["rebalance_count"], 1)
        self.assertEqual(rep.extra["assessment_status"], "insufficient")
        self.assertTrue(any("重估" in reason
                            for reason in rep.extra["assessment_reasons"]))

    def test_documented_400_row_ridge_and_pcr_run_has_seven_paired_days(self):
        panel = _panel(n=400, seed=42)
        zoo = ModelZoo({**C.RBSA, "auto_equity": False})
        reports = zoo.run(panel, ["ridge", "pcr"], workers=1, max_rebal=1)
        for key, rep in reports.items():
            with self.subTest(model=key):
                self.assertTrue(np.isfinite(rep.oos_r2))
                self.assertEqual(rep.extra["oos_n"], 7)
                self.assertEqual(rep.extra["oos_start"], "2023-07-06")
                self.assertEqual(rep.extra["oos_end"], "2023-07-14")
                self.assertEqual(rep.extra["rebalance_count"], 1)
                self.assertEqual(rep.extra["oos_coverage"], 1.0)
                self.assertEqual(rep.extra["assessment_status"], "insufficient")
        rank = zoo.rank(reports)
        self.assertEqual(rank["可推荐"].tolist(), ["否", "否"])

    def test_counts_only_finite_pairs_and_windows_with_pairs(self):
        dates = pd.bdate_range("2024-01-01", periods=165)
        x = np.random.default_rng(23).normal(0, 0.01, (len(dates), 1))
        y = 0.9 * x[:, 0]
        y[100] = np.nan
        y[104] = -np.inf
        x[107] = np.nan
        y[160:] = np.inf
        zoo = ModelZoo(_cfg())
        with np.errstate(invalid="ignore"):
            rep = zoo._run_one("ridge", x, y, ["a"], dates, [100, 120, 160], None)
        self.assertEqual(rep.extra["oos_n"], 57)
        self.assertEqual(rep.extra["oos_start"], dates[101].strftime("%Y-%m-%d"))
        self.assertEqual(rep.extra["oos_end"], dates[159].strftime("%Y-%m-%d"))
        self.assertEqual(rep.extra["oos_gap_n"], 8)
        self.assertAlmostEqual(rep.extra["oos_coverage"], 57 / 65)
        self.assertEqual(rep.extra["rebalance_count"], 2)
        self.assertEqual(rep.extra["assessment_status"], "insufficient")
        self.assertEqual(rep.extra["assessment_reasons"], [
            "样本外仅 57 个交易日，低于门槛 60",
            "有效重估窗口仅 2 个，低于门槛 3",
        ])
        self.assertEqual(len(rep.syn_ret), 64)
        self.assertEqual(len(rep.fit_diag), 3)
        self.assertAlmostEqual(rep.oos_r2, 1.0)
        self.assertEqual(zoo.rank({"ridge": rep}).iloc[0]["可推荐"], "是")

    def test_assessment_threshold_boundaries_and_negative_r2(self):
        from market_research.modeling.engine import _coverage_summary

        dates = pd.bdate_range("2024-01-01", periods=61)
        for n in (59, 60, 61):
            for refits in (2, 3):
                with self.subTest(paired_days=n, refits=refits):
                    assessment = _coverage_summary(dates[:n], refits, 61, -0.5)
                    sufficient = n >= 60 and refits >= 3
                    self.assertEqual(assessment["assessment_status"],
                                     "sufficient" if sufficient else "insufficient")
                    self.assertEqual(len(assessment["assessment_reasons"]),
                                     (n < 60) + (refits < 3))

    def test_constant_target_has_coverage_but_no_defined_assessment_score(self):
        panel = _panel(n=200)
        panel["__FUND__"] = 1.0
        rep = ModelZoo(_cfg()).run(panel, ["ridge"], workers=1)["ridge"]
        self.assertEqual(rep.extra["oos_n"], 99)
        self.assertEqual(rep.extra["oos_coverage"], 1.0)
        self.assertEqual(rep.extra["rebalance_count"], 5)
        self.assertTrue(np.isnan(rep.oos_r2))
        self.assertEqual(rep.extra["assessment_status"], "insufficient")
        self.assertEqual(rep.extra["assessment_reasons"], ["样本外 R² 未定义"])

    def test_rank_keeps_r2_order_and_exposes_status(self):
        _, sufficient = self._report(max_rebal=5)
        _, insufficient = self._report(max_rebal=1)
        rank = ModelZoo.rank({"sufficient": sufficient,
                              "insufficient": insufficient})
        self.assertIn("样本外天数", rank.columns)
        self.assertIn("有效重估", rank.columns)
        status = dict(zip(rank["key"], rank["评估状态"]))
        self.assertEqual(status["insufficient"], "不足")
        self.assertEqual(status["sufficient"], "有效")
        recommended = dict(zip(rank["key"], rank["可推荐"]))
        self.assertEqual(recommended["insufficient"], "否")
        self.assertEqual(recommended["sufficient"], "是")
        # 排名仍按样本外 R² 降序，资格标注不改变顺序。
        r2 = list(rank["样本外R²"])
        self.assertEqual(r2, sorted(r2, reverse=True))

    def test_minimum_fit_is_w_plus_30_and_marks_insufficient(self):
        cfg = {**C.RBSA, "window": 100, "refit": 20, "min_obs": 60,
               "auto_equity": False, "rebal_mode": "linspace"}
        rep = ModelZoo(cfg).run(_panel(n=131), ["ridge"], workers=1,
                                max_rebal=1)["ridge"]
        self.assertEqual(rep.extra["oos_n"], 30)
        self.assertEqual(rep.extra["assessment_status"], "insufficient")
        with self.assertRaisesRegex(ValueError, "至少需要 100\\+30"):
            ModelZoo(cfg).run(_panel(n=130), ["ridge"], workers=1,
                              max_rebal=1)

    def test_common_oos_intersection_and_short_overlap_blocker(self):
        idx = pd.bdate_range("2022-01-03", periods=300)
        y = pd.Series(np.linspace(-0.01, 0.01, len(idx)), index=idx)
        base = dict(name="m", family="线性", desc="", interpretable=True,
                    oos_r2=0.9, oos_te=0.1, oos_ic=1.0, win=0.6, beta=0.9,
                    vol_ratio=1.0, fit_r2_mean=0.9, fit_te_mean=0.1,
                    latest_weights=pd.Series(dtype=float),
                    weights_last_date=None, weights_hist=pd.DataFrame(),
                    extra={"assessment_status": "sufficient"}, elapsed=0.0)
        long = ModelReport(key="long", syn_ret=y.copy(), oos_y=y, **base)
        short = ModelReport(key="short", syn_ret=y.iloc[:40].copy(),
                            oos_y=y.iloc[:40], **base)
        ModelZoo._attach_common_oos({"long": long, "short": short})
        self.assertEqual(long.extra["common_oos_n"], 40)
        self.assertEqual(short.extra["common_oos_n"], 40)
        self.assertAlmostEqual(long.extra["common_oos_r2"], 1.0)
        self.assertIn("跨模型共同 OOS 不足",
                      ModelZoo.blocking_reasons(short))

    def test_failed_and_non_causal_reports_are_not_recommended(self):
        zoo = ModelZoo({**C.RBSA, "auto_equity": False})
        with patch("market_research.modeling.engine.fit_window", side_effect=ValueError("x")):
            failed = zoo.run(_panel(n=400), ["pcr"], workers=1, max_rebal=1)["pcr"]
        self.assertIn("拟合失败", ModelZoo.blocking_reasons(failed))
        self.assertEqual(failed.extra["assessment_status"], "failed")
        self.assertEqual(failed.extra["assessment_reasons"], ["ValueError: x"])
        self.assertEqual(failed.extra["oos_n"], 0)
        self.assertIsNone(failed.extra["oos_start"])
        self.assertIsNone(failed.extra["oos_end"])
        self.assertEqual(failed.extra["oos_gap_n"], 7)
        self.assertEqual(failed.extra["oos_coverage"], 0.0)
        self.assertEqual(failed.extra["rebalance_count"], 0)

        _, rep = self._report(max_rebal=5)
        rep.extra["causal_pool_used"] = False
        rep.extra["causal_required"] = True
        self.assertIn("未使用因果面板", ModelZoo.blocking_reasons(rep))
        rank = ModelZoo.rank({"ridge": rep})
        self.assertEqual(rank.iloc[0]["可推荐"], "否")
        self.assertIn("未使用因果面板", rank.iloc[0]["阻断原因"])


if __name__ == "__main__":
    unittest.main()
