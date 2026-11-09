"""Incomplete index pools recover without refetching successful series."""
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research import config as C
from market_research.data import panels, quality as quality_mod, storage
from market_research.errors import CacheMiss, NavBasisUnavailable


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
        before = storage.metadata("index_universe_2024-01-01")
        with patch.object(panels, "fetch_universe") as fetch:
            result = panels.load_index_universe("2024-01-01", verbose=False, allow_fetch=False)
            fetch.assert_not_called()
        pd.testing.assert_frame_equal(result, self.frame, check_freq=False)
        # 缺列照旧留痕（只挂在返回值 attrs），但只读请求不改共享元数据
        self.assertEqual(result.attrs["missing_assets"], ["missing"])
        self.assertEqual(storage.metadata("index_universe_2024-01-01"), before)


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


class EmptyNavTests(unittest.TestCase):
    """空净值必须在 verbose 下也给出明确错误，而不是打印时越界。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def test_verbose_load_reports_missing_data_instead_of_index_error(self):
        idx = pd.bdate_range("2024-01-01", periods=10)
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]), name="nav")
        empty.attrs.update(nav_basis=panels.NAV_BASIS_TOTAL, nav_degraded=False,
                           nav_degraded_reason="")
        px = pd.DataFrame({"801780": 100.0 + np.arange(len(idx))}, index=idx)
        with patch.object(panels, "load_fund_nav_cached", return_value=empty), \
             patch.object(panels, "load_index_universe", return_value=px):
            with self.assertRaisesRegex(ValueError, "没有可用数据"):
                panels.load_panel("110022", start="2024-01-01",
                                  end=idx[-1].strftime("%Y-%m-%d"), verbose=True)


class NavBasisPolicyTests(unittest.TestCase):
    """P1-20：total 口径构造失败时默认失败，不得用累计净值冒充。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.path_patch.start()
        self.idx = pd.bdate_range("2024-01-01", periods=5)
        self.cache_name = "fund_nav_110022_2024-01-01_total"

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def _series(self, value, degraded=False):
        series = pd.Series(value + np.arange(len(self.idx), dtype=float),
                           index=self.idx, name="nav")
        if degraded:
            series.attrs["degraded"] = True
        return series

    def _load(self, policy, total=None, cum=None):
        with patch.object(C, "NAV_DEGRADED_POLICY", policy), \
             patch("market_research.tasks.sync.register"), \
             patch("market_research.tasks.sync.cached", return_value=None), \
             patch.object(panels, "fetch_fund_total_return",
                          return_value=total if total is not None else self._series(100.0, True)), \
             patch.object(panels, "fetch_fund_nav",
                          return_value=cum if cum is not None else self._series(90.0)):
            return panels.load_fund_nav_cached("110022", "2024-01-01")

    def test_reject_policy_raises_and_writes_nothing(self):
        with self.assertRaises(NavBasisUnavailable) as ctx:
            self._load("reject")
        message = str(ctx.exception)
        self.assertIn("累计净值", message)
        self.assertIn("受影响口径", message)
        self.assertFalse((Path(self.directory.name) / f"{self.cache_name}.csv").exists())
        self.assertIsNone(storage.metadata(self.cache_name))

    def test_allow_cum_returns_marked_series_without_caching(self):
        series = self._load("allow_cum")
        self.assertEqual(series.attrs["nav_basis"], panels.NAV_BASIS_CUM)
        self.assertTrue(series.attrs["nav_degraded"])
        self.assertIn("110022", series.attrs["nav_degraded_reason"])
        self.assertEqual(series.name, "fund_110022")
        self.assertIsNone(storage.metadata(self.cache_name))

    def test_successful_total_series_is_cached_and_round_trips(self):
        first = self._load("reject", total=self._series(100.0))
        self.assertEqual(first.attrs["nav_basis"], panels.NAV_BASIS_TOTAL)
        self.assertFalse(first.attrs["nav_degraded"])
        meta = storage.metadata(self.cache_name)
        self.assertEqual(meta["basis"], panels.NAV_BASIS_TOTAL)
        self.assertEqual(meta["v"], C.DATA_CACHE_VERSION)

        second = self._load("reject", total=self._series(999.0))
        pd.testing.assert_series_equal(first, second, check_freq=False)

    def test_manual_refresh_is_used_by_next_normal_read(self):
        from datetime import datetime
        from market_research.tasks import sync
        end = self.idx[-1].strftime("%Y-%m-%d")
        name = sync.history_name({"asset_key": "fund:110022:total"})
        old = self._series(100.0).to_frame()
        storage.write(name, old, {"through": end, "info": {"data_end": end}})
        fresh = self._series(200.0)
        clock = datetime(2024, 1, 5, 18, 40, tzinfo=sync.SHANGHAI)
        with patch.object(sync, "now", return_value=clock), \
             patch("market_research.tasks.sync.register"), \
             patch("market_research.data.marketdata.fetch_fund_total_return",
                   return_value=fresh) as fetch:
            refreshed = panels.load_fund_nav_cached(
                "110022", "2024-01-01", refresh=True)
            again = panels.load_fund_nav_cached("110022", "2024-01-01")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(refreshed.iloc[0], 200.0)
        self.assertEqual(again.iloc[0], 200.0)

    def test_load_panel_exposes_degraded_basis_and_warning(self):
        nav = self._series(90.0)
        nav.attrs.update(nav_basis=panels.NAV_BASIS_CUM, nav_degraded=True,
                         nav_degraded_reason="110022 无法构造总回报口径")
        px = pd.DataFrame({"801780": 100.0 + np.arange(len(self.idx))}, index=self.idx)
        with patch.object(panels, "load_fund_nav_cached", return_value=nav), \
             patch.object(panels, "load_index_universe", return_value=px):
            out = panels.load_panel("110022", start="2024-01-01",
                                    end=self.idx[-1].strftime("%Y-%m-%d"),
                                    verbose=False)
        self.assertEqual(out.attrs["nav_basis"], panels.NAV_BASIS_CUM)
        self.assertTrue(out.attrs["nav_degraded"])
        self.assertTrue(any("降级" in w for w in out.attrs["warnings"]))


