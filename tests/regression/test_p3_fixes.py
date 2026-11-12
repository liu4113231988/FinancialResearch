# -*- coding: utf-8 -*-
"""P3 一致性与边界防御的离线回归（不依赖网络或真实行情缓存）。"""
import os
import pickle
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.config as C


class LassoEmptySupportTests(unittest.TestCase):
    def test_all_constant_columns_do_not_raise(self):
        from market_research.modeling.estimators import fit_window
        rng = np.random.default_rng(5)
        X = np.ones((120, 3))
        y = rng.normal(size=120)
        result = fit_window("lasso", X, y, ["a", "b", "c"],
                            params={}, equity_ratio=0.9)
        self.assertEqual(result.extra["n_support"], 1)
        values = result.weights.reindex(["a", "b", "c"]).fillna(0.0).values
        self.assertTrue(np.isfinite(values).all())


class PcrWindowTests(unittest.TestCase):
    def test_holdout_does_not_change_pca_training_input(self):
        from market_research.modeling.estimators import PCA, fit_window
        X = np.random.default_rng(42).normal(size=(300, 4)) * .01
        y = X @ [.3, .3, .2, .1]
        revised = X.copy()
        revised[240:, 0] *= 100
        original_fit = PCA.fit
        inputs = []

        def capture(pca, values):
            inputs.append(values.copy())
            return original_fit(pca, values)

        with patch.object(PCA, "fit", capture):
            for values in (X, revised):
                fit_window("pcr", values, y, list("abcd"), equity_ratio=.9)
        self.assertEqual(len(inputs), 4)
        np.testing.assert_array_equal(inputs[0], inputs[2])
        self.assertFalse(np.array_equal(inputs[1], inputs[3]))

    def test_single_factor_and_short_training_windows(self):
        from market_research.modeling.estimators import fit_window
        X = np.array([[1., 0.], [2., 1.], [3., 2.]])
        for width in (1, 2):
            with self.subTest(width=width):
                fit = fit_window("pcr", X[:, :width], X[:, 0], list("ab"[:width]))
                self.assertEqual(fit.extra["n_components"], 1)
                self.assertTrue(np.isfinite(fit.pred).all())
        with self.assertRaisesRegex(ValueError, "PCR"):
            fit_window("pcr", X[:, :0], X[:, 0], [])
        with self.assertRaisesRegex(ValueError, "PCR"):
            fit_window("pcr", X[:2], X[:2, 0], list("ab"))


class NeweyWestDegenerateTests(unittest.TestCase):
    def test_constant_nonzero_series_has_no_false_significance(self):
        from market_research.research.robustness import _nw_mean_t
        self.assertTrue(np.isnan(_nw_mean_t(np.full(120, 0.01), lags=5)))
        rng = np.random.default_rng(1)
        self.assertTrue(np.isfinite(_nw_mean_t(rng.normal(size=120), lags=5)))

    def test_short_and_short_sample_still_return_nan(self):
        from market_research.research.robustness import _nw_mean_t
        self.assertTrue(np.isnan(_nw_mean_t(np.arange(10.), lags=5)))


class E1ConstantBlockTests(unittest.TestCase):
    def test_constant_block_is_counted_not_propagated(self):
        from market_research.research.experiments import ic_term_structure
        rng = np.random.default_rng(23)
        n = 400
        idx = pd.bdate_range("2018-01-02", periods=n)
        signal = rng.normal(size=n)
        signal[120:220] = 0.5                  # 多个块内信号常数 → spearman NaN
        ret = 0.01 * signal + rng.normal(0, 0.003, n)
        nav = (1 + ret).cumprod()
        out = ic_term_structure(pd.DataFrame({"score": signal}, index=idx),
                                pd.Series(nav, index=idx), horizons=(20,))
        row = out.iloc[0]
        self.assertGreaterEqual(row["ICIR_NaN块数"], 1)
        self.assertTrue(np.isfinite(row["ICIR"]))


