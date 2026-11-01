# -*- coding: utf-8 -*-
"""基金池退化路径回归（不联网）。

对应 2026-11-01 全量复查发现的基金池退化路径问题：
空候选应返回带列空表、池文件损坏要留日志而不是静默用 14 只。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import market_research.config as C
import market_research.funds.universe as U


class EnrichEmptyTest(unittest.TestCase):
    def test_empty_codes_returns_typed_empty_frame(self):
        out = U.enrich([], verbose=False)
        self.assertTrue(out.empty)
        self.assertIn("基金代码", out.columns)
        self.assertIn("成立时间", out.columns)


class CorruptPoolTest(unittest.TestCase):
    def test_corrupt_pool_warns_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            fp = Path(d) / "fund_pool.csv"
            fp.write_bytes(b"\x80\x81\x82")          # 非法 UTF-8，pd.read_csv 必抛
            with mock.patch.object(C, "DATA_DIR", Path(d)):
                with self.assertLogs("market_research.funds.universe",
                                     level="WARNING") as logs:
                    out = U.load_pool()
            self.assertTrue(out.empty)
            self.assertIn("基金池文件不可用", "\n".join(logs.output))

    def test_missing_code_column_warns(self):
        with tempfile.TemporaryDirectory() as d:
            fp = Path(d) / "fund_pool.csv"
            fp.write_text("foo,bar\n1,2", encoding="utf-8")
            with mock.patch.object(C, "DATA_DIR", Path(d)):
                with self.assertLogs("market_research.funds.universe",
                                     level="WARNING") as logs:
                    out = U.load_pool()
            self.assertTrue(out.empty)
            self.assertIn("基金代码", "\n".join(logs.output))

    def test_pool_codes_falls_back_to_extended(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "DATA_DIR", Path(d)):
                self.assertEqual(U.pool_codes(), list(C.EXTENDED_FUNDS.keys()))


if __name__ == "__main__":
    unittest.main()
