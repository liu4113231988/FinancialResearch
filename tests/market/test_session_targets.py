import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research.market.targets import horizon_data
from market_research.market.technical import direction_forecast,compare_forecasts,analyze_technical
from market_research.research.calibration import calibrate_probabilities
from market_research.research.market_validation import paired_evidence,evaluate_asset
from tests.market.test_execution import bars
from tests.market.test_technical import sample


class SessionTargetTests(unittest.TestCase):
    def test_missing_target_session_is_not_replaced_by_next_quote(self):
        frame=bars([100,110,120,80,90,100,110,120]); calendar=frame.index
        close=frame.close.drop(calendar[2])
        observed,_,_=horizon_data(close,2)
        actual,positions,axis=horizon_data(close,2,calendar)
        self.assertAlmostEqual(observed.iloc[0],-.2)
        self.assertTrue(np.isnan(actual.iloc[0]))
        self.assertAlmostEqual(actual.loc[calendar[1]],80/110-1)
        self.assertAlmostEqual(actual.loc[calendar[3]],.25)
        np.testing.assert_array_equal(positions,[0,1,3,4,5,6,7])
        pd.testing.assert_index_equal(axis,calendar)

    def test_forecast_comparison_and_evidence_share_session_samples(self):
        frame=bars([100,110,120,80,90,100,110,120]); calendar=frame.index
        frame=frame.drop(calendar[2])
        a=[.5]*len(frame); b=[.75]*len(frame)
        comparison=compare_forecasts(frame,{'series':a},{'series':b},2,frame.index[0],calendar=calendar)
        evidence=paired_evidence(frame,a,b,2,32,calendar=calendar)
        self.assertEqual(comparison['start'],str(calendar[1].date()))
        self.assertEqual(comparison['n'],3)
        self.assertEqual(evidence['n'],3)
        self.assertAlmostEqual(comparison['candidate']['brier'],(.75**2+2*.25**2)/3)
        self.assertAlmostEqual(comparison['brier_improvement'],evidence['brier_improvement_uncertainty']['point_estimate'])
        self.assertEqual(evidence['horizon_basis'],'calendar_sessions')

    def test_training_maturity_uses_sessions_and_freezes_before_missing_cutoff_quote(self):
        full=sample(400); calendar=full.index
        frame=full.drop(calendar[[100,250,300]])
        features=pd.DataFrame({'x':1.},index=frame.index); cutoff=calendar[300]
        fitted=[]; positions={date:i for i,date in enumerate(calendar)}
        quotes=set(frame.index)
        class Spy:
            def fit(self,x,y):
                endings=[positions[d]+5 for d in x.index]
                assert all(calendar[e] in quotes and e<=300 for e in endings)
                self.end=max(endings); self.p=float(y.mean()); fitted.append((len(x),self.end)); return self
            def predict_proba(self,x):
                assert self.end<=min(positions[x.index[0]],300)
                return np.array([[1-self.p,self.p]])
        with patch('market_research.market.technical.make_pipeline',side_effect=lambda *args:Spy()):
            out=direction_forecast(frame,features,5,frame.index[0],str(cutoff.date()),calendar=calendar)
            changed=frame.copy(); changed.loc[changed.index>cutoff,'close']*=100
            second=direction_forecast(changed,features,5,frame.index[0],str(cutoff.date()),calendar=calendar)
        self.assertTrue(out['ok'])
        self.assertEqual(fitted[-1],(291,299))
        np.testing.assert_allclose(out['series'],second['series'],equal_nan=True)

    def test_refit_clock_advances_during_missing_sessions(self):
        full=sample(400); calendar=full.index; frame=full.drop(calendar[300:331])
        features=pd.DataFrame({'x':1.},index=frame.index); predictions={}; fits=[]
        class Spy:
            def fit(self,x,y):
                fits.append(x.index[-1]); self.number=len(fits); return self
            def predict_proba(self,x):
                predictions[x.index[0]]=self.number
                return np.array([[.4,.6]])
        with patch('market_research.market.technical.make_pipeline',side_effect=lambda *args:Spy()):
            direction_forecast(frame,features,5,frame.index[0],calendar=calendar)
        self.assertGreater(predictions[calendar[331]],predictions[calendar[299]])

    def test_training_window_counts_valid_labels_before_applying_limit(self):
        full=sample(100); calendar=full.index; frame=full.drop(calendar[80])
        features=pd.DataFrame({'x':1.},index=frame.index); training=[]
        class Spy:
            def fit(self,x,y):
                training.append(x.index); return self
            def predict_proba(self,x): return np.array([[.4,.6]])
        with patch('market_research.market.technical.MIN_TRAIN',20),patch('market_research.market.technical.MAX_TRAIN',30), \
             patch('market_research.market.technical.make_pipeline',side_effect=lambda *args:Spy()):
            out=direction_forecast(frame,features,5,frame.index[0],str(calendar[90].date()),calendar=calendar)
        self.assertEqual(out['training_rows'],30)
        self.assertNotIn(calendar[75],training[-1])

    def test_calibration_excludes_missing_endpoint_and_labels_crossing_holdout(self):
        full=bars(100+np.tile([0.,1.],60)); calendar=full.index
        frame=full.drop(calendar[[40,89]])
        probability=pd.Series(np.tile([.8,.2],60),index=calendar).reindex(frame.index)
        out,meta=calibrate_probabilities(frame,probability,1,str(calendar[20].date()),str(calendar[90].date()),
            str(calendar[19].date()),calendar=calendar)
        self.assertEqual(meta['status'],'fitted')
        self.assertEqual(meta['n'],66)
        self.assertEqual(meta['last_label_end'],str(calendar[88].date()))
        self.assertTrue(out.loc[:calendar[88]].isna().all())
        self.assertEqual(meta['horizon_basis'],'calendar_sessions')

    def test_session_forecasts_remain_prefix_stable(self):
        full=sample(420); calendar=full.index; frame=full.drop(calendar[[70,330]])
        features=pd.DataFrame({'x':np.sin(np.arange(len(frame)))},index=frame.index)
        end=frame.index[380]
        class Spy:
            def fit(self,x,y): self.p=float(y.mean()); return self
            def predict_proba(self,x): return np.array([[1-self.p,self.p]])
        with patch('market_research.market.technical.make_pipeline',side_effect=lambda *args:Spy()):
            a=direction_forecast(frame,features,5,frame.index[0],calendar=calendar)
            b=direction_forecast(frame.loc[:end],features.loc[:end],5,frame.index[0],calendar=calendar[calendar<=end])
        np.testing.assert_allclose(a['series'][:len(b['series'])],b['series'],equal_nan=True)

    def test_complete_quotes_have_identical_targets_with_or_without_calendar(self):
        frame=sample(100)
        a,p,axis=horizon_data(frame.close,20)
        b,q,sessions=horizon_data(frame.close,20,frame.index)
        pd.testing.assert_series_equal(a,b)
        np.testing.assert_array_equal(p,q)
        pd.testing.assert_index_equal(axis,sessions)

    def test_offline_training_comparison_and_calibration_receive_same_calendar(self):
        frame=sample(400); calendar=frame.index; frame=frame.drop(calendar[80])
        plan=dict(start=str(calendar[0].date()),development_start=str(calendar[40].date()),
            calibration_start=str(calendar[100].date()),holdout_start=str(calendar[280].date()),end=str(calendar[-1].date()))
        probability=[.6]*len(frame)
        with patch('market_research.research.market_validation.direction_forecast',return_value={'series':probability}) as forecast, \
             patch('market_research.research.market_validation.calibrate_probabilities',wraps=calibrate_probabilities) as calibrate:
            result=evaluate_asset(frame,frame.close,plan,26,calendar=calendar)
        self.assertTrue(all(c.kwargs['calendar'].equals(calendar) for c in forecast.call_args_list+calibrate.call_args_list))
        self.assertEqual(result['horizon_basis'],'calendar_sessions')
        for horizon,stages in result['horizons'].items():
            for rows in stages.values():
                for row in rows:
                    self.assertEqual(row['horizon_basis'],row['evidence']['horizon_basis'])
            self.assertTrue(all(r['horizon_basis']=='calendar_sessions' for r in result['calibration'][horizon]))

    def test_page_reports_calendar_basis_and_warmup_fallback(self):
        frame=sample(400); start=frame.index[300]
        for calendar,basis in ((frame.index,'calendar_sessions'),(frame.index[1:],'observed_bars')):
            out=analyze_technical(frame,display_start=start,calendar=calendar)
            self.assertTrue(all(r['horizon_basis']==basis for r in out['forecasts']))
            self.assertTrue(any(('会话计数' if basis=='calendar_sessions' else '有效日线') in n for n in out['notices']))

    def test_invalid_horizon_and_calendar_are_rejected(self):
        frame=sample(20)
        for horizon,calendar in ((True,None),(0,None),(5,frame.index[:-1]),(5,frame.index[::-1]),
                                  (5,frame.index.append(frame.index[-1:])),(5,frame.index+pd.Timedelta(hours=1))):
            with self.subTest(horizon=horizon),self.assertRaises(ValueError):
                horizon_data(frame.close,horizon,calendar)


if __name__=='__main__':
    unittest.main()
