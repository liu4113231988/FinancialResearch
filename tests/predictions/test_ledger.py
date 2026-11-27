"""预测台账与结算口径的单元测试。"""
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_research.config as C
from market_research.predictions import commands, judge, ledger


AS_OF = "2026-10-30"
SESSIONS = pd.bdate_range("2026-10-01", "2026-12-31")


def _settlement_time(day):
    return datetime.combine(day, time(16), ledger.SHANGHAI)


def _observed(prices, end="2026-11-11", checked=None):
    prices.attrs.update(
        observed_data_start=str(prices.index[0].date()), observed_data_end=end,
        request_started_at=f"{checked or end}T15:00:00+08:00",
        observed_at=f"{checked or end}T15:01:00+08:00")
    return prices


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
        "meta": {"nav_basis": "total", "nav_date": AS_OF,
                 "nav_snapshot_version": "a" * 64, "nav_data_version": "b" * 64,
                 "nav_observed_at": f"{AS_OF}T21:00:00+08:00",
                 "nav_observed_data_end": AS_OF,
                 "nav_announcement_at": None,
                 "forecast_generated_at": f"{AS_OF}T21:55:00+08:00",
                 "analysis_generated_at": f"{AS_OF}T22:00:00+08:00",
                 "provenance": {"git_commit": "abc123", "code_version": "1",
                                "data_stamp": "stamp",
                                "generated_at": f"{AS_OF}T22:00:00+08:00"}},
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
                 "nav_basis": "total", "nav_date": AS_OF,
                 "nav_snapshot_version": "a" * 64, "nav_data_version": "b" * 64,
                 "nav_observed_at": f"{AS_OF}T21:00:00+08:00",
                 "nav_observed_data_end": AS_OF,
                 "nav_announcement_at": None,
                 "analysis_generated_at": f"{AS_OF}T22:00:00+08:00",
                 "provenance": {"git_commit": "abc123", "code_version": "1",
                                "data_stamp": "stamp",
                                "generated_at": f"{AS_OF}T22:00:00+08:00"}},
        "current": {"date": AS_OF, "position": 0},
        "trades": [{"date": AS_OF, "action": "卖出", "health": -0.6,
                    "reason": "健康分跌破离场线"}],
    }


def _rising_bars():
    """从 100 起每个交易日 +1；保留日内高低价，避免被当成一字板。"""
    values = [100 + i for i in range(len(SESSIONS))]
    return _observed(pd.DataFrame({"open": values,
                         "high": [value + 1 for value in values],
                         "low": [value - 1 for value in values],
                         "close": values}, index=SESSIONS), end="2026-11-30")


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


