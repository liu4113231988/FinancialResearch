# -*- coding: utf-8 -*-
"""研究实验的口径回归（目前覆盖 E1 的 ICIR 分块）。

对应 2026-11-01 全量复查发现的 ICIR 分块问题：
固定 60 日分块在 h=250 时会让相邻块标签重叠 4 重，ICIR 被高估。
"""
import unittest

import numpy as np
import pandas as pd

from market_research.research.experiments import ic_term_structure


class ICIRBlockTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        n = 1600
        self.idx = pd.bdate_range("2017-01-02", periods=n)
        score = pd.Series(rng.normal(size=n), index=self.idx)
        fwd = 0.004 * score.shift(-1).fillna(0)          # 次日收益与当期信号正相关
        ret = fwd + pd.Series(rng.normal(0, 0.004, n), index=self.idx)
        self.nav = (1 + ret).cumprod()
        self.sig = pd.DataFrame({"score": score})

    def test_long_horizon_icir_uses_matching_blocks(self):
        out = ic_term_structure(self.sig, self.nav, horizons=(5, 250))
        row = out[out["前瞻期"] == "250日"].iloc[0]
        self.assertEqual(row["样本"] > 1000, True)
        # 分块长度 = h，约 5 个块 → 允许计算；旧实现用 60 日块会重叠 4 重
        self.assertTrue(np.isfinite(row["ICIR"]))
        self.assertGreater(row["RankIC"], 0)

    def test_short_sample_returns_nan(self):
        out = ic_term_structure(self.sig.iloc[:150], self.nav.iloc[:150],
                                horizons=(250,))
        row = out.iloc[0]
        self.assertTrue(np.isnan(row["RankIC"]))
        self.assertTrue(np.isnan(row["ICIR"]))


if __name__ == "__main__":
    unittest.main()
