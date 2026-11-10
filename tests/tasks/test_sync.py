"""Durability, scheduling, corporate-action replacement and failure recovery."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import market_research.config as C
import market_research.data.storage as storage
import market_research.tasks.sync as sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        valuation = patch.object(sync, "_accumulate_valuation")
        valuation.start()
        self.addCleanup(valuation.stop)
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.config = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.config.start()
        self.current = sync.datetime(2026, 10, 30, 18, 31, tzinfo=sync.SHANGHAI)
        self.frame = pd.DataFrame({"close": [10., 11., 12.]}, index=pd.to_datetime(["2026-10-20", "2026-10-29", "2026-10-30"]))

    def tearDown(self):
        self.config.stop()
        self.directory.cleanup()

    def test_daily_schedule_catchup_and_retry(self):
        with patch.dict(os.environ, {"FIL_SYNC_TIME": "18:30"}):
            asset = {"last_success": "2026-10-29T18:31:00+08:00"}
            self.assertFalse(sync.due(asset, self.current.replace(hour=18, minute=29)))
            self.assertTrue(sync.due(asset, self.current))
            asset["last_attempt"] = self.current.isoformat()
            self.assertFalse(sync.due(asset, self.current))
            self.assertTrue(sync.due(asset, self.current + sync.timedelta(minutes=31)))
            self.assertTrue(sync.due({}, self.current.replace(hour=9)))

    def test_registered_only_and_persistent_success(self):
        sync.register("stock", "600519", "qfq")
        sync.register("stock", "sh600519", "qfq")
        with patch.object(sync, "fetch", return_value=self.frame) as fetch:
            sync.tick(self.current)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.args[1], "1990-01-01")
            sync.tick(self.current + sync.timedelta(minutes=31))
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(sync.assets()), 1)
        self.assertEqual(sync.assets()[0]["mode"], "full")

    def test_adjusted_history_replaced_after_distribution(self):
        sync.register("stock", "600519", "qfq")
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current)
        revised = self.frame * .9
        with patch.object(sync, "fetch", return_value=revised) as fetch:
            sync.tick(self.current + sync.timedelta(days=1))
        self.assertEqual(fetch.call_args.args[1], "1990-01-01")
        actual = storage.read(sync.history_name(sync.assets()[0]))
        pd.testing.assert_frame_equal(actual, revised, check_names=False)

    def test_incremental_unadjusted_preserves_old_history(self):
        sync.register("stock", "600519", "")
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current)
        incoming = pd.DataFrame({"close": [13., 14.]}, index=pd.to_datetime(["2026-10-30", "2026-10-31"]))
        with patch.object(sync, "fetch", return_value=incoming) as fetch:
            sync.tick(self.current + sync.timedelta(days=1))
        self.assertEqual(fetch.call_args.args[1], "2026-10-20")
        actual = storage.read(sync.history_name(sync.assets()[0]))
        self.assertEqual(actual.close.tolist(), [10., 11., 13., 14.])
        self.assertEqual(sync.assets()[0]["mode"], "incremental")

    def test_failure_and_truncated_adjusted_response_preserve_data(self):
        sync.register("stock", "600519", "hfq")
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current)
        with patch.object(sync, "fetch", return_value=self.frame.iloc[1:]):
            sync.tick(self.current + sync.timedelta(days=1))
        self.assertIn("截断", sync.assets()[0]["error"])
        actual = storage.read(sync.history_name(sync.assets()[0]))
        pd.testing.assert_frame_equal(actual, self.frame, check_names=False)
        with patch.object(sync, "fetch", side_effect=RuntimeError("offline")):
            sync.tick(self.current + sync.timedelta(days=1, hours=1))
        self.assertEqual(sync.assets()[0]["error"], "offline")

    def test_discovery_uses_only_existing_pool_columns_and_serves_history(self):
        storage.write("index_universe_2026-01-01", self.frame.rename(columns={"close":"sh000300"}), {"start":"2026-01-01", "end":None})
        sync.discover()
        self.assertEqual([x["code"] for x in sync.assets()], ["sh000300"])
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current)
        actual = sync.cached("index", "sh000300", "", "2026-10-29", "2026-10-30")
        self.assertEqual(len(actual), 2)
        self.assertIsNone(sync.cached("index", "sh000300", "", "2026-10-29", "2026-10-31"))

    def test_single_scheduler_and_fund_degradation_guard(self):
        with sync.leader() as first:
            with sync.leader() as second:
                self.assertTrue(first)
                self.assertFalse(second)
        import market_research.data.marketdata as D
        series = self.frame.close.copy()
        series.attrs["degraded"] = True
        with patch.object(D, "fetch_fund_total_return", return_value=series):
            with self.assertRaisesRegex(ValueError, "降级"):
                sync.fetch({"kind":"fund", "code":"110022", "basis":"total"}, sync.HISTORY_START, "2026-10-30")

    def test_manual_refresh_overlays_canonical_without_truncating_history(self):
        name = sync.history_name({"asset_key": "stock:sh600519:"})
        storage.write(name, self.frame, {
            "through": "2026-10-30", "info": {"data_end": "2026-10-30"}})
        incoming = self.frame.iloc[1:] * .9
        incoming.loc[pd.Timestamp("2026-10-31")] = [13.0]
        self.assertTrue(sync.overlay_refresh(
            "stock", "sh600519", "", incoming, "2026-10-31"))
        actual = sync.cached("stock", "sh600519", "",
                             "2026-10-01", "2026-10-31")
        self.assertEqual(actual.close.tolist(), [10.0, 11.0 * .9,
                                                12.0 * .9, 13.0])
        meta = storage.metadata(name)
        self.assertEqual(meta["through"], "2026-10-31")
        self.assertEqual(meta["info"]["data_end"], "2026-10-31")

    def test_adjusted_partial_refresh_cannot_mix_adjustment_bases(self):
        name = sync.history_name({"asset_key": "stock:sh600519:qfq"})
        storage.write(name, self.frame, {"through": "2026-10-30"})
        with self.assertRaisesRegex(ValueError, "拒绝拼接部分历史"):
            sync.overlay_refresh("stock", "sh600519", "qfq",
                                 self.frame.iloc[1:] * .9, "2026-10-30")
        pd.testing.assert_frame_equal(storage.read(name), self.frame,
                                      check_freq=False)

    def test_manual_refresh_does_not_create_partial_canonical(self):
        self.assertFalse(sync.overlay_refresh(
            "stock", "sh600519", "qfq", self.frame, "2026-10-30"))
        name = sync.history_name({"asset_key": "stock:sh600519:qfq"})
        self.assertIsNone(storage.read(name))

    def test_manual_retry_is_durable_without_erasing_success(self):
        key = sync.register("stock", "600519", "qfq")
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current)
        original = sync.assets()[0]["last_success"]
        self.assertTrue(sync.request_sync(key))
        asset = sync.assets()[0]
        self.assertEqual(asset["state"], "pending")
        self.assertEqual(asset["last_success"], original)
        self.assertTrue(sync.due(asset, self.current + sync.timedelta(seconds=1)))
        with patch.object(sync, "fetch", return_value=self.frame):
            sync.tick(self.current + sync.timedelta(seconds=1))
        self.assertEqual(sync.assets()[0]["state"], "success")

    def test_active_retry_not_reset_and_api_validation(self):
        from market_research.api.app import app
        from fastapi.testclient import TestClient
        key = sync.register("stock", "600519", "qfq")
        with storage.connection() as db:
            db.execute("UPDATE sync_assets SET state='running',last_attempt=? WHERE asset_key=?", [sync.now().isoformat(),key])
        self.assertFalse(sync.request_sync(key))
        client = TestClient(app)
        for route, body in [("/api/sync/assets",[1]), ("/api/sync/assets",{"kind":"stock","code":"bad"}), ("/api/sync/retry",{}), ("/api/sync/retry",{"asset_key":"unknown"})]:
            self.assertEqual(client.post(route,json=body).status_code,400)
        response = client.post("/api/sync/assets",json={"kind":"index","code":"sh000300"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(client.get('/api/sync').json()['assets']),2)


class DataEndFreshnessTests(unittest.TestCase):
    """113：through 只是检查日，不能替代真实数据末日判新鲜。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.config = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.config.start()
        self.name = sync.history_name({"asset_key": "index:sh000300:"})
        self.frame = pd.DataFrame(
            {"close": [10., 11.]},
            index=pd.to_datetime(["2026-10-29", "2026-10-30"]))

    def tearDown(self):
        self.config.stop()
        self.directory.cleanup()

    def test_lagging_source_is_not_served_as_fresh(self):
        storage.write(self.name, self.frame,
                      {"through": "2026-10-30",
                       "info": {"data_end": "2026-10-01"}})
        self.assertIsNone(sync.cached("index", "sh000300", "",
                                      "2026-10-01", "2026-10-30"))

    def test_weekend_lag_still_serves(self):
        storage.write(self.name, self.frame,
                      {"through": "2026-11-08",
                       "info": {"data_end": "2026-11-06"}})
        out = sync.cached("index", "sh000300", "", "2026-10-01", "2026-11-08")
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 2)

    def test_legacy_meta_without_data_end_keeps_old_behavior(self):
        storage.write(self.name, self.frame, {"through": "2026-11-08"})
        self.assertIsNotNone(sync.cached("index", "sh000300", "",
                                         "2026-10-01", "2026-11-08"))

    def test_corrupt_sync_metadata_is_treated_as_not_ready(self):
        with storage.connection() as db:
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)",
                       [self.name, "{not-json", "{}", "deadbeef"])
        with self.assertLogs(sync.LOG, level="WARNING"):
            self.assertIsNone(sync.cached("index", "sh000300", "",
                                          "2026-10-01", "2026-10-30"))

    def test_datasets_skips_corrupt_rows(self):
        storage.write(self.name, self.frame, {"through": "2026-11-08"})
        with storage.connection() as db:
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)",
                       ["broken", "{not-json", "{}", "deadbeef"])
        with self.assertLogs(sync.LOG, level="WARNING"):
            rows = sync.datasets()
        names = [name for name, _, _ in rows]
        self.assertIn(self.name, names)
        self.assertNotIn("broken", names)

    def test_settlement_can_read_history_older_than_check_date(self):
        # 结算只需要判定窗口内的历史收盘，不能因“今天还没同步”而视为无数据。
        storage.write(self.name, self.frame,
                      {"through": "2026-10-30",
                       "info": {"data_end": "2026-10-30"}})
        self.assertIsNone(sync.cached("index", "sh000300", "",
                                      "2026-10-01", "2026-11-09"))
        out = sync.cached("index", "sh000300", "", "2026-10-01", "2026-11-09",
                          fresh=False)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 2)


