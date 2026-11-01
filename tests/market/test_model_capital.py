from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.market.test_execution import bars
from market_research.market.capital import CapitalConfig
from market_research.market.execution import compare_probability_execution
from market_research.research.market_validation import run_plan


class ModelCapitalTests(unittest.TestCase):
    def test_valid_predictions_with_no_affordable_lot_are_not_missing_data(self):
        frame=bars([100]*5); probability=pd.Series(.8,index=frame.index)
        out=compare_probability_execution(frame,probability,probability,capital_config=CapitalConfig(initial_cash=1000))
        self.assertEqual(out['status'],'evaluated')
        self.assertEqual(out['account_mode'],'capital')
        for key in ('base','candidate','buy_hold'):
            row=out['strategies'][key][1]
            self.assertEqual(row['fills'],0)
            self.assertEqual(row['ending_cash'],1000)
            self.assertEqual(row['ending_units'],0)
            self.assertGreater(row['blocked_count'],0)
            self.assertGreater(row['blocked_reasons']['insufficient_cash_for_lot'],0)
            self.assertFalse(row['exposure_control']['matched'])

    def test_partial_fills_and_commission_are_shared_by_paired_models(self):
        frame=bars([10]*5); frame['volume']=1000
        probability=pd.Series([.8,.8,.2,.2,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,max_participation=.1,volume_unit='shares')
        out=compare_probability_execution(frame,probability,probability,capital_config=cfg)
        for key in ('base','candidate'):
            row=out['strategies'][key][0]
            self.assertEqual(row['ending_cash'],9985)
            self.assertEqual(row['ending_units'],0)
            self.assertEqual(row['closed_trades'],1)
            self.assertEqual(row['fees_cash'],20)
            self.assertEqual(row['partial_fills'],3)
            self.assertNotIn('cash_path',row)
            self.assertNotIn('closed_cycles',row)

    def test_missing_prediction_masks_both_accounts_orders(self):
        frame=bars([10]*4)
        base=pd.Series([.8,.2,.8,.8],index=frame.index)
        candidate=pd.Series([.8,np.nan,.8,.8],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005)
        out=compare_probability_execution(frame,base,candidate,capital_config=cfg)
        self.assertEqual(out['missing_prediction_sessions'],1)
        for key in ('base','candidate'):
            self.assertEqual(out['strategies'][key][0]['fills'],1)
            self.assertTrue(out['strategies'][key][0]['open_position'])

    def test_identical_actions_use_identical_conditions_for_all_three_accounts(self):
        frame=bars([10]*5); base=pd.Series(.8,index=frame.index); candidate=base+.1
        out=compare_probability_execution(frame,base,candidate,capital_config=CapitalConfig(initial_cash=10005))
        for i in range(3):
            original=out['strategies']['base'][i]
            for key in ('candidate','buy_hold'):
                other=out['strategies'][key][i]
                for field in ('net_return','fees_cash','ending_cash','ending_units','fills'):
                    self.assertEqual(original[field],other[field])

    def test_calendar_gap_blocks_both_accounts_without_fake_fills(self):
        frame=bars([10]*4); partial=frame.drop(frame.index[1])
        probability=pd.Series([.8,np.nan,np.nan],index=partial.index)
        out=compare_probability_execution(partial,probability,probability,calendar=frame.index,capital_config=CapitalConfig())
        for key in ('base','candidate'):
            row=out['strategies'][key][1]
            self.assertTrue(row['calendar_verified'])
            self.assertEqual(row['fills'],0)
            self.assertEqual(len(row['missing_sessions']),1)

    def test_normal_direction_runner_passes_capital_config_and_frozen_cutoff(self):
        frame=bars([10]*200)
        plan=dict(start=str(frame.index[0].date()),development_start=str(frame.index[40].date()),
                  holdout_start=str(frame.index[120].date()),end=str(frame.index[-1].date()),
                  assets=[dict(kind='stock',code='sh600519',adjust='',capital={'initial_cash':10005})])
        with patch('market_research.research.market_validation.read_bars',return_value=frame), \
             patch('market_research.research.market_validation.direction_forecast',return_value={'series':[.8]*len(frame)}) as forecast:
            out=run_plan(plan,Path('.'))
        self.assertTrue(out['complete'])
        self.assertEqual(out['planned_exposure_controls'],0)
        cutoff=str((frame.index[120]-pd.Timedelta(days=1)).date())
        self.assertTrue(all(call.kwargs['training_end']==cutoff for call in forecast.call_args_list))
        asset=out['assets'][0]['result']
        self.assertEqual(asset['account_mode'],'capital')
        for stages in asset['horizons'].values():
            for stage in ('development','holdout'):
                for comparison in stages[stage]:
                    execution=comparison['execution']
                    self.assertEqual(execution['account_mode'],'capital')
                    self.assertEqual(execution['strategies']['base'][0]['config']['initial_cash'],10005)


if __name__=='__main__':unittest.main()
