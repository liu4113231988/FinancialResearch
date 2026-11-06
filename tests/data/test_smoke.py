# -*- coding: utf-8 -*-
"""P2-35：真实冒烟的失败分类与结果落盘。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_research.errors import NavBasisUnavailable, ProviderContractError
from market_research.ops import smoke


class ClassifyFailureTests(unittest.TestCase):
    def test_contract_error_is_data_source(self):
        self.assertEqual(
            smoke.classify_failure("analyze", ProviderContractError("rename")),
            "data_source")

    def test_network_error_is_data_source(self):
        self.assertEqual(
            smoke.classify_failure("fetch", ConnectionError("down")),
            "data_source")

    def test_local_value_error_is_logic(self):
        self.assertEqual(smoke.classify_failure("report", ValueError("bad")),
                         "logic")

    def test_key_error_in_data_stage_is_logic(self):
        self.assertEqual(smoke.classify_failure("fetch", KeyError("column")),
                         "logic")

    def test_nav_basis_failure_is_data_source(self):
        self.assertEqual(
            smoke.classify_failure("analyze", NavBasisUnavailable("no total")),
            "data_source")


class SmokeRunTests(unittest.TestCase):
    def test_failure_is_recorded_with_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("market_research.data.marketdata.load_panel",
                       side_effect=ProviderContractError("字段改名")):
                result = smoke.run("110022", tmp)
            self.assertFalse(result["ok"])
            self.assertEqual(result["stage"], "fetch")
            self.assertEqual(result["failure_class"], "data_source")
            saved = json.loads((Path(tmp) / "smoke_result.json").read_text(
                encoding="utf-8"))
            self.assertFalse(saved["ok"])
            self.assertEqual(saved["failure_class"], "data_source")


if __name__ == "__main__":
    unittest.main()
