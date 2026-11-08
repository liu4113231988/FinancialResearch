# -*- coding: utf-8 -*-
"""报告渲染的回归：结论文字必须跟随数据，权重和不能写死。

对应 2026-11-01 全量复查发现的报告硬编码问题：结论措辞、权重和、E3 组合数。
用最小 main 字典渲染，不依赖网络、缓存或 akshare。
"""
import os
import base64
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research.research.commands.report import validate_publish
from market_research.research.report import build_report
from market_research.funds.payloads import holdings_payload


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


def _render(main, experiments=None, summary=None, extra_meta=None):
    with tempfile.TemporaryDirectory() as d:
        p = build_report(main, summary if summary is not None else pd.DataFrame(),
                         experiments or {}, {}, path=Path(d) / "r.html",
                         extra_meta=extra_meta or {})
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


class NavBasisRenderTests(unittest.TestCase):
    """P1-20：报告顶部必须标出净值口径，降级时带 data_basis 与原因。"""

    def test_total_basis_is_visible(self):
        html = _render(_main(), extra_meta={
            "nav_basis": "total",
            "data_basis": "事后月度锚点校准；尚非历史时点可得数据"})
        self.assertIn("净值口径：<b>总回报（分红再投资）</b>", html)
        self.assertIn("事后月度锚点校准", html)

    def test_degraded_cum_basis_is_warned(self):
        html = _render(_main(), extra_meta={
            "nav_basis": "cum", "nav_degraded": True,
            "data_basis": "累计净值（total 构造失败，已降级）；不可按总回报解读",
            "nav_degraded_reason": "total 净值构造失败"})
        self.assertIn("净值口径：累计净值（降级）", html)
        self.assertIn("不可按总回报解读", html)


class DisclosureTests(unittest.TestCase):
    """P2-36：数据披露在报告首页显著位置，且文案可配置。"""

    def test_banner_precedes_hero_and_states_limits(self):
        html = _render(_main())
        banner = html.find('class="disclosure"')
        hero = html.find('<header class="hero">')
        self.assertGreater(banner, 0)
        self.assertLess(banner, hero)
        self.assertIn("公开抓取", html)
        self.assertIn("不完整", html)
        self.assertIn("不构成投资建议", html)

    def test_text_override_is_used(self):
        with patch.dict(os.environ, {"FIL_DISCLOSURE_TEXT": "自定义披露文案"},
                        clear=False):
            html = _render(_main())
        self.assertIn("自定义披露文案", html)
        self.assertIn("数据与使用声明", html)


class PublishGateTests(unittest.TestCase):
    """P2-32：发布前校验关键表格与窗口一致性。"""

    def _main(self, days=1000, status="ok"):
        return {
            "backtest": {"metrics": pd.DataFrame({"样本天数": [days]})},
            "completeness": {"status": status, "reasons": ["全部模型失败"]},
        }

    def _tables(self, days=1000, missing=None):
        tables = {
            "跨基金汇总": pd.DataFrame({"样本天数": [days, days - 200]}),
            "E1_IC期限结构": pd.DataFrame({"信号": ["x"], "样本": [100]}),
            "E2_通道消融": pd.DataFrame({"样本天数": [days]}),
            "E3_参数敏感性": pd.DataFrame({"x": [1]}),
            "E4_周期匹配": pd.DataFrame({"x": [1]}),
        }
        if missing:
            tables[missing] = pd.DataFrame()
        return tables

    def test_consistent_inputs_pass(self):
        tables = self._tables()
        self.assertEqual(
            validate_publish(self._main(), tables["跨基金汇总"], tables), [])

    def test_missing_table_refuses(self):
        tables = self._tables(missing="E2_通道消融")
        problems = validate_publish(self._main(), tables["跨基金汇总"], tables)
        self.assertTrue(any("E2_通道消融" in p for p in problems))

    def test_window_mismatch_refuses(self):
        tables = self._tables(days=2000)
        problems = validate_publish(self._main(), tables["跨基金汇总"], tables)
        self.assertTrue(any("窗口不一致" in p for p in problems))

    def test_incomplete_main_case_refuses(self):
        tables = self._tables()
        problems = validate_publish(self._main(status="failed"),
                                    tables["跨基金汇总"], tables)
        self.assertTrue(any("failed" in p for p in problems))


