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
from market_research.research.future_validation import (_plan_hash,
                                                        evaluate_real_trades,
                                                        evidence_hash,
                                                        load_execution,
                                                        load_freeze_evidence,
                                                        load_plan, validate_plan,
                                                        validate_execution,
                                                        validate_freeze_evidence)

PLAN = C.ROOT / "docs" / "future-validation-plan.json"
LEDGER = C.ROOT / "docs" / "future-validation-execution.json"
EVIDENCE = C.ROOT / "docs" / "future-validation-freeze-evidence.json"
SCRIPT = C.ROOT / "scripts" / "validate_real_trades.py"
FREEZE_SCRIPT = C.ROOT / "scripts" / "freeze_future_validation_evidence.py"


def _validation(as_of):
    return validate_execution(load_plan(PLAN), load_execution(LEDGER),
                              as_of=as_of)


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
        scheduled = _validation("2026-11-10")
        collecting = _validation("2027-01-01")
        overdue = _validation("2027-06-01")
        self.assertEqual(scheduled["status"], "scheduled")
        self.assertEqual(collecting["status"], "collecting_future_samples")
        self.assertEqual(overdue["status"], "overdue_incomplete")
        self.assertEqual(scheduled["items_registered"], 6)
        self.assertEqual(scheduled["items_pending"], 6)
        self.assertEqual(len(scheduled["protocol_sha256"]), 64)

    def test_protocol_rejects_embedded_execution_state(self):
        plan = load_plan(PLAN)
        plan["status"] = "registered_not_executed"
        with self.assertRaisesRegex(ValueError, "不得含执行状态"):
            validate_plan(plan, as_of="2026-11-10")
        plan = load_plan(PLAN)
        plan["items"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "协议文件必须保持冻结"):
            validate_plan(plan, as_of="2026-11-10")