class FundA3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=C.ROOT / "output")
        self.patch = patch.object(C, "DATA_DIR", Path(self.tmp.name))
        self.patch.start()
        self.axis = pd.bdate_range("2024-12-20", "2025-03-31")
        calendar = patch("market_research.data.providers.calendar.bundled_calendar",
                         return_value=(self.axis, {}))
        calendar.start()
        self.addCleanup(calendar.stop)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _result(self, as_of, observed, generated, *, basis="total", error=None):
        result = _fund_analyze_result()
        result["forecast"]["as_of"] = str(as_of)
        meta = result["meta"]
        meta.update(nav_basis=basis, nav_date=str(as_of),
                    nav_observed_at=observed.isoformat(),
                    nav_observed_data_end=str(as_of),
                    nav_announcement_at=None,
                    forecast_generated_at=generated.isoformat(),
                    analysis_generated_at=generated.isoformat(),
                    provenance={**meta["provenance"],
                                "generated_at": generated.isoformat()})
        if error:
            meta["data_quality"] = {"status": "degraded", "source_error": error}
        return result

    def test_stale_nav_is_not_registered(self):
        result = self._result("2025-01-02",
                              datetime(2025, 1, 15, 15, 20, tzinfo=ledger.SHANGHAI),
                              datetime(2025, 1, 15, 16, tzinfo=ledger.SHANGHAI))
        result["forecast"]["horizons"] = [{"horizon": 5, "ok": True, "probability": 0.6}]
        outcome = ledger.record_result_ex(
            "analyze", {"end": None}, result,
            now=datetime(2025, 1, 15, 16, tzinfo=ledger.SHANGHAI))
        self.assertEqual(outcome["status"], "not_recorded")
        self.assertEqual(outcome["reason"], "stale_nav")
        self.assertEqual(ledger._records(), [])

    def test_evening_observation_registers(self):
        observed = datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI)
        result = self._result("2025-01-02", observed,
                              observed + timedelta(minutes=10))
        outcome = ledger.record_result_ex(
            "analyze", {}, result,
            now=datetime(2025, 1, 2, 22, tzinfo=ledger.SHANGHAI))
        self.assertEqual(outcome["status"], "recorded")
        self.assertEqual(outcome["recorded"], 2)
        params = json.loads(ledger._records()[0]["params"])
        self.assertEqual(params["nav_observed_data_end"], "2025-01-02")
        self.assertEqual(params["nav_announcement_at"], None)
        self.assertEqual(params["analysis_generated_at"],
                         (observed + timedelta(minutes=10)).isoformat())

    def test_next_morning_observation_registers(self):
        observed = datetime(2025, 1, 3, 9, tzinfo=ledger.SHANGHAI)
        result = self._result("2025-01-02", observed,
                              observed + timedelta(minutes=10))
        outcome = ledger.record_result_ex(
            "analyze", {}, result,
            now=datetime(2025, 1, 3, 9, 30, tzinfo=ledger.SHANGHAI))
        self.assertEqual(outcome["status"], "recorded")
        self.assertEqual(outcome["recorded"], 2)

    def test_long_holiday_keeps_last_completed_session(self):
        axis = pd.to_datetime(["2025-01-02", "2025-02-05", "2025-02-06",
                               "2025-02-07", "2025-02-10", "2025-02-11"])
        observed = datetime(2025, 2, 5, 9, tzinfo=ledger.SHANGHAI)
        result = self._result("2025-01-02", observed,
                              observed + timedelta(minutes=10))
        with patch("market_research.data.providers.calendar.bundled_calendar",
                   return_value=(axis, {})):
            outcome = ledger.record_result_ex(
                "analyze", {}, result,
                now=observed + timedelta(minutes=20))
        self.assertEqual(outcome["status"], "recorded")

    def test_refresh_failure_and_duplicate_have_distinct_reasons(self):
        result = self._result(
            "2025-01-02", datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI),
            datetime(2025, 1, 2, 22, tzinfo=ledger.SHANGHAI))
        now = datetime(2025, 1, 2, 23, tzinfo=ledger.SHANGHAI)
        failed = self._result(
            "2025-01-02", datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI),
            datetime(2025, 1, 2, 22, tzinfo=ledger.SHANGHAI), error="刷新失败")
        self.assertEqual(ledger.record_result_ex("analyze", {}, failed, now=now)["reason"],
                         "refresh_failed")
        self.assertEqual(ledger.record_result_ex("analyze", {}, result, now=now)["status"],
                         "recorded")
        result["forecast"]["horizons"][0]["probability"] = 0.10
        self.assertEqual(ledger.record_result_ex("analyze", {}, result, now=now)["reason"],
                         "duplicate")
        first = next(row for row in ledger._records() if row["horizon"] == 5)
        self.assertEqual(first["probability"], 0.62)

    def test_matured_fund_is_not_backfilled(self):
        result = self._result(
            "2025-01-02", datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI),
            datetime(2025, 1, 2, 22, tzinfo=ledger.SHANGHAI))
        result["forecast"]["horizons"] = [item for item in result["forecast"]["horizons"]
                                          if item["horizon"] == 5]
        outcome = ledger.record_result_ex(
            "analyze", {}, result,
            now=datetime(2025, 1, 15, 16, tzinfo=ledger.SHANGHAI))
        self.assertEqual(outcome["reason"], "matured")

    def test_old_snapshot_is_stale_at_registration_even_before_maturity(self):
        observed = datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI)
        result = self._result("2025-01-02", observed, observed + timedelta(hours=1))
        now = datetime(2025, 1, 7, 16, tzinfo=ledger.SHANGHAI)
        outcome = ledger.record_result_ex("analyze", {}, result, now=now)
        self.assertEqual((outcome["reason"], outcome["matured"]), ("stale_nav", 0))
        rule = _fund_backtest_result()
        rule["meta"].update(result["meta"])
        rule["current"]["date"] = rule["trades"][0]["date"] = "2025-01-02"
        with patch.object(ledger, "RULE_EVAL_SESSIONS", 3):
            outcome = ledger.record_result_ex("backtest", {}, rule, now=now)
        self.assertEqual(outcome["reason"], "matured")
        self.assertEqual(ledger._records(), [])

    def test_missing_or_invalid_evidence_is_unknown(self):
        observed = datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI)
        for key, value in (("nav_observed_at", "2025-01-02T21:00:00"),
                           ("nav_observed_at", None), ("nav_date", "2025-01-01"),
                           ("nav_data_version", ""), ("forecast_generated_at", None),
                           ("analysis_generated_at", "2025-01-02T20:00:00+08:00")):
            with self.subTest(key=key, value=value):
                result = self._result("2025-01-02", observed, observed + timedelta(hours=1))
                result["meta"][key] = value
                self.assertEqual(ledger.record_result_ex(
                    "analyze", {}, result, now=observed + timedelta(hours=2))["reason"],
                    "availability_unknown")
        result = self._result("2025-01-02", observed, observed + timedelta(hours=1))
        with patch("market_research.data.providers.calendar.bundled_calendar",
                   side_effect=ValueError("no coverage")):
            outcome = ledger.record_result_ex(
                "analyze", {}, result, now=observed + timedelta(hours=2))
        self.assertEqual(outcome["reason"], "availability_unknown")
        self.assertEqual(ledger._records(), [])

    def test_market_outcome_separates_direction_and_rule(self):
        with patch("market_research.data.providers.calendar.bundled_calendar",
                   return_value=(SESSIONS, {})):
            outcome = ledger.record_result_ex(
                "market", {}, _market_result(),
                now=datetime(2026, 10, 30, 16, tzinfo=ledger.SHANGHAI))
        self.assertEqual(outcome["status"], "recorded")
        self.assertEqual(outcome["direction"], 2)
        self.assertEqual(outcome["rule"], 1)

    def test_cancelled_and_replaced_attempts_do_not_register(self):
        from market_research.tasks.store import DurableJobStore, bind_attempt

        jobs = DurableJobStore()
        observed = datetime(2025, 1, 2, 21, tzinfo=ledger.SHANGHAI)
        result = self._result("2025-01-02", observed, observed + timedelta(hours=1))
        now = observed + timedelta(hours=2)
        jid, _ = jobs.enqueue("110022", [], "analyze", {})
        first = jobs.claim()
        jobs.retry_crashed(jid, first["token"], "retry")
        second = jobs.claim()
        with bind_attempt(first["token"]):
            outcome = ledger.record_result_ex("analyze", {}, result, now=now, job_id=jid)
        self.assertEqual(outcome["reason"], "cancelled")
        self.assertTrue(jobs.cancel(jid))
        with bind_attempt(second["token"]):
            outcome = ledger.record_result_ex("analyze", {}, result, now=now, job_id=jid)
        self.assertEqual(outcome["reason"], "cancelled")
        self.assertEqual(ledger._records(), [])

    def test_read_only_fund_audit_separates_evidence_status(self):
        params = {"nav_date": "2025-01-02", "nav_basis": "total",
                  "nav_snapshot_version": "a" * 64, "nav_data_version": "b" * 64,
                  "nav_observed_at": "2025-01-02T21:00:00+08:00",
                  "nav_observed_data_end": "2025-01-02",
                  "forecast_generated_at": "2025-01-02T21:30:00+08:00",
                  "analysis_generated_at": "2025-01-02T22:00:00+08:00",
                  "data_stamp": "stamp"}
        verified = ledger._make(
            asset_kind="fund", asset_code="110022", asset_name="已核验",
            as_of="2025-01-02", source="direction", direction="up",
            horizon=5, probability=0.6, price_basis="total",
            params=params)
        verified["created_at"] = datetime(2025, 1, 2, 22, 30, tzinfo=ledger.SHANGHAI).timestamp()
        legacy = ledger._make(
            asset_kind="fund", asset_code="110022", asset_name="旧记录",
            as_of="2025-01-03", source="direction", direction="up",
            horizon=5, probability=0.6, price_basis="total", params={})
        ledger._insert([verified, legacy])
        path = C.DATA_DIR / "market.duckdb"
        before = path.read_bytes()
        with patch.object(ledger, "_schema", side_effect=AssertionError("audit attempted DDL")):
            audit = ledger.fund_audit()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(audit["total"], 2)
        self.assertEqual(audit["verified"], 1)
        self.assertEqual(audit["pending_evidence"], 1)
        self.assertEqual([(row["price_basis"], row["evidence_status"])
                          for row in audit["groups"]],
                         [("total", "pending_evidence"), ("total", "verified")])


