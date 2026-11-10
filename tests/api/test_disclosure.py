# -*- coding: utf-8 -*-
"""P2-36：Web 声明接口的默认口径与可配置升级位。"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from market_research.api.app import create_app
from market_research import data_license, disclosure


class DisclosureApiTests(unittest.TestCase):
    def test_default_internal_wording(self):
        with patch.dict(os.environ, {"FIL_DISCLOSURE_SCOPE": "internal"},
                        clear=False):
            info = disclosure.payload()
        self.assertEqual(info["scope"], "internal")
        self.assertEqual(info["position"], "top")
        self.assertIn("公开抓取", info["text"])
        self.assertIn("不构成投资建议", info["text"])
        self.assertEqual(info["data_source_license"]["status"], "review_required")

    def test_external_scope_and_override(self):
        env = {"FIL_DISCLOSURE_SCOPE": "external",
               "FIL_DISCLOSURE_POSITION": "footer",
               "FIL_DISCLOSURE_TEXT": "对外版本披露"}
        with patch.dict(os.environ, env, clear=False):
            info = disclosure.payload()
        self.assertEqual(info["scope"], "external")
        self.assertEqual(info["position"], "footer")
        self.assertEqual(info["text"], "对外版本披露")

    def test_api_is_public_even_with_token_mode(self):
        env = {"FIL_ACCESS_CONTROL": "token", "FIL_API_TOKENS": "secret"}
        with patch.dict(os.environ, env, clear=False):
            client = TestClient(create_app())
            response = client.get("/api/disclosure")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text", response.json())


class ResponseMetaEnvelopeTests(unittest.TestCase):
    def _assert_complete_meta(self, response):
        meta = response.json()["meta"]
        for key in ("disclaimer", "data_as_of", "data_latency",
                    "model_limits", "data_source_license", "config_hash"):
            self.assertIn(key, meta)
        self.assertFalse(meta["model_limits"]["tradeable_signal"])
        self.assertEqual(meta["data_source_license"]["status"], "review_required")
        self.assertEqual(meta["data_latency"]["status"], "unknown")

    def test_route_validation_and_auth_errors_all_carry_meta(self):
        client = TestClient(create_app())
        self._assert_complete_meta(client.get("/api/v1/health"))
        self._assert_complete_meta(
            client.post("/api/v1/analyze", json={"code": "1100"}))
        with patch.dict(os.environ, {"FIL_ACCESS_CONTROL": "token",
                                     "FIL_API_TOKENS": "secret"}, clear=False):
            denied = TestClient(create_app()).get("/api/catalog")
        self.assertEqual(denied.status_code, 401)
        self._assert_complete_meta(denied)

    def test_non_api_json_is_not_enveloped(self):
        client = TestClient(create_app())
        body = client.get("/openapi.json").json()
        self.assertNotIn("meta", body)

    def test_raw_fixture_distribution_is_denied_by_default(self):
        env = {"FIL_DATA_LICENSE_APPROVED": "",
               "FIL_DATA_LICENSE_REVIEW_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            status = data_license.status()
            self.assertFalse(data_license.redistribution_allowed())
        self.assertEqual(status["status"], "review_required")
        self.assertFalse(status["raw_fixture_redistribution_allowed"])
        with patch.dict(os.environ, {"FIL_DATA_LICENSE_APPROVED": "1",
                                     "FIL_DATA_LICENSE_REVIEW_ID": "LEGAL-2026-01"}):
            self.assertTrue(data_license.redistribution_allowed())
            self.assertEqual(data_license.status()["review_id"], "LEGAL-2026-01")


if __name__ == "__main__":
    unittest.main()
