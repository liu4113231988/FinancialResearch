# -*- coding: utf-8 -*-
"""P2-36：Web 声明接口的默认口径与可配置升级位。"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from market_research.api.app import create_app
from market_research import disclosure


class DisclosureApiTests(unittest.TestCase):
    def test_default_internal_wording(self):
        with patch.dict(os.environ, {"FIL_DISCLOSURE_SCOPE": "internal"},
                        clear=False):
            info = disclosure.payload()
        self.assertEqual(info["scope"], "internal")
        self.assertEqual(info["position"], "top")
        self.assertIn("公开抓取", info["text"])
        self.assertIn("不构成投资建议", info["text"])

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


if __name__ == "__main__":
    unittest.main()
