# -*- coding: utf-8 -*-
"""基金结果装载层的回归：残差尺度、披露滞后、择时措辞。

对应 2026-11-01 全量复查发现的残差尺度、披露滞后与择时措辞问题。
这些断言都自带最小复现输入，不依赖网络与 akshare。
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.config as C
import market_research.funds.holdings as H
from market_research.funds.payloads import holdings_payload, model_payload
from market_research.modeling.engine import ModelReport
from market_research.research.report import timing_comparison


def _model_report(idx, syn_ret, level=2.5):
    """构造一个最小可用的 ModelReport（300 个交易日）。"""
    weights = pd.Series({"sh000300": 0.35, "801120": 0.25})
    hist = pd.DataFrame({"sh000300": [0.35, 0.34], "801120": [0.25, 0.26]},
                        index=[idx[0], idx[-1]])
    return ModelReport(
        key="pcr", name="PCR", family="线性", desc="", interpretable=True,
        oos_r2=0.9, oos_te=0.05, oos_ic=0.9, win=0.5, beta=1.0,
        vol_ratio=1.0, fit_r2_mean=0.95, fit_te_mean=0.04,
        latest_weights=weights, weights_last_date=idx[-1],
        weights_hist=hist, syn_ret=syn_ret, extra={}, elapsed=0.1,
    )


class ResidualScaleTest(unittest.TestCase):
    def test_residual_is_normalized_like_curve(self):
        idx = pd.bdate_range("2021-01-04", periods=300)
        rng = np.random.default_rng(7)
        syn = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
        # 基金净值起点刻意设为 2.5：旧实现用未归一净值减从 1 起的基准，
        # 残差会整体抬到 ~2.5，图上全为正。
        nav = 2.5 * np.exp(syn.cumsum()) * np.exp(
            np.cumsum(rng.normal(0, 0.001, len(idx))))
        out = model_payload(_model_report(idx, syn, level=2.5), nav)

        self.assertAlmostEqual(out["curve"]["fund"][0], 1.0, places=6)
        self.assertAlmostEqual(out["curve"]["synth"][0], 1.0, places=6)
        vals = np.array([v for v in out["residual"]["value"] if v is not None])
        self.assertGreater(len(vals), 0)
        # 归一化后残差应围绕 0 波动，而不是停留在净值水平（~2.5）
        self.assertLess(abs(float(vals.mean())), 0.30)
        self.assertLess(float(np.abs(vals).max()), 1.0)


class WeightsPayloadTest(unittest.TestCase):
    def test_subthreshold_items_are_dropped_and_n_counts_positive_weights_only(self):
        from market_research.funds.payloads import _weights_payload
        w = pd.Series({"sh000300": 0.5, "801120": 0.3,
                       "801010": 0.004, "801080": 0.0})
        out = _weights_payload(w)
        self.assertEqual([x["code"] for x in out["items"]], ["sh000300", "801120"])
        self.assertEqual(out["n"], 3)          # 恰好为 0 的项不计入
        # 旧字段 n_zero 名为"零权重数"、实为"低于展示阈值数"，且无任何消费方
        self.assertEqual(set(out), {"items", "sum", "n"})
        self.assertEqual(_weights_payload(None), {"items": [], "sum": 0.0, "n": 0})


class DisclosureLagTest(unittest.TestCase):
    def test_statutory_deadlines(self):
        self.assertEqual(H.disclosure_lag_days("2024-06-30"), 62)   # 半年报 8/31
        self.assertEqual(H.disclosure_lag_days("2024-12-31"), 90)   # 年报次年 3/31
        self.assertEqual(H.disclosure_lag_days("2024-03-31"), 31)   # 季报（前十大）
        self.assertEqual(H.disclosure_lag_days("2024-09-30"), 31)
        # 无法识别的季末仍给兜底值，而不是抛异常
        self.assertEqual(H.disclosure_lag_days("2024-07-15"), H.DISCLOSURE_LAG_DAYS)
        self.assertEqual(H.disclosure_lag_days(None), H.DISCLOSURE_LAG_DAYS)


def _holding_panel(n=10):
    idx = pd.bdate_range("2022-04-01", periods=n)
    return idx, pd.DataFrame({"801120": np.full(len(idx), 1.0)}, index=idx)


def _coverage(mapped, unmapped=0.0):
    total = mapped + unmapped
    return pd.DataFrame({
        "已映射权重": [mapped], "未映射权重": [unmapped],
        "合计": [total], "覆盖率": [mapped / total if total else np.nan],
    }, index=[pd.Timestamp("2021-12-31")])


class HoldingBenchmarkCashTest(unittest.TestCase):
    def _hr(self, weight, coverage="default"):
        idx, panel = _holding_panel()
        exposure = pd.DataFrame({"801120": [weight]},
                                index=[pd.Timestamp("2021-12-31")])
        if isinstance(coverage, pd.DataFrame):
            cov = coverage
        elif coverage == "default":
            cov = _coverage(weight)
        else:
            cov = None
        # 显式 0 股息：这里的断言只关心残差腿，不受 RBSA 配置影响
        return H.holding_benchmark(exposure, panel, cash_rate=0.015,
                                   dividend_yield=0.0, coverage=cov)

    def test_cash_leg_accrues(self):
        hr = self._hr(50.0)
        self.assertGreater(len(hr), 0)
        daily = (1.015 ** (1 / 252) - 1)
        # 50% 明确是现金/债券 → 每日约半份货币收益
        self.assertAlmostEqual(float(hr.iloc[0]), 0.5 * daily, places=9)

    def test_full_exposure_has_no_cash_leg(self):
        hr = self._hr(100.0)
        self.assertAlmostEqual(float(hr.iloc[0]), 0.0, places=12)

    def test_unmapped_residual_does_not_accrue_cash_without_coverage(self):
        """P2-23：没有覆盖率表时，残差不能被当成现金计息。"""
        hr = self._hr(50.0, coverage=None)
        self.assertGreater(len(hr), 0)
        self.assertAlmostEqual(float(hr.iloc[0]), 0.0, places=12)

    def test_unmapped_stock_is_zero_return_not_cash(self):
        """P2-23：明确记账的未映射股票按 0 收益，只有现金/债券腿计息。"""
        hr = self._hr(50.0, coverage=_coverage(50.0, 30.0))  # 合计 80 → 现金 20
        daily = (1.015 ** (1 / 252) - 1)
        self.assertAlmostEqual(float(hr.iloc[0]), 0.2 * daily, places=9)


class HoldingBenchmarkPanelGapTest(unittest.TestCase):
    """P3-B：行业面板缺列必须显式暴露，不能静默转成现金。"""

    def test_missing_industry_is_exposed_and_not_cash(self):
        idx, panel = _holding_panel()
        exposure = pd.DataFrame({"801120": [40.0], "801780": [40.0]},
                                index=[pd.Timestamp("2021-12-31")])
        cov = _coverage(80.0)                       # 已映射 80，现金 20
        hr = H.holding_benchmark(exposure, panel, cash_rate=0.015,
                                 dividend_yield=0.0, coverage=cov)
        self.assertEqual(hr.attrs["missing_industries"], ["801780"])
        diag = hr.attrs["diagnostics"]
        self.assertAlmostEqual(float(diag.iloc[0]["面板缺失权重"]), 40.0)
        daily = (1.015 ** (1 / 252) - 1)
        # 只有 20% 现金计息；缺失的 40% 不能变成现金（否则会是 0.6）
        self.assertAlmostEqual(float(hr.iloc[0]), 0.2 * daily, places=9)


class HoldingBenchmarkDividendTest(unittest.TestCase):
    """行业指数不含股息，价格指数基准会系统性低估（P1-10）。"""

    def _hr(self, div, weight=100.0):
        idx, panel = _holding_panel()
        exposure = pd.DataFrame({"801120": [weight]},
                                index=[pd.Timestamp("2021-12-31")])
        cov = None if weight < 0 else _coverage(max(weight, 0.0))
        return H.holding_benchmark(exposure, panel, cash_rate=0.015,
                                   dividend_yield=div, coverage=cov)

    def test_dividend_leg_scales_with_stock_weight(self):
        daily_div = (1.02 ** (1 / 252) - 1)
        self.assertAlmostEqual(float(self._hr(0.02).iloc[0]), daily_div, places=9)
        daily_cash = (1.015 ** (1 / 252) - 1)
        mixed = 0.4 * daily_div + 0.6 * daily_cash
        self.assertAlmostEqual(float(self._hr(0.02, 40.0).iloc[0]), mixed, places=9)

    def test_zero_dividend_keeps_legacy_numbers(self):
        self.assertAlmostEqual(float(self._hr(0.0).iloc[0]), 0.0, places=12)

    def test_default_follows_rbsa_config_and_is_recorded(self):
        with patch.dict(H.C.RBSA, {"index_dividend_annual": 0.03}):
            hr = self._hr(None)
            self.assertAlmostEqual(float(hr.attrs["dividend_yield"]), 0.03)
        self.assertAlmostEqual(float(hr.iloc[0]), (1.03 ** (1 / 252) - 1), places=9)

    def test_negative_dividend_is_rejected(self):
        # 负股息相当于凭空做空收益，属于参数错误而不是"情景"
        with self.assertRaises(ValueError):
            self._hr(-0.01)


class ExposureCoverageTest(unittest.TestCase):
    """未映射权重 ≠ 现金：必须能拆开（P1-11 的可离线部分）。"""

    def _holdings(self, rows):
        return pd.DataFrame(rows,
                            columns=["季末", "股票代码", "占净值比例", "完整"])

    def test_coverage_splits_unmapped_from_cash(self):
        hd = self._holdings([
            [pd.Timestamp("2022-12-31"), "600000", 40.0, True],
            [pd.Timestamp("2022-12-31"), "830799", 10.0, True],    # 北交所，未映射
            [pd.Timestamp("2022-12-31"), "600001", 30.0, False],   # 季报，应剔除
        ])
        cov = H.exposure_coverage(hd, pd.Series({"600000": "银行"}))
        row = cov.loc[pd.Timestamp("2022-12-31")]
        self.assertAlmostEqual(float(row["已映射权重"]), 40.0)
        self.assertAlmostEqual(float(row["未映射权重"]), 10.0)
        self.assertAlmostEqual(float(row["合计"]), 50.0)
        self.assertAlmostEqual(float(row["覆盖率"]), 0.8)
        # 暴露表仍只含能映射的行业（行为不变），覆盖率负责说明差额
        exp = H.semiannual_exposure(hd, pd.Series({"600000": "银行"}))
        self.assertAlmostEqual(float(exp.sum(axis=1).iloc[0]), 40.0)

    def _weights(self):
        return pd.DataFrame({"801120": [0.6], "801080": [0.4]},
                            index=[pd.Timestamp("2022-12-31")])

    def _exposure(self):
        return pd.DataFrame({"801120": [40.0], "801080": [20.0]},
                            index=[pd.Timestamp("2022-12-31")])

    def _coverage(self):
        return pd.DataFrame({"已映射权重": [60.0], "未映射权重": [15.0],
                             "合计": [75.0], "覆盖率": [0.8]},
                            index=[pd.Timestamp("2022-12-31")])

    def test_align_reports_unmapped_and_explains_it(self):
        al = H.align_vs_rbsa(self._weights(), self._exposure(),
                             coverage=self._coverage())
        self.assertTrue(al["ok"])
        self.assertAlmostEqual(float(al["periods"]["未映射权重"].iloc[0]), 15.0)
        self.assertAlmostEqual(float(al["periods"]["持仓合计"].iloc[0]), 75.0)
        self.assertAlmostEqual(float(al["unmapped_mean"]), 15.0)
        self.assertIn("未映射", al["coverage_note"])

    def test_align_without_coverage_admits_it_cannot_tell(self):
        al = H.align_vs_rbsa(self._weights(), self._exposure())
        self.assertTrue(al["ok"])
        self.assertTrue(np.isnan(float(al["periods"]["未映射权重"].iloc[0])))
        self.assertIn("无法区分", al["coverage_note"])


def _holdings_out():
    """构造一次真实持仓拟合结果（DataFrame 均为真实结构，非 mock）。"""
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


class HoldingsPayloadTest(unittest.TestCase):
    """P3-C：持仓 payload 必须带出真实内容，而不是只断言字段存在。"""

    def test_payload_carries_summary_constraints_and_industry_rows(self):
        p = holdings_payload(_holdings_out())
        self.assertTrue(p["ok"])
        self.assertEqual(p["code"], "110022")
        self.assertEqual(p["name"], "易方达消费行业")
        self.assertEqual(p["summary"]["n_periods"], 3)
        self.assertAlmostEqual(p["summary"]["mad_mean"], 0.081)
        self.assertAlmostEqual(p["summary"]["corr_last"], 0.68)
        self.assertEqual([r["期数"] for r in p["periods"]],
                         ["2024Q2", "2024Q4", "2025Q2"])
        # 行业表首列统一为「行业」，且内容来自真实索引
        self.assertEqual(list(p["by_industry"][0])[0], "行业")
        self.assertEqual(p["by_industry"][0]["行业"], "2024Q2")
        self.assertAlmostEqual(p["by_industry"][0]["食品饮料"], 52.0)
        self.assertAlmostEqual(p["by_industry"][0]["银行"], 20.0)
        # 披露约束原样带出（4 条，含 Q2/Q4 滞后与共同行业口径）
        self.assertEqual(len(p["constraints"]), 4)
        self.assertIn("+62 天", p["constraints"][1])
        self.assertIn("+90 天", p["constraints"][1])
        self.assertIn("共同行业", p["constraints"][2])
        self.assertIn("≥60 个共同交易日", p["constraints"][3])
        # 比较/基准的明细不能被摘要吞掉
        self.assertEqual(p["compare"]["n_days"], 240)
        self.assertEqual([r["基准"] for r in p["compare"]["stats"]],
                         ["基金实际", "RBSA合成基准", "持仓基准"])
        self.assertEqual(p["benchmark"]["missing_industries"],
                         ["801780", "801950"])
        self.assertEqual(p["benchmark"]["residual_treatment"],
                         "缺失行业权重按 0 收益处理，不按现金计息")
        self.assertAlmostEqual(
            p["benchmark"]["diagnostics"][0]["面板缺失权重"], 12.0)

    def test_failed_fit_is_explicit_and_keeps_constraints(self):
        p = holdings_payload({"ok": False, "code": "110022",
                              "reason": "共同行业不足 60 个交易日"})
        self.assertFalse(p["ok"])
        self.assertEqual(p["reason"], "共同行业不足 60 个交易日")
        self.assertEqual(len(p["constraints"]), 4)


class TimingComparisonTest(unittest.TestCase):
    def test_three_states(self):
        cls, text, tail = timing_comparison({"年化收益": 0.052, "夏普": 0.31})
        self.assertEqual(cls, "ok")
        self.assertIn("跑赢", text)
        self.assertNotIn("跑输", text)
        self.assertIn("不足以证明", tail)

        cls, text, _ = timing_comparison({"年化收益": -0.031, "夏普": -0.2})
        self.assertEqual(cls, "bad")
        self.assertIn("跑输", text)
        self.assertIn("-3.10%", text)

        cls, text, _ = timing_comparison({"年化收益": 0.0, "夏普": 0.0})
        self.assertEqual(cls, "")
        self.assertIn("持平", text)

    def test_missing_is_not_a_negative_conclusion(self):
        for tc in ({}, {"年化收益": float("nan"), "夏普": 0.1},
                   {"年化收益": None}):
            cls, text, tail = timing_comparison(tc)
            self.assertEqual(cls, "")
            self.assertIn("无法计算", text)
            self.assertNotIn("跑输", text)
            self.assertNotIn("跑赢", text)
            self.assertTrue(tail)


class ExposureCoverageEdgeTest(unittest.TestCase):
    """P1-14：全映射 / 全未映射 / 空持仓都不得抛 KeyError。"""

    def _holdings(self, code):
        return pd.DataFrame([[pd.Timestamp("2024-06-30"), code, 80.0, True]],
                            columns=["季末", "股票代码", "占净值比例", "完整"])

    def test_all_mapped_gives_coverage_one(self):
        cov = H.exposure_coverage(self._holdings("600000"),
                                  pd.Series({"600000": "银行"}))
        self.assertAlmostEqual(float(cov["已映射权重"].iloc[0]), 80.0)
        self.assertAlmostEqual(float(cov["未映射权重"].iloc[0]), 0.0)
        self.assertAlmostEqual(float(cov["合计"].iloc[0]), 80.0)
        self.assertAlmostEqual(float(cov["覆盖率"].iloc[0]), 1.0)

    def test_all_unmapped_gives_coverage_zero(self):
        cov = H.exposure_coverage(self._holdings("830799"),
                                  pd.Series({"600000": "银行"}))
        self.assertAlmostEqual(float(cov["已映射权重"].iloc[0]), 0.0)
        self.assertAlmostEqual(float(cov["未映射权重"].iloc[0]), 80.0)
        self.assertAlmostEqual(float(cov["覆盖率"].iloc[0]), 0.0)

    def test_empty_holdings_return_empty_table(self):
        hd = pd.DataFrame(columns=["季末", "股票代码", "占净值比例", "完整"])
        self.assertTrue(H.exposure_coverage(hd, pd.Series({"600000": "银行"})).empty)


class HoldingBenchmarkSimpleReturnTest(unittest.TestCase):
    """P1-16：持仓基准是简单组合收益，不再是加权对数收益。"""

    def test_weighted_simple_portfolio_return(self):
        # 两个行业各 50%，次日价格 ×2 / ×0.5 → 等权（每日再平衡）简单组合
        # 收益 = 0.5*1 + 0.5*(-0.5) = 25%；旧的对数加权实现给出 ≈0。
        idx = pd.bdate_range("2024-09-02", periods=2)
        panel = pd.DataFrame({"801780": [100.0, 200.0],
                              "801790": [100.0, 50.0]}, index=idx)
        exposure = pd.DataFrame({"801780": [50.0], "801790": [50.0]},
                                index=[pd.Timestamp("2024-06-30")])
        hr = H.holding_benchmark(exposure, panel, cash_rate=0.0, dividend_yield=0.0)
        self.assertAlmostEqual(float(hr.iloc[-1]), 0.25, places=10)


class CompareBenchmarksIRTest(unittest.TestCase):
    """P1-17：IR 必须基于相对明确参照的超额日收益，而不是序列自身。"""

    def test_identical_paths_yield_zero_ir(self):
        idx = pd.bdate_range("2024-01-01", periods=80)
        nav = pd.Series(np.exp(np.cumsum(0.001 + 0.0005 * np.sin(np.arange(80)))),
                        index=idx)
        out = H.compare_benchmarks(nav, nav, nav.pct_change().dropna())
        self.assertTrue(out["ok"])
        ir = out["stats"].set_index("基准")["信息比率IR"]
        for name in ("基金实际", "RBSA合成基准", "持仓基准", "持仓−RBSA（跟踪差）"):
            self.assertAlmostEqual(float(ir[name]), 0.0, places=10,
                                   msg=f"{name} 完全同路径时 IR 应为 0")

    def test_ir_uses_excess_over_fund(self):
        idx = pd.bdate_range("2024-01-01", periods=80)
        rs = np.tile([0.01, -0.01], 40)
        fund = pd.Series(np.full(80, 1.0), index=idx)          # 净值恒为 1
        rbsa = pd.Series(np.full(80, 1.0), index=idx)
        # holding_ret 是简单日收益序列，含非零跟踪误差
        holding = pd.Series(rs[1:] + 0.001, index=idx[1:])
        out = H.compare_benchmarks(fund, rbsa, holding)
        st = out["stats"].set_index("基准")
        ex = pd.Series(rs[1:] + 0.001, index=idx[1:])
        expected = float(ex.mean() / ex.std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(float(st.loc["持仓基准", "信息比率IR"]),
                               expected, places=8)
        # 旧实现会给基金自身算出非零 IR（这里基金自身 IR 必须为 0）
        self.assertAlmostEqual(float(st.loc["基金实际", "信息比率IR"]), 0.0, places=10)
        # 跟踪差行参照 RBSA：d = h − s = h，IR 同样按超额/跟踪误差计算
        self.assertEqual(st.loc["持仓−RBSA（跟踪差）", "IR参照"], "RBSA合成基准")
        self.assertAlmostEqual(float(st.loc["持仓−RBSA（跟踪差）", "信息比率IR"]),
                               expected, places=8)

    def test_constant_nonzero_excess_with_zero_tracking_error_is_nan(self):
        # 超额恒为 0.001、跟踪误差为 0（构造上严格为零，不靠浮点相消）
        idx = pd.bdate_range("2024-01-01", periods=80)
        flat = pd.Series(np.full(80, 1.0), index=idx)
        h = pd.Series(np.full(79, 0.001), index=idx[1:])
        out = H.compare_benchmarks(flat, flat.copy(), h)
        st = out["stats"].set_index("基准")
        self.assertAlmostEqual(float(st.loc["基金实际", "信息比率IR"]), 0.0, places=12)
        self.assertAlmostEqual(float(st.loc["RBSA合成基准", "信息比率IR"]), 0.0, places=12)
        self.assertTrue(np.isnan(float(st.loc["持仓基准", "信息比率IR"])))
        self.assertTrue(np.isnan(float(st.loc["持仓−RBSA（跟踪差）", "信息比率IR"])))

    def test_period_return_is_compounded_simple(self):
        idx = pd.bdate_range("2024-01-01", periods=80)
        flat = pd.Series(np.full(80, 1.0), index=idx)
        h = pd.Series(np.full(79, 0.01), index=idx[1:])
        out = H.compare_benchmarks(flat, flat.copy(), h)
        st = out["stats"].set_index("基准")
        self.assertAlmostEqual(float(st.loc["持仓基准", "区间收益"]),
                               1.01 ** 79 - 1, places=10)


class LoadFundHoldingsCacheTest(unittest.TestCase):
    """P1-18：持仓缓存必须跟随请求年份与新披露，失败年份不得伪装完整。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=C.ROOT / "output")
        p = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _ak_frame(quarter, code="600000", pct=9.9):
        return pd.DataFrame({"股票代码": [code], "股票名称": ["测试股"],
                             "占净值比例": [pct], "持股数": [1000],
                             "持仓市值": [10000.0], "季度": [quarter]})

    def _write_cache(self, code, quarter):
        df = H._normalize_holdings(self._ak_frame(quarter))
        df.to_csv(C.DATA_DIR / f"fund_holdings_{code}_v{H.HOLDINGS_CACHE_VERSION}.csv",
                  index=False, encoding="utf-8-sig")

    def _meta(self, code):
        fp = C.DATA_DIR / f"fund_holdings_{code}_v{H.HOLDINGS_CACHE_VERSION}_meta.json"
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None

    def test_stale_cache_is_extended_to_requested_years(self):
        # TODO 复现：缓存只有 2024 一期，请求 2020 起 → 必须补取，而不是直接返回
        code = "110022"
        self._write_cache(code, "2024年2季度")
        def fake_fetch(symbol, date):
            return self._ak_frame(f"{date}年2季度") if date == "2020" else pd.DataFrame()
        import akshare as ak
        with patch.object(ak, "fund_portfolio_hold_em", side_effect=fake_fetch) as fetch, \
             patch("time.sleep"):
            hd = H.load_fund_holdings(code, start_year=2020, verbose=False)
        self.assertGreaterEqual(fetch.call_count, 1)
        periods = set(hd["期数"])
        self.assertIn("2020Q2", periods)
        self.assertIn("2024Q2", periods)
        meta = self._meta(code)
        self.assertEqual(meta["failed_years"], [])
        self.assertIn(2020, meta["fetched_years"])

    def test_fully_covered_cache_does_not_refetch(self):
        """P1-18/P1-21：终版年份 + 未到披露季的当年，缓存命中不打网络。"""
        code = "110022"
        year = pd.Timestamp.today().year
        # 当年已拿到 Q2：Q4 要到次年 1 月末才开始披露 → 本次调用不重抓；
        # 过去年份披露窗口均已关闭 → 终版。这正是离线测试不打网络的前提。
        self._write_cache(code, f"{year}年2季度")
        years = list(range(2020, year + 1))
        fp = C.DATA_DIR / f"fund_holdings_{code}_v{H.HOLDINGS_CACHE_VERSION}_meta.json"
        fp.write_text(json.dumps({"fetched_years": years, "failed_years": [],
                                  "start_year": 2020, "end_year": years[-1],
                                  "refreshed_at": "2026-01-01"}), encoding="utf-8")
        import akshare as ak
        with patch.object(ak, "fund_portfolio_hold_em") as fetch:
            hd = H.load_fund_holdings(code, start_year=2020, verbose=False)
        fetch.assert_not_called()
        self.assertEqual(set(hd["期数"]), {f"{year}Q2"})

    def test_open_window_year_is_not_frozen_by_fetched(self):
        """P1-21：披露窗口未关闭的年份，fetched/空结果不得永久冻结。

        旧口径把「当年 1 月的空结果」记入 fetched 后永不重试，Q2/Q4 新披露
        的完整持仓不会被增量拉取；新口径在披露季开始后必须重试。
        """
        code = "110022"
        self._write_cache(code, "2024年2季度")
        years = list(range(2020, pd.Timestamp.today().year + 1))
        fp = C.DATA_DIR / f"fund_holdings_{code}_v{H.HOLDINGS_CACHE_VERSION}_meta.json"
        fp.write_text(json.dumps({"fetched_years": years, "failed_years": [],
                                  "start_year": 2020, "end_year": years[-1],
                                  "refreshed_at": "2026-01-01"}), encoding="utf-8")
        import akshare as ak
        with patch.object(ak, "fund_portfolio_hold_em",
                          return_value=self._ak_frame(
                              f"{pd.Timestamp.today().year}年2季度")) as fetch, \
             patch("time.sleep"):
            hd = H.load_fund_holdings(code, start_year=2020, verbose=False)
        # 当年（含 Q2 披露季已开始）必须被重新拉取
        called_years = {call.kwargs.get("date") for call in fetch.call_args_list}
        self.assertIn(str(pd.Timestamp.today().year), called_years)
        self.assertIn(f"{pd.Timestamp.today().year}Q2", set(hd["期数"]))
        # 重试后 pending 记录了仍未终版的年份（Q4 未到披露季）
        meta = self._meta(code)
        self.assertIn(str(pd.Timestamp.today().year), meta["pending_years"])

    def test_failed_year_is_retried_and_not_faked_as_complete(self):
        code = "110022"
        self._write_cache(code, "2024年2季度")
        years = [y for y in range(2020, pd.Timestamp.today().year + 1) if y != 2023]
        fp = C.DATA_DIR / f"fund_holdings_{code}_v{H.HOLDINGS_CACHE_VERSION}_meta.json"
        fp.write_text(json.dumps({"fetched_years": years, "failed_years": [2023],
                                  "start_year": 2020, "end_year": years[-1],
                                  "refreshed_at": "2026-01-01"}), encoding="utf-8")
        import akshare as ak
        with patch.object(ak, "fund_portfolio_hold_em",
                          return_value=self._ak_frame("2023年2季度")), \
             patch("time.sleep"):
            hd = H.load_fund_holdings(code, start_year=2020, verbose=False)
        self.assertIn("2023Q2", set(hd["期数"]))
        self.assertEqual(self._meta(code)["failed_years"], [])


