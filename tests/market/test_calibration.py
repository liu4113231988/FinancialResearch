from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research.research.calibration import calibrate_probabilities
from market_research.research.market_validation import evaluate_asset, main, run_plan, validate_plan
from tests.market.test_execution import bars


def fixture():
    frame=bars(100+np.tile([0.,1.],60))
    probability=pd.Series(np.tile([.8,.2],60),index=frame.index)
    start=str(frame.index[20].date()); holdout=str(frame.index[90].date())
    cutoff=str(frame.index[19].date())
    return frame,probability,start,holdout,cutoff


class CalibrationTests(unittest.TestCase):
    def test_chronological_fit_only_emits_holdout_and_keeps_missing_predictions(self):
        frame,p,start,holdout,cutoff=fixture(); p.iloc[-2]=np.nan
        out,meta=calibrate_probabilities(frame,p,1,start,holdout,cutoff)
        self.assertEqual(meta['status'],'fitted')
        self.assertTrue(out.loc[:cutoff].isna().all())
        self.assertTrue(out.loc[start:frame.index[89]].isna().all())
        self.assertTrue(np.isnan(out.iloc[-2]))
        self.assertTrue(out.dropna().between(0,1).all())
        self.assertGreater(meta['slope'],0)
        self.assertLess(meta['last_label_end'],holdout)
        self.assertEqual(meta['n'],69)
        self.assertEqual(meta['start'],start)

    def test_future_prices_and_future_probabilities_cannot_change_calibration_parameters(self):
        frame,p,start,holdout,cutoff=fixture()
        out,meta=calibrate_probabilities(frame,p,1,start,holdout,cutoff)
        changed=frame.copy(); changed.loc[holdout:,'close']*=100
        altered=p.copy(); altered.iloc[-1]=.95
        after,metadata=calibrate_probabilities(changed,altered,1,start,holdout,cutoff)
        self.assertEqual(meta,metadata)
        pd.testing.assert_series_equal(out.iloc[:-1],after.iloc[:-1])
        self.assertNotEqual(out.iloc[-1],after.iloc[-1])

    def test_targets_crossing_holdout_boundary_never_enter_calibration(self):
        frame,p,start,holdout,cutoff=fixture()
        _,meta=calibrate_probabilities(frame,p,5,start,holdout,cutoff)
        self.assertEqual(meta['n'],13)
        self.assertEqual(meta['last_label_end'],str(frame.index[85].date()))
        self.assertEqual(meta['status'],'insufficient')

    def test_sample_and_class_gates_do_not_fall_back_to_raw_probability(self):
        frame,p,start,holdout,cutoff=fixture()
        for data,horizon in ((frame,20),(bars(np.arange(1.,121.)),1)):
            out,meta=calibrate_probabilities(data,p,horizon,start,holdout,cutoff)
            self.assertEqual(meta['status'],'insufficient')
            self.assertTrue(out.isna().all())

    def test_constant_and_reversed_mapping_are_rejected(self):
        frame,p,start,holdout,cutoff=fixture()
        for probability,status in ((p*0+.5,'degenerate'),(1-p,'non_monotone')):
            out,meta=calibrate_probabilities(frame,probability,1,start,holdout,cutoff)
            self.assertEqual(meta['status'],status)
            self.assertTrue(out.isna().all())

    def test_probability_endpoints_are_finite_and_input_is_unchanged(self):
        frame,p,start,holdout,cutoff=fixture(); p=p.where(p>.5,0).where(p<.5,1)
        before=p.copy()
        out,meta=calibrate_probabilities(frame,p,1,start,holdout,cutoff)
        self.assertEqual(meta['status'],'fitted')
        self.assertTrue(np.isfinite(out.dropna()).all())
        pd.testing.assert_series_equal(p,before)

    def test_validation_rejects_overlapping_windows_dates_and_invalid_probabilities(self):
        frame,p,start,holdout,cutoff=fixture()
        for probability,args in ((p,(start,holdout,start)),(p.iloc[::-1],(start,holdout,cutoff)),
                                 (p*0+np.inf,(start,holdout,cutoff)),(p*0+1.1,(start,holdout,cutoff))):
            with self.subTest(args=args),self.assertRaises(ValueError):
                calibrate_probabilities(frame,probability,1,*args)

    def test_optional_plan_window_is_strict_and_rejects_rule_mode(self):
        plan=dict(start='2021-01-01',development_start='2022-01-01',calibration_start='2023-01-01',
                  holdout_start='2025-01-01',end='2026-10-30',assets=[dict(kind='index',code='sh000300')])
        validate_plan(plan)
        for date in ('2022-01-01','2025-01-01','2023-1-1'):
            with self.assertRaises(ValueError):
                validate_plan(plan|{'calibration_start':date})
        with self.assertRaises(ValueError):
            run_plan(plan,Path('.'),execution_only=True)

    def test_opt_in_freezes_before_calibration_and_records_all_five_variants(self):
        frame=bars(100+np.sin(np.arange(400)*.3))
        plan=dict(start=str(frame.index[0].date()),development_start=str(frame.index[40].date()),
                  calibration_start=str(frame.index[100].date()),holdout_start=str(frame.index[280].date()),
                  end=str(frame.index[-1].date()),assets=[dict(kind='index',code='sh000300')])
        probability=pd.Series(.5+.25*np.sin(np.arange(400)),index=frame.index)
        corrected=probability.where(probability.index>=pd.Timestamp(plan['holdout_start']))
        with patch('market_research.research.market_validation.read_bars',return_value=frame), \
             patch('market_research.research.market_validation.direction_forecast',return_value={'series':probability.tolist()}) as forecast, \
             patch('market_research.research.market_validation.calibrate_probabilities',return_value=(corrected,{'status':'fitted'})) as calibrate:
            out=run_plan(plan,Path('.'))
        cutoff=str((frame.index[100]-pd.Timedelta(days=1)).date())
        self.assertTrue(all(c.kwargs['training_end']==cutoff for c in forecast.call_args_list))
        self.assertEqual(calibrate.call_count,10)
        self.assertEqual(out['planned_comparisons'],26)
        self.assertEqual(out['planned_calibration_comparisons'],10)
        self.assertEqual(out['planned_execution_scenarios'],234)
        self.assertEqual(out['planned_exposure_controls'],156)
        self.assertEqual(len(out['assets'][0]['result']['calibration']['5']),5)
        self.assertFalse(out['calibration_complete'])  # 20-day holdout has fewer than 20 independent observations.
        rows=out['assets'][0]['result']['horizons']['5']['development']
        self.assertTrue(all(row['end']<plan['calibration_start'] for row in rows))

    def test_disabled_protocol_retains_original_training_cutoff_and_no_calibration(self):
        frame,p,start,holdout,cutoff=fixture()
        plan=dict(start=str(frame.index[0].date()),development_start=start,holdout_start=holdout,end=str(frame.index[-1].date()))
        with patch('market_research.research.market_validation.direction_forecast',return_value={'series':p.tolist()}) as forecast:
            result=evaluate_asset(frame,frame.close,plan,16)
        self.assertNotIn('calibration',result)
        expected=str((pd.Timestamp(holdout)-pd.Timedelta(days=1)).date())
        self.assertTrue(all(c.kwargs['training_end']==expected for c in forecast.call_args_list))

    def test_capital_constraints_are_passed_to_calibrated_execution(self):
        frame,p,start,holdout,cutoff=fixture()
        plan=dict(start=str(frame.index[0].date()),development_start=str(frame.index[5].date()),
                  calibration_start=start,holdout_start=holdout,end=str(frame.index[-1].date()))
        corrected=p.where(p.index>=pd.Timestamp(holdout))
        from market_research.market.capital import CapitalConfig
        with patch('market_research.research.market_validation.direction_forecast',return_value={'series':p.tolist()}), \
             patch('market_research.research.market_validation.calibrate_probabilities',return_value=(corrected,{'status':'fitted'})):
            out=evaluate_asset(frame,frame.close,plan,26,capital_config=CapitalConfig(initial_cash=10005))
        for row in out['calibration']['5']:
            execution=row['comparison']['execution']
            self.assertEqual(execution['account_mode'],'capital')
            self.assertEqual(execution['strategies']['candidate'][1]['config']['initial_cash'],10005)
            self.assertFalse(execution['strategies']['candidate'][1]['exposure_control']['matched'])

    def test_incomplete_calibration_is_saved_and_cli_returns_nonzero(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as directory:
            source=Path(directory)/'plan.json'; target=Path(directory)/'report.json'
            source.write_text('{}',encoding='utf-8')
            report={'complete':True,'calibration_complete':False}
            with patch('market_research.research.market_validation.run_plan',return_value=report),contextlib.redirect_stdout(io.StringIO()):
                code=main(['--manifest',str(source),'--output',str(target)])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),report)


if __name__=='__main__':
    unittest.main()
