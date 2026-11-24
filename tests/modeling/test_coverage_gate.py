"""B2/P3-R：样本外覆盖与评估资格（只标注，不改变排名顺序）。"""
import unittest

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.modeling.engine import (MIN_OOS_SESSIONS, MIN_REFITS,
                                             ModelZoo)


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
        # 排名仍按样本外 R² 降序，资格标注不改变顺序。
        r2 = list(rank["样本外R²"])
        self.assertEqual(r2, sorted(r2, reverse=True))


if __name__ == "__main__":
    unittest.main()
