"""B2/P3-R：样本外覆盖与评估资格（只标注，不改变排名顺序）。"""
import unittest

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
        from market_research.modeling.engine import _failed_report
        failed = _failed_report("pcr", ValueError("x"))
        self.assertIn("拟合失败", ModelZoo.blocking_reasons(failed))

        _, rep = self._report(max_rebal=5)
        rep.extra["causal_pool_used"] = False
        rep.extra["causal_required"] = True
        self.assertIn("未使用因果面板", ModelZoo.blocking_reasons(rep))
        rank = ModelZoo.rank({"ridge": rep})
        self.assertEqual(rank.iloc[0]["可推荐"], "否")
        self.assertIn("未使用因果面板", rank.iloc[0]["阻断原因"])


if __name__ == "__main__":
    unittest.main()
