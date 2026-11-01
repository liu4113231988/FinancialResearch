"""Incomplete index pools recover without refetching successful series."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research import config as C
from market_research.data import panels, storage
from market_research.errors import CacheMiss


class PartialPoolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.pool_patch = patch.object(C, "NAME_OF", {"first": "First", "missing": "Missing"})
        self.path_patch.start()
        self.pool_patch.start()
        self.frame = pd.DataFrame({"first": [1., 2.]}, index=pd.date_range("2024-01-01", periods=2))
        storage.write("index_universe_2024-01-01", self.frame,
                      {"v": C.DATA_CACHE_VERSION, "n_candidates": 2, "start": "2024-01-01", "end": None})

    def tearDown(self):
        self.pool_patch.stop()
        self.path_patch.stop()
        self.directory.cleanup()

    def test_only_missing_series_are_requested_and_reused_on_next_load(self):
        recovered = self.frame.rename(columns={"first": "missing"}) * 3
        with patch.object(panels, "fetch_universe", return_value=recovered) as fetch:
            result = panels.load_index_universe("2024-01-01", verbose=False)
            fetch.assert_called_once_with(["missing"], "2024-01-01", None, verbose=False)
            pd.testing.assert_series_equal(result["first"], self.frame["first"], check_freq=False)
            pd.testing.assert_series_equal(result["missing"], recovered["missing"], check_freq=False)
            again = panels.load_index_universe("2024-01-01", verbose=False)
            self.assertEqual(fetch.call_count, 1)
            pd.testing.assert_frame_equal(result, again, check_freq=False)
        self.assertEqual(storage.metadata("index_universe_2024-01-01")["info"]["n_missing"], 0)

    def test_failed_recovery_keeps_good_prices_and_missing_status(self):
        with patch.object(panels, "fetch_universe", side_effect=RuntimeError("offline")):
            with self.assertLogs(panels.LOG, level="WARNING"):
                result = panels.load_index_universe("2024-01-01", verbose=False)
        pd.testing.assert_frame_equal(result, self.frame, check_freq=False)
        self.assertEqual(storage.metadata("index_universe_2024-01-01")["info"]["missing_assets"], ["missing"])

    def test_read_only_mode_raises_instead_of_fetching(self):
        # 请求线程内的接口用 allow_fetch=False：缓存缺失必须立刻失败，
        # 不能替用户触发抓取（不可取消、不占队列名额）。
        with patch.object(panels, "fetch_universe") as fetch:
            with self.assertRaises(CacheMiss):
                panels.load_index_universe("2023-01-02", verbose=False, allow_fetch=False)
            fetch.assert_not_called()

    def test_read_only_mode_serves_warm_cache_without_backfilling(self):
        with patch.object(panels, "fetch_universe") as fetch:
            result = panels.load_index_universe("2024-01-01", verbose=False, allow_fetch=False)
            fetch.assert_not_called()
        pd.testing.assert_frame_equal(result, self.frame, check_freq=False)
        # 缺列照旧留痕，只是不去补取
        self.assertEqual(storage.metadata("index_universe_2024-01-01")["info"]["missing_assets"], ["missing"])


class FixedWindowTests(unittest.TestCase):
    """P1-15：固定历史窗口不得被结束日期之后的净值改变。"""

    def _load(self, nav, px):
        end = pd.bdate_range("2024-01-01", periods=100)[-1].strftime("%Y-%m-%d")
        with patch.object(panels, "load_fund_nav_cached", return_value=nav), \
             patch.object(panels, "load_index_universe", return_value=px):
            return panels.load_panel("110022", start="2024-01-01", end=end,
                                     verbose=False)

    def test_post_end_nav_extension_cannot_drop_full_history_index(self):
        # TODO 最小复现：指数与净值在 100 个工作日上完全一致；只把净值延长到
        # 120 日，历史窗口内结果必须不变 —— 旧实现会因覆盖率分母被 end 之后的
        # 日期稀释而把完整覆盖历史的指数误删。
        idx = pd.bdate_range("2024-01-01", periods=100)
        px = pd.DataFrame({"801780": 100.0 + np.arange(100)}, index=idx)
        nav120 = pd.Series(100.0 + np.arange(120),
                           index=pd.bdate_range("2024-01-01", periods=120))
        out = self._load(nav120, px)
        self.assertEqual(list(out.columns), ["801780", "__FUND__"])
        self.assertEqual(len(out), 100)

    def test_no_surviving_index_raises_clear_error(self):
        idx = pd.bdate_range("2024-01-01", periods=100)
        px = pd.DataFrame({"801780": [100.0] * 10 + [np.nan] * 90}, index=idx)
        nav = pd.Series(100.0 + np.arange(100), index=idx)
        with self.assertRaises(ValueError) as ctx:
            self._load(nav, px)
        self.assertIn("候选指数", str(ctx.exception))
