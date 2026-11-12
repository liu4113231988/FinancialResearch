"""预测台账与结算口径的单元测试。"""
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_research.config as C
from market_research.predictions import judge, ledger


AS_OF = "2026-10-30"
SESSIONS = pd.bdate_range("2026-10-01", "2026-12-31")


def _market_result(as_of=AS_OF, signals=None, forecasts=None):
    return {
        "kind": "stock",
        "code": "sh600519",
        "name": "测试股票",
        "meta": {"adjust": "qfq",
                 "analysis_generated_at": f"{as_of}T15:03:00+08:00",
                 "data_quality": {"sources": {"asset": {
                     "request_started_at": f"{as_of}T15:01:00+08:00",
                     "observed_at": f"{as_of}T15:02:00+08:00",
                     "observed_data_end": as_of}}},
                 "provenance": {"git_commit": "abc123", "code_version": "1",
                                "data_stamp": "stamp"}},
        "technical": {
            "as_of": as_of,
            "score": 80,
            "action": "关注买入条件",
            "trend": "偏强",
            "latest": {"ma20": 10, "rsi": 55, "macd": 0.3},
            "context": {"environment": "偏强"},
            "levels": {"support": 9.5},
            "reasons": ["均线多头排列"],
            "forecasts": forecasts if forecasts is not None else [
                {"horizon": 5, "ok": True, "probability": 0.7,
                 "baseline": 0.52, "training_rows": 300,
                 "evaluation": {"accuracy": 0.55, "brier": 0.24}},
                {"horizon": 20, "ok": True, "probability": 0.45,
                 "baseline": 0.51, "training_rows": 300,
                 "evaluation": {"accuracy": 0.53, "brier": 0.25}},
            ],
            "signals": signals if signals is not None else [
                {"date": as_of, "type": "buy", "label": "买入信号",
                 "close": 10, "score": 80},
            ],
        },
    }


def _fund_analyze_result():
    return {
        "code": "110022",
        "name": "测试基金",
        "meta": {"nav_basis": "total",
                 "provenance": {"git_commit": "abc123", "code_version": "1",
                                "data_stamp": "stamp"}},
        "forecast": {
            "basis": "fund_nav", "as_of": AS_OF,
            "horizons": [
                {"horizon": 5, "ok": True, "probability": 0.62,
                 "baseline": 0.55, "training_rows": 400,
                 "evaluation": {"accuracy": 0.58, "brier": 0.23}},
                {"horizon": 20, "ok": True, "probability": 0.48,
                 "baseline": 0.54, "training_rows": 400,
                 "evaluation": {"accuracy": 0.54, "brier": 0.24}},
            ],
        },
    }


def _fund_backtest_result():
    return {
        "meta": {"code": "110022", "name": "测试基金", "model": "enet_slsqp",
                 "end": AS_OF, "thresholds": {"exit": -0.5, "enter": -0.2},
                 "health_stats": {"min": -0.6},
                 "provenance": {"git_commit": "abc123", "code_version": "1",
                                "data_stamp": "stamp"}},
        "current": {"date": AS_OF, "position": 0},
        "trades": [{"date": AS_OF, "action": "卖出", "health": -0.6,
                    "reason": "健康分跌破离场线"}],
    }


def _rising_bars():
    """从 100 起每个交易日 +1；保留日内高低价，避免被当成一字板。"""
    values = [100 + i for i in range(len(SESSIONS))]
    return pd.DataFrame({"open": values,
                         "high": [value + 1 for value in values],
                         "low": [value - 1 for value in values],
                         "close": values}, index=SESSIONS)


class LedgerRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()
        calendar = patch("market_research.data.providers.calendar.bundled_calendar", return_value=(SESSIONS, {}))
        calendar.start()
        self.addCleanup(calendar.stop)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_market_records_both_sources_and_dedupe(self):
        count = ledger.record_result("market", {}, _market_result(),
                                     now=datetime(2026, 11, 2))
        # 2 条方向概率 + 1 条规则事件（无固定方向周期）
        self.assertEqual(count, 3)
        self.assertEqual(ledger.record_result("market", {}, _market_result(),
                                             now=datetime(2026, 11, 2)), 0)
        rows = ledger._records()
        self.assertEqual({row["source"] for row in rows}, {"direction", "rule"})
        self.assertEqual({row["horizon"] for row in rows}, {0, 5, 20})
        self.assertEqual({row["regime"] for row in rows}, {"strong"})
        probability = [row for row in rows if row["source"] == "direction"]
        self.assertEqual(probability[0]["probability"], 0.7)
        rule = [row for row in rows if row["source"] == "rule"]
        self.assertEqual(len(rule), 1)
        params = json.loads(rule[0]["params"])
        self.assertEqual(params["horizon_semantics"], "none")
        self.assertEqual(params["evaluation_sessions"], ledger.RULE_EVAL_SESSIONS)
        for row in probability:
            recorded = json.loads(row["params"])
            self.assertEqual(recorded["thresholds"],
                             {"direction": 0.5, "entry": 0.6, "exit": 0.4})
            self.assertIn("horizon_basis", recorded)
            self.assertEqual(recorded["data_observed_at"], f"{AS_OF}T15:02:00+08:00")

    def test_market_registration_uses_completed_sessions_and_post_close_input(self):
        result = _market_result()
        asset = result["meta"]["data_quality"]["sources"]["asset"]
        with patch.object(ledger, "_insert", side_effect=len):
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 10, 30, 10)), 0)
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 11, 2, 16)), 0)
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 11, 8, 16)), 0)
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 10, 30, 16)), 3)
            with patch("market_research.data.providers.calendar.bundled_calendar",
                       return_value=(pd.to_datetime([AS_OF, "2026-11-10"]), {})):
                self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 11, 9, 16)), 3)
            asset["request_started_at"] = f"{AS_OF}T10:00:00+08:00"
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 10, 30, 16)), 0)
            asset["request_started_at"] = f"{AS_OF}T15:01:00+08:00"
            asset["observed_data_end"] = "2026-10-29"
            self.assertEqual(ledger.record_result("market", {}, result, now=datetime(2026, 10, 30, 16)), 0)

    def test_feature_buckets_are_available_for_summary(self):
        result = _market_result(signals=[])
        result["technical"]["latest"].update(
            {"atr": 1.0, "ma20": 100.0, "boll_upper": 115.0,
             "boll_lower": 90.0})
        result["technical"]["context"] = {
            "environment": "偏强", "latest": {"atr_percentile": 0.9}}
        self.assertEqual(ledger.record_result(
            "market", {}, result, now=datetime(2026, 11, 2)), 2)
        rows = ledger._records()
        self.assertEqual({row["vol_bucket"] for row in rows}, {"high"})
        self.assertEqual({row["trend_bucket"] for row in rows}, {"up"})
        vol = ledger.summary(group_by="vol_bucket")
        self.assertEqual([group["key"] for group in vol["groups"]], ["high"])
        trend = ledger.summary(group_by="trend_bucket")
        self.assertEqual([group["key"] for group in trend["groups"]], ["up"])
        self.assertEqual(vol["overall"]["decided"], 0)

    def test_historical_window_not_recorded(self):
        result = _market_result(as_of="2026-09-01", signals=[])
        self.assertEqual(ledger.record_result("market", {}, result,
                                              now=datetime(2026, 11, 8)), 0)

    def test_fund_probability_and_rule_records(self):
        self.assertEqual(ledger.record_result("analyze", {},
                                              _fund_analyze_result(),
                                              now=datetime(2026, 11, 2)), 2)
        self.assertEqual(ledger.record_result("backtest", {},
                                              _fund_backtest_result(),
                                              now=datetime(2026, 11, 2)), 1)
        rows = ledger._records()
        rule = [row for row in rows if row["source"] == "rule"]
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule[0]["asset_kind"], "fund")
        self.assertEqual(rule[0]["direction"], "down")
        self.assertEqual(rule[0]["price_basis"], "total")
        self.assertEqual(rule[0]["horizon"], ledger.RULE_HORIZON)
        self.assertEqual(json.loads(rule[0]["params"])["horizon_semantics"], "none")

    def test_historical_end_rejected_even_when_as_of_is_recent(self):
        with patch.object(ledger, "_insert") as insert:
            self.assertEqual(ledger.record_result(
                "market", {"end": "2026-10-30"}, _market_result(),
                now=datetime(2026, 11, 8)), 0)
        insert.assert_not_called()
        self.assertTrue(ledger.is_historical_window(
            {"window_end": "2026-10-30"}, now=datetime(2026, 11, 8)))
        self.assertFalse(ledger.is_historical_window(
            {"end": "2026-11-08"}, now=datetime(2026, 11, 8)))

    def test_degraded_nav_basis_kept_on_rule_record(self):
        result = _fund_backtest_result()
        result["meta"]["nav_basis"] = "cum"
        self.assertEqual(ledger.record_result("backtest", {}, result,
                                              now=datetime(2026, 11, 2)), 1)
        self.assertEqual(ledger._records()[0]["price_basis"], "cum")


