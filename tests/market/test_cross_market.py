"""跨市场相关性的滞后对齐、缓存降级与供应商映射。"""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.data.providers.indices import fetch_us_index_sina
from market_research.market.cross_market import (_hedge_hint, cross_market_block)


class CrossMarketTests(unittest.TestCase):
    def _fixture(self, n=180):
        dates = pd.bdate_range("2025-01-02", periods=n)
        domestic_returns = pd.Series(
            np.linspace(-0.006, 0.006, n) + 0.001 * np.sin(np.arange(n) / 4.0),
            index=dates)
        domestic_close = 100 * (1 + domestic_returns).cumprod()
        # 美股 t 的收益 = 本地 t+1 的收益：按“严格早于本地日”对齐后应完全同向。
        us_values = np.r_[domestic_returns.to_numpy()[1:], domestic_returns.iloc[-1]]
        us_close = pd.Series(100 * (1 + us_values).cumprod(), index=dates)
        return dates, domestic_close, us_close.to_frame("close")

    def test_lagged_alignment_avoids_same_day_future_information(self):
        dates, domestic, us = self._fixture()
        with patch("market_research.tasks.sync.cached", return_value=us):
            out = cross_market_block(domestic, "2025-01-02",
                                     dates[-1].strftime("%Y-%m-%d"))
        self.assertTrue(out["ok"])
        item = out["items"][0]
        self.assertEqual(item["code"], "IXIC")
        self.assertGreater(item["corr_lag_full"], 0.99)
        self.assertGreater(item["corr_lag_120"], 0.99)
        self.assertEqual(item["latest_us_date"], dates[-2].strftime("%Y-%m-%d"))
        self.assertIn(item["signal"], ("tailwind", "headwind", "mixed"))
        self.assertIn("前一交易日", out["notes"][0])

    def test_missing_cache_returns_explicit_unavailable(self):
        dates, domestic, _ = self._fixture()
        with patch("market_research.tasks.sync.cached", return_value=None):
            out = cross_market_block(domestic, "2025-01-02",
                                     dates[-1].strftime("%Y-%m-%d"))
        self.assertFalse(out["ok"])
        self.assertTrue(all(item["signal"] == "unavailable" for item in out["items"]))
        self.assertIn("缓存不可用", out["items"][0]["hint"])

    def test_hedge_hint_uses_rho_times_us_return(self):
        self.assertEqual(_hedge_hint(0.5, 0.01, 150)["signal"], "tailwind")
        self.assertEqual(_hedge_hint(0.5, -0.01, 150)["signal"], "headwind")
        self.assertEqual(_hedge_hint(0.5, -0.01, 150)["confidence"], "medium")
        self.assertEqual(_hedge_hint(0.1, 0.01, 150)["signal"], "mixed")
        self.assertEqual(_hedge_hint(None, 0.01, 150)["signal"], "unavailable")

    def test_provider_maps_short_code_to_sina_symbol(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                              "close": [100.0, 101.0]})
        with patch("market_research.data.providers.indices._retry",
                   return_value=frame) as retry:
            series = fetch_us_index_sina("INX", "2025-01-01")
        self.assertEqual(retry.call_args.kwargs["symbol"], C.US_INDEX_SYMBOL["INX"])
        self.assertEqual(series.name, "INX")
        self.assertEqual(len(series), 2)


if __name__ == "__main__":
    unittest.main()