class NavBasisMetaTests(unittest.TestCase):
    def test_meta_follows_actual_series_basis(self):
        from market_research.funds.analysis import nav_basis_meta
        total = nav_basis_meta({"nav_basis": "total"})
        self.assertIn("锚点校准", total["data_basis"])
        self.assertEqual(total["nav_basis"], "total")
        degraded = nav_basis_meta({"nav_basis": "cum", "nav_degraded": True,
                                   "nav_degraded_reason": "缺锚点"})
        self.assertIn("累计净值", degraded["data_basis"])
        self.assertNotIn("锚点校准", degraded["data_basis"])
        self.assertEqual(degraded["nav_degraded_reason"], "缺锚点")
        legacy = nav_basis_meta({"nav_basis": "cum"})
        self.assertIn("收益口径偏低", legacy["data_basis"])


class CompletenessTests(unittest.TestCase):
    """P2-31：结果完整性契约。"""

    def _payload(self, r2=0.5, error=None):
        block = {"metrics": {"oos_r2": r2}, "extra": {}}
        if error:
            block["extra"]["error"] = error
        return {"m": block}

    def test_partial_when_a_model_or_optional_section_missing(self):
        from market_research.funds.analysis import result_completeness
        comp = result_completeness(
            payload=self._payload(), correlation={"top": [{"code": "801780"}]},
            forecast={"available": True}, valuation={"available": False,
                                                     "note": "PE 覆盖不足"},
            drift={"ok": True}, quality={"status": "ok"})
        self.assertEqual(comp["status"], "partial")
        self.assertTrue(comp["ok"])
        self.assertEqual(comp["sections"]["models"],
                         {"status": "ok", "completed": 1, "total": 1})
        self.assertEqual([m["section"] for m in comp["missing"]], ["valuation"])

    def test_failed_when_all_models_fail(self):
        from market_research.funds.analysis import result_completeness
        comp = result_completeness(
            payload=self._payload(r2=None, error="拟合失败"),
            correlation={"top": []}, forecast={"available": False, "reason": "样本不足"},
            valuation={"available": False}, drift={"ok": False, "reason": "无结果"},
            quality={"status": "ok"})
        self.assertEqual(comp["status"], "failed")
        self.assertFalse(comp["ok"])
        self.assertIn("拟合失败", " ".join(m["reason"] for m in comp["missing"]))
        self.assertEqual(comp["sections"]["models"]["completed"], 0)