class RegistrationServiceTests(unittest.TestCase):
    def test_cache_hit_resets_stale_registration_marker(self):
        import pickle
        from market_research.services import analysis as service
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.pkl"
            evidence = _fund_analyze_result()["meta"]
            path.write_bytes(pickle.dumps({
                "code": "110022",
                "meta": {**evidence, "registration": {"status": "recorded", "recorded": 2}}}))
            cached_bytes = path.read_bytes()
            with patch.object(service, "fund_cache_path", return_value=path), \
                    patch.object(service.sync, "register"), \
                    patch.object(ledger, "record_result_ex") as register:
                response = service.submit("analyze", {"code": "110022"})
            register.assert_not_called()
            self.assertEqual(path.read_bytes(), cached_bytes)
        self.assertTrue(response["cached"])
        meta = response["result"]["meta"]
        for field, value in evidence.items():
            self.assertEqual(meta[field], value)
        registration = meta["registration"]
        self.assertEqual(registration["status"], "not_recorded")
        self.assertEqual(registration["reason"], "cache_hit")

    def test_write_failure_only_degrades_registration(self):
        from market_research.services import analysis as service
        result = _fund_analyze_result()
        with patch("market_research.data.providers.calendar.bundled_calendar",
                   return_value=(SESSIONS, {})), \
                patch.object(ledger, "_now", return_value=datetime(
                    2026, 10, 30, 23, tzinfo=ledger.SHANGHAI)), \
                patch.object(ledger, "_insert", side_effect=OSError("locked")):
            outcome = service._record_predictions("analyze", {}, result)
        self.assertEqual(outcome["reason"], "write_failed")
        self.assertEqual(result["meta"]["registration"], outcome)
        self.assertTrue(result.get("ok", True))


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
        return _observed(pd.DataFrame({"open": values,
                             "high": [value + 1 for value in values],
                             "low": [value - 1 for value in values],
                             "close": values}, index=SESSIONS))

    def _judge(self, record, prices, sessions, today):
        return judge.judge_record(record, prices, sessions, today,
                                  now=_settlement_time(today))

    def test_direction_up_and_down(self):
        prices = self._prices()
        up = self._judge(self._record("up"), prices, SESSIONS,
                                date(2026, 11, 20))
        self.assertEqual(up["label"], "correct")
        down = self._judge(self._record("down"), prices, SESSIONS,
                                  date(2026, 11, 20))
        self.assertEqual(down["label"], "incorrect")

    def test_pending_before_target(self):
        verdict = self._judge(self._record(), self._prices(), SESSIONS,
                                     date(2026, 11, 4))
        self.assertEqual(verdict["status"], "pending")

    def test_settle_cannot_use_tomorrows_grace_quote(self):
        axis = pd.bdate_range("2025-01-02", periods=10)
        target, next_session = axis[5:7]
        bars = pd.DataFrame({"open": 100., "high": 102., "low": 99.,
                             "close": 101., "volume": 1000.}, index=axis)
        bars.loc[axis[0], "close"] = 100.
        bars = bars.drop(target)
        _observed(bars, str(next_session.date()))
        record = {**self._record(), "as_of": "2025-01-02", "price_basis": ""}
        with patch.object(ledger, "pending_records", return_value=[record]), \
                patch.object(ledger, "update_record") as update, \
                patch("market_research.tasks.sync.cached", return_value=bars), \
                patch("market_research.data.providers.calendar.bundled_calendar",
                      return_value=(axis, {})):
            counts = judge.settle(now=_settlement_time(target.date()))
            self.assertEqual(counts["pending"], 1)
            update.assert_not_called()
            counts = judge.settle(now=_settlement_time(next_session.date()))
            self.assertEqual(counts["correct"], 1)
            self.assertEqual(update.call_args.kwargs["judge_date"],
                             next_session.date().isoformat())
            self.assertAlmostEqual(update.call_args.kwargs["realized_return"], .01)
            counts = judge.settle(today=next_session.date(),
                                  now=_settlement_time(axis[-1].date()))
            self.assertEqual(counts["correct"], 1)
            self.assertTrue(counts["diagnostic_only"])
            self.assertEqual(update.call_count, 1)
        self.assertEqual(self._judge(record, bars, axis, target.date())["status"],
                         "pending")

    def test_concurrent_settlement_does_not_double_count(self):
        prices = self._prices()
        with patch.object(ledger, "pending_records",
                          return_value=[self._record()]), \
                patch.object(ledger, "update_record", return_value=0), \
                patch.object(judge, "_load_prices", return_value=prices), \
                patch.object(judge, "_sessions", return_value=SESSIONS):
            counts = judge.settle(now=_settlement_time(date(2026, 11, 20)))
        self.assertEqual(counts["checked"], 1)
        self.assertEqual(counts["correct"], 0)

    def test_calendar_outage_keeps_missing_quotes_pending(self):
        prices = self._prices().loc[:"2026-11-05"]
        record = self._record()
        with patch.object(ledger, "pending_records", return_value=[record]), \
                patch.object(ledger, "update_record") as update, \
                patch("market_research.tasks.sync.cached", return_value=prices), \
                patch("market_research.data.providers.calendar.bundled_calendar",
                      side_effect=ValueError("未覆盖判定日期")):
            counts = judge.settle(now=_settlement_time(date(2026, 11, 20)))
        self.assertEqual(counts["pending"], 1)
        update.assert_not_called()

    def test_default_settlement_uses_shanghai_date(self):
        utc = datetime(2026, 11, 5, 17, tzinfo=timezone.utc)
        with patch.object(ledger, "_now", return_value=utc), \
                patch.object(ledger, "pending_records", return_value=[]):
            counts = judge.settle()
        self.assertEqual(counts["cutoff_date"], "2026-11-06")
        self.assertEqual(counts["checked_at"], "2026-11-06T01:00:00+08:00")

    def test_settle_waits_for_post_close_snapshot(self):
        target = SESSIONS[SESSIONS.get_loc(AS_OF) + 5]
        bars = self._prices().loc[:target].copy()
        bars.attrs.update(request_started_at=f"{target.date()}T09:59:00+08:00",
                          observed_at=f"{target.date()}T10:00:00+08:00",
                          observed_data_end=str(target.date()))
        record = self._record()
        with patch.object(ledger, "pending_records", return_value=[record]), \
                patch.object(ledger, "update_record") as update, \
                patch.object(judge, "_load_prices", return_value=bars), \
                patch.object(judge, "_sessions", return_value=SESSIONS):
            for hour in (10, 16):
                now = datetime.combine(target.date(), time(hour), ledger.SHANGHAI)
                counts = judge.settle(now=now)
                self.assertEqual(counts["pending"], 1)
                self.assertEqual(counts["awaiting_close_evidence"], 1)
                update.assert_not_called()
            output = io.StringIO()
            with patch.object(ledger, "_now", return_value=now), redirect_stdout(output):
                self.assertEqual(commands.main(["settle"]), 0)
            self.assertEqual(json.loads(output.getvalue())["awaiting_close_evidence"], 1)
            update.assert_not_called()
            bars.attrs.update(request_started_at=f"{target.date()}T15:00:00+08:00",
                              observed_at=f"{target.date()}T15:00:00+08:00")
            self.assertEqual(judge.settle(now=now)["correct"], 1)
            self.assertEqual(update.call_args.kwargs["judge_date"], str(target.date()))

    def test_market_requires_coverage_and_ordered_source_timestamps(self):
        bars = self._prices().loc[:"2026-11-06"].copy()
        observed = _observed(bars, "2026-11-06").attrs.copy()
        record = {**self._record(kind="index"),
                  "params": {"horizon_basis": "observed_bars"}}
        now = datetime(2026, 11, 7, 1, tzinfo=timezone.utc)
        with patch.object(judge, "_sessions", return_value=SESSIONS):
            for change in (
                {"request_started_at": "2026-11-06T14:59:00+08:00"},
                {"observed_at": "2026-11-06T15:01:00"},
                {"observed_at": "2026-11-07T09:01:00+08:00"},
                {"observed_at": "2026-11-06T14:59:00+08:00"},
                {"observed_data_end": "2026-11-05"},
                {"observed_at": None, "fetched_at": observed["observed_at"]},
            ):
                with self.subTest(change=change):
                    bars.attrs = {**observed, **change}
                    self.assertEqual(judge.judge_record(record, bars, bars.index, now=now)["status"],
                                     "pending")
            bars.attrs = observed
            self.assertEqual(judge.judge_record(record, bars, bars.index, now=now)["label"],
                             "correct")
        with patch.object(judge, "_sessions", return_value=pd.DatetimeIndex([])):
            self.assertEqual(judge.judge_record(record, bars, bars.index, now=now)["status"],
                             "pending")

        holiday_axis = pd.to_datetime(["2025-01-20", "2025-01-21", "2025-01-22",
                                       "2025-01-23", "2025-01-24", "2025-01-27",
                                       "2025-02-05"])
        bars = _observed(pd.Series(range(100, 107), index=holiday_axis), "2025-02-05")
        bars.attrs.update(request_started_at="2025-02-05T09:59:00+08:00",
                          observed_at="2025-02-05T10:00:00+08:00")
        with patch.object(judge, "_sessions", return_value=holiday_axis):
            verdict = judge.judge_record({**record, "as_of": "2025-01-20"},
                                         bars, holiday_axis,
                                         now=datetime(2025, 2, 5, 10, tzinfo=ledger.SHANGHAI))
        self.assertEqual(verdict["judge_date"], "2025-01-27")

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
                        expected = frames["qfq" if basis is None else basis]
                        pd.testing.assert_frame_equal(prices, expected.loc[:pd.Timestamp(today)])
                        self.assertLessEqual(prices.index.max().date(), today)
                        verdict = self._judge(record, prices, SESSIONS, today)
                        self.assertEqual(verdict["label"], label)

    def test_grace_session_used_when_target_missing(self):
        prices = self._prices().drop(pd.Timestamp("2026-11-06"))
        verdict = self._judge(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "correct")
        self.assertEqual(verdict["reason"], "grace_session")
        self.assertEqual(verdict["actual_sessions"], 6)

    def test_observed_bars_no_feed_terminal_after_grace(self):
        axis = pd.bdate_range("2026-10-01", "2026-11-05")
        prices = pd.Series([100.0 + i for i in range(len(axis))], index=axis)
        record = self._record()
        record["params"] = json.dumps({"horizon_basis": "observed_bars"})
        today = date(2026, 11, 20)
        with patch.object(judge, "_sessions", return_value=SESSIONS):
            self.assertEqual(self._judge(record, prices, axis, today)["status"],
                             "pending")
            prices.attrs["source_checked_through"] = today.isoformat()
            _observed(prices, end="2026-11-05", checked="2026-11-20")
            verdict = self._judge(record, prices, axis, today)
        self.assertEqual(verdict["status"], "settled")
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "no_feed")

    def test_observed_bars_no_feed_stays_pending_within_grace(self):
        axis = pd.bdate_range("2026-10-01", "2026-11-05")
        prices = pd.Series([100.0 + i for i in range(len(axis))], index=axis)
        record = self._record()
        record["params"] = json.dumps({"horizon_basis": "observed_bars"})
        verdict = self._judge(record, prices, axis, date(2026, 11, 9))
        self.assertEqual(verdict["status"], "pending")

    def test_fetch_source_checked_bounds_use_returned_observations(self):
        bars = self._prices().loc[:pd.Timestamp("2026-11-05")].copy()
        bars.attrs.update(observed_data_start=str(bars.index[0].date()),
                          observed_data_end="2026-11-05")
        with patch("market_research.market.analysis.load_bars", return_value=bars):
            prices = judge._load_prices(self._record(), date(2026, 11, 20), True)
        self.assertEqual(prices.attrs["source_checked_from"], str(bars.index[0].date()))
        self.assertEqual(prices.attrs["source_checked_through"], "2026-11-05")

    def test_fetch_short_history_does_not_certify_missing_quotes(self):
        bars = self._prices().loc[:pd.Timestamp("2026-11-05")].copy()
        bars.attrs.update(observed_data_start=str(bars.index[0].date()),
                          observed_data_end="2026-11-05",
                          request_started_at="2026-11-20T15:00:00+08:00",
                          observed_at="2026-11-20T15:01:00+08:00")
        with patch.object(ledger, "pending_records",
                          return_value=[self._record()]), \
                patch.object(ledger, "update_record") as update, \
                patch("market_research.market.analysis.load_bars",
                      return_value=bars), \
                patch.object(judge, "_sessions", return_value=SESSIONS):
            counts = judge.settle(now=_settlement_time(date(2026, 11, 20)),
                                  fetch=True)
        self.assertEqual(counts["pending"], 1)
        update.assert_not_called()

    def test_missing_session_evidence_hashes_all_scanned_quotes(self):
        axis = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06",
                               "2025-01-07", "2025-01-08", "2025-01-09",
                               "2025-01-13", "2025-01-14", "2025-01-15"])
        target, grace_end, scanned = axis[5], axis[7], axis[-1]
        prices = pd.Series([100.0, 102.0], index=[axis[0], scanned])
        _observed(prices, end=str(scanned.date()), checked=str(scanned.date()))
        prices.attrs["request_started_at"] = f"{scanned.date()}T15:00:00+08:00"
        record = {**self._record(), "as_of": str(axis[0].date())}
        verdict = judge.judge_record(
            record, prices, axis, date(2025, 1, 16),
            now=datetime(2025, 1, 16, 16, tzinfo=ledger.SHANGHAI))
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "missing_session")
        evidence = verdict["settlement_evidence"]
        self.assertEqual(evidence["decision_through"], str(grace_end.date()))
        self.assertEqual(evidence["input_through"], str(scanned.date()))
        rows = prices.loc[str(axis[0].date()):str(scanned.date())]
        content = pd.util.hash_pandas_object(rows, index=True).values.tobytes()
        digest = hashlib.sha256(json.dumps([rows.name]).encode() + content).hexdigest()
        self.assertEqual(evidence["input_sha256"], digest)

    def test_missing_signal_close_stays_pending_within_grace(self):
        # 同步滞后导致信号日收盘还没入库时，宽限期内不能永久定案。
        prices = self._prices().drop(pd.Timestamp(AS_OF))
        verdict = self._judge(self._record(), prices, SESSIONS,
                                     date(2026, 11, 9))
        self.assertEqual(verdict["status"], "pending")
        verdict = self._judge(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "insufficient")

    def test_missing_session_blocked_for_stock(self):
        prices = self._prices().drop(pd.to_datetime(
            ["2026-11-06", "2026-11-09", "2026-11-10"]))
        verdict = self._judge(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "missing_session")

    def test_missing_quote_needs_complete_grace_and_successful_source_check(self):
        # Two real grace sessions after a holiday, with no usable target quote.
        axis = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06",
                               "2025-01-07", "2025-01-08", "2025-01-09",
                               "2025-01-13", "2025-01-14"])
        record = {**self._record(), "as_of": "2025-01-02"}
        prices = pd.Series([100., float("nan")], index=axis[[0, 5]])
        prices.attrs["source_checked_through"] = "2025-01-14"
        today = date(2025, 1, 15)
        _observed(prices, end="2025-01-02", checked="2025-01-14")
        for sessions, cutoff in ((axis[:-1], today), (axis, axis[-1].date())):
            self.assertEqual(self._judge(record, prices, sessions, cutoff)["status"],
                             "pending")
        prices.attrs["source_error"] = "刷新失败"
        self.assertEqual(self._judge(record, prices, axis, today)["status"], "pending")
        prices.attrs.pop("source_error")
        self.assertEqual(self._judge(record, prices, axis, today)["reason"],
                         "missing_session")

    def test_missing_nav_insufficient_for_fund(self):
        prices = self._prices().drop(pd.to_datetime(
            ["2026-11-06", "2026-11-09", "2026-11-10"]))
        verdict = self._judge(self._record(kind="fund"), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "insufficient")

    def test_one_price_limit_blocked(self):
        prices = self._prices()
        target = pd.Timestamp("2026-11-06")
        value = float(prices.loc[target, "close"])
        prices.loc[target, ["open", "high", "low", "close"]] = value
        verdict = self._judge(self._record(), prices, SESSIONS,
                                     date(2026, 11, 20))
        self.assertEqual(verdict["label"], "blocked")
        self.assertEqual(verdict["reason"], "limit_one_price")

    def test_flat_close_is_neutral_for_both_directions(self):
        prices = _observed(pd.Series(1.0, index=SESSIONS))
        for direction in ("up", "down"):
            record = self._record(direction)
            record["source"] = "direction"
            record["probability"] = 0.55
            verdict = self._judge(record, prices, SESSIONS,
                                         date(2026, 11, 20))
            self.assertEqual(verdict["label"], "neutral")
            self.assertEqual(verdict["realized_return"], 0.0)

    def test_rule_event_is_observed_not_a_direction_call(self):
        record = self._record("up")
        record["source"] = "rule"
        record["horizon"] = ledger.RULE_HORIZON
        verdict = self._judge(record, self._prices(), SESSIONS,
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
        # C1：顺延样本覆盖 h+1/h+2 会话，不进固定 h 的命中率分母
        self.assertEqual(stats["decided"], 0)
        self.assertIsNone(stats["hit_rate"])
        self.assertEqual(stats["excluded_grace_rows"], 1)
        self.assertEqual(stats["grace_scored_rows"], 1)
        self.assertAlmostEqual(stats["grace_return_mean"], 0.02)
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
        pd.testing.assert_index_equal(sessions, axis[axis <= "2026-12-10"])

    def test_fund_settlement_uses_recorded_nav_basis(self):
        today = date(2026, 12, 10)
        nav = pd.Series(1., index=SESSIONS)
        for basis in ("cum", "total"):
            for fetch in (False, True):
                record = {"asset_kind": "fund", "asset_code": "110022",
                          "price_basis": basis, "as_of": AS_OF}
                with self.subTest(basis=basis, fetch=fetch), \
                     patch("market_research.data.panels.load_fund_nav_cached",
                           return_value=nav) as loader:
                    prices = judge._load_prices(record, today, fetch)
                    self.assertLessEqual(prices.index.max().date(), today)
                    self.assertEqual(loader.call_args.kwargs["basis"], basis)
                    self.assertEqual(loader.call_args.kwargs["allow_fetch"], fetch)

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

    def test_index_prices_obey_cutoff_on_fetch_and_cache(self):
        today = date(2026, 11, 6)
        record = {"asset_kind": "index", "asset_code": "sh000300",
                  "price_basis": "", "as_of": AS_OF}
        frame = _rising_bars()
        with patch("market_research.tasks.sync.cached", return_value=frame) as cached, \
             patch("market_research.market.analysis.load_bars", return_value=frame) as loader:
            for fetch in (False, True):
                prices = judge._load_prices(record, today, fetch)
                self.assertLessEqual(prices.index.max().date(), today)
        self.assertEqual(cached.call_args.args[-1], today.isoformat())
        self.assertEqual(loader.call_args.args[-2], today.isoformat())

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
                counts = judge.settle(now=_settlement_time(today))
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
            counts = judge.settle(now=_settlement_time(date(2026, 12, 10)))
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
        row = ledger._records()[0]
        evidence = json.loads(row["settlement_evidence"])
        self.assertEqual(evidence["rule_version"], judge.SETTLEMENT_RULE_VERSION)
        self.assertEqual(evidence["observed_at"], bars.attrs["observed_at"])
        self.assertEqual(evidence["input_through"], row["judge_date"])
        self.assertRegex(evidence["input_sha256"], r"^[0-9a-f]{64}$")
        ledger.update_record(row["id"], label="insufficient", settlement_evidence={})
        stored = {item["id"]: item for item in ledger._records()}[row["id"]]
        self.assertEqual(stored["settlement_evidence"], row["settlement_evidence"])
        self.assertEqual(stored["label"], row["label"])

    def test_actual_sessions_column_persists_on_old_databases(self):
        ledger.record_result("market", {}, _market_result(),
                             now=datetime(2026, 11, 2))
        row = ledger._records()[0]
        ledger.update_record(row["id"], status="settled", label="correct",
                             actual_sessions=6)
        stored = [item for item in ledger._records()
                  if item["id"] == row["id"]][0]
        self.assertEqual(stored["actual_sessions"], 6)
        self.assertIsNone(stored["settlement_evidence"])

    def test_settle_keeps_pending_when_prices_unavailable(self):
        ledger.record_result("market", {}, _market_result(),
                             now=datetime(2026, 11, 2))
        with patch.object(judge, "_load_prices",
                          side_effect=ValueError("本地行情缓存未同步")):
            counts = judge.settle(now=_settlement_time(date(2026, 12, 10)))
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
                counts = judge.settle(now=_settlement_time(date(2026, 11, 8)))
        self.assertEqual(counts["unavailable"], 0)
        self.assertEqual(counts["correct"], 1)
        self.assertGreater(update.call_args.kwargs["realized_return"], 0)


class ScoredBasisTest(unittest.TestCase):
    """C1：Brier 按 P(R>0)（平盘=0）计分，命中率仍剔平盘，两套分母不合并。"""

    def test_blocked_with_known_target_price_enters_brier_not_hit_rate(self):
        rows = [
            {"source": "direction", "probability": 0.8, "direction": "up",
             "realized_return": 0.02, "label": "blocked", "status": "settled"},
            {"source": "direction", "probability": 0.3, "direction": "down",
             "realized_return": -0.01, "label": "correct", "status": "settled"},
        ]
        stats = ledger._stats(rows)
        # 涨跌停/零成交不改变"方向是否看对"的概率评分，只影响可成交性。
        self.assertEqual(stats["decided"], 1)
        self.assertEqual(stats["hit_rate"], 1.0)
        self.assertEqual(stats["probability_rows"], 2)
        self.assertEqual(stats["blocked_scored_rows"], 1)
        self.assertEqual(stats["brier_basis"],
                         "flat_as_down;blocked_if_target_known;grace_excluded")
        self.assertEqual(stats["scoring_version"], "flat_as_down_v3")
        expected = ((0.8 - 1) ** 2 + (0.3 - 0) ** 2) / 2
        self.assertAlmostEqual(stats["brier"], expected)

    def test_brier_includes_neutral_but_hit_rate_excludes_it(self):
        rows = [
            {"source": "direction", "probability": 0.8,
             "realized_return": 0.02, "label": "correct", "status": "settled"},
            {"source": "direction", "probability": 0.8,
             "realized_return": 0.0, "label": "neutral", "status": "settled"},
            {"source": "direction", "probability": 0.3,
             "realized_return": -0.01, "label": "correct", "status": "settled"},
        ]
        stats = ledger._stats(rows)
        self.assertEqual(stats["decided"], 2)
        self.assertEqual(stats["hit_rate"], 1.0)
        self.assertEqual(stats["hit_rate_basis"], "direction_only")
        self.assertEqual(stats["probability_rows"], 3)
        self.assertEqual(stats["neutral_scored_rows"], 1)
        self.assertEqual(stats["brier_basis"],
                         "flat_as_down;blocked_if_target_known;grace_excluded")
        expected = ((0.8 - 1) ** 2 + (0.8 - 0) ** 2 + (0.3 - 0) ** 2) / 3
        self.assertAlmostEqual(stats["brier"], expected)
        self.assertIn("平盘目标为 0", stats["note"])

    def test_grace_rows_do_not_enter_fixed_horizon_denominators(self):
        """C1：顺延 6/7 日收益不得冒充固定 5 日样本进入命中率或 Brier。"""
        rows = [
            {"source": "direction", "probability": 0.6, "direction": "up",
             "realized_return": 0.02, "label": "correct", "status": "settled"},
            {"source": "direction", "probability": 0.9, "direction": "up",
             "realized_return": -0.03, "label": "incorrect",
             "reason": "grace_session", "actual_sessions": 6,
             "status": "settled"},
        ]
        stats = ledger._stats(rows)
        self.assertEqual(stats["decided"], 1)
        self.assertEqual(stats["hit_rate"], 1.0)
        # 若顺延样本混入，Brier 会变成两个误差的平均（明显更高）
        self.assertEqual(stats["probability_rows"], 1)
        self.assertAlmostEqual(stats["brier"], (0.6 - 1.0) ** 2)
        # 顺延收益仍单列可见，不与固定 h 口径合并
        self.assertEqual(stats["grace_settled"], 1)
        self.assertEqual(stats["excluded_grace_rows"], 1)
        self.assertAlmostEqual(stats["grace_return_mean"], -0.03)
        self.assertEqual(stats["scoring_version"], "flat_as_down_v3")


if __name__ == "__main__":
    unittest.main()
