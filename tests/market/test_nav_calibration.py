# -*- coding: utf-8 -*-
"""总回报校准的因果性回归。

对应 TODO「全量功能/逻辑/设计复查（2026-11-01）」的 P1-15：
旧实现对相邻锚点做 `interpolate(method="time")`，t 日的缩放系数会用到未来锚点。
"""
import unittest

import numpy as np
import pandas as pd

from market_research.data.providers.funds import calibrate_total_return


def _setup(anchor80_shift=0.0):
    idx = pd.bdate_range("2022-01-03", periods=120)
    # 单位净值按 0.1%/日 匀速上涨
    unit = pd.Series(1.001 ** np.arange(len(idx)), index=idx)
    # 第 80 天起累计净值多出 5%（模拟数据源口径差异），制造非 1 的缩放系数
    cum = unit * np.where(np.arange(len(idx)) >= 80, 1.05, 1.0)
    positions = list(range(20, 120, 20))          # 20/40/60/80/100
    truth = 1.001 ** np.array(positions) - 1.0
    truth[-1] += anchor80_shift                    # 只改最后一个锚点（未来信息）
    anchors = pd.Series(truth * 100.0, index=idx[positions])
    return cum, unit, anchors


class CalibrationCausalityTest(unittest.TestCase):
    def test_future_anchor_does_not_change_past(self):
        cum1, unit1, a1 = _setup()
        cum2, unit2, a2 = _setup(anchor80_shift=0.10)
        f1 = calibrate_total_return(cum1, unit1, a1)
        f2 = calibrate_total_return(cum2, unit2, a2)
        self.assertGreater(len(f1), 50)

        # 只改最后一个锚点；在它之前的所有日期必须完全不受影响
        # （前向填充只用"当时已公布"的锚点；旧的 interpolate 会提前泄漏）。
        cutoff = a1.index[-2]
        window = f1.loc[:cutoff]
        other = f2.loc[window.index]
        self.assertGreater(len(window), 50)
        self.assertLess(float((window - other).abs().max()), 1e-9)

    def test_drops_days_before_first_anchor(self):
        cum, unit, anchors = _setup()
        out = calibrate_total_return(cum, unit, anchors)
        # 首个锚点之前没有"当时可知"的系数，旧实现会 bfill 未来锚点
        self.assertGreaterEqual(out.index[0], anchors.index[0])

    def test_hits_anchor_values(self):
        cum, unit, anchors = _setup()
        out = calibrate_total_return(cum, unit, anchors)
        for d, v in anchors.items():
            got = float(out.loc[:d].iloc[-1])
            self.assertAlmostEqual(got / (1.0 + v / 100.0), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
