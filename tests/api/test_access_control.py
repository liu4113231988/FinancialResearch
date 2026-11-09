# -*- coding: utf-8 -*-
"""P2-24：默认单用户无认证；开启令牌模式后未授权必须被拒绝。"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from market_research.api.app import create_app
from market_research import security


class AccessControlTests(unittest.TestCase):
    def test_default_mode_is_single_user(self):
        with patch.dict(os.environ, {"FIL_ACCESS_CONTROL": "off"}, clear=False):
            client = TestClient(create_app())
            response = client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)

    def test_token_mode_rejects_missing_or_wrong_token(self):
        env = {"FIL_ACCESS_CONTROL": "token", "FIL_API_TOKENS": "teamA:secret"}
        with patch.dict(os.environ, env, clear=False):
            client = TestClient(create_app())
            missing = client.get("/api/catalog")
            wrong = client.get("/api/catalog",
                               headers={"Authorization": "Bearer nope"})
            good = client.get("/api/catalog",
                              headers={"Authorization": "Bearer secret"})
            health = client.get("/api/health")
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(good.status_code, 200)
        self.assertEqual(health.status_code, 200)   # 探针不要求业务授权

    def test_cors_preflight_allows_configured_auth_headers(self):
        env = {"FIL_ACCESS_CONTROL": "token", "FIL_API_TOKENS": "teamA:secret",
               "FIL_CORS_ORIGINS": "https://research.example"}
        with patch.dict(os.environ, env, clear=False):
            client = TestClient(create_app())
            for header, value in (("authorization", "Bearer secret"),
                                  ("x-api-key", "secret")):
                preflight = client.options("/api/catalog", headers={
                    "Origin": "https://research.example",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": header,
                })
                self.assertEqual(preflight.status_code, 200, header)
                allowed = preflight.headers["access-control-allow-headers"].lower()
                self.assertIn(header, allowed)
            actual = client.get("/api/catalog", headers={
                "Origin": "https://research.example",
                "Authorization": "Bearer secret",
            })
            self.assertEqual(actual.status_code, 200)
            self.assertEqual(actual.headers["access-control-allow-origin"],
                             "https://research.example")
            rejected = client.options("/api/catalog", headers={
                "Origin": "https://other.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            })
            self.assertEqual(rejected.status_code, 400)

    def test_identity_carries_tenant_and_scopes(self):
        with patch.dict(os.environ, {"FIL_API_TOKENS": "teamA:secret"},
                        clear=False):
            identity = security.identity_for_token("secret")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.tenant, "teamA")
        self.assertTrue(identity.authenticated)
        self.assertIn("read", identity.scopes)

    def test_local_identity_is_anonymous(self):
        identity = security.current_identity()
        self.assertFalse(identity.authenticated)
        self.assertEqual(identity.tenant, "default")


if __name__ == "__main__":
    unittest.main()