class DataQualityTests(unittest.TestCase):
    """P2-27：过期 / 缺失 / 口径降级统一成一份 data_quality。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def test_ok_when_fresh_and_complete(self):
        quality = quality_mod.build_quality(
            data_end=date.today(), missing_assets=[], n_expected=47)
        self.assertEqual(quality["status"], "ok")
        self.assertFalse(quality["stale"])
        self.assertEqual(quality["reasons"], [])

    def test_stale_marks_partial_with_reason(self):
        quality = quality_mod.build_quality(
            data_end=date.today() - timedelta(days=30), n_expected=47)
        self.assertEqual(quality["status"], "partial")
        self.assertTrue(quality["stale"])
        self.assertEqual(quality["stale_days"], 30)
        self.assertTrue(any("阈值" in r for r in quality["reasons"]))

    def test_missing_assets_escalate_by_ratio(self):
        few = quality_mod.build_quality(data_end=date.today(),
                                        missing_assets=["801780"], n_expected=47)
        self.assertEqual(few["status"], "partial")
        self.assertEqual(few["n_missing"], 1)
        many = quality_mod.build_quality(data_end=date.today(),
                                         missing_assets=[f"S{i}" for i in range(12)],
                                         n_expected=47)
        self.assertEqual(many["status"], "degraded")
        self.assertGreater(many["missing_ratio"], C.DATA_MISSING_RATIO_LIMIT)

    def test_nav_degrade_and_source_error_are_degraded(self):
        quality = quality_mod.build_quality(
            data_end=date.today(), nav_degraded=True,
            nav_reason="缺单位净值", source_error="TimeoutError: 超时")
        self.assertEqual(quality["status"], "degraded")
        self.assertIn("累计净值", " ".join(quality["reasons"]))
        self.assertIn("取数失败", " ".join(quality["reasons"]))

    def test_cached_fallback_is_marked_not_silent(self):
        from market_research.data.cache import _cached, _with_info
        meta = {"v": 1, "code": "110022"}
        frame = pd.DataFrame({"nav": [1.0, 1.1]},
                             index=pd.bdate_range("2024-01-01", periods=2))
        storage.write("quality_demo", frame, _with_info(meta, frame))

        def broken():
            raise TimeoutError("上游超时")

        out = _cached("quality_demo", broken, meta=meta, refresh=True)
        self.assertTrue(out.attrs["stale"])
        self.assertIn("TimeoutError", out.attrs["source_error"])
        self.assertIn("last_error", storage.metadata("quality_demo")["info"])
        pd.testing.assert_frame_equal(out, frame, check_freq=False)

    def test_cached_fallback_refuses_invalid_meta(self):
        from market_research.data.cache import _cached, _with_info
        frame = pd.DataFrame({"nav": [1.0]}, index=pd.bdate_range("2024-01-01", periods=1))
        storage.write("quality_demo", frame, _with_info({"v": 1}, frame))

        def broken():
            raise TimeoutError("上游超时")

        with self.assertRaises(TimeoutError):
            _cached("quality_demo", broken, meta={"v": 2}, refresh=True)

    def test_load_panel_reports_missing_candidates(self):
        idx = pd.bdate_range("2024-01-01", periods=60)
        nav = pd.Series(100.0 + np.arange(60), index=idx, name="nav")
        nav.attrs.update(nav_basis=panels.NAV_BASIS_TOTAL, nav_degraded=False,
                         nav_degraded_reason="")
        px = pd.DataFrame({"801780": 100.0 + np.arange(60)}, index=idx)
        px.attrs.update(missing_assets=["000300"], n_expected=3)
        with patch.object(panels, "load_fund_nav_cached", return_value=nav), \
             patch.object(panels, "load_index_universe", return_value=px):
            out = panels.load_panel("110022", start="2024-01-01",
                                    end=idx[-1].strftime("%Y-%m-%d"),
                                    verbose=False)
        quality = out.attrs["data_quality"]
        self.assertEqual(quality["missing_assets"], ["000300"])
        self.assertEqual(quality["n_expected"], 3)
        self.assertNotEqual(quality["status"], "ok")
        self.assertTrue(any("000300" in w for w in out.attrs["warnings"]))


class ReadOnlyNoWriteTests(unittest.TestCase):
    """108：allow_fetch=False 的请求线程不得产生任何 DuckDB 写副作用。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.path_patch.start()
        self.frame = pd.DataFrame(
            {"first": [1., 2., 3.]}, index=pd.date_range("2024-01-01", periods=3))
        storage.write("index_universe_2024-01-01", self.frame,
                      {"v": C.DATA_CACHE_VERSION,
                       "n_candidates": len(C.NAME_OF),
                       "start": "2024-01-01", "end": C.END_DATE})

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def test_universe_read_only_never_writes_metadata_or_prices(self):
        before = storage.metadata("index_universe_2024-01-01")
        with patch.object(panels, "fetch_universe") as fetch, \
             patch.object(panels, "_cache_info_write") as info_write, \
             patch.object(panels.storage, "write") as write:
            out = panels.load_index_universe("2024-01-01", verbose=False,
                                            allow_fetch=False)
            fetch.assert_not_called()
            info_write.assert_not_called()
            write.assert_not_called()
        self.assertEqual(storage.metadata("index_universe_2024-01-01"), before)
        self.assertEqual(list(out.columns), ["first"])

    def test_fund_nav_read_only_does_not_register_sync_asset(self):
        import market_research.tasks.sync as sync
        with patch.object(sync, "register") as register, \
             patch.object(sync, "cached", return_value=None), \
             patch.object(panels, "fetch_fund_total_return") as fetch:
            with self.assertRaises(CacheMiss):
                panels.load_fund_nav_cached("110022", "2024-01-01",
                                            allow_fetch=False)
            register.assert_not_called()
            fetch.assert_not_called()


