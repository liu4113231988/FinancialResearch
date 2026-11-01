import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.market.test_execution import bars
from market_research.market.execution import probability_targets, compare_probability_execution
from market_research.research.market_validation import run_plan
from market_research.serialization import _jsafe


class ModelExecutionTests(unittest.TestCase):
    def test_fixed_hysteresis_boundaries_and_validation(self):
        probability=pd.Series([.599,.6,.401,.4,.5,np.nan])
        np.testing.assert_allclose(probability_targets(probability),[np.nan,1,np.nan,0,np.nan,np.nan],equal_nan=True)
        pd.testing.assert_series_equal(probability_targets(probability.iloc[:4]),probability_targets(probability).iloc[:4])
        for invalid in (-.1,1.1,np.inf):
            with self.assertRaises(ValueError): probability_targets(pd.Series([invalid]))
        with self.assertRaises(ValueError): probability_targets(pd.Series([.5,.6],index=[0,0]))

    def test_common_start_prevents_earning_before_candidate_exists(self):
        frame=bars([100,200,300],[100,210,330])
        base=pd.Series([.8,.8,.8],index=frame.index)
        candidate=pd.Series([np.nan,.9,.9],index=frame.index)
        result=compare_probability_execution(frame,base,candidate)
        self.assertEqual(result['start'],str(frame.index[1].date()))
        self.assertEqual(result['n_common_predictions'],2)
        self.assertFalse(result['sample_sufficient'])
        for key in ('base','candidate','buy_hold'):
            self.assertAlmostEqual(result['strategies'][key][0]['net_return'],.1)
            self.assertEqual(result['strategies'][key][0]['fills'],1)

    def test_interior_missing_prediction_suppresses_both_new_orders(self):
        frame=bars([100]*4)
        base=pd.Series([.8,.2,.8,.8],index=frame.index)
        candidate=pd.Series([.8,np.nan,.8,.8],index=frame.index)
        result=compare_probability_execution(frame,base,candidate)
        self.assertEqual(result['missing_prediction_sessions'],1)
        for key in ('base','candidate'):
            self.assertEqual(result['strategies'][key][0]['fills'],1)
            self.assertTrue(result['strategies'][key][0]['open_position'])

    def test_missing_neutral_and_last_day_only_are_distinguished(self):
        frame=bars([100]*5)
        neutral=pd.Series(.5,index=frame.index)
        out=compare_probability_execution(frame,neutral,neutral)
        self.assertEqual(out['status'],'evaluated')
        self.assertEqual(out['strategies']['candidate'][0]['fills'],0)
        self.assertIsNone(out['strategies']['candidate'][0]['win_rate'])
        missing=pd.Series(np.nan,index=frame.index)
        out=compare_probability_execution(frame,neutral,missing)
        self.assertEqual(out['status'],'insufficient')
        self.assertEqual(out['strategies'],{})
        missing.iloc[-1]=.9
        self.assertEqual(compare_probability_execution(frame,neutral,missing)['status'],'insufficient')

    def test_calendar_and_terminal_marking_apply_to_both_models(self):
        frame=bars([100,100,200,300])
        probability=pd.Series([.8,.8,np.nan,np.nan],index=frame.index)
        out=compare_probability_execution(frame,probability,probability)
        self.assertEqual(out['end'],str(frame.index[-1].date()))
        self.assertAlmostEqual(out['strategies']['base'][0]['net_return'],2.)
        partial=frame.drop(frame.index[1])
        out=compare_probability_execution(partial,probability.reindex(partial.index),probability.reindex(partial.index),calendar=frame.index)
        for key in ('base','candidate'):
            row=out['strategies'][key][0]
            self.assertEqual(row['fills'],0)
            self.assertEqual(row['blocked_count'],1)
            self.assertEqual(len(row['missing_sessions']),1)
        json.dumps(_jsafe(out),allow_nan=False)

    def test_direction_report_passes_calendar_to_model_execution(self):
        frame=bars([100]*100)
        plan=dict(start=str(frame.index[0].date()),development_start=str(frame.index[20].date()),
                  holdout_start=str(frame.index[70].date()),end=str(frame.index[-1].date()),
                  assets=[dict(kind='stock',code='sh600519',calendar_csv='calendar.csv')])
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'output') as directory:
            pd.DataFrame({'date':frame.index}).to_csv(Path(directory)/'calendar.csv',index=False)
            with patch('market_research.research.market_validation.read_bars',return_value=frame), \
                 patch('market_research.research.market_validation.evaluate_asset',return_value={'horizons':{'5':{'holdout':[{'n':1}]}}}) as evaluate:
                out=run_plan(plan,Path(directory))
            self.assertEqual(evaluate.call_args.kwargs['calendar'].tolist(),frame.index.tolist())
            self.assertIsNotNone(out['assets'][0]['calendar_sha256'])
            self.assertEqual(out['planned_execution_scenarios'],144)


if __name__=='__main__': unittest.main()