class HoldingsQuarterTests(unittest.TestCase):
    @staticmethod
    def _frame(quarters):
        return pd.DataFrame({"股票代码": ["600000"] * len(quarters),
                             "股票名称": ["x"] * len(quarters),
                             "占净值比例": [1.] * len(quarters),
                             "持股数": [100] * len(quarters),
                             "持仓市值": [1000.] * len(quarters),
                             "季度": quarters})

    def test_invalid_quarter_labels_are_dropped_without_raising(self):
        from market_research.funds import holdings as H
        out = H._normalize_holdings(self._frame([
            "2024年2季度", "未知", "9999年2季度", "２０２４年2季度",
            "2024年5季度", "2023年4季度"]))
        self.assertEqual(len(out), 2)
        self.assertEqual(sorted(out["期数"].tolist()), ["2023Q4", "2024Q2"])
        self.assertTrue(out["完整"].all())

    def test_retype_cache_roundtrip_keeps_dtypes(self):
        from market_research.funds import holdings as H
        out = H._normalize_holdings(self._frame(["2024年2季度", "未知"]))
        back = H._retype_holdings(out.copy())
        self.assertEqual(len(back), 1)
        self.assertTrue(bool(back["完整"].iloc[0]))


class DdofConsistencyTests(unittest.TestCase):
    def test_pick_single_beta_matches_ols(self):
        from market_research.research.commands.increment import _pick_single
        rng = np.random.default_rng(7)
        X = rng.normal(size=(200, 3))
        y = 0.6 * X[:, 1] + rng.normal(0, 0.2, 200)
        j, beta = _pick_single(X, y, by="mse")
        expected = float(np.cov(y, X[:, j])[0, 1] / np.var(X[:, j], ddof=1))
        self.assertAlmostEqual(beta, expected, places=12)
        self.assertAlmostEqual(beta, float(np.polyfit(X[:, j], y, 1)[0]),
                               places=10)


class DisclosureAndErrorTests(unittest.TestCase):
    def test_percentile_note_discloses_current_observation(self):
        from market_research.market.context import context_features, context_payload
        from market_research.market.technical import indicators
        from tests.market.test_technical import sample
        bars = sample(150)
        note = context_payload(context_features(bars, indicators(bars),
                                                bars.close), None)["note"]
        self.assertIn("含当日自身", note)
        self.assertIn("1/N", note)
        self.assertIn("不是排除当日的历史分位", note)

    def test_dual_source_failure_keeps_both_messages(self):
        import akshare as ak
        from market_research.market.analysis import load_bars
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(C, "DATA_DIR", Path(folder)):
            with patch.object(ak, "stock_zh_a_hist",
                              side_effect=RuntimeError("primary timeout")), \
                 patch.object(ak, "stock_zh_a_hist_tx",
                              side_effect=ValueError("fallback empty")):
                with self.assertRaises(ValueError) as ctx:
                    load_bars("stock", "sh600519", "2024-01-01", "2025-01-01")
        message = str(ctx.exception)
        self.assertIn("主源 RuntimeError: primary timeout", message)
        self.assertIn("备源 ValueError: fallback empty", message)


class WindowFutureEndTests(unittest.TestCase):
    """结束日期上界：未来区间会触发全量抓取，必须在入口拒绝。"""
    def test_future_end_is_rejected_before_any_fetch(self):
        from market_research.validation.window import parse_window, resolve_window
        with self.assertRaisesRegex(ValueError, "不能晚于今天"):
            parse_window("2020-01-01", "2099-12-31", today="2026-11-08")
        future = (pd.Timestamp.today() + pd.Timedelta(days=365)).strftime("%Y-%m-%d")
        with self.assertRaisesRegex(ValueError, "不能晚于今天"):
            resolve_window(start="2020-01-01", end=future)

    def test_today_itself_is_allowed(self):
        from market_research.validation.window import parse_window
        self.assertEqual(parse_window("2020-01-01", "2026-11-08",
                                      today="2026-11-08"),
                         ("2020-01-01", "2026-11-08"))


class EmptyRollingWindowTests(unittest.TestCase):
    def test_all_windows_skipped_reports_readable_failure(self):
        from market_research.modeling.engine import ModelZoo
        cfg = {"window": 10, "min_obs": 1000, "refit": 5,
               "equity_ratio": 0.9, "auto_equity": False}
        dates = pd.bdate_range("2024-01-01", periods=30)
        with self.assertRaisesRegex(ValueError, "无有效拟合窗口"):
            ModelZoo(cfg)._run_one("ridge", np.ones((30, 2)), np.zeros(30),
                                   ["a", "b"], dates, [10, 15, 20], None)


