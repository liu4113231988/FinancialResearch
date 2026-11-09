"""predictions CLI：migrate 的 dry-run / apply / 幂等与 summary 分支。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import market_research.config as C
from market_research.predictions import commands, ledger


def _legacy_row(code, as_of, horizon, action='buy'):
    """构造口径修正前的 5/20 规则行（新代码已不再产生）。"""
    return {
        "id": ledger._identity('stock', code, as_of, 'rule', horizon),
        "created_at": 1.0, "as_of": as_of, "asset_kind": 'stock',
        "asset_code": code, "asset_name": None, "source": "rule",
        "direction": "up" if action == "buy" else "down",
        "horizon": horizon, "probability": None, "rule_score": 60.0,
        "rule_action": action, "price_basis": "qfq", "regime": "strong",
        "features": None, "model_version": "v0", "params": None,
        "data_stamp": None,
    }


class MigrateCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(C, 'DATA_DIR', Path(self.tmp.name))
        self.patch.start()
        ledger._insert([
            _legacy_row('sh600519', '2026-10-30', 5),
            _legacy_row('sh600519', '2026-10-30', 20),
            _legacy_row('sh600000', '2026-10-29', 5, 'sell'),
            _legacy_row('sh600000', '2026-10-29', 20, 'sell'),
        ])

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = commands.main(argv, prog='predictions')
        return code, json.loads(out.getvalue())

    def test_dry_run_reports_without_writing(self):
        code, result = self._run(['migrate'])
        self.assertEqual(code, 0)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['legacy_rows'], 4)
        self.assertEqual(result['groups'], 2)
        self.assertEqual(result['would_delete'], 2)
        self.assertEqual(ledger.summary()['overall']['legacy_rows'], 4)

    def test_apply_is_idempotent(self):
        code, first = self._run(['migrate', '--apply'])
        self.assertEqual(code, 0)
        self.assertEqual(first['kept'], 2)
        self.assertEqual(first['deleted'], 2)
        overall = ledger.summary()['overall']
        self.assertEqual(overall['legacy_rows'], 0)
        self.assertEqual(overall['rule_events'], 2)
        self.assertEqual(overall['total'], 2)
        rows = ledger._records()
        self.assertEqual({row['horizon'] for row in rows},
                         {ledger.RULE_HORIZON})
        for row in rows:
            params = json.loads(row['params'])
            self.assertEqual(params['horizon_semantics'], 'none')
            self.assertEqual(params['migrated_from'], 'legacy_rule_5_20')
        code, second = self._run(['migrate', '--apply'])
        self.assertEqual(code, 0)
        self.assertEqual(second['deleted'], 0)
        self.assertEqual(second['legacy_rows'], 0)

    def test_summary_branch_still_works(self):
        code, result = self._run(['summary', '--group-by', 'source'])
        self.assertEqual(code, 0)
        self.assertEqual(result['group_by'], 'source')
        self.assertIn('overall', result)

    def test_feature_bucket_groups_are_accepted_by_cli(self):
        for field in ('vol_bucket', 'trend_bucket'):
            code, result = self._run(['summary', '--group-by', field])
            self.assertEqual(code, 0)
            self.assertEqual(result['group_by'], field)
            self.assertIn('groups', result)
        self.assertIn('vol_bucket', ledger.GROUP_FIELDS)
        self.assertIn('trend_bucket', ledger.GROUP_FIELDS)

    def test_unknown_group_is_rejected(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                commands.main(['summary', '--group-by', 'unknown_bucket'],
                              prog='predictions')
        self.assertEqual(error.exception.code, 2)
        with self.assertRaisesRegex(ValueError, 'group_by'):
            ledger.summary(group_by='unknown_bucket')


if __name__ == '__main__':
    unittest.main()
