from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import market_research.research.market_validation as validation
from market_research.research.market_snapshot import prepare_snapshot
from tests.market.test_execution import bars
from tests.market.test_validation_coverage import comparisons, execution


class ValidationCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path.cwd()/'output')
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.frame=bars([100]*300)
        self.frame.rename_axis('date').to_csv(self.root/'quotes.csv')
        pd.DataFrame({'date':self.frame.index}).to_csv(self.root/'calendar.csv',index=False)
        self.plan=dict(start=str(self.frame.index[0].date()),development_start=str(self.frame.index[40].date()),
                       holdout_start=str(self.frame.index[170].date()),end=str(self.frame.index[-1].date()),
                       assets=[dict(kind='stock',code='sh600519',csv='quotes.csv',benchmark_csv='quotes.csv',calendar_csv='calendar.csv')])

    def test_truncated_asset_is_rejected_before_execution(self):
        self.frame.iloc[:-1].rename_axis('date').to_csv(self.root/'quotes.csv')
        with patch.object(validation,'evaluate_execution_asset',return_value=execution()) as evaluate:
            report=validation.run_plan(self.plan,self.root,execution_only=True)
        self.assertFalse(report['complete'])
        self.assertEqual(report['assets'][0]['status'],'unavailable')
        self.assertIn('末会话',report['assets'][0]['error'])
        evaluate.assert_not_called()

    def test_truncated_benchmark_is_rejected_before_training(self):
        self.frame.iloc[:-1].rename_axis('date').to_csv(self.root/'benchmark.csv')
        self.plan['assets'][0]['benchmark_csv']='benchmark.csv'
        with patch.object(validation,'evaluate_asset',return_value=comparisons()) as evaluate:
            report=validation.run_plan(self.plan,self.root)
        self.assertFalse(report['complete'])
        self.assertIn('基准',report['assets'][0]['error'])
        evaluate.assert_not_called()

    def test_calendar_must_be_valid_and_bracket_requested_dates(self):
        dates=self.frame.index
        for calendar in (dates[:-1],dates[1:],dates[::-1],dates.append(dates[-1:]),
                         dates.insert(0,pd.NaT),dates+pd.Timedelta(hours=1),dates.tz_localize('UTC')):
            with self.subTest(calendar=str(calendar[:1])),self.assertRaises(ValueError):
                validation.check_calendar_coverage(self.frame,calendar,self.plan)

    def test_weekend_end_uses_last_calendar_session(self):
        frame=self.frame.loc[:'2026-02-20']
        self.assertEqual(frame.index[-1].weekday(),4)
        plan=self.plan|{'end':'2026-02-22'}
        calendar=frame.index.append(pd.DatetimeIndex(['2026-02-23']))
        self.assertEqual(validation.check_calendar_coverage(frame,calendar,plan),'2026-02-20')

    def test_calendar_exclusion_during_warmup_cannot_be_hidden(self):
        pd.DataFrame({'date':self.frame.index.delete(10)}).to_csv(self.root/'calendar.csv',index=False)
        with patch.object(validation,'evaluate_asset',return_value=comparisons()) as evaluate:
            report=validation.run_plan(self.plan,self.root)
        self.assertFalse(report['complete'])
        self.assertIn('日历外',report['assets'][0]['error'])
        evaluate.assert_not_called()

    def test_interior_quote_gap_keeps_existing_execution_diagnostic(self):
        date=self.frame.index[80]
        self.frame.drop(date).rename_axis('date').to_csv(self.root/'quotes.csv')
        report=validation.run_plan(self.plan,self.root,execution_only=True)
        self.assertTrue(report['complete'])
        self.assertIn(str(date.date()),report['assets'][0]['result']['development']['strategies']['rules'][0]['missing_sessions'])
        self.assertEqual(report['assets'][0]['expected_end'],self.plan['end'])

    def test_no_calendar_is_explicitly_unverified(self):
        asset=self.plan['assets'][0]; asset.pop('calendar_csv')
        report=validation.run_plan(self.plan,self.root,execution_only=True)
        self.assertTrue(report['complete'])
        self.assertIsNone(report['assets'][0]['calendar_sha256'])
        self.assertIsNone(report['assets'][0]['expected_end'])

    def test_interior_benchmark_gap_is_disclosed_without_filling(self):
        date=self.frame.index[80]
        self.frame.drop(date).rename_axis('date').to_csv(self.root/'benchmark.csv')
        self.plan['assets'][0]['benchmark_csv']='benchmark.csv'
        with patch.object(validation,'evaluate_asset',return_value=comparisons()):
            report=validation.run_plan(self.plan,self.root)
        self.assertTrue(report['complete'])
        self.assertEqual(report['assets'][0]['benchmark_missing_dates'],[str(date.date())])

    def test_snapshot_checks_explicit_calendar_and_reused_benchmark(self):
        self.frame.iloc[:-1].rename_axis('date').to_csv(self.root/'benchmark.csv')
        self.plan['assets'][0]['benchmark_csv']='benchmark.csv'
        receipt=prepare_snapshot(self.plan,self.root,self.root/'snapshot')
        self.assertFalse(receipt['complete'])
        self.assertIn('末会话',receipt['errors'][0]['error'])
        self.plan['assets'][0]['benchmark_csv']='quotes.csv'
        self.frame.iloc[:-1].rename_axis('date').to_csv(self.root/'quotes.csv')
        receipt=prepare_snapshot(self.plan,self.root,self.root/'snapshot-tail')
        self.assertFalse(receipt['complete'])

    def test_snapshot_rechecks_shared_benchmark_against_each_explicit_calendar(self):
        self.frame.drop(self.frame.index[80]).rename_axis('date').to_csv(self.root/'other.csv')
        pd.DataFrame({'date':self.frame.index.delete(80)}).to_csv(self.root/'other-calendar.csv',index=False)
        self.plan['assets'].append(dict(kind='stock',code='sz000001',csv='other.csv',
            benchmark_csv='quotes.csv',calendar_csv='other-calendar.csv'))
        receipt=prepare_snapshot(self.plan,self.root,self.root/'snapshot-reuse')
        self.assertFalse(receipt['complete'])
        self.assertEqual(len(receipt['errors']),1)
        self.assertEqual(receipt['errors'][0]['code'],'sz000001')
        self.assertIn('日历外',receipt['errors'][0]['error'])

    def test_mixed_calendar_manifest_does_not_inherit_a_missing_vendor_file(self):
        self.plan['assets'].append(dict(kind='index',code='sh000300',csv='quotes.csv',benchmark_csv='quotes.csv'))
        target=self.root/'mixed-calendar'
        receipt=prepare_snapshot(self.plan,self.root,target)
        self.assertTrue(receipt['complete'])
        import json
        saved=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('calendar_csv',saved['assets'][1])
        self.assertFalse((target/'calendar.csv').exists())


if __name__=='__main__':
    unittest.main()