class CorruptCacheTests(unittest.TestCase):
    """110：metadata 损坏时缓存应自愈，而不是把冷缓存 202 打成 500。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.path_patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.path_patch.start()
        self.frame = pd.DataFrame(
            {"nav": [1., 1.1]},
            index=pd.date_range("2024-01-01", periods=2, name="date"))

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    @staticmethod
    def _corrupt(name="broken"):
        with storage.connection() as db:
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)",
                       [name, "{not-json", "{}", "deadbeef"])

    def test_corrupt_metadata_rebuilt_by_builder(self):
        from market_research.data.cache import _cached
        self._corrupt()
        with self.assertLogs("market_research.data.cache", level="WARNING"):
            out = _cached("broken", lambda: self.frame, meta={"v": 1})
        pd.testing.assert_frame_equal(out, self.frame, check_freq=False)
        pd.testing.assert_frame_equal(storage.read("broken"), self.frame,
                                      check_freq=False)

    def test_corrupt_metadata_raises_cachemiss_in_read_only(self):
        from market_research.data.cache import _cached
        self._corrupt()
        with self.assertRaises(CacheMiss):
            _cached("broken", lambda: self.fail("只读模式不得抓取"),
                    meta={"v": 1}, allow_fetch=False)

    def test_corrupt_sidecar_meta_does_not_block_rebuild(self):
        from market_research.data.cache import _cached
        (C.DATA_DIR / "broken.meta.json").write_text("{bad", encoding="utf-8")
        out = _cached("broken", lambda: self.frame, meta={"v": 1})
        pd.testing.assert_frame_equal(out, self.frame, check_freq=False)

    def test_info_write_on_corrupt_metadata_does_not_raise(self):
        from market_research.data.cache import _cache_info_write
        self._corrupt()
        with self.assertLogs("market_research.data.cache", level="WARNING"):
            _cache_info_write("broken", {"n_missing": 0})


class FetchErrorVisibilityTests(unittest.TestCase):
    """112：短序列/抓取失败原因必须一路透传到 panel warnings。"""
    def test_universe_keeps_builder_fetch_errors(self):
        frame = pd.DataFrame({"first": [1., 2.]},
                             index=pd.date_range("2024-01-01", periods=2))
        frame.attrs["fetch_errors"] = ["missing: 仅 2 行"]
        with tempfile.TemporaryDirectory(dir=C.ROOT / "output") as folder:
            with patch.object(C, "DATA_DIR", Path(folder)), \
                 patch.object(C, "NAME_OF", {"first": "First", "missing": "Missing"}), \
                 patch.object(panels, "fetch_universe", return_value=frame):
                out = panels.load_index_universe("2024-01-01", verbose=False)
                info = storage.metadata("index_universe_2024-01-01")["info"]
            self.assertIn("仅 2 行", " ".join(out.attrs["fetch_errors"]))
            self.assertIn("仅 2 行", " ".join(info["fetch_errors"]))

    def test_short_series_reason_reaches_panel_warnings(self):
        idx = pd.bdate_range("2024-01-01", periods=60)
        nav = pd.Series(100.0 + np.arange(60), index=idx, name="nav")
        nav.attrs.update(nav_basis=panels.NAV_BASIS_TOTAL, nav_degraded=False,
                         nav_degraded_reason="")
        px = pd.DataFrame({"801780": 100.0 + np.arange(60)}, index=idx)
        px.attrs.update(missing_assets=["000300"], n_expected=3,
                        fetch_errors=["000300: 仅 120 行"])
        with patch.object(panels, "load_fund_nav_cached", return_value=nav), \
             patch.object(panels, "load_index_universe", return_value=px):
            out = panels.load_panel("110022", start="2024-01-01",
                                    end=idx[-1].strftime("%Y-%m-%d"),
                                    verbose=False)
        self.assertTrue(any("仅 120 行" in w for w in out.attrs["warnings"]))

    def test_verbose_false_still_logs_short_series(self):
        from market_research.data.providers import indices

        def fake(code, start, end):
            n = 300 if code == "good" else 2
            return code, pd.Series(np.arange(n, dtype=float) + 1,
                                   index=pd.date_range("2024-01-01", periods=n),
                                   name=code)

        with patch.object(indices, "_fetch_one", side_effect=fake), \
             self.assertLogs(indices.LOG, level="WARNING") as records:
            out = indices.fetch_universe(["good", "short"], "2024-01-01",
                                         verbose=False)
        self.assertEqual(list(out.columns), ["good"])
        self.assertIn("仅 2 行", " ".join(records.output))
