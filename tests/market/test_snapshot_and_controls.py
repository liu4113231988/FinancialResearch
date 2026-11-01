import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.market.test_execution import bars
from market_research.market.execution import simulate_execution, matched_exposure_control, execution_diagnostics
from market_research.market.analysis import analyze_market
from market_research.research.market_snapshot import prepare_snapshot
from market_research.data.providers.calendar import bundled_calendar


class ExposureControlTests(unittest.TestCase):
    def test_control_matches_mean_exposure_not_initial_allocation(self):
        frame=bars([100,100,200,400])
        active=simulate_execution(frame,pd.Series([np.nan,np.nan,1,np.nan],index=frame.index),0,0)
        passive=simulate_execution(frame,pd.Series(1.,index=frame.index),0,0)
        control=matched_exposure_control(active,passive)
        self.assertTrue(control['matched'])
        self.assertTrue(control['ex_post'])
        self.assertAlmostEqual(control['average_exposure'],active['average_exposure'],places=11)
        self.assertNotAlmostEqual(control['initial_allocation'],active['average_exposure'],places=3)
        self.assertAlmostEqual(control['excess_net_return'],active['net_return']-control['net_return'])

    def test_zero_exposure_has_zero_control_costs_and_no_fake_edge(self):
        frame=bars([100]*5)
        out=execution_diagnostics(frame,pd.Series(np.nan,index=frame.index))
        for row in out['strategies']['rules']:
            control=row['exposure_control']
            self.assertEqual(control['initial_allocation'],0)
            self.assertEqual(control['net_return'],0)
            self.assertEqual(control['fees_initial_capital'],0)
            self.assertEqual(control['excess_net_return'],0)
            self.assertNotIn('exposure',row)

    def test_identical_strategy_and_passive_have_zero_excess_after_costs(self):
        frame=bars([100,90,110,120])
        passive=simulate_execution(frame,pd.Series(1.,index=frame.index),10,5)
        control=matched_exposure_control(passive,passive)
        self.assertAlmostEqual(control['initial_allocation'],1,places=10)
        self.assertAlmostEqual(control['excess_net_return'],0,places=10)
        self.assertAlmostEqual(control['fees_initial_capital'],passive['fees_initial_capital'],places=10)

    def test_unreachable_exposure_and_different_dates_are_not_fabricated(self):
        frame=bars([100]*3)
        passive=simulate_execution(frame,pd.Series(1.,index=frame.index),0,0)
        self.assertFalse(matched_exposure_control(passive|{'average_exposure':1.},passive)['matched'])
        with self.assertRaises(ValueError): matched_exposure_control(passive|{'dates':[]},passive)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.frame=bars([100]*100)
        self.plan=dict(start=str(self.frame.index[0].date()),development_start=str(self.frame.index[20].date()),
                       holdout_start=str(self.frame.index[70].date()),end=str(self.frame.index[-1].date()),
                       assets=[dict(kind='stock',code='sh600519')])

    def test_local_snapshot_calendar_and_existing_directory_protection(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as d:
            root=Path(d); target=root/'snapshot'
            with patch('market_research.research.market_snapshot.read_bars',return_value=self.frame), \
                 patch('market_research.research.market_snapshot.bundled_calendar',return_value=(self.frame.index,{'source':'test'})), \
                 patch('market_research.research.market_snapshot.load_bars') as fetch:
                receipt=prepare_snapshot(self.plan,root,target,with_calendar=True)
                self.assertTrue(receipt['complete']); fetch.assert_not_called()
                saved=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['assets'][0]['calendar_csv'],'calendar.csv')
                self.assertTrue((target/saved['assets'][0]['csv']).is_file())
                self.assertNotIn('csv',self.plan['assets'][0])
                before=(target/'manifest.json').read_bytes()
                with self.assertRaises(FileExistsError): prepare_snapshot(self.plan,root,target)
                self.assertEqual((target/'manifest.json').read_bytes(),before)

    def test_only_missing_asset_is_fetched_without_registering_it(self):
        def read(asset,*args):
            if asset['kind']=='stock': raise ValueError('missing')
            return self.frame
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as d, \
             patch('market_research.research.market_snapshot.read_bars',side_effect=read), \
             patch('market_research.research.market_snapshot.load_bars',return_value=self.frame) as fetch:
            receipt=prepare_snapshot(self.plan,Path(d),Path(d)/'snapshot',fetch_missing=True)
            self.assertTrue(receipt['complete'])
            self.assertEqual(fetch.call_count,1)
            self.assertTrue(fetch.call_args.kwargs['_fetch_only'])
            self.assertEqual(receipt['files'][0]['source'],'fetched_for_validation')

    def test_stale_or_duplicate_snapshot_is_not_reported_complete(self):
        for invalid in (self.frame.iloc[:-1],pd.concat([self.frame,self.frame.iloc[[-1]]])):
            with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as d, \
                 patch('market_research.research.market_snapshot.read_bars',return_value=invalid), \
                 patch('market_research.research.market_snapshot.bundled_calendar',return_value=(self.frame.index,{'source':'test'})):
                receipt=prepare_snapshot(self.plan,Path(d),Path(d)/'snapshot',with_calendar=True)
                self.assertFalse(receipt['complete'])
                manifest=json.loads((Path(d)/'snapshot/manifest.json').read_text(encoding='utf-8'))
                self.assertTrue(manifest['assets'][0]['csv'].startswith('unavailable-'))

    def test_explicit_calendar_is_copied_instead_of_referencing_mutable_source(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as d:
            root=Path(d); pd.DataFrame({'date':self.frame.index}).to_csv(root/'custom.csv',index=False)
            plan=self.plan|{'assets':[self.plan['assets'][0]|{'calendar_csv':'custom.csv'}]}
            with patch('market_research.research.market_snapshot.read_bars',return_value=self.frame):
                receipt=prepare_snapshot(plan,root,root/'snapshot')
            saved=json.loads((root/'snapshot/manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(receipt['complete'])
            copied=saved['assets'][0]['calendar_csv']
            self.assertNotEqual(copied,'custom.csv')
            self.assertTrue((root/'snapshot'/copied).exists())
            self.assertEqual(len(receipt['explicit_calendars']),1)

    def test_vendor_calendar_rejects_uncovered_future(self):
        with self.assertRaisesRegex(ValueError,'未覆盖'):
            bundled_calendar('2021-01-01','2099-12-31')


class LiveCalendarTests(unittest.TestCase):
    def test_live_analysis_uses_vendor_calendar_and_preserves_missing_session(self):
        frame=bars([100]*100); partial=frame.drop(frame.index[10])
        with patch('market_research.market.analysis.load_bars',return_value=partial), \
             patch('market_research.tasks.sync.cached',return_value=None), \
             patch('market_research.data.providers.calendar.bundled_calendar',return_value=(frame.index,{'source':'test','sha256':'test'})):
            result=analyze_market('index','sh000300','2025-01-01','2025-06-01')
        self.assertTrue(result['quality']['calendar_verified'])
        self.assertEqual(result['quality']['calendar_missing_count'],1)
        row=result['technical']['execution']['strategies']['rules'][0]
        self.assertTrue(row['calendar_verified'])
        self.assertEqual(row['missing_sessions'],[str(frame.index[10].date())])

    def test_invalid_vendor_dates_degrade_to_unverified_execution(self):
        frame=bars([100]*100)
        with patch('market_research.market.analysis.load_bars',return_value=frame), \
             patch('market_research.tasks.sync.cached',return_value=None), \
             patch('market_research.data.providers.calendar.bundled_calendar',return_value=(frame.index[:-1],{'source':'test'})):
            result=analyze_market('index','sh000300','2025-01-01','2025-06-01')
        self.assertFalse(result['quality']['calendar_verified'])
        self.assertTrue(any('日历之外' in text for text in result['warnings']))


if __name__=='__main__': unittest.main()
