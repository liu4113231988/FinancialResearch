# -*- coding: utf-8 -*-
"""C3：计算/数据/证据/入账/成交分层的结果契约。"""
import unittest

from market_research import status


class StatusLayerTests(unittest.TestCase):
    def test_all_layers_ok_is_not_degraded(self):
        block = status.build(computation="ok", data_quality="ok",
                            assessment="sufficient", registration="recorded",
                            execution="evaluated")
        self.assertEqual(block["summary"]["status"], "ok")
        self.assertFalse(block["summary"]["degraded"])
        self.assertFalse(block["summary"]["blocking"])
        self.assertEqual(block["summary"]["degraded_layers"], [])
        self.assertIn("未入账不等于计算失败", block["note"])

    def test_not_recorded_is_advisory_not_a_degradation(self):
        block = status.build(computation="ok", data_quality="ok",
                            assessment="sufficient", registration="not_recorded")
        # 历史区间不写台账是正常状态：提示可见，但不把结果判成部分失败
        self.assertEqual(block["summary"]["status"], "ok")
        self.assertFalse(block["summary"]["blocking"])
        self.assertFalse(block["summary"]["degraded"])
        self.assertIn("registration", block["summary"]["advisory_layers"])
        self.assertIn("registration", block["summary"]["degraded_layers"])

    def test_insufficient_assessment_still_partial_without_blocking(self):
        block = status.build(computation="ok", assessment="insufficient")
        self.assertEqual(block["summary"]["status"], "partial")
        self.assertFalse(block["summary"]["blocking"])
        self.assertIn("assessment", block["summary"]["degraded_layers"])

    def test_computation_failure_blocks_regardless_of_other_layers(self):
        block = status.build(computation="failed", data_quality="ok",
                            assessment="unknown", registration="not_recorded")
        self.assertEqual(block["summary"]["status"], "failed")
        self.assertTrue(block["summary"]["blocking"])
        self.assertFalse(block["summary"]["degraded"])

    def test_unavailable_data_is_worse_than_partial(self):
        block = status.build(computation="ok", data_quality="unavailable")
        self.assertEqual(block["summary"]["status"], "partial")
        self.assertIn("data_quality", block["summary"]["degraded_layers"])

    def test_illegal_values_are_rejected_loudly(self):
        for kwargs in ({"computation": "done"}, {"data_quality": "ok!"},
                       {"assessment": "verified"}, {"registration": "ok"},
                       {"execution": "ok"}):
            with self.assertRaises(ValueError):
                status.build(**kwargs)

    def test_never_emits_verified_wording(self):
        block = status.build(assessment="sufficient", registration="recorded")
        text = str(block)
        self.assertNotIn("已验证", text)
        self.assertNotIn("verified", text.lower())


class LayerAggregationTests(unittest.TestCase):
    def test_assessment_layer_prefers_sufficient_and_never_defaults_to_it(self):
        self.assertEqual(status.assessment_layer(["insufficient", "sufficient"]),
                         "sufficient")
        self.assertEqual(status.assessment_layer(["insufficient", "unknown"]),
                         "insufficient")
        self.assertEqual(status.assessment_layer(["failed", "failed"]), "failed")
        self.assertEqual(status.assessment_layer([]), "unknown")
        self.assertEqual(status.assessment_layer([None, None]), "unknown")

    def test_market_evidence_mapping_keeps_missing_evidence_conservative(self):
        self.assertEqual(status.evidence_assessment(
            ["historical_diagnostic"]), "sufficient")
        self.assertEqual(status.evidence_assessment(
            ["independent_verification_pending"]), "sufficient")
        self.assertEqual(status.evidence_assessment(
            ["insufficient", "not_computable"]), "insufficient")
        self.assertEqual(status.evidence_assessment(["not_computable"]), "unknown")
        self.assertEqual(status.evidence_assessment([]), "unknown")


class FromResultTests(unittest.TestCase):
    def test_fund_result_layers(self):
        result = {
            "status": "partial",
            "completeness": {"status": "partial", "ok": True,
                             "reasons": ["估值数据不可用"]},
            "meta": {"data_quality": {"status": "ok"},
                     "registration": {"status": "recorded", "recorded": 3}},
            "models": {"pcr": {"assessment": {"status": "insufficient"}},
                       "ridge": {"assessment": {"status": "failed"}}},
        }
        block = status.from_result(result)
        self.assertEqual(block["computation"], "partial")
        self.assertEqual(block["data_quality"], "ok")
        self.assertEqual(block["assessment"], "insufficient")
        self.assertEqual(block["registration"], "recorded")
        self.assertEqual(block["execution"], "not_applicable")
        self.assertIn("估值数据不可用", block["summary"]["reasons"])

    def test_market_result_layers_use_forecast_evidence(self):
        result = {
            "status": "partial",
            "completeness": {"ok": True, "status": "partial"},
            "meta": {"data_quality": {"status": "partial"}},
            "technical": {"forecasts": [{"evidence_state": "historical_diagnostic"},
                                        {"evidence_state": "insufficient"}]},
        }
        block = status.from_result(result)
        self.assertEqual(block["assessment"], "sufficient")
        self.assertEqual(block["data_quality"], "partial")
        self.assertEqual(block["summary"]["status"], "partial")

    def test_missing_fields_degrade_to_unknown_instead_of_ok(self):
        block = status.from_result({})
        self.assertEqual(block["assessment"], "unknown")
        self.assertEqual(block["registration"], "unknown")
        self.assertEqual(block["data_quality"], "unavailable")

    def test_refresh_writes_block_on_result(self):
        result = {"status": "ok", "completeness": {"status": "ok", "ok": True},
                  "meta": {}}
        block = status.refresh(result)
        self.assertIs(result["status_layers"], block)
        # 未知入账/未知数据质量属于"没证据"，仍按降级提示
        self.assertEqual(block["summary"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
