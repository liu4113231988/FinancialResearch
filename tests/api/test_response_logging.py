import logging
import os
import unittest
from unittest.mock import patch

from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from market_research.api.app import RESPONSE_LOG, create_app


class ResponseLoggingTests(unittest.TestCase):
    def client(self, selection="", limit=""):
        env = {"FIL_API_RESPONSE_LOG": selection,
               "FIL_API_RESPONSE_LOG_MAX_BYTES": limit,
               "FIL_API_LEGACY": "1", "FIL_ACCESS_CONTROL": "off"}
        with patch.dict(os.environ, env):
            app = create_app()

        @app.get("/api/v1/test-text")
        def plain_text():
            return PlainTextResponse("not JSON")

        @app.get("/api/v1/test-error")
        def error():
            raise RuntimeError("internal details")

        return TestClient(app, raise_server_exceptions=False)

    def test_disabled_by_default(self):
        with self.assertNoLogs(RESPONSE_LOG, level=logging.INFO):
            self.assertEqual(self.client().get("/api/v1/health").status_code, 200)

    def test_path_selection_and_final_response(self):
        client = self.client("/api/v1/health, /api/v1/result/*")
        with patch("market_research.api.routes.tasks.services.JOBS.get", return_value=None), \
             self.assertLogs(RESPONSE_LOG, level=logging.INFO) as logged:
            response = client.get("/api/v1/health?probe=private-query")
            result = client.get("/api/v1/result/nonexistent-job")
            client.get("/api/health")
            client.get("/api/v1/disclosure")
        self.assertEqual(len(logged.records), 2)
        for record, actual in zip(logged.records, (response, result)):
            message = record.getMessage()
            self.assertIn(f"status={actual.status_code}", message)
            self.assertTrue(message.endswith("body=" + actual.text))
        self.assertNotIn("private-query", logged.records[0].getMessage())

    def test_all_includes_json_errors_and_excludes_non_api_and_plain_text(self):
        client = self.client("all")
        with self.assertLogs("market_research.api.app", level=logging.ERROR), \
             self.assertLogs(RESPONSE_LOG, level=logging.INFO) as logged:
            client.get("/api/health")
            invalid = client.post("/api/v1/analyze", json={"code": "bad"})
            error = client.get("/api/v1/test-error")
            client.get("/openapi.json")
            client.get("/api/v1/test-text")
        self.assertEqual(len(logged.records), 3)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(error.status_code, 500)
        self.assertTrue(logged.records[-1].getMessage().endswith("body=" + error.text))
        self.assertNotIn("internal details", logged.records[-1].getMessage())

    def test_truncation_leaves_response_unchanged_and_zero_allows_full_body(self):
        for limit in ("31", "0"):
            with self.subTest(limit=limit), self.assertLogs(RESPONSE_LOG, level=logging.INFO) as logged:
                response = self.client("all", limit).get("/api/v1/disclosure")
            message = logged.records[0].getMessage()
            self.assertIn(f"bytes={len(response.content)}", message)
            if limit == "0":
                self.assertTrue(message.endswith("body=" + response.text))
            else:
                prefix = response.content[:31].decode("utf-8", errors="ignore")
                self.assertTrue(message.endswith("body=" + prefix + " [truncated]"))
