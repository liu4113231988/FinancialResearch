# -*- coding: utf-8 -*-
"""退化样本的回归：必须返回"无结论"，不能伪装成负结论。

对应 2026-11-01 全量复查发现的退化样本问题（必须返回"无结论"）。
"""
import unittest

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.research.robustness import (
    _label_columns, block_bootstrap, effective_sample_size)


class EffectiveSampleSizeTest(unittest.TestCase):
    def test_early_return_has_all_keys(self):
        idx = pd.bdate_range("2024-01-01", periods=40)
        one = pd.DataFrame({"a": np.zeros(len(idx))}, index=idx)
        out = effective_sample_size(one)
        self.assertFalse(out["ok"])
        for key in ("n_funds", "n_days", "n_eff", "n_eff_ratio",
                    "pc1_share", "avg_corr"):
            self.assertIn(key, out)
            if key in ("n_eff", "n_eff_ratio", "pc1_share", "avg_corr"):
                self.assertTrue(np.isnan(out[key]))

    def test_short_overlap_has_all_keys(self):
        idx = pd.bdate_range("2024-01-01", periods=30)
        two = pd.DataFrame({"a": np.arange(30.0), "b": np.arange(30.0) * 2},
                           index=idx)
        out = effective_sample_size(two)
        self.assertFalse(out["ok"])
        self.assertIn("avg_corr", out)


class BlockBootstrapTest(unittest.TestCase):
    def test_short_sample_returns_nan_not_signal(self):
        idx = pd.bdate_range("2024-01-01", periods=120)
        rng = np.random.default_rng(3)
        d = pd.DataFrame({"a": rng.normal(0, 0.01, 120),
                          "b": rng.normal(0, 0.01, 120)}, index=idx)
        out = block_bootstrap(d, block=250, n_boot=200)
        # n=120 < 3×250：p 值必须是 NaN，且结构完整，不能抛 KeyError
        self.assertTrue(np.isnan(out["p_value"]))
        for key in ("share_pos", "boot_mean", "boot_p05", "boot_p95"):
            self.assertIn(key, out)


class LabelColumnsTest(unittest.TestCase):
    def test_ac_shares_do_not_collide(self):
        # 构造同一基金 A/C 两个代码、简称相同的场景
        codes = list(C.EXTENDED_FUNDS.keys())[:2]
        if len(codes) < 2:
            self.skipTest("扩池样本不足")
        code_a, code_b = codes[0], codes[1]
        original = C.EXTENDED_FUNDS[code_b]
        C.EXTENDED_FUNDS[code_b] = C.EXTENDED_FUNDS[code_a]
        try:
            df = pd.DataFrame({code_a: [0.1], code_b: [0.2]})
            out = _label_columns(df)
            self.assertEqual(len(set(out.columns)), 2)
            self.assertTrue(any(c.endswith(f"({code_b})") for c in out.columns))
        finally:
            C.EXTENDED_FUNDS[code_b] = original


if __name__ == "__main__":
    unittest.main()