class ExecutionLedgerTests(unittest.TestCase):
    def test_protocol_has_no_mutable_execution_state(self):
        plan = load_plan(PLAN)
        self.assertNotIn("status", plan)
        for item in plan["items"]:
            self.assertNotIn("status", item)
            self.assertNotIn("evidence", item)
        self.assertEqual(plan["execution_ledger"],
                         "docs/future-validation-execution.json")

    def test_ledger_is_bound_to_the_frozen_protocol(self):
        ledger = load_execution(LEDGER)
        self.assertEqual(ledger["protocol_sha256"], _plan_hash(load_plan(PLAN)))
        report = _validation("2026-11-10")
        self.assertTrue(report["execution_verified"])
        self.assertEqual(report["execution_status"], "registered_not_executed")

    def test_ledger_rejects_mismatched_protocol_hash(self):
        ledger = load_execution(LEDGER)
        ledger["protocol_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "协议指纹不匹配"):
            validate_execution(load_plan(PLAN), ledger, as_of="2026-11-10")

    def test_ledger_rejects_unknown_item_ids(self):
        ledger = load_execution(LEDGER)
        ledger["items"][0]["id"] = "unknown_item"
        with self.assertRaisesRegex(ValueError, "一一对应"):
            validate_execution(load_plan(PLAN), ledger, as_of="2026-11-10")

    def test_completion_requires_evidence_in_ledger(self):
        ledger = load_execution(LEDGER)
        ledger["items"][0]["status"] = "complete"
        with self.assertRaisesRegex(ValueError, "不得无证据标记完成"):
            validate_execution(load_plan(PLAN), ledger, as_of="2027-06-01")

    def test_evidence_growth_does_not_change_protocol_digest(self):
        plan = load_plan(PLAN)
        digest = _plan_hash(plan)
        ledger = load_execution(LEDGER)
        ledger["items"][0]["status"] = "complete"
        ledger["items"][0]["evidence"] = ["真实成交记录与可复现最小输入"]
        ledger["status"] = "in_progress"
        report = validate_execution(plan, ledger, as_of="2027-06-01")
        self.assertEqual(_plan_hash(plan), digest)
        self.assertEqual(report["items_pending"], 5)
        self.assertEqual(report["status"], "overdue_incomplete")


class FreezeEvidenceTests(unittest.TestCase):
    def test_evidence_is_bound_to_protocol_and_ledger(self):
        plan = load_plan(PLAN)
        report = validate_freeze_evidence(plan, load_execution(LEDGER),
                                          load_freeze_evidence(EVIDENCE))
        self.assertTrue(report["verified"])
        self.assertEqual(report["protocol_sha256"], _plan_hash(plan))
        self.assertEqual(report["evidence_sha256"],
                         load_execution(LEDGER)["freeze_evidence_sha256"])
        self.assertFalse(report["raw_data_included"])

    def test_frozen_snapshot_rejects_tampering(self):
        evidence = load_freeze_evidence(EVIDENCE)
        evidence["runtime"]["python"] = "0.0.0"
        with self.assertRaisesRegex(ValueError, "自身指纹不匹配"):
            validate_freeze_evidence(load_plan(PLAN), load_execution(LEDGER),
                                     evidence)

    def test_raw_data_and_fake_inputs_are_rejected(self):
        ledger = load_execution(LEDGER)
        evidence = load_freeze_evidence(EVIDENCE)
        evidence["data_authorization"]["raw_data_included"] = True
        evidence["evidence_sha256"] = evidence_hash(evidence)
        with self.assertRaisesRegex(ValueError, "不得包含原始数据"):
            validate_freeze_evidence(load_plan(PLAN), ledger, evidence)
        evidence = load_freeze_evidence(EVIDENCE)
        evidence["inputs"]["declared_no_future_data"] = False
        evidence["evidence_sha256"] = evidence_hash(evidence)
        with self.assertRaisesRegex(ValueError, "不得伪造原始输入指纹"):
            validate_freeze_evidence(load_plan(PLAN), ledger, evidence)

    def test_ledger_binding_mismatch_is_rejected(self):
        ledger = load_execution(LEDGER)
        ledger["freeze_evidence_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "台账绑定的冻结证据指纹不匹配"):
            validate_freeze_evidence(load_plan(PLAN), ledger,
                                     load_freeze_evidence(EVIDENCE))

    def test_freeze_day_declares_no_future_inputs(self):
        evidence = load_freeze_evidence(EVIDENCE)
        self.assertEqual(evidence["freeze_date"],
                         load_plan(PLAN)["pre_registration"]["freeze_date"])
        self.assertTrue(evidence["inputs"]["declared_no_future_data"])
        self.assertEqual(evidence["inputs"]["status"],
                         "no_future_inputs_at_freeze")
        self.assertTrue(evidence["code"]["git_dirty"])
        self.assertTrue(evidence["code"]["source_sha256"])
        self.assertTrue(evidence["runtime"]["lock_files_sha256"])

    def test_cli_verifies_and_refuses_overwrite(self):
        verified = subprocess.run([sys.executable, str(FREEZE_SCRIPT)],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(verified.returncode, 0, verified.stdout)
        self.assertIn('"verified": true', verified.stdout)
        overwrite = subprocess.run([sys.executable, str(FREEZE_SCRIPT),
                                    "--apply"], capture_output=True, text=True,
                                   check=False)
        self.assertNotEqual(overwrite.returncode, 0)
        self.assertIn("拒绝覆盖", overwrite.stdout)


class ProtocolHashTests(unittest.TestCase):
    def test_declared_digest_matches_frozen_plan(self):
        plan = load_plan(PLAN)
        declared = plan["pre_registration"]["protocol_sha256"]
        self.assertEqual(len(declared), 64)
        self.assertEqual(_plan_hash(plan), declared)
        self.assertEqual(validate_plan(plan, as_of="2026-11-10")["protocol_sha256"],
                         declared)

    def test_digest_can_be_written_back_without_changing_itself(self):
        plan = load_plan(PLAN)
        plan["pre_registration"]["protocol_sha256"] = None
        digest = _plan_hash(plan)
        plan["pre_registration"]["protocol_sha256"] = digest
        self.assertEqual(_plan_hash(plan), digest)
        plan["items"][0]["protocol_sha256"] = digest
        self.assertEqual(_plan_hash(plan), digest)

    def test_other_protocol_changes_still_change_the_digest(self):
        plan = load_plan(PLAN)
        baseline = _plan_hash(plan)
        plan["items"][0]["completion_criteria"].append("额外条件")
        self.assertNotEqual(_plan_hash(plan), baseline)


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
        self.assertTrue(report["plan"]["execution_verified"])
        self.assertEqual(report["trades"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