class AssetWatchdogTests(unittest.TestCase):
    """P2-37：单资产超时必须被隔离，且后续资产继续执行。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.config = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.directory.cleanup()

    def test_timeout_does_not_block_next_asset_or_write_after_abort(self):
        first = sync.register("stock", "600519", "qfq")
        second = sync.register("stock", "000001", "qfq")

        def fake_synchronize(asset, current, abort=None):
            if str(asset["code"]).endswith("600519"):
                while not abort.wait(0.01):
                    pass
                sync._check_abort(abort)
            return "full", 1

        with patch.dict(os.environ, {"FIL_SYNC_ASSET_TIMEOUT": "0.05"}, clear=False), \
             patch.object(sync, "discover"), \
             patch.object(sync, "_accumulate_valuation"), \
             patch.object(sync, "synchronize", side_effect=fake_synchronize):
            sync.tick(sync.now())
        states = {row["asset_key"]: row for row in sync.assets()}
        self.assertIn("watchdog_timeout", states[first]["error"])
        self.assertEqual(states[second]["state"], "success")

        with patch.object(storage, "write") as write:
            with self.assertRaises(sync.AssetSyncTimeout):
                sync._synchronize_with_watchdog(
                    {"asset_key": "late", "kind": "stock", "code": "600519",
                     "basis": "qfq"},
                    sync.now(), timeout=0.02, runner=fake_synchronize)
        write.assert_not_called()

    def test_aborted_synchronize_rejects_canonical_write(self):
        abort = sync.threading.Event()
        abort.set()
        with patch.object(storage, "write") as write, \
             self.assertRaises(sync.AssetSyncAborted):
            sync.synchronize({"asset_key": "x", "kind": "stock",
                              "code": "600519", "basis": "qfq"},
                             sync.now(), abort=abort)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