def _holdings_out():
    return {
        "ok": True, "code": "110022", "name": "易方达消费行业",
        "n_periods": 3, "mad_mean": 0.081, "corr_mean": 0.62,
        "corr_first": 0.55, "corr_last": 0.68, "corr_valid": 3,
        "broad_exposure": 0.11, "unmapped_mean": 0.12,
        "coverage_mean": 0.78, "dividend_yield": 0.02, "elapsed": 1.2,
        "broad_note": "宽基暴露约 11%，单列报告",
        "coverage_note": "未映射与现金已拆开",
        "periods": pd.DataFrame({
            "期数": ["2024Q2", "2024Q4", "2025Q2"],
            "权重相关": [0.55, 0.63, 0.68],
            "平均绝对偏差": [0.09, 0.08, 0.07],
        }),
        "by_industry": pd.DataFrame(
            {"食品饮料": [52.0], "银行": [20.0]}, index=["2024Q2"]),
        "detail": pd.DataFrame({
            "期数": ["2024Q2"], "行业": ["食品饮料"], "权重": [52.0],
        }),
        "compare": {
            "ok": True, "reason": None, "n_days": 240,
            "stats": pd.DataFrame({
                "基准": ["基金实际", "RBSA合成基准", "持仓基准"],
                "信息比率IR": [0.0, 0.31, 0.42],
            }),
        },
        "benchmark": {
            "available": True, "missing_industries": ["801780", "801950"],
            "residual_treatment": "缺失行业权重按 0 收益处理，不按现金计息",
            "note": "", "diagnostics": pd.DataFrame(
                {"季末": ["2024Q2"], "面板缺失权重": [12.0]}),
        },
    }


class HoldingsSectionRenderTests(unittest.TestCase):
    """P3-C：报告 3.6 必须按真实 payload 渲染，而不是只挂占位标题。"""

    def test_holdings_section_renders_real_numbers_and_warnings(self):
        payload = holdings_payload(_holdings_out())
        html = _render(_main(), extra_meta={"holdings": payload})
        self.assertIn("3.6 真实持仓拟合验证", html)
        # 汇总指标来自 payload，而不是写死的文案
        self.assertIn("可用期数 <b>3</b>", html)
        self.assertIn("<b>8.1%</b>", html)                   # mad_mean
        self.assertIn("<b>0.62</b>", html)                   # corr_mean
        self.assertIn("首期 0.55 → 末期 0.68", html)
        self.assertIn("<b>78%</b>", html)                    # coverage_mean
        # 期数表与比较表真实渲染
        self.assertIn("2024Q2", html)
        self.assertIn("信息比率IR", html)
        self.assertIn("持仓基准", html)
        # 行业面板缺失必须告警，并写明按 0 收益处理
        self.assertIn("行业面板缺失 2 个持仓行业", html)
        self.assertIn("801780", html)
        self.assertIn("801950", html)
        self.assertIn("持仓基准口径：缺失行业权重按 0 收益处理", html)
        self.assertIn("未映射权重是抓不到行业的股票仓位，不是现金", html)
        self.assertIn("面板缺失权重", html)

    def test_unavailable_holdings_is_not_silently_omitted(self):
        html = _render(_main(), extra_meta={
            "holdings": {"ok": False, "reason": "共同行业不足 60 个交易日"}})
        self.assertIn("3.6 真实持仓拟合验证", html)
        self.assertIn("未生成：共同行业不足 60 个交易日", html)
        self.assertIn("不代表持仓拟合通过", html)


class ReportIconTests(unittest.TestCase):
    """报告是本地 HTML，图标必须以 data URI 内嵌，图标声明先于标题。"""

    def test_report_head_embeds_decodable_icons(self):
        html = _render(_main())
        icon = html.find('rel="icon"')
        title = html.find("<title>")
        self.assertGreater(icon, -1)
        self.assertLess(icon, title)

        svg = re.search(r'href="data:image/svg\+xml;base64,([^"]+)"', html)
        png = re.search(r'href="data:image/png;base64,([^"]+)"', html)
        self.assertIsNotNone(svg)
        self.assertIsNotNone(png)
        self.assertTrue(base64.b64decode(svg.group(1)).startswith(b"<svg"))
        self.assertTrue(base64.b64decode(png.group(1)).startswith(b"\x89PNG"))


class PredictionLedgerSectionTests(unittest.TestCase):
    def test_section_has_placeholder_without_payload(self):
        html = _render(_main())
        self.assertIn("预测台账回验", html)
        self.assertIn("预测台账汇总不可用", html)

    def test_section_renders_metrics_and_boundary(self):
        overall = {"current_total": 12, "decided": 8, "hit_rate": 0.625,
                   "brier": 0.2345, "rule_events": 3,
                   "rule_return_mean": 0.012, "pending": 1,
                   "legacy_rows": 2, "note": "方向命中率口径说明"}
        html = _render(_main(), extra_meta={"predictions": {"overall": overall}})
        self.assertIn("预测台账回验", html)
        self.assertIn("62.5%", html)
        self.assertIn("0.2345", html)
        self.assertIn("方向命中率口径说明", html)
        self.assertIn("不可互相印证", html)

    def test_read_failure_reason_is_escaped(self):
        html = _render(_main(), extra_meta={
            "predictions": {"ok": False, "reason": "StorageBusy <锁忙>"}})
        self.assertIn("StorageBusy &lt;锁忙&gt;", html)
        self.assertNotIn("<锁忙>", html)


if __name__ == "__main__":
    unittest.main()