class JudgeTests(unittest.TestCase):
    def _record(self, direction="up", horizon=5, kind="stock"):
        return {"as_of": AS_OF, "horizon": horizon, "direction": direction,
                "asset_kind": kind, "asset_code": "sh600519",
                "price_basis": "qfq", "id": "x"}

    def _prices(self):
        values = [100 + i for i in range(len(SESSIONS))]
        base = SESSIONS.get_indexer([pd.Timestamp(AS_OF)])[0]
        # 目标日收盘高于信号日收盘
        position = SESSIONS.get_indexer([pd.Timestamp("2026-11-06")])[0]
        values[position] = values[base] + 6
        return pd.DataFrame({"open": values,
                             "high": [value + 1 for value in values],
                             "low": [value - 1 for value in values],
                             "close": values}, index=SESSIONS)

    def test_direction_up_and_down(self):
        prices = self._prices()
        up = judge.judge_record(self._record("up"), prices, SESSIONS,
                                date(2026, 11, 20))
        self.assertEqual(up["label"], "correct")
        down = judge.judge_record(self._record("down"), prices, SESSIONS,
                                  date(2026, 11, 20))
        self.assertEqual(down["label"], "incorrect")

    def test_pending_before_target(self):
        verdict = judge.judge_record(self._record(), self._prices(), SESSIONS,
                                     date(2026, 11, 4))
        self.assertEqual(verdict["status"], "pending")

    def test_stock_settlement_uses_frozen_price_basis(self):
        adjusted = self._prices()
        raw = adjusted.copy()
        raw.loc[raw.index > pd.Timestamp(AS_OF)] -= 20
        frames = {"": raw, "qfq": adjusted, "hfq": adjusted * 2}
        today = date(2026, 11, 20)
        for fetch in (False, True):
            with patch("market_research.tasks.sync.cached",
                       side_effect=lambda kind, code, basis, *a, **kw: frames[basis]), \
                 patch("market_research.market.analysis.load_bars",
                       side_effect=lambda kind, code, start, end, basis, **kw: frames[basis]):
                for basis, label in (("", "incorrect"), ("qfq", "correct"),
                                     ("hfq", "correct"), (None, "correct")):
                    with self.subTest(fetch=fetch, basis=basis):
                        record = self._record()
                        if basis is None:
                            record.pop("price_basis")
                        else:
                            record["price_basis"] = basis
                        prices = judge._load_prices(record, today, fetch)
                        self.assertIs(prices, frames["qfq" if basis is None else basis])
                        verdict = judge.judge_record(record, prices, SESSIONS, today)
                        self.assertEqual(verdict["label"], label)

    def test_grace_session_used_when_target_missing(self):
        prices = self._prices().drop(pd.Timestamp("2026-11-06"))
        verdict = judge.judge_record(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "correct")
        self.assertEqual(verdict["reason"], "grace_session")
        self.assertEqual(verdict["actual_sessions"], 6)

    def test_observed_bars_no_feed_terminal_after_grace(self):
        axis = pd.bdate_range("2026-10-01", "2026-11-05")
        prices = pd.Series([100.0 + i for i in range(len(axis))], index=axis)
        record = self._record()
        record["params"] = json.dumps({"horizon_basis": "observed_bars"})
        verdict = judge.judge_record(record, prices, axis, date(2026, 11, 20))
        self.assertEqual(verdict["status"], "settled")
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "no_feed")

    def test_observed_bars_no_feed_stays_pending_within_grace(self):
        axis = pd.bdate_range("2026-10-01", "2026-11-05")
        prices = pd.Series([100.0 + i for i in range(len(axis))], index=axis)
        record = self._record()
        record["params"] = json.dumps({"horizon_basis": "observed_bars"})
        verdict = judge.judge_record(record, prices, axis, date(2026, 11, 9))
        self.assertEqual(verdict["status"], "pending")

    def test_missing_signal_close_stays_pending_within_grace(self):
        # 同步滞后导致信号日收盘还没入库时，宽限期内不能永久定案。
        prices = self._prices().drop(pd.Timestamp(AS_OF))
        verdict = judge.judge_record(self._record(), prices, SESSIONS,
                                     date(2026, 11, 9))
        self.assertEqual(verdict["status"], "pending")
        verdict = judge.judge_record(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "insufficient")

    def test_missing_session_blocked_for_stock(self):
        prices = self._prices().drop(pd.to_datetime(
            ["2026-11-06", "2026-11-09", "2026-11-10"]))
        verdict = judge.judge_record(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "missing_session")

    def test_missing_nav_insufficient_for_fund(self):
        prices = self._prices().drop(pd.to_datetime(
            ["2026-11-06", "2026-11-09", "2026-11-10"]))
        verdict = judge.judge_record(self._record(kind="fund"), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "insufficient")

    def test_one_price_limit_blocked(self):
        prices = self._prices()
        target = pd.Timestamp("2026-11-06")
        value = float(prices.loc[target, "close"])
        prices.loc[target, ["open", "high", "low", "close"]] = value
        verdict = judge.judge_record(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "limit_one_price")

    def test_flat_close_is_neutral_for_both_directions(self):
        prices = pd.Series(1.0, index=SESSIONS)
        for direction in ("up", "down"):
            record = self._record(direction)
            record["source"] = "direction"
            record["probability"] = 0.55
            verdict = judge.judge_record(record, prices, SESSIONS,
                                         date(2026, 11, 20))
            self.assertEqual(verdict["label"], "neutral")
            self.assertEqual(verdict["realized_return"], 0.0)

    def test_rule_event_is_observed_not_a_direction_call(self):
        record = self._record("up")
        record["source"] = "rule"
        record["horizon"] = ledger.RULE_HORIZON
        verdict = judge.judge_record(record, _rising_bars(), SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "observed")
        self.assertGreater(verdict["realized_return"], 0)


