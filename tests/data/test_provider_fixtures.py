# -*- coding: utf-8 -*-
"""P2-35：供应商契约 fixture 必须覆盖全部来源并可离线重放。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research.data.provenance import (CONTRACTS, require_columns,
                                             resolve_columns)
from market_research.errors import ProviderContractError
from scripts import record_provider_fixtures as recorder
from tests.data import provider_fixtures


def _offline(_ak):
    raise RuntimeError("offline")


class ProviderFixtureTests(unittest.TestCase):
    def test_manifest_covers_all_contracts_and_replays_cleanly(self):
        available = provider_fixtures.validate_manifest()
        self.assertEqual(available, sorted(CONTRACTS))
        for source in available:
            with self.subTest(source=source):
                frame = provider_fixtures.replay(source)
                info = provider_fixtures.load_manifest()["sources"][source]
                self.assertEqual(list(frame.columns), info["fields"])
                self.assertEqual(len(frame), len(info["rows"]))
                values = frame[info["numeric_field"]].to_numpy(dtype=float)
                self.assertTrue(np.isfinite(values).all())
                if source != "fund_cum_return":
                    self.assertTrue((values > 0).all())

    def test_replay_uses_the_same_contract_validator_as_production(self):
        frame = provider_fixtures.replay("fund_nav")
        require_columns(frame, CONTRACTS["fund_nav"]["fields"], "fund_nav")
        with self.assertRaises(ProviderContractError):
            require_columns(frame.rename(columns={"累计净值": "净值"}),
                            CONTRACTS["fund_nav"]["fields"], "fund_nav")

    def test_manifest_rejects_raw_redistribution_or_missing_source(self):
        manifest = provider_fixtures.load_manifest()
        manifest["raw_data_included"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "原始第三方响应"):
                provider_fixtures.validate_manifest(path)
            manifest["raw_data_included"] = False
            del manifest["sources"]["fund_nav"]
            path.write_text(json.dumps(manifest, ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不一致"):
                provider_fixtures.validate_manifest(path)


class ProviderColumnDriftTests(unittest.TestCase):
    """真实 AkShare 把「日期」改名为「净值日期」时仍可录制脱敏观测指纹。"""

    @staticmethod
    def frame(date_column="净值日期", value_column="累计净值"):
        return pd.DataFrame({
            date_column: ["2026-11-02", "2026-11-03", "2026-11-04"],
            value_column: [1.0, 1.01, 1.02],
            "无关列": [0, 0, 0],
        })

    def test_resolve_columns_accepts_registered_alias(self):
        mapping = resolve_columns(self.frame(), "fund_nav")
        self.assertEqual(mapping, {"日期": "净值日期", "累计净值": "累计净值"})
        self.assertEqual(list(mapping.values()), ["净值日期", "累计净值"])

    def test_resolve_columns_keeps_canonical_names(self):
        mapping = resolve_columns(self.frame("日期"), "fund_nav")
        self.assertEqual(mapping, {"日期": "日期", "累计净值": "累计净值"})

    def test_resolve_columns_rejects_unregistered_drift(self):
        with self.assertRaises(ProviderContractError):
            resolve_columns(self.frame("成交日"), "fund_nav")
        with self.assertRaises(ProviderContractError):
            resolve_columns(self.frame(value_column="净值"), "fund_nav")

    def test_observation_keeps_real_columns_and_alias_evidence(self):
        selected, aliases = recorder._contract_frame("fund_nav", self.frame())
        obs = recorder._observation("fund_nav", selected, aliases)
        self.assertEqual(obs["fields"], ["净值日期", "累计净值"])
        self.assertEqual(obs["contract_fields"],
                         CONTRACTS["fund_nav"]["fields"])
        self.assertEqual(obs["field_aliases_applied"], {"日期": "净值日期"})
        self.assertIs(obs["raw_response_included"], False)
        self.assertEqual(obs["row_count"], 3)
        self.assertNotIn("rows", obs)

    def test_manifest_accepts_sanitized_live_observation(self):
        selected, aliases = recorder._contract_frame("fund_nav", self.frame())
        obs = recorder._observation("fund_nav", selected, aliases)
        manifest = provider_fixtures.load_manifest()
        manifest["sources"]["fund_nav"]["live_observation"] = obs
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False),
                            encoding="utf-8")
            self.assertEqual(provider_fixtures.validate_manifest(path),
                             sorted(CONTRACTS))

    def test_manifest_rejects_raw_or_unregistered_alias_in_observation(self):
        selected, aliases = recorder._contract_frame("fund_nav", self.frame())
        base = recorder._observation("fund_nav", selected, aliases)
        variants = [
            {**base, "raw_response_included": True},
            {**base, "rows": [{"净值日期": "2026-11-02"}]},
            {**base, "field_aliases_applied": {"日期": "成交日"}},
            {**base, "contract_fields": ["净值日期", "累计净值"]},
        ]
        manifest = provider_fixtures.load_manifest()
        for obs in variants:
            with self.subTest(obs=obs):
                manifest["sources"]["fund_nav"]["live_observation"] = obs
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "contracts.json"
                    path.write_text(json.dumps(manifest, ensure_ascii=False),
                                    encoding="utf-8")
                    with self.assertRaises(ValueError):
                        provider_fixtures.validate_manifest(path)

    def test_record_live_reports_failed_source_without_fabricating(self):
        manifest = provider_fixtures.load_manifest()
        for info in manifest["sources"].values():
            info.pop("live_observation", None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracts.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False),
                            encoding="utf-8")
            calls = {source: _offline for source in recorder.LIVE_CALLS}
            with patch.object(recorder, "MANIFEST", path), \
                    patch.object(recorder, "LIVE_CALLS", calls), \
                    patch.dict(sys.modules, {"akshare": object()}):
                result, raw_frames, errors = recorder.record_live()
        self.assertEqual(set(errors), set(CONTRACTS))
        self.assertTrue(all("offline" in error for error in errors.values()))
        self.assertEqual(raw_frames, {})
        self.assertFalse(any("live_observation" in info
                             for info in result["sources"].values()))


if __name__ == "__main__":
    unittest.main()
