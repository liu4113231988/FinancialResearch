# -*- coding: utf-8 -*-
"""抓取超时必须是线程/请求级（P2-37），不能改进程全局 socket 默认值。"""
import os
import socket
import threading
import unittest
from unittest.mock import patch

import pandas as pd

import market_research.data.http as http
from market_research.data import provenance
from market_research.errors import ProviderContractError


class HttpTimeoutTests(unittest.TestCase):
    def test_context_does_not_touch_global_socket_default(self):
        before = socket.getdefaulttimeout()
        with http.http_timeout(1.5):
            self.assertEqual(http._thread_timeout(), 1.5)
            self.assertEqual(socket.getdefaulttimeout(), before)
        self.assertIsNone(http._thread_timeout())
        self.assertEqual(socket.getdefaulttimeout(), before)

    def test_nested_context_restores_previous(self):
        with http.http_timeout(3):
            with http.http_timeout(1):
                self.assertEqual(http._thread_timeout(), 1)
            self.assertEqual(http._thread_timeout(), 3)
        self.assertIsNone(http._thread_timeout())

    def test_timeout_is_not_visible_to_other_threads(self):
        seen = []

        def probe():
            with http.http_timeout(2):
                seen.append(http._thread_timeout())

        with http.http_timeout(1):
            worker = threading.Thread(target=probe)
            worker.start()
            worker.join()
            self.assertEqual(http._thread_timeout(), 1)
        self.assertEqual(seen, [2])

    def test_requests_timeout_is_injected_only_when_absent(self):
        import requests
        self.assertTrue(getattr(requests.Session.request,
                                "_fil_timeout_patched", False))
        with http.http_timeout(2):
            self.assertEqual(http._apply_request_timeout({})["timeout"], 2)
            self.assertEqual(
                http._apply_request_timeout({"timeout": 5})["timeout"], 5)


class RetryAndProvenanceTests(unittest.TestCase):
    """P2-29：可恢复错误重试、编程错误立即失败、抓取留痕。"""

    def setUp(self):
        provenance.reset()
        self.env = patch.dict(os.environ, {"FIL_HTTP_MAX_RPS": "0"},
                              clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        provenance.reset()

    def test_retries_transient_then_records_success(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("temporary")
            return "ok"

        self.assertEqual(http._retry(flaky, tries=3, wait=0,
                                     source="fund_nav"), "ok")
        self.assertEqual(len(calls), 3)
        entry = provenance.snapshot()[0]
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["source"], "fund_nav")

    def test_programming_error_is_not_retried(self):
        calls = []

        def broken():
            calls.append(1)
            raise ValueError("bad field")

        with self.assertRaises(ValueError):
            http._retry(broken, tries=4, wait=0, source="index_sina")
        self.assertEqual(len(calls), 1)
        entry = provenance.snapshot()[0]
        self.assertFalse(entry["ok"])
        self.assertIn("bad field", entry["error"])

    def test_exhausted_retries_mark_degraded(self):
        def always_fail():
            raise TimeoutError("timeout")

        with self.assertRaises(TimeoutError):
            http._retry(always_fail, tries=2, wait=0, source="csindex_pe")
        entry = provenance.snapshot()[0]
        self.assertFalse(entry["ok"])
        self.assertTrue(entry["degraded"])

    def test_rate_limit_default_parsing(self):
        with patch.dict(os.environ, {"FIL_HTTP_MAX_RPS": "0"}, clear=False):
            http.rate_limit()          # 关闭时不应阻塞
            self.assertEqual(http._max_rps(), 0.0)
        with patch.dict(os.environ, {"FIL_HTTP_MAX_RPS": "abc"}, clear=False):
            self.assertEqual(http._max_rps(), 8.0)


class ContractValidationTests(unittest.TestCase):
    def test_missing_columns_raise_with_source(self):
        frame = pd.DataFrame({"日期": ["2024-01-01"], "收盘": [1.0]})
        with self.assertRaises(ProviderContractError) as ctx:
            provenance.require_columns(frame, ["date", "close"], "index_sina")
        self.assertIn("index_sina", str(ctx.exception))
        self.assertIn("stock_zh_index_daily", str(ctx.exception))

    def test_all_columns_present_passes(self):
        frame = pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]})
        provenance.require_columns(frame, ["date", "close"], "index_sina")


if __name__ == "__main__":
    unittest.main()