class ConsistencyTests(unittest.TestCase):
    def test_ledger_thresholds_match_execution(self):
        from market_research.market import execution
        self.assertEqual(ledger.DIRECTION_THRESHOLD, 0.5)
        self.assertEqual(ledger.ENTRY_PROBABILITY, execution.ENTRY_PROBABILITY)
        self.assertEqual(ledger.EXIT_PROBABILITY, execution.EXIT_PROBABILITY)

    def test_rule_rows_excluded_from_direction_stats(self):
        rows = [
            {"source": "direction", "probability": 0.7, "label": "correct",
             "realized_return": 0.02},
            {"source": "rule", "probability": None, "label": "incorrect",
             "realized_return": -0.01},
        ]
        stats = ledger._stats(rows)
        self.assertEqual(stats["decided"], 1)
        self.assertEqual(stats["hit_rate"], 1.0)
        self.assertEqual(stats["rule_events"], 1)
        self.assertEqual(stats["legacy_rows"], 0)
        self.assertEqual(stats["current_total"], 2)

    def test_legacy_rule_rows_counted_separately(self):
        rows = [
            {"source": "rule", "horizon": 5, "probability": None,
             "label": "correct", "realized_return": 0.01},
            {"source": "rule", "horizon": 20, "probability": None,
             "label": "incorrect", "realized_return": -0.01},
            {"source": "direction", "horizon": 5, "probability": 0.6,
             "label": "correct", "realized_return": 0.02},
        ]
        stats = ledger._stats(rows)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["legacy_rows"], 2)
        self.assertEqual(stats["current_total"], 1)
        self.assertEqual(stats["hit_rate"], 1.0)

    def test_grace_and_no_feed_are_diagnostic_counts(self):
        rows = [
            {"source": "direction", "probability": 0.7, "label": "correct",
             "realized_return": 0.02, "reason": "grace_session",
             "actual_sessions": 6},
            {"source": "direction", "probability": 0.6, "label": "blocked",
             "reason": "no_feed"},
        ]
        stats = ledger._stats(rows)
        self.assertEqual(stats["grace_settled"], 1)
        self.assertEqual(stats["no_feed"], 1)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["decided"], 1)       # 默认命中率分母不变
        self.assertIn("actual_sessions", stats["note"])

    def test_result_nav_basis_registers_matching_sync_asset(self):
        from market_research.services import analysis as service
        with patch.object(service.sync, "register") as register:
            service._register_result_nav_basis(
                {"code": "110022"},
                {"meta": {"nav_basis": "cum"}})
        register.assert_called_once_with("fund", "110022", "cum")

    def test_observed_bars_settlement_uses_price_axis(self):
        axis = pd.bdate_range("2026-10-01", "2026-12-31").delete([10, 11, 12])
        prices = pd.Series(100.0, index=axis)
        record = {"as_of": AS_OF,
                  "params": json.dumps({"horizon_basis": "observed_bars"})}
        with patch.object(judge, "_sessions", side_effect=AssertionError(
                "有效日线口径不得回退到交易日历")):
            sessions = judge._sessions_for(record, prices, date(2026, 12, 10))
        pd.testing.assert_index_equal(sessions, judge._normalize(axis))

    def test_fund_settlement_uses_recorded_nav_basis(self):
        record = {"asset_kind": "fund", "asset_code": "110022",
                  "price_basis": "cum", "as_of": AS_OF}
        with patch("market_research.data.panels.load_fund_nav_cached",
                   return_value=pd.Series(dtype=float)) as loader:
            judge._load_prices(record, date(2026, 12, 10), fetch=False)
        self.assertEqual(loader.call_args.kwargs["basis"], "cum")

    def test_settlement_reads_history_without_requiring_today_sync(self):
        # 结算只需要历史收盘；若沿用“同步到今天”的新鲜度门槛，记录会被
        # 误判为没有数据，之后再也没有机会重判。
        record = {"asset_kind": "stock", "asset_code": "sh600519",
                  "price_basis": "qfq", "as_of": AS_OF}
        frame = _rising_bars()
        with patch("market_research.tasks.sync.cached",
                   return_value=frame) as cached:
            judge._load_prices(record, date(2026, 12, 10), fetch=False)
        self.assertFalse(cached.call_args.kwargs["fresh"])

    def test_fund_settlement_runs_through_real_loader(self):
        # G-01 回归：基金分支必须用真实签名调用 load_fund_nav_cached，且只用
        # 判定日之前的净值。旧实现传了不存在的 end=，结算必然 TypeError；
        # 即使去掉该参数，命中同步历史时序列也会延伸到判定日之后。
        as_of, today = date(2026, 10, 20), date(2026, 11, 8)
        dates = pd.bdate_range("2026-09-01", "2026-11-13")
        history = pd.DataFrame(
            {"close": [1.0 + 0.01 * n for n in range(len(dates))]},
            index=dates)
        record = {"id": "fund-1", "asset_kind": "fund", "asset_code": "110022",
                  "asset_name": "测试基金", "price_basis": "total",
                  "source": "direction", "direction": "up", "horizon": 5,
                  "as_of": as_of.isoformat(),
                  "params": json.dumps({"horizon_basis": "observed_bars"})}
        self.assertGreater(history.index.max(), pd.Timestamp(today))
        with patch("market_research.tasks.sync.cached", return_value=history):
            prices = judge._load_prices(record, today, fetch=False)
            self.assertIsInstance(prices, pd.Series)
            self.assertLessEqual(prices.index.max(), pd.Timestamp(today))
            with patch.object(ledger, "pending_records", return_value=[record]), \
                    patch.object(ledger, "update_record") as update:
                counts = judge.settle(today=today)
        self.assertEqual(counts["unavailable"], 0)
        self.assertEqual(counts["correct"], 1)
        self.assertEqual(update.call_args.kwargs["judge_date"], "2026-10-27")
        self.assertGreater(update.call_args.kwargs["realized_return"], 0)


class SettleAndSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()
        calendar = patch("market_research.data.providers.calendar.bundled_calendar", return_value=(SESSIONS, {}))
        calendar.start()
        self.addCleanup(calendar.stop)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_settle_and_summary(self):
        ledger.record_result("market", {}, _market_result(), now=datetime(2026, 11, 2))
        bars = _rising_bars()
        with patch.object(judge, "_load_prices", return_value=bars), \
                patch.object(judge, "_sessions", return_value=SESSIONS):
            counts = judge.settle(today=date(2026, 12, 10))
        self.assertEqual(counts["checked"], 3)
        self.assertEqual(counts["pending"], 0)
        # 上涨行情：5 日方向概率（up）判对、20 日（down）判错；
        # 规则买入是状态事件，只记收益（observed），不参与方向命中率。
        self.assertEqual(counts["correct"], 1)
        self.assertEqual(counts["incorrect"], 1)
        self.assertEqual(counts["observed"], 1)
        overall = ledger.summary()["overall"]
        self.assertEqual(overall["total"], 3)
        self.assertEqual(overall["decided"], 2)
        self.assertEqual(overall["hit_rate"], 0.5)
        self.assertEqual(overall["hit_rate_basis"], "direction_only")
        self.assertEqual(overall["rule_events"], 1)
        self.assertGreater(overall["rule_return_mean"], 0)
        self.assertIsNotNone(overall["brier"])

    def test_actual_sessions_column_persists_on_old_databases(self):
        ledger.record_result("market", {}, _market_result(),
                             now=datetime(2026, 11, 2))
        row = ledger._records()[0]
        ledger.update_record(row["id"], status="settled", label="correct",
                             actual_sessions=6)
        stored = [item for item in ledger._records()
                  if item["id"] == row["id"]][0]
        self.assertEqual(stored["actual_sessions"], 6)

    def test_settle_keeps_pending_when_prices_unavailable(self):
        ledger.record_result("market", {}, _market_result(),
                             now=datetime(2026, 11, 2))
        with patch.object(judge, "_load_prices",
                          side_effect=ValueError("本地行情缓存未同步")):
            counts = judge.settle(today=date(2026, 12, 10))
        self.assertEqual(counts["unavailable"], 3)
        self.assertEqual(counts["insufficient"], 0)
        self.assertIn("ValueError", counts["unavailable_reasons"][0])
        self.assertEqual(ledger.summary()["overall"]["pending"], 3)


class CumOfflineSettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_cum_fund_settles_offline_without_fetch_or_total_mix(self):
        from market_research.data import storage
        dates = pd.bdate_range("2026-09-01", "2026-11-13")
        frame = pd.DataFrame(
            {"cum": [1.0 + 0.01 * n for n in range(len(dates))]},
            index=dates)
        meta = {"v": C.DATA_CACHE_VERSION, "code": "110022",
                "start": "2020-01-01", "basis": "cum"}
        storage.write("fund_nav_110022_2026-10-01", frame.iloc[-10:],
                      {**meta, "start": "2026-10-01"})
        storage.write("fund_nav_110022_2020-01-01", frame, meta)
        storage.write("fund_nav_110022_2020-01-01_total", frame * 2,
                      {**meta, "basis": "total"})
        record = {"id": "cum-1", "asset_kind": "fund", "asset_code": "110022",
                  "price_basis": "cum", "source": "direction",
                  "direction": "up", "horizon": 5,
                  "as_of": "2026-10-20",
                  "params": json.dumps({"horizon_basis": "observed_bars"})}
        with patch("market_research.data.providers.funds.fetch_fund_nav",
                   side_effect=AssertionError("离线结算不得联网")), \
             patch("market_research.tasks.sync.register",
                   side_effect=AssertionError("只读结算不得登记同步资产")):
            prices = judge._load_prices(record, date(2026, 11, 8), fetch=False)
            self.assertEqual(prices.attrs["nav_basis"], "cum")
            self.assertEqual(prices.loc[pd.Timestamp("2026-10-20")],
                             frame.loc[pd.Timestamp("2026-10-20"), "cum"])
            with patch.object(ledger, "pending_records", return_value=[record]), \
                    patch.object(ledger, "update_record") as update:
                counts = judge.settle(today=date(2026, 11, 8))
        self.assertEqual(counts["unavailable"], 0)
        self.assertEqual(counts["correct"], 1)
        self.assertGreater(update.call_args.kwargs["realized_return"], 0)


if __name__ == "__main__":
    unittest.main()
