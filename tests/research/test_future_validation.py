# -*- coding: utf-8 -*-
"""六项未来验证预注册与真实成交诊断的离线契约测试。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

import market_research.config as C
from market_research.research.future_validation import (evaluate_real_trades,
                                                        load_plan, validate_plan)

PLAN = C.ROOT / "docs" / "future-validation-plan.json"
SCRIPT = C.ROOT / "scripts" / "validate_real_trades.py"


def _trades():
    return pd.DataFrame([
        {"signal_date": "2026-11-02", "available_at": "2026-11-02",
         "execution_date": "2026-11-03", "symbol": "510300", "side": "buy",
         "quantity": 10, "price": 100.0, "benchmark_price": 100.0,
         "fee": 1.0, "tax": 0.0, "slippage": 0.0},
        {"signal_date": "2026-11-04", "available_at": "2026-11-04",
         "execution_date": "2026-11-05", "symbol": "510300", "side": "sell",
         "quantity": 10, "price": 110.0, "benchmark_price": 109.5,
         "fee": 1.0, "tax": 0.0, "slippage": 0.0},
    ])


class FuturePlanTests(unittest.TestCase):
    def test_registered_plan_reports_real_status_without_claiming_success(self):
        plan = load_plan(PLAN)
        scheduled = validate_plan(plan, as_of="2026-11-10")
        collecting = validate_plan(plan, as_of="2027-01-01")
        overdue = validate_plan(plan, as_of="2027-06-01")
        self.assertEqual(scheduled["status"], "scheduled")
        self.assertEqual(collecting["status"], "collecting_future_samples")
        self.assertEqual(overdue["status"], "overdue_incomplete")
        self.assertEqual(scheduled["items_registered"], 6)
        self.assertEqual(scheduled["items_pending"], 6)
        self.assertEqual(len(scheduled["protocol_sha256"]), 64)

    def test_completion_without_evidence_is_rejected(self):
        plan = load_plan(PLAN)
        plan["items"][0]["status"] = "complete"
        with self.assertRaisesRegex(ValueError, "不得无证据标记完成"):
            validate_plan(plan, as_of="2027-06-01")


class RealTradesTests(unittest.TestCase):
    def test_fifo_cost_and_slippage_are_reported(self):
        report = evaluate_real_trades(_trades())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["row_count"], 2)
        self.assertEqual(report["future_leak_rows"], 0)
        self.assertAlmostEqual(report["fifo_realized_pnl"], 98.0)
        self.assertAlmostEqual(report["explicit_cost"], 2.0)
        self.assertEqual(report["open_quantity"], 0.0)
        self.assertLess(report["mean_slippage_bps"], 0)  # 卖出价高于基准=有利成交
        self.assertGreater(report["max_abs_slippage_bps"], 0)
        self.assertIn("正值表示", report["slippage_sign_convention"])

    def test_future_information_and_oversell_are_flagged(self):
        frame = _trades()
        frame.loc[0, "available_at"] = "2026-11-03"
        frame.loc[1, "quantity"] = 20
        report = evaluate_real_trades(frame)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["future_leak_rows"], 1)
        self.assertTrue(any("超过持仓" in item for item in report["violations"]))

    def test_required_columns_and_bad_side_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            evaluate_real_trades(_trades().drop(columns=["benchmark_price"]))
        frame = _trades()
        frame.loc[0, "side"] = "hold"
        with self.assertRaisesRegex(ValueError, "side"):
            evaluate_real_trades(frame)

    def test_cli_writes_auditable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            trades = Path(tmp) / "trades.csv"
            output = Path(tmp) / "diagnostic.json"
            _trades().to_csv(trades, index=False)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--trades", str(trades),
                 "--as-of", "2026-11-10", "--out", str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["plan"]["status"], "scheduled")
        self.assertEqual(report["trades"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
