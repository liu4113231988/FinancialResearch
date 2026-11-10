# -*- coding: utf-8 -*-
"""P3-H：兼容路径下线策略与自动迁移脚本。"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import market_research.config as C
from market_research.api import version
from market_research.api.app import create_app

SCRIPT = C.ROOT / "scripts" / "migrate_api_paths.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_api_paths", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LegacyPolicyTests(unittest.TestCase):
    def test_default_sunset_and_explicit_switch(self):
        with patch.dict(os.environ, {"FIL_API_SUNSET": "",
                                    "FIL_API_LEGACY": "auto"}, clear=False):
            self.assertTrue(version.legacy_enabled())
            self.assertEqual(version.legacy_sunset(),
                             version.LEGACY_SUNSET_HTTP_DATE)
            self.assertIn("2027", version.legacy_sunset())
            response = TestClient(create_app()).get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["sunset"],
                         version.LEGACY_SUNSET_HTTP_DATE)
        with patch.dict(os.environ, {"FIL_API_LEGACY": "0"}):
            self.assertFalse(version.legacy_enabled())
        with patch.dict(os.environ, {"FIL_API_LEGACY": "1"}):
            self.assertTrue(version.legacy_enabled())

    def test_legacy_routes_can_be_disabled_without_touching_v1(self):
        with patch.dict(os.environ, {"FIL_API_LEGACY": "0"}, clear=False):
            client = TestClient(create_app())
            self.assertEqual(client.get("/api/health").status_code, 404)
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            paths = client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/v1/health", paths)
        self.assertNotIn("/api/health", paths)


class ApiPathMigrationTests(unittest.TestCase):
    def test_only_unversioned_quoted_paths_are_rewritten(self):
        tool = _load_script()
        source = ("const a = '/api/health';\n"
                  "const b = `/api/analyze?code=110022`;\n"
                  "const c = '/api/v1/health';\n"
                  "const d = '/static/app.js';\n")
        updated, count = tool.replace_paths(source)
        self.assertEqual(count, 2)
        self.assertEqual(updated.count("'/api/v1/health'"), 2)
        self.assertIn("`/api/v1/analyze?code=110022`", updated)
        self.assertIn("const d = '/static/app.js'", updated)

    def test_cli_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory(dir=C.ROOT / "output") as tmp:
            target = Path(tmp) / "sample.js"
            original = "fetch('/api/catalog')"
            target.write_text(original, encoding="utf-8")
            dry = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(target)],
                capture_output=True, text=True, check=True)
            self.assertIn("dry-run", dry.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            applied = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(target), "--apply"],
                capture_output=True, text=True, check=True)
            self.assertIn("apply", applied.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "fetch('/api/v1/catalog')")


if __name__ == "__main__":
    unittest.main()
