# -*- coding: utf-8 -*-
"""结果缓存版本清理回归。

对应 2026-11-01 全量复查发现的问题：
config 注释声称"旧版缓存会自动清理"，但此前没有任何清理代码。
"""
import tempfile
import unittest
from pathlib import Path

import market_research.config as C
import market_research.services.analysis as services


class ResultCacheCleanupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._old_dir = services.CACHE_DIR
        self._old_cleaned = services._CLEANED
        services.CACHE_DIR = self.dir
        services._CLEANED = False

    def tearDown(self):
        services.CACHE_DIR = self._old_dir
        services._CLEANED = self._old_cleaned
        self._tmp.cleanup()

    def test_removes_other_versions_keeps_current(self):
        old_v = self.dir / "result_v7_110022_deadbeef00.pkl"
        other = self.dir / "result_v8_110022_cafebabe00.pkl"
        current = self.dir / f"result_v{C.CACHE_VERSION}_110022_1234567890.pkl"
        keep = self.dir / "fund_nav_110022_total.csv"
        for p in (old_v, other, current, keep):
            p.write_bytes(b"x")

        services._cleanup_stale_results()

        self.assertFalse(old_v.exists())
        self.assertFalse(other.exists())
        self.assertTrue(current.exists())
        self.assertTrue(keep.exists())          # 只清 result_*，不碰数据缓存

    def test_runs_once_per_process(self):
        stale = self.dir / "result_v7_x_0000000000.pkl"
        services._cleanup_stale_results()
        stale.write_bytes(b"x")                 # 清理后再出现的旧文件不再处理
        services._cleanup_stale_results()
        self.assertTrue(stale.exists())


class RunInfoFingerprintTest(unittest.TestCase):
    def test_window_and_dirty_recorded(self):
        from market_research.research.runinfo import data_summary, run_fingerprint

        data = data_summary()
        for key in ("start_date", "end_date", "window_years",
                    "min_window_years", "data_stamp"):
            self.assertIn(key, data)

        fp = run_fingerprint()
        self.assertIn("git_dirty", fp)
        self.assertIn(fp["git_dirty"], (True, False, None))
        self.assertIn("window_years", fp["data"])


if __name__ == "__main__":
    unittest.main()
