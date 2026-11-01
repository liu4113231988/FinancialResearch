# -*- coding: utf-8 -*-
"""报告渲染的回归：结论文字必须跟随数据，权重和不能写死。

对应 TODO「全量功能/逻辑/设计复查（2026-11-01）」的 P0-2 / P0-3 / P1-2。
用最小 main 字典渲染，不依赖网络、缓存或 akshare。
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from market_research.research.report import build_report


def _main(tc_ret=-0.031, sumw=0.55):
    idx = pd.bdate_range("2021-01-04", periods=300)
    nav = pd.Series(np.linspace(1.0, 1.30, len(idx)), index=idx)
    syn = pd.Series(np.linspace(1.0, 1.25, len(idx)), index=idx)
    exposure = pd.DataFrame({
        "名称": ["沪深300", "食品饮料"],
        "层级": ["宽基", "行业"],
        "权重": [0.30, sumw - 0.30],
    })
    metrics = pd.DataFrame(
        {"年化收益": [0.06, 0.08, 0.05],
         "夏普": [0.50, 0.70, 0.40],
         "最大回撤": [-0.20, -0.25, -0.21],
         "年化波动": [0.20, 0.20, 0.20]},
        index=["择时策略(融合)", "买入持有(100%)", "恒定仓位对照"])
    groups = pd.DataFrame({"平均前瞻收益": [-0.002, -0.001, 0.0, 0.001, 0.002]})
    return {
        "name": "测试基金", "code": "110022",
        "nav": nav, "syn_nav": syn,
        "fit_diag": pd.DataFrame({"r2": [0.90, 0.92], "te": [0.04, 0.05]}),
        "exposure": exposure,
        "snapshot": {"趋势分": 0.1, "偏离分": -0.1},
        "style_drift": {},
        "backtest": {
            "metrics": metrics,
            "control_name": "恒定仓位对照",
            "timing_contrib": {"年化收益": tc_ret, "夏普": -0.2, "最大回撤": 0.01},
            "groups": groups,
            "win_long": 0.52, "ic": 0.01, "icir": 0.10, "turnover": 9.5,
            "avg_position": 0.60,
        },
    }


def _render(main, experiments=None, summary=None):
    with tempfile.TemporaryDirectory() as d:
        p = build_report(main, summary if summary is not None else pd.DataFrame(),
                         experiments or {}, {}, path=Path(d) / "r.html")
        return p.read_text(encoding="utf-8")


class ReportConclusionTest(unittest.TestCase):
    def test_losing_case(self):
        html = _render(_main(tc_ret=-0.031))
        self.assertIn("策略<b>跑输</b>恒定仓位对照", html)
        self.assertNotIn("策略<b>跑赢</b>", html)

    def test_winning_case(self):
        html = _render(_main(tc_ret=0.052))
        self.assertIn("策略<b>跑赢</b>恒定仓位对照", html)
        self.assertIn("+5.20%", html)
        # 领先时也不能继续断言"全部差异都来自仓位水平"
        self.assertNotIn("全部差异都来自「仓位水平」", html)

    def test_missing_is_not_losing(self):
        html = _render(_main(tc_ret=float("nan")))
        self.assertIn("无法计算", html)
        self.assertNotIn("策略<b>跑输</b>", html)
        self.assertNotIn("多数基金跑输", html)

    def test_weight_sum_is_computed(self):
        html = _render(_main(sumw=0.55))
        self.assertIn("权重和 <b>55%</b>", html)
        self.assertNotIn("权重和为股票仓位假设 0.90", html)
        self.assertNotIn("Σw<sub>i</sub> = 0.90", html)

    def test_stable_style_uses_existing_css_class(self):
        main = _main()
        main["style_drift"] = {"ok": True, "stable": True, "verdict": "风格稳定"}
        html = _render(main)
        # 旧代码写 class="lead good"，CSS 里只有 .lead.ok，稳定态等于没有样式
        self.assertIn('class="lead ok"', html)
        self.assertNotIn('class="lead good"', html)
        self.assertIn("风格稳定", html)

    def test_e3_combo_count_not_row_count(self):
        exp = {"_e3_stat": {"n": 108, "combos": 27, "pos": 0.5,
                            "mean": -0.1, "median": -0.12}}
        summary = pd.DataFrame({"净贡献_夏普": [-0.20, -0.10],
                                "RankIC": [0.01, -0.02]})
        html = _render(_main(), exp, summary)
        self.assertIn("27 组参数组合", html)
        self.assertIn("27 组参数", html)      # 方法/局限章节同样不能写死 108
        self.assertNotIn("108 组参数", html)


if __name__ == "__main__":
    unittest.main()
