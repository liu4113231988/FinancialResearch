# -*- coding: utf-8 -*-
"""P2-33：备份 → 校验 → 恢复 → 一致性检查的闭环。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_research.config as C
import market_research.audit as audit
from market_research.data import storage
from market_research.ops import backup as ops


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir(parents=True)
        self.patch = patch.object(C, "DATA_DIR", self.data_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)
        frame = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0, 5.0]},
                             index=pd.date_range("2024-01-01", periods=5))
        storage.write("pe_panel", frame, {"source": "test", "start": "2024-01-01"})
        audit.record("test_backup", ok=True, subject="unit-test")

    def test_backup_verify_restore_roundtrip(self):
        dest = Path(self.tmp.name) / "backup"
        manifest = ops.backup(dest)
        self.assertEqual(manifest["n_datasets"], 1)
        self.assertTrue((dest / "manifest.json").exists())
        self.assertEqual(ops.verify(dest)["ok"], True)

        result = ops.restore(dest)
        self.assertTrue(result["ok"])
        self.assertEqual(result["live"]["n_price_rows"],
                         manifest["n_price_rows"])
        restored = storage.read("pe_panel")
        self.assertEqual(len(restored), 5)
        self.assertTrue((self.data_dir / "audit.jsonl").exists())

    def test_verify_flags_tampered_manifest(self):
        dest = Path(self.tmp.name) / "backup"
        ops.backup(dest)
        manifest = json.loads((dest / "manifest.json").read_text("utf-8"))
        manifest["revision_hash"] = "broken"
        (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = ops.verify(dest)
        self.assertFalse(result["ok"])
        self.assertTrue(any("revision" in p for p in result["problems"]))

    def test_restore_rejects_invalid_backup(self):
        with self.assertRaises(ops.BackupError):
            ops.restore(Path(self.tmp.name) / "missing")


if __name__ == "__main__":
    unittest.main()