class IndustryMapCacheTest(unittest.TestCase):
    """P1-11（代码部分）：部分行业抓取失败不得被永久冻结，须留痕并补取。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=C.ROOT / "output")
        for p in (patch.object(C, "DATA_DIR", Path(self.tmp.name)),
                  patch.object(C, "NAME_OF",
                               {"801010": "农林牧渔", "801120": "食品饮料"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _meta_fp(self):
        return (C.DATA_DIR /
                f"industry_map_v{H.HOLDINGS_CACHE_VERSION}_meta.json")

    def _meta(self):
        return json.loads(self._meta_fp().read_text(encoding="utf-8"))

    def _expire_cooldown(self):
        meta = self._meta()
        meta["last_retry_at"] = "2020-01-01T00:00:00"
        self._meta_fp().write_text(json.dumps(meta), encoding="utf-8")

    def test_partial_failure_keeps_missing_state_and_backfills(self):
        import akshare as ak
        def flaky(symbol):
            if symbol == "801010":
                raise RuntimeError("offline")
            return pd.DataFrame({"证券代码": ["600519"]})
        with patch.object(ak, "index_component_sw", side_effect=flaky):
            with self.assertLogs(H.LOG, level="WARNING"):
                m1 = H.load_industry_map(verbose=False)
        self.assertIn("600519", m1.index)                 # 成功的行业可用
        self.assertEqual(self._meta()["missing_industries"], ["801010"])

        # 冷却期内不重试：不触发任何抓取
        with patch.object(ak, "index_component_sw", side_effect=flaky) as fetch:
            m2 = H.load_industry_map(verbose=False)
            fetch.assert_not_called()
        self.assertIn("600519", m2.index)

        # 冷却期过后：只补取缺失行业，并与缓存合并
        self._expire_cooldown()
        def ok(symbol):
            return pd.DataFrame({"证券代码": ["000333"]})
        with patch.object(ak, "index_component_sw", side_effect=ok) as fetch:
            m3 = H.load_industry_map(verbose=False)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("600519", m3.index)                 # 旧映射保留
        self.assertIn("000333", m3.index)                 # 新行业并入
        self.assertEqual(self._meta()["missing_industries"], [])

        # 完整映射之后走缓存，不再抓取
        with patch.object(ak, "index_component_sw") as fetch:
            H.load_industry_map(verbose=False)
            fetch.assert_not_called()

    def test_refresh_preserves_failed_industries_and_replaces_successful_ones(self):
        import akshare as ak
        cached = pd.Series({"600519": "食品饮料", "000001": "农林牧渔"},
                           name="industry")
        cached.index.name = "code"
        cached.to_frame().to_csv(
            C.DATA_DIR / f"industry_map_v{H.HOLDINGS_CACHE_VERSION}.csv")
        with patch.object(ak, "index_component_sw",
                          side_effect=RuntimeError("offline")), patch("time.sleep"):
            with self.assertLogs(H.LOG, level="WARNING"):
                failed = H.load_industry_map(refresh=True, verbose=False)
        self.assertEqual(failed.to_dict(), cached.to_dict())

        def partial(symbol):
            if symbol == "801010":
                return pd.DataFrame({"证券代码": ["000333"]})
            raise RuntimeError("offline")
        with patch.object(ak, "index_component_sw", side_effect=partial), patch("time.sleep"):
            with self.assertLogs(H.LOG, level="WARNING"):
                merged = H.load_industry_map(refresh=True, verbose=False)
        self.assertNotIn("000001", merged.index)       # 成功行业整体替换旧成分
        self.assertEqual(merged["000333"], "农林牧渔")
        self.assertEqual(merged["600519"], "食品饮料")  # 失败行业完整保留
        persisted = pd.read_csv(
            C.DATA_DIR / f"industry_map_v{H.HOLDINGS_CACHE_VERSION}.csv",
            index_col=0, dtype=str).iloc[:, 0]
        self.assertEqual(persisted.to_dict(), merged.to_dict())
        self.assertEqual(self._meta()["missing_industries"], ["801120"])

    def test_failed_rebuild_is_throttled(self):
        import akshare as ak
        with patch.object(ak, "index_component_sw",
                          side_effect=RuntimeError("offline")):
            with self.assertLogs(H.LOG, level="WARNING"):
                m1 = H.load_industry_map(verbose=False)
        self.assertTrue(m1.empty)
        with patch.object(ak, "index_component_sw") as fetch:
            m2 = H.load_industry_map(verbose=False)
            fetch.assert_not_called()                     # 冷却期内不重打网络
        self.assertTrue(m2.empty)
        self._expire_cooldown()
        with patch.object(ak, "index_component_sw",
                          return_value=pd.DataFrame({"证券代码": ["600519"]})):
            m3 = H.load_industry_map(verbose=False)
        self.assertIn("600519", m3.index)


def _backtest_inputs(health_values):
    idx = pd.bdate_range("2024-01-01", periods=60)
    equity = pd.DataFrame({"拟合健康策略": np.linspace(1.0, 1.05, 60),
                           "买入持有": np.linspace(1.0, 1.03, 60)}, index=idx)
    health = pd.DataFrame({
        "health_score": health_values,
        "fit_r2": np.full(60, 0.9),
        "te_ratio": np.full(60, 1.0),
        "active_alpha": np.zeros(60),
        "alpha_t": np.zeros(60),
        "fit_reliable": np.ones(60),
    }, index=idx)
    decision = pd.Series([1] * 60, index=idx, dtype=int)
    metrics = pd.DataFrame(
        {"夏普": [0.5, 0.4], "算术夏普": [0.6, 0.5]},
        index=["拟合健康策略", "买入持有"])
    bt = {
        "equity": equity, "metrics": metrics,
        "window_start": idx[0], "window_end": idx[-1],
        "control_name": "买入持有", "trades": [],
        "timing_contrib": {"年化收益": 0.0, "夏普": 0.0, "最大回撤": 0.0},
        "controls": {}, "timing_contrib_all": {"仅赛道估值通道": {}},
        "avg_position_all": {}, "ic": 0.0, "icir": 0.0, "turnover": 0.0,
        "avg_position": 1.0, "win_long": 0.5,
    }
    return bt, health, decision


class BacktestCurrentPayloadTest(unittest.TestCase):
    """P2 基金页「当前建议仓位/健康分」：缺数据或门控不可触发时必须标注。"""

    def _payload(self, health_values, valuation=None):
        from market_research.funds.payloads import _backtest_payload
        bt, health, decision = _backtest_inputs(health_values)
        return _backtest_payload("110022", "测试基金", "ridge", True,
                                 bt, health, decision, 1.0,
                                 valuation=valuation)

    def test_current_position_and_degraded_reasons(self):
        p = self._payload(np.linspace(0.6, 0.3, 60))       # 从未跌破 -0.5
        cur = p["current"]
        self.assertTrue(cur["available"])
        self.assertEqual(cur["position"], 1)
        self.assertAlmostEqual(cur["position_pct"], 1.0)
        self.assertTrue(cur["degraded"])
        self.assertIn("从未触及离场线", " ".join(cur["notes"]))
        self.assertIn("非投资建议", cur["disclaimer"])

    def test_missing_health_is_unavailable_not_normal(self):
        p = self._payload(np.full(60, np.nan))
        cur = p["current"]
        self.assertFalse(cur["available"])
        self.assertIsNone(cur["position"])
        self.assertIsNone(cur["health"])
        self.assertEqual(cur["state"], "不可用")
        self.assertIn("健康分全部缺失", " ".join(cur["notes"]))

    def test_valuation_fallback_reaches_variants_and_payload(self):
        valuation = {"available": True, "degraded": True, "coverage": 0.5,
                     "fallback_weight": 0.2,
                     "fallback_assets": [{"code": "801780", "name": "银行",
                                          "weight": 0.2, "kind": "行业"}],
                     "note": "行业估值不可用，已用沪深300 宽基替代"}
        p = self._payload(np.linspace(0.6, 0.3, 60), valuation=valuation)
        self.assertTrue(p["valuation"]["degraded"])
        variant = next(v for v in p["variants"] if "估值" in v["name"])
        self.assertTrue(variant["degraded"])
        self.assertIn("行业估值不可用", variant["note"])


if __name__ == "__main__":
    unittest.main()
