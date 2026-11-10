# -*- coding: utf-8 -*-
"""P2-34：环境分档与结果指纹。"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_research.config as C
import market_research.data.storage as storage
from market_research.research.runinfo import config_hash, config_snapshot, result_provenance
from market_research.services.analysis import (_checkpoint_for, _data_stamp_for,
                                               _stamp, result_cache_path)


class ProvenanceTests(unittest.TestCase):
    def test_result_fingerprint_has_versions(self):
        info = result_provenance()
        for key in ("environment", "code_version", "config_version",
                    "git_commit", "generated_at"):
            self.assertIn(key, info)
        self.assertEqual(info["environment"], C.ENV)

    def test_stamp_attaches_to_meta(self):
        payload = {"ok": True, "meta": {"n_days": 10}}
        stamped = _stamp(payload)
        self.assertIn("provenance", stamped["meta"])
        self.assertEqual(stamped["meta"]["provenance"]["code_version"],
                         C.CACHE_VERSION)

    def test_stamp_creates_meta_when_missing(self):
        payload = {"ok": True}
        stamped = _stamp(payload, "stamp-v1")
        self.assertEqual(stamped["meta"]["provenance"]["data_stamp"], "stamp-v1")
        self.assertTrue(stamped["meta"]["provenance"]["generated_at"])

    def test_config_hash_changes_with_research_and_window_parameters(self):
        baseline = config_hash()
        snapshot = config_snapshot()
        self.assertIn("RBSA", snapshot)
        self.assertIn("SIGNAL", snapshot)
        self.assertIn("window", snapshot)
        self.assertIn("config_hash", result_provenance())
        baseline_path = result_cache_path("110022", ["enet_slsqp"], {})
        with patch.dict(C.RBSA, {"window": C.RBSA["window"] + 1}):
            self.assertNotEqual(config_hash(), baseline)
            self.assertNotEqual(
                result_cache_path("110022", ["enet_slsqp"], {}), baseline_path)
        with patch.object(C, "MIN_WINDOW_YEARS", C.MIN_WINDOW_YEARS + 1):
            self.assertNotEqual(config_hash(), baseline)

    def test_checkpoint_fingerprint_changes_with_config(self):
        import market_research.tasks.checkpoints as checkpoints
        with tempfile.TemporaryDirectory(dir=C.ROOT / "output") as tmp, \
             patch.object(checkpoints, "root", return_value=Path(tmp)):
            first = _checkpoint_for("analyze", {"code": "110022"},
                                    "job-config", "stamp")
            baseline = first.fingerprint
            with patch.dict(C.RBSA, {"window": C.RBSA["window"] + 1}):
                second = _checkpoint_for("analyze", {"code": "110022"},
                                         "job-config", "stamp")
            self.assertNotEqual(second.fingerprint, baseline)
            self.assertIn("config_hash", second.fingerprint)

    def test_data_stamp_changes_when_data_version_changes(self):
        """数据变化后指纹必须随之变化；基金窗口只跟踪自己的依赖。"""
        frame = pd.DataFrame({"a": [1.0]},
                             index=pd.date_range("2020-01-01", periods=1, name="date"))
        with tempfile.TemporaryDirectory(dir=C.ROOT / "output") as tmp:
            with patch.object(C, "DATA_DIR", Path(tmp)):
                before = _data_stamp_for("market", {})
                market_payload = _stamp({"ok": True}, before)
                storage.write("pe_panel", frame, {"v": 1})
                after = _data_stamp_for("market", {})
                self.assertNotEqual(before, after)
                self.assertNotEqual(
                    market_payload["meta"]["provenance"]["data_stamp"],
                    _stamp({"ok": True}, after)["meta"]["provenance"]["data_stamp"])
                # 其它基金的缓存变化不影响本基金的窗口指纹
                fund_before = _data_stamp_for("analyze", {"code": "110022"})
                storage.write("fund_nav_000001_2016-01-01_total", frame, {})
                self.assertEqual(fund_before, _data_stamp_for("analyze", {"code": "110022"}))
                storage.write("fund_nav_110022_2016-01-01_total", frame, {})
                self.assertNotEqual(fund_before,
                                    _data_stamp_for("analyze", {"code": "110022"}))

    def test_env_switch_moves_default_data_dir(self):
        env = {k: v for k, v in os.environ.items() if k != "FIL_DATA_DIR"}
        env["FIL_ENV"] = "dev"
        out = subprocess.run(
            [sys.executable, "-c",
             "import market_research.config as C; print(C.ENV); print(C.DATA_DIR)"],
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "dev")
        self.assertIn(os.path.join("output", "dev", "data"), lines[1])

    def test_unknown_env_falls_back_to_local(self):
        env = {k: v for k, v in os.environ.items() if k != "FIL_DATA_DIR"}
        env["FIL_ENV"] = "staging"
        out = subprocess.run(
            [sys.executable, "-c",
             "import market_research.config as C; print(C.ENV)"],
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "local")


if __name__ == "__main__":
    unittest.main()
