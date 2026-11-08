import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from market_research.market.execution import simulate_execution, rule_targets, execution_diagnostics
from market_research.research.market_validation import run_plan
from market_research.serialization import _jsafe


def bars(opens, closes=None):
    opens=np.array(opens,dtype=float); closes=opens.copy() if closes is None else np.array(closes,dtype=float)
    return pd.DataFrame({'open': opens, 'close': closes, 'high': np.maximum(opens,closes)*1.1,
                         'low': np.minimum(opens,closes)*.9, 'volume':100.}, index=pd.bdate_range('2025-01-01',periods=len(opens)))


class ExecutionTests(unittest.TestCase):
    def test_signal_cannot_capture_gap_before_next_open(self):
        frame=bars([100,200],[100,210])
        out=simulate_execution(frame,pd.Series([1.,np.nan],index=frame.index),0,0)
        self.assertAlmostEqual(out['net_return'],.05)
        self.assertEqual(out['trades'][0]['signal_date'],'2025-01-01')
        self.assertEqual(out['trades'][0]['date'],'2025-01-02')
        self.assertTrue(out['open_position'])
        self.assertIsNone(out['win_rate'])

    def test_round_trip_fee_slippage_and_first_day_drawdown(self):
        frame=bars([100]*3)
        signal=pd.Series([1.,0.,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,10,5)
        expected=(1-.0005)/(1+.0005)*(1-.001)/(1+.001)
        self.assertAlmostEqual(out['net_return'],expected-1)
        self.assertAlmostEqual(out['max_drawdown'],expected-1)
        self.assertEqual(out['closed_trades'],1)
        self.assertEqual(out['win_rate'],0)
        self.assertGreater(out['fees_initial_capital'],0)
        self.assertFalse(out['open_position'])

    def test_restricted_order_expires_without_stale_execution(self):
        frame=bars([100]*4); frame['limit_up']=[110,100,110,110]
        signal=pd.Series([1.,np.nan,np.nan,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,0,0)
        self.assertEqual(out['fills'],0)
        self.assertEqual(out['blocked_orders'][0]['reason'],'upper_limit')
        signal.iloc[1]=1
        retry=simulate_execution(frame,signal,0,0)
        self.assertEqual(retry['trades'][0]['date'],str(frame.index[2].date()))

    def test_zero_volume_and_lower_limit_prevent_fills(self):
        frame=bars([100]*5); frame['volume']=[100,0,100,100,100]
        frame['limit_down']=[90,90,90,100,90]
        signal=pd.Series([1,1,0,np.nan,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,0,0)
        self.assertEqual([b['reason'] for b in out['blocked_orders']],['zero_volume','lower_limit'])
        self.assertEqual(out['fills'],1)
        self.assertTrue(out['open_position'])

    def test_calendar_gap_is_not_filled_with_an_executable_price(self):
        frame=bars([100,100,200]); calendar=frame.index
        partial=frame.drop(frame.index[1])
        out=simulate_execution(partial,pd.Series([1,np.nan],index=partial.index),0,0,calendar)
        self.assertEqual(out['net_return'],0)
        self.assertEqual(out['fills'],0)
        self.assertEqual(out['blocked_orders'][0]['reason'],'missing_session')
        self.assertEqual(out['missing_sessions'],[str(calendar[1].date())])
        self.assertTrue(out['calendar_verified'])

    def test_known_closed_session_cannot_originate_an_order(self):
        frame=bars([100]*3); frame['is_session']=[True,False,True]
        signal=pd.Series([np.nan,1,np.nan],index=frame.index)
        self.assertEqual(simulate_execution(frame,signal,0,0)['fills'],0)

    def test_explicit_permissions_and_invalid_metadata(self):
        frame=bars([100]*3); frame['buy_allowed']=['true','false','true']
        signal=pd.Series([1,np.nan,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,0,0)
        self.assertEqual(out['blocked_orders'][0]['reason'],'explicit_restriction')
        frame.loc[frame.index[0],'buy_allowed']='maybe'
        with self.assertRaises(ValueError): simulate_execution(frame,signal)
        for fee in (-1,np.inf,True):
            with self.assertRaises(ValueError): simulate_execution(bars([100]*3),signal,fee)

    def test_execution_prefix_unchanged_by_future_prices(self):
        frame=bars([100,101,102,200,50])
        signal=pd.Series([1,np.nan,0,1,0],index=frame.index)
        full=simulate_execution(frame,signal,0,0)
        prior=simulate_execution(frame.iloc[:3],signal.iloc[:3],0,0)
        self.assertEqual(full['equity'][:3],prior['equity'])

    def test_intraday_extrema_never_select_open_fill_and_costs_are_monotone(self):
        frame=bars([100]*3)
        frame['high']=frame['low']=100.
        signal=pd.Series([1,0,np.nan],index=frame.index)
        outputs=[simulate_execution(frame,signal,f,s) for f,s in ((0,0),(10,5),(30,10))]
        self.assertEqual([x['fills'] for x in outputs],[2,2,2])
        self.assertGreater(outputs[0]['net_return'],outputs[1]['net_return'])
        self.assertGreater(outputs[1]['net_return'],outputs[2]['net_return'])

    def test_rule_exit_wins_conflict_and_no_forced_terminal_liquidation(self):
        frame=bars([100]*3)
        flags=pd.DataFrame({'entry':[True,True,True],'exit':[False,True,False]},index=frame.index)
        self.assertEqual(rule_targets(flags).tolist(),[1,0,1])
        out=simulate_execution(frame,pd.Series([np.nan,1,0],index=frame.index),0,0)
        self.assertEqual(out['fills'],1)
        self.assertEqual(out['closed_trades'],0)
        self.assertTrue(out['open_position'])

    def test_flat_round_trip_does_not_depress_win_rate(self):
        # 一笔盈利 + 一笔平局：旧口径 0.5，修复后应为 1.0 且平局单列。
        frame=bars([100,110,120,120,120])
        signal=pd.Series([1,0,1,0,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,0,0)
        self.assertEqual(out['closed_trades'],2)
        self.assertEqual(out['flat_trades'],1)
        self.assertEqual(out['win_rate'],1.0)
        json.dumps(_jsafe(out),allow_nan=False)

    def test_constraint_data_missing_is_explicit_and_t1_is_declared(self):
        frame=bars([100]*3)
        signal=pd.Series([1.,0.,np.nan],index=frame.index)
        out=simulate_execution(frame,signal,0,0)
        self.assertFalse(out['constraint_data_available'])
        self.assertEqual(out['t1_locked_count'],0)
        self.assertTrue(any('T+1' in text for text in out['assumptions']))
        loaded=bars([100]*3); loaded['limit_up']=[110.,110.,110.]
        with_limit=simulate_execution(loaded,signal,0,0)
        self.assertTrue(with_limit['constraint_data_available'])
        self.assertEqual(with_limit['constraint_coverage']['limit_up'],3)

    def test_summaries_keep_constraints_and_are_json_safe(self):
        frame=bars([100]*10)
        out=execution_diagnostics(frame,pd.Series(np.nan,index=frame.index))
        self.assertEqual(len(out['strategies']['rules']),3)
        for row in out['strategies']['rules']:
            self.assertEqual(row['net_return'],0)
            self.assertFalse(row['calendar_verified'])
            self.assertEqual(row['constraint_coverage']['limit_up'],0)
            self.assertNotIn('equity',row)
        json.dumps(_jsafe(out),allow_nan=False)

    def test_offline_execution_does_not_load_benchmark_or_train_models(self):
        frame=bars([100]*100)
        plan=dict(start=str(frame.index[0].date()), development_start=str(frame.index[20].date()),
                  holdout_start=str(frame.index[70].date()),end=str(frame.index[-1].date()),
                  assets=[dict(kind='stock',code='sh600519')])
        with patch('market_research.research.market_validation.read_bars',return_value=frame) as load, \
             patch('market_research.research.market_validation.direction_forecast',side_effect=AssertionError('Unexpected training')):
            out=run_plan(plan,Path('.'),execution_only=True)
        self.assertTrue(out['complete'])
        self.assertEqual(load.call_count,1)
        self.assertIn('holdout',out['assets'][0]['result'])


if __name__=='__main__': unittest.main()
