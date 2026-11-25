"""P1-25：逐窗因果选池。未来缺数不得改写历史窗口的因子集/权重/预测。"""
import unittest

import numpy as np
import pandas as pd

from market_research.modeling.engine import ModelZoo


def _panel(n=320, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    returns = rng.normal(0.0002, 0.008, (n, 3))
    prices = np.exp(np.cumsum(returns, axis=0))
    frame = pd.DataFrame(prices, columns=["a", "b", "c"], index=dates)
    frame["__FUND__"] = 100 * np.exp(
        np.cumsum(returns[:, 0] * 0.4 + returns[:, 1] * 0.4))
    return frame


def _cfg():
    return dict(window=60, refit=10, min_obs=45, min_coverage=0.95,
                l1_ratio=0.5, alpha_grid=(1e-4, 1e-3, 1e-2),
                min_weight=0.01, max_assets=8, equity_ratio=0.9,
                auto_equity=False, rebal_mode="linspace",
                index_dividend_annual=0.0, max_rebal=20)


def _run(frame):
    frame = frame.copy()
    frame.attrs["causal_pool"] = frame.copy()
    return ModelZoo(_cfg()).run(frame, ["ridge"], workers=1, max_rebal=20)["ridge"]


class CausalSelectionTests(unittest.TestCase):
    def test_future_gap_does_not_rewrite_historical_windows(self):
        full = _panel()
        cut = full.index[200]
        truncated = full.copy()
        truncated.loc[truncated.index >= cut, "b"] = np.nan

        rep_full = _run(full)
        rep_gap = _run(truncated)
        self.assertTrue(rep_full.extra["causal_pool_used"])
        self.assertTrue(rep_gap.extra["causal_pool_used"])

        union = rep_full.weights_hist.columns.union(rep_gap.weights_hist.columns)
        common = rep_full.weights_hist.index.intersection(
            rep_gap.weights_hist.index)
        pre = common[common < cut]
        self.assertGreater(len(pre), 0)
        pd.testing.assert_frame_equal(
            rep_full.weights_hist.reindex(index=pre, columns=union).fillna(0.0),
            rep_gap.weights_hist.reindex(index=pre, columns=union).fillna(0.0))

        a = rep_full.syn_ret.loc[:cut - pd.Timedelta(days=1)]
        b = rep_gap.syn_ret.loc[:cut - pd.Timedelta(days=1)]
        pd.testing.assert_series_equal(a, b, check_freq=False, check_names=False)

    def test_late_windows_drop_unavailable_factor(self):
        frame = _panel()
        cut = frame.index[200]
        frame.loc[frame.index >= cut, "b"] = np.nan
        rep = _run(frame)
        late = rep.fit_diag.loc[rep.fit_diag.index >= cut]
        self.assertTrue((late["dropped_factors"] >= 1).any())
        # 末尾窗口的 b 已完全不可得，只剩 2 个因子。
        self.assertLessEqual(int(rep.fit_diag.iloc[-1]["n_factors"]), 2)

    def test_missing_required_factor_creates_explicit_gap(self):
        frame = _panel()
        gap_days = frame.index[250:252]
        frame.loc[gap_days, "b"] = np.nan
        rep = _run(frame)
        # 训练窗口内覆盖率仍达 0.95，b 仍被选中；预测段缺报价的日期必须留空，
        # 不能把未知收益当 0 或跨缺口拼接。
        self.assertGreaterEqual(rep.extra["oos_gap_n"], 1)
        self.assertLess(rep.extra["oos_coverage"], 1.0)
        for day in gap_days:
            self.assertNotIn(day, rep.syn_ret.index)
        # 不连续样本不能按 252 日/年直接年化
        self.assertEqual(rep.extra["te_basis"], "calendar_adjusted")
        self.assertLess(rep.extra["oos_periods_per_year"], 252.0)
        # 覆盖缺口的训练窗口也不能按 252 日/年直接年化
        self.assertLess(float(rep.fit_diag["te_ppy"].min()), 252.0)

    def test_future_new_factor_is_not_used_in_early_windows(self):
        frame = _panel()
        rng = np.random.default_rng(9)
        late = np.exp(np.cumsum(rng.normal(0, 0.008, 120)))
        frame["d"] = np.nan
        frame.loc[frame.index[200:], "d"] = late
        rep = _run(frame)
        first = str(rep.fit_diag.iloc[0]["selected"]).split(",")
        last = str(rep.fit_diag.iloc[-1]["selected"]).split(",")
        self.assertNotIn("d", first)
        self.assertIn("d", last)

    def test_prefix_missing_factor_enters_only_when_window_coverage_allows(self):
        frame = _panel()
        frame.loc[frame.index[:80], "b"] = np.nan
        rep = _run(frame)
        first = str(rep.fit_diag.iloc[0]["selected"]).split(",")
        last = str(rep.fit_diag.iloc[-1]["selected"]).split(",")
        self.assertNotIn("b", first)
        self.assertIn("b", last)
        self.assertIn("0.", str(rep.fit_diag.iloc[0]["dropped"]))

    def test_single_factor_window_does_not_crash(self):
        frame = _panel()
        frame["b"] = np.nan
        frame["c"] = np.nan
        rep = _run(frame)
        self.assertEqual(int(rep.fit_diag.iloc[-1]["n_factors"]), 1)
        self.assertGreater(rep.oos_r2, -10.0)

    def test_all_factors_missing_skips_early_windows(self):
        frame = _panel()
        cut = frame.index[200]
        frame.loc[frame.index < cut, ["a", "b", "c"]] = np.nan
        rep = _run(frame)
        self.assertGreaterEqual(rep.fit_diag.index[0], cut)

    def test_selection_audit_is_persisted(self):
        rep = _run(_panel())
        diag = rep.fit_diag
        for column in ("train_start", "train_end", "selected", "dropped",
                       "fingerprint", "n_factors"):
            self.assertIn(column, diag.columns)
        self.assertTrue((diag["fingerprint"].str.len() == 16).all())
        self.assertTrue(diag["train_start"].notna().all())
        self.assertTrue(diag["train_end"].notna().all())
        self.assertEqual(diag.iloc[0]["n_factors"], 3)
        self.assertEqual(rep.extra["te_basis"], "daily")


if __name__ == "__main__":
    unittest.main()
