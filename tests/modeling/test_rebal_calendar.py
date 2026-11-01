# -*- coding: utf-8 -*-
"""Web 重估点抽稀的前缀稳定性（P1 修复轮关闭项）。

背景：linspace 抽稀按样本总长度采样，追加数据会让历史重估点位移
（实测 52/60 位移），历史输出被未来数据改写。calendar 模式锚点只由
日期决定；限流改为"保留最新 max_rebal 个锚点、从头部丢弃"后，
已选过的历史点不会被挤出（只会新增），同样前缀稳定。
"""
import unittest

import pandas as pd

import market_research.config as C
from market_research.modeling.engine import ModelZoo


class RebalCalendarTests(unittest.TestCase):
    W = C.RBSA["window"]
    REFIT = C.RBSA["refit"]

    def test_web_default_is_calendar(self):
        self.assertEqual(C.RBSA["rebal_mode"], "calendar")

    def test_calendar_thinning_only_drops_oldest_points(self):
        mr = 30                      # 季度锚点 ~41 个 → 限流必然生效
        dates0 = pd.bdate_range("2010-01-04", periods=2600)
        dates1 = pd.bdate_range("2010-01-04", periods=2700)   # 追加 ~1 季度
        a = ModelZoo._rebal_calendar(len(dates0), self.W, self.REFIT, mr, dates0)
        b = ModelZoo._rebal_calendar(len(dates1), self.W, self.REFIT, mr, dates1)
        self.assertLessEqual(len(a), mr)
        self.assertLessEqual(len(b), mr)
        common = sorted(set(a) & set(b))
        # 保留下来的历史点一个都没有位移；被丢弃的只可能是 a 的最老一段
        self.assertEqual(common, a[len(a) - len(common):])
        # 未超限流时不丢点：追加数据只会新增重估点
        full0 = ModelZoo._rebal_calendar(len(dates0), self.W, self.REFIT, None, dates0)
        full1 = ModelZoo._rebal_calendar(len(dates1), self.W, self.REFIT, None, dates1)
        self.assertEqual(full0, full1[:len(full0)])

    def test_linspace_thinning_drifts_for_contrast(self):
        mr = 30
        dates0 = pd.bdate_range("2010-01-04", periods=2600)
        dates1 = pd.bdate_range("2010-01-04", periods=2700)
        a = ModelZoo._rebal_idx(len(dates0), self.W, self.REFIT, mr)
        b = ModelZoo._rebal_idx(len(dates1), self.W, self.REFIT, mr)[:len(a)]
        drift = sum(1 for x, y in zip(a, b) if x != y)
        self.assertGreater(drift, 0)      # 对照：linspace 限流下历史点位移


if __name__ == "__main__":
    unittest.main()
