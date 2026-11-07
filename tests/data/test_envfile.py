import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market_research import envfile


class EnvFileTest(unittest.TestCase):
    def test_parse_comments_quotes_and_export(self):
        values = envfile.parse(
            "\ufeff# comment\n\nFIL_PORT=8877\nexport FIL_HOST='127.0.0.1'\n"
            'FIL_TOKEN="a=b"\nbroken line\n'
        )
        self.assertEqual(values["FIL_PORT"], "8877")
        self.assertEqual(values["FIL_HOST"], "127.0.0.1")
        self.assertEqual(values["FIL_TOKEN"], "a=b")
        self.assertNotIn("broken line", values)

    def test_load_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FIL_PORT=9999\nFIL_LOG_LEVEL=DEBUG\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"FIL_PORT": "8877", "FIL_ENV_FILE": str(path)}, clear=False):
                self.assertTrue(envfile.load())
                self.assertEqual(os.environ["FIL_PORT"], "8877")
                self.assertEqual(os.environ["FIL_LOG_LEVEL"], "DEBUG")

    def test_load_without_env_file_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(envfile.load())
            self.assertEqual(os.environ, {})

    def test_load_missing_file_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.env"
            with mock.patch.dict(os.environ, {"FIL_ENV_FILE": str(missing)}, clear=True):
                self.assertFalse(envfile.load())

    def test_load_reports_values_used_by_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FIL_MAX_JOBS=3\nFIL_JOB_MEMORY_MB=512\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"FIL_ENV_FILE": str(path)}, clear=True):
                envfile.load()
                from market_research.settings import Settings
                settings = Settings.load()
                self.assertEqual(settings.max_jobs, 3)
                self.assertEqual(settings.job_memory_mb, 512)


if __name__ == "__main__":
    unittest.main()
