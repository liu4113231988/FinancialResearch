# -*- coding: utf-8 -*-
"""波动率目标预热口径：预热用中性 0.5，就绪后只在 [min, 1.0] 内缩放。"""
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import market_research
import market_research.config as C
from market_research.modeling.signals import SignalEngine


def _syn(n=320, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.Series(np.exp(np.cumsum(rng.normal(0.0004, 0.012, n))), index=idx)


class VolTargetWarmupTests(unittest.TestCase):
    def test_warmup_is_config_neutral_half(self):
        self.assertEqual(C.SIGNAL["vol_target_warmup"], 0.5)
        self.assertLess(C.SIGNAL["vol_target_warmup"], C.SIGNAL["vol_target_max"])
        pos = SignalEngine().vol_target_channel(_syn())["vol_target_pos"]
        # 前 60 日窗口尚不足 min_periods=30 之后的最初一段时间必须是 0.5，
        # 不能是 1.0（旧实现），也不能是 NaN（会被下游静默补值）
        self.assertTrue(pos.iloc[:29].eq(0.5).all())

    def test_ready_positions_stay_within_limits(self):
        pos = SignalEngine().vol_target_channel(_syn())["vol_target_pos"]
        ready = pos.iloc[80:]
        self.assertTrue(np.isfinite(pos).all())
        self.assertLessEqual(float(ready.max()), C.SIGNAL["vol_target_max"] + 1e-12)
        self.assertGreaterEqual(float(ready.min()), C.SIGNAL["vol_target_min"] - 1e-12)
        # 顺波动模式（高波动减仓）确实在缩放；逆波动模式常被 1.0 上限截断
        engine = SignalEngine()
        engine.cfg = dict(C.SIGNAL, vol_target_mode="target")
        scaled = engine.vol_target_channel(_syn())["vol_target_pos"]
        self.assertGreater(scaled.iloc[80:].nunique(), 1)
        self.assertLessEqual(float(scaled.max()), C.SIGNAL["vol_target_max"] + 1e-12)

    def test_reindex_uses_same_warmup(self):
        syn = _syn()
        idx = pd.bdate_range(end=syn.index[-1], periods=len(syn) + 40)
        pos = SignalEngine().vol_target_channel(syn, idx=idx)["vol_target_pos"]
        head = pos.loc[pos.index < syn.index[0]]
        self.assertTrue(head.eq(C.SIGNAL["vol_target_warmup"]).all())

    def test_pipeline_and_experiments_read_same_constant(self):
        """两条消费路径都必须读同一常量，且不再各自硬编码预热值。

        旧的缺陷正是 pipeline 填 0.5、experiments 填 1.0：同一条"波动率目标"
        曲线在两个入口口径不同。这里直接盯住消费点，防止再次分叉。
        """
        root = Path(market_research.__file__).resolve().parent
        for rel in ("funds/pipeline.py", "research/experiments.py"):
            src = (root / rel).read_text(encoding="utf-8")
            self.assertIn('SIGNAL["vol_target_warmup"]', src, msg=rel)
            self.assertNotIn(".fillna(1.0)", src, msg=rel)

    def test_warmup_is_clipped_into_position_limits(self):
        engine = SignalEngine()
        engine.cfg = dict(C.SIGNAL, vol_target_warmup=2.0)
        pos = engine.vol_target_channel(_syn())["vol_target_pos"]
        self.assertLessEqual(float(pos.iloc[:29].max()), C.SIGNAL["vol_target_max"])


if __name__ == "__main__":
    unittest.main()
