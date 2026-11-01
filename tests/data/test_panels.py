"""Incomplete index pools recover without refetching successful series."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
