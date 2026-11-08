"""DuckDB 迁移、事务与路由资源生命周期的离线回归。"""
import sys
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import pandas as pd
import market_research.config as C
import market_research.data.marketdata as D
import market_research.data.storage as storage
from market_research.errors import StorageBusy


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()
        self.frame = pd.DataFrame({"a": [1., 2.], "b": [3., float("nan")]}, index=pd.date_range("2020-01-01", periods=2, name="date"))

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_legacy_migration_refresh_and_version(self):
        self.frame.to_csv(C.DATA_DIR / "legacy.csv")
        (C.DATA_DIR / "legacy.meta.json").write_text(json.dumps({"v": 1}), encoding="utf-8")
        with patch.object(D, "_atomic_csv_write", side_effect=AssertionError("CSV write")):
            actual = D._cached("legacy", lambda: self.fail("should migrate"), {"v": 1})
            pd.testing.assert_frame_equal(actual, storage.read("legacy"), check_freq=False)
            initial = D.data_stamp()
            D._cached("legacy", lambda: self.fail("cache miss"), {"v": 1})
            D._cache_info_write("legacy", {"n_missing": 0})
            self.assertEqual(initial, D.data_stamp())
            updated = self.frame * 2
            D._cached("legacy", lambda: updated, {"v": 2})
            self.assertNotEqual(initial, D.data_stamp())
            # P2-27：刷新失败但旧缓存 meta 仍匹配 → 带 stale 标记沿用旧数据，
            # 不再让一次网络故障打断分析（失败原因写进 info.last_error）。
            fallback = D._cached("legacy", lambda: (_ for _ in ()).throw(RuntimeError("fetch")), {"v": 2}, refresh=True)
            self.assertTrue(fallback.attrs["stale"])
            self.assertIn("RuntimeError", fallback.attrs["source_error"])
            self.assertIn("last_error", storage.metadata("legacy")["info"])
            pd.testing.assert_frame_equal(storage.read("legacy"), updated, check_freq=False)
            self.assertTrue((C.DATA_DIR / "legacy.csv").exists())

    def test_cross_process_writers(self):
        import subprocess
        code = "from pathlib import Path; import sys,pandas as pd; from src import storage,config as C; C.DATA_DIR=Path(sys.argv[1]); storage.write(sys.argv[2],pd.DataFrame({'x':[1.]}, index=pd.date_range('2020-01-01',periods=1)),{'v':1})"
        children = [subprocess.Popen([sys.executable, "-c", code, self.tmp.name, key], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for key in ("one", "two")]
        for child in children:
            _, error = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
        self.assertIsNotNone(storage.read("one"))
        self.assertIsNotNone(storage.read("two"))

    def test_fund_stamp_ignores_stocks_but_tracks_indices(self):
        storage.write("fund_nav_example", self.frame, {})
        initial = D.data_stamp(exclude_stocks=True)
        global_initial = D.data_stamp()
        for name in ("market_v1_stock_sh600519_qfq_2020", "sync_history_stock_sh600519_qfq"):
            storage.write(name, self.frame, {})
        self.assertEqual(initial, D.data_stamp(exclude_stocks=True))
        self.assertNotEqual(global_initial, D.data_stamp())
        storage.write("index_universe_example", self.frame, {})
        self.assertNotEqual(initial, D.data_stamp(exclude_stocks=True))

    def test_run_summary_uses_database_instead_of_csv_backups(self):
        from market_research.research.runinfo import data_summary
        storage.write("fund_nav_example", self.frame, {})
        storage.write("index_universe_example", self.frame, {})
        self.frame.to_csv(C.DATA_DIR / "fund_nav_old_backup.csv")
        summary = data_summary()
        self.assertEqual(summary["storage"], "duckdb")
        self.assertEqual(summary["n_datasets"], 2)
        self.assertEqual(summary["n_fund_nav_cached"], 1)
        self.assertEqual(summary["n_index_cache"], 1)
        self.assertEqual(summary["n_price_rows"], 8)

    def test_fund_stamp_isolates_other_funds_and_keeps_shared_dependencies(self):
        names = ["fund_nav_110022_2016-01-01_total", "sync_history_fund_110022_total"]
        for name in names:
            storage.write(name, self.frame, {})
        stamp = lambda: D.data_stamp(exclude_stocks=True, fund_code="110022")
        initial = stamp()
        for name in ["fund_nav_000001_2016-01-01_total", "sync_history_fund_000001_total"]:
            storage.write(name, self.frame, {})
        self.assertEqual(initial, stamp())
        for name in names + ["index_universe_2016-01-01", "pe_panel", "unknown_dependency"]:
            previous = stamp()
            storage.write(name, self.frame * 2, {})
            self.assertNotEqual(previous, stamp(), name)

    def test_fund_stamp_narrows_to_fetch_window(self):
        # P1 修复轮：传入抓取窗口后，stamp 只统计本基金本窗口的实际依赖 ——
        # 其它窗口的重复缓存（同步进程会刷新它们）不再让结果缓存失效。
        for name in ("fund_nav_110022_2016-01-01_total",
                     "fund_nav_110022_2015-01-01_total",
                     "index_universe_2016-01-01", "index_universe_2015-01-01",
                     "pe_panel"):
            storage.write(name, self.frame, {})
        stamp = lambda: D.data_stamp(exclude_stocks=True, fund_code="110022",
                                     fetch_start="2016-01-01")
        initial = stamp()
        # 其它窗口的缓存被刷新（同步进程就是这么做的）→ 指纹不变
        for name in ("fund_nav_110022_2015-01-01_total",
                     "index_universe_2015-01-01"):
            storage.write(name, self.frame * 3, {})
        self.assertEqual(initial, stamp(), name)
        # 本窗口依赖与共享估值仍然计入
        for name in ("fund_nav_110022_2016-01-01_total",
                     "index_universe_2016-01-01", "pe_panel"):
            previous = stamp()
            storage.write(name, self.frame * 2, {})
            self.assertNotEqual(previous, stamp(), name)

    def test_prune_universe_windows_removes_only_stale_windows(self):
        for name in ("index_universe_2016-01-01", "index_universe_2015-01-01",
                     "fund_nav_110022_2015-01-01_total", "pe_panel"):
            storage.write(name, self.frame, {})
        removed = storage.prune_universe_windows(["index_universe_2016-01-01"])
        self.assertEqual(removed, ["index_universe_2015-01-01"])
        self.assertIsNone(storage.read("index_universe_2015-01-01"))
        self.assertIsNotNone(storage.read("index_universe_2016-01-01"))
        # 只动 index_universe_ 命名空间
        self.assertIsNotNone(storage.read("fund_nav_110022_2015-01-01_total"))
        self.assertIsNotNone(storage.read("pe_panel"))
        self.assertEqual(storage.summary()["n_index_cache"], 1)

    def test_transaction_rollback(self):
        storage.write("x", self.frame, {"v": 1})
        invalid = self.frame.astype(object)
        invalid.iloc[0, 0] = "not a price"
        with self.assertRaises(Exception):
            storage.write("x", invalid, {"v": 2})
        pd.testing.assert_frame_equal(storage.read("x"), self.frame, check_freq=False)
        self.assertEqual(storage.metadata("x"), {"v": 1})

    def test_metadata_basis_changes_invalidate_results_but_info_does_not(self):
        storage.write("x", self.frame, {"v": 1})
        initial = storage.stamp()
        storage.write_metadata("x", {"v": 2})
        changed = storage.stamp()
        self.assertNotEqual(initial, changed)
        D._cache_info_write("x", {"fetched_at": "later"})
        self.assertEqual(changed, storage.stamp())
        self.assertEqual(storage.metadata("x"), {"v": 2, "info": {"fetched_at": "later"}})

    def test_descriptive_updates_do_not_replace_current_metadata(self):
        storage.write("x", self.frame, {"v": 2, "info": {"source": "original"}})
        with patch.object(storage, "metadata", side_effect=AssertionError("unlocked read")):
            D._cache_info_write("x", {"n_missing": 0})
        self.assertEqual(storage.metadata("x"), {"v": 2, "info": {"source": "original", "n_missing": 0}})

    def test_connection_times_out_with_explicit_busy_error(self):
        """P2-30：同步长时间持锁时，请求必须超时并给出明确原因。"""
        acquired = threading.Event()
        release = threading.Event()

        def hold_lock():
            storage._LOCK.acquire()
            acquired.set()
            release.wait(5)
            storage._LOCK.release()

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        acquired.wait(2)
        try:
            with self.assertRaises(StorageBusy) as ctx:
                with storage.connection(timeout=0.15):
                    pass
            self.assertIn("仍未获得锁", str(ctx.exception))
            self.assertIn("同步", str(ctx.exception))
        finally:
            release.set()
            holder.join(2)
        self.assertGreaterEqual(storage.LOCK_STATS["timeouts"], 1)


class CorruptMetadataTests(unittest.TestCase):
    """110：单条损坏 metadata 不能让全库数据指纹（结果缓存键）计算失败。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_stamp_survives_corrupt_metadata(self):
        frame = pd.DataFrame({"a": [1., 2.]},
                             index=pd.date_range("2020-01-01", periods=2))
        storage.write("good", frame, {"v": 1})
        baseline = D.data_stamp()
        with storage.connection() as db:
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)",
                       ["broken", "{not-json", "{}", "deadbeef"])
        with self.assertLogs("market_research.data.storage", level="WARNING"):
            changed = D.data_stamp()
        self.assertNotEqual(baseline, changed)


if __name__ == "__main__":
    unittest.main()