class RebalanceCountTests(unittest.TestCase):
    def test_failed_first_model_does_not_zero_n_rebal(self):
        from types import SimpleNamespace
        from market_research.funds.analysis import _n_rebal
        reps = {"ridge": SimpleNamespace(weights_hist=pd.DataFrame()),
                "enet": SimpleNamespace(weights_hist=pd.DataFrame(
                    {"801010": [.5, .6]}, index=[0, 1]))}
        self.assertEqual(_n_rebal(reps), 2)
        self.assertEqual(_n_rebal({}), 0)


class SortedAlignmentTests(unittest.TestCase):
    @staticmethod
    def _frames():
        weights = pd.DataFrame(
            {"801010": [.5, .4], "801120": [.5, .6]},
            index=pd.to_datetime(["2023-06-30", "2024-06-30"]))
        exposure = pd.DataFrame(
            {"801010": [40., 30.], "801120": [60., 70.]},
            index=pd.to_datetime(["2023-12-31", "2024-12-31"]))
        return weights, exposure

    def test_unsorted_public_inputs_match_sorted_ones(self):
        from market_research.funds import holdings as H
        weights, exposure = self._frames()
        baseline = H.align_vs_rbsa(weights, exposure)
        shuffled = H.align_vs_rbsa(weights.iloc[::-1], exposure.iloc[::-1])
        self.assertTrue(baseline["ok"])
        self.assertTrue(shuffled["ok"])
        pd.testing.assert_frame_equal(baseline["periods"], shuffled["periods"])


class EmptyExclusionTests(unittest.TestCase):
    def test_no_exclusions_returns_typed_empty_frame(self):
        import market_research.funds.universe as U
        cand = pd.DataFrame({"基金代码": ["000001"], "基金简称": ["A"]})
        excl = pd.DataFrame(columns=["基金代码", "基金简称", "排除原因"])
        det = pd.DataFrame({"基金代码": ["000001"], "基金简称": ["A"],
                            "成立时间": [pd.Timestamp("2020-01-01")],
                            "当前规模_亿": [100.], "管理人": ["X"],
                            "策略": ["主动管理"]})
        with patch.object(U, "candidates", return_value=(cand, excl)), \
             patch.object(U, "enrich", return_value=det):
            pool, ex = U.build_pool(target_per_layer=20, verbose=False)
        self.assertEqual(len(pool), 1)
        self.assertTrue(ex.empty)
        self.assertEqual(list(ex.columns), ["基金代码", "基金简称", "排除原因"])


class SyncOnceSwitchTests(unittest.TestCase):
    def test_disabled_switch_blocks_once(self):
        import market_research.tasks.sync as sync
        with patch.object(sync, "tick") as tick, \
             patch.object(sync.logs, "configure"), \
             patch.object(sys, "argv", ["sync", "--once"]), \
             patch.dict(os.environ, {"FIL_SYNC_ENABLED": "0"}):
            sync.main()
        tick.assert_not_called()

    def test_enabled_once_still_runs(self):
        import market_research.tasks.sync as sync
        with patch.object(sync, "tick") as tick, \
             patch.object(sync.logs, "configure"), \
             patch.object(sys, "argv", ["sync", "--once"]), \
             patch.dict(os.environ, {"FIL_SYNC_ENABLED": "1"}):
            sync.main()
        tick.assert_called_once()


class ResultCacheTouchTests(unittest.TestCase):
    def test_cache_hit_refreshes_mtime(self):
        import market_research.services.analysis as S
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.pkl"
            path.write_bytes(pickle.dumps({"meta": {}}))
            old = time.time() - 40 * 86400
            os.utime(path, (old, old))
            with patch.object(S, "fund_cache_path", return_value=path), \
                 patch.object(S, "_cleanup_stale_results"), \
                 patch.object(S.sync, "register"):
                out = S.submit("analyze", {"code": "110022"})
            self.assertTrue(out["cached"])
            self.assertGreater(path.stat().st_mtime, old)


class CancelledResultTests(unittest.TestCase):
    def test_cancel_queue_timeout_and_failure_are_distinguished(self):
        from fastapi.testclient import TestClient
        from market_research.api.app import app
        import market_research.services.analysis as S
        client = TestClient(app)
        for error, code, reason in (
                ("任务已取消", 409, "cancelled"),
                ("排队超时：请重新提交", 500, "queue_timeout"),
                ("RuntimeError: fit failed", 500, "failed")):
            with self.subTest(error=error), \
                 patch.object(S.JOBS, "get",
                              return_value={"status": "error", "error": error}):
                response = client.get("/api/result/job-1")
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.json()["reason"], reason)


if __name__ == "__main__":
    unittest.main()
