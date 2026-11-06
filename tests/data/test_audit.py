"""P2-26：审计日志的脱敏、截断与失败静默。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market_research.audit as audit
import market_research.config as C


class AuditRecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(C, "DATA_DIR", Path(self.directory.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def _lines(self):
        path = Path(self.directory.name) / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_record_writes_expected_fields(self):
        entry = audit.record("analysis.run", subject="analyze:110022",
                             detail={"status": "ok", "job_id": "abc"},
                             duration_s=1.234)
        self.assertTrue(entry["ts"])
        self.assertIn("event", entry)
        self.assertEqual(entry["event"], "analysis.run")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["subject"], "analyze:110022")
        self.assertEqual(entry["detail"]["status"], "ok")
        self.assertEqual(entry["duration_s"], 1.23)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "analysis.run")

    def test_sensitive_keys_are_redacted(self):
        entry = audit.record("analysis.run",
                             detail={"params": {"alpha": 1},
                                     "token": "secret-value",
                                     "nested": {"authorization": "Bearer x"}})
        self.assertEqual(entry["detail"]["params"], "<redacted>")
        self.assertEqual(entry["detail"]["token"], "<redacted>")
        self.assertEqual(entry["detail"]["nested"]["authorization"], "<redacted>")

    def test_long_strings_and_deep_objects_are_truncated(self):
        entry = audit.record("data.write",
                             detail={"note": "x" * 500,
                                     "deep": {"a": {"b": {"c": 1}}}})
        self.assertEqual(len(entry["detail"]["note"]), 200)
        self.assertEqual(entry["detail"]["deep"]["a"]["b"], "<truncated>")

    def test_write_failure_is_silent(self):
        with tempfile.TemporaryDirectory() as parent:
            blocked = Path(parent) / "not-a-dir"
            blocked.write_text("file", encoding="utf-8")
            with patch.object(C, "DATA_DIR", blocked):
                entry = audit.record("analysis.run", detail={"code": "110022"})
        self.assertIn("event", entry)
        self.assertEqual(entry["event"], "analysis.run")


if __name__ == "__main__":
    unittest.main()
