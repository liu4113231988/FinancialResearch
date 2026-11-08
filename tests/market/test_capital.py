import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.market.test_execution import bars
from market_research.market.capital import CapitalConfig, parse_capital_config, simulate_capital, capital_diagnostics
from market_research.research.market_validation import validate_plan, run_plan
from market_research.serialization import _jsafe


class CapitalTests(unittest.TestCase):
    def test_lots_and_minimum_commission_conserve_cash(self):
        frame=bars([10]*3)
        signal=pd.Series([1,0,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,lot_size=100,minimum_commission=5)
        out=simulate_capital(frame,signal,cfg,0,0)
        self.assertEqual([t['quantity'] for t in out['trades']],[1000,1000])
        self.assertEqual(out['fees_cash'],10)
        self.assertEqual(out['ending_cash'],9995)
        self.assertEqual(out['ending_units'],0)
        self.assertEqual(out['closed_trades'],1)
        self.assertAlmostEqual(out['closed_cycles'][0]['pnl'],-10)
        self.assertTrue(all(c>=0 for c in out['cash_path']))

    def test_rate_or_floor_whichever_is_greater_and_no_overdraft(self):
        frame=bars([10]*3); signal=pd.Series([1,0,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,minimum_commission=5)
        out=simulate_capital(frame,signal,cfg,10,0)
        self.assertEqual(out['trades'][0]['quantity'],900)
        self.assertAlmostEqual(out['fees_cash'],18)
        self.assertAlmostEqual(out['ending_cash'],10005-18)
        for trade in out['trades']:
            self.assertEqual(trade['quantity']%100,0)
            self.assertGreaterEqual(trade['cash_after'],0)

    def test_cash_too_small_for_one_lot_is_not_fractionally_invested(self):
        frame=bars([100]*3); signal=pd.Series([1,1,np.nan],index=frame.index)
        out=simulate_capital(frame,signal,CapitalConfig(initial_cash=1000),0,0)
        self.assertEqual(out['fills'],0)
        self.assertEqual(out['ending_cash'],1000)
        self.assertEqual(out['blocked_orders'][0]['reason'],'insufficient_cash_for_lot')
        self.assertIsNone(out['win_rate'])

    def test_capacity_uses_previous_volume_not_current_day_volume(self):
        frame=bars([10]*3); frame['volume']=[1000,1000000,1000000]
        signal=pd.Series([1,np.nan,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,max_participation=.1,volume_unit='shares')
        out=simulate_capital(frame,signal,cfg,0,0)
        changed=frame.copy(); changed.loc[changed.index[1],'volume']=100
        other=simulate_capital(changed,signal,cfg,0,0)
        self.assertEqual(out['trades'][0]['quantity'],100)
        self.assertEqual(other['trades'][0]['quantity'],100)
        self.assertEqual(out['trades'][0]['previous_volume'],1000)
        self.assertEqual(out['partial_fills'],1)

    def test_partial_exit_closes_cycle_only_when_flat(self):
        frame=bars([10]*5); frame['volume']=1000
        signal=pd.Series([1,1,0,0,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,max_participation=.1,volume_unit='shares')
        out=simulate_capital(frame,signal,cfg,0,0)
        self.assertEqual(out['units_path'],[0,100,200,100,0])
        self.assertEqual(out['closed_trades'],1)
        self.assertEqual(out['fees_cash'],20)
        self.assertEqual(out['ending_cash'],9985)
        self.assertEqual(out['closed_cycles'][0]['pnl'],-20)
        before=simulate_capital(frame.iloc[:4],signal.iloc[:4],cfg,0,0)
        self.assertEqual(before['closed_trades'],0)
        self.assertTrue(before['open_position'])

    def test_unknown_capacity_cannot_silently_allow_a_fill(self):
        frame=bars([10]*3).drop(columns='volume')
        signal=pd.Series([1,np.nan,np.nan],index=frame.index)
        cfg=CapitalConfig(max_participation=.1,volume_unit='shares')
        out=simulate_capital(frame,signal,cfg,0,0)
        self.assertEqual(out['fills'],0)
        self.assertEqual(out['blocked_orders'][0]['reason'],'unknown_previous_volume')

    def test_capacity_cannot_borrow_volume_from_a_closed_session(self):
        frame=bars([10]*3); frame['is_session']=[False,True,True]
        # A closed session's signal is also suppressed, so no order is invented.
        out=simulate_capital(frame,pd.Series([1,np.nan,np.nan],index=frame.index),CapitalConfig(max_participation=.1,volume_unit='shares'),0,0)
        self.assertEqual(out['fills'],0)

    def test_invalid_profile_and_non_raw_or_index_quotes_are_rejected(self):
        for raw in ({'lot_size':True},{'lot_size':1.5},{'initial_cash':0},{'minimum_commission':np.inf},
                    {'max_participation':0},{'max_participation':.1},{'typo':1}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_capital_config(raw)
        plan=dict(start='2021-01-01',development_start='2023-01-01',holdout_start='2025-01-01',end='2026-10-30')
        for asset in ({'kind':'stock','code':'sh600519','adjust':'qfq','capital':{}},
                      {'kind':'index','code':'sh000300','adjust':'','capital':{}}):
            with self.assertRaises(ValueError):validate_plan(plan|{'assets':[asset]})

    def test_flat_cycles_do_not_depress_win_rate(self):
        frame=bars([10]*5)
        signal=pd.Series([1,0,1,0,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=100000,lot_size=100,minimum_commission=0)
        out=simulate_capital(frame,signal,cfg,0,0)
        self.assertEqual(out['closed_trades'],2)
        self.assertEqual(out['flat_cycles'],2)
        self.assertIsNone(out['win_rate'])

    def test_signal_semantics_separates_pulse_from_persistent(self):
        frame=bars([10]*4); frame['volume']=1000
        cfg=CapitalConfig(initial_cash=10005,max_participation=.1,volume_unit='shares')
        pulse=simulate_capital(frame,pd.Series([1,np.nan,np.nan,np.nan],index=frame.index),cfg,0,0)
        persistent=simulate_capital(frame,pd.Series([1,1,1,np.nan],index=frame.index),cfg,0,0)
        self.assertEqual(pulse['signal_semantics'],'pulse_events')
        self.assertEqual(persistent['signal_semantics'],'persistent_target')
        self.assertIn('不会跨日自动重试补仓',' '.join(pulse['assumptions']))
        self.assertIn('每个交易日都会用剩余现金重新尝试买入',' '.join(persistent['assumptions']))
        json.dumps(_jsafe(persistent),allow_nan=False)

    def test_partial_fill_retries_every_full_target_session(self):
        # 目标为满仓时，每个交易日都会用剩余现金继续补仓 —— 不是"只在新目标
        # 出现时补一次"。假设文本必须与这个行为一致（P1-10 剩余项）。
        frame=bars([10]*4); frame['volume']=1000
        signal=pd.Series([1,1,1,np.nan],index=frame.index)
        cfg=CapitalConfig(initial_cash=10005,max_participation=.1,volume_unit='shares')
        out=simulate_capital(frame,signal,cfg,0,0)
        self.assertEqual([t['quantity'] for t in out['trades']],[100,100,100])
        self.assertEqual(out['units_path'],[0,100,200,300])
        self.assertIn('每个交易日都会用剩余现金重新尝试买入',' '.join(out['assumptions']))

    def test_calendar_gap_and_slippage_preserve_execution_boundary(self):
        frame=bars([10,10,20]); signal=pd.Series([1,np.nan,np.nan],index=frame.index)
        partial=frame.drop(frame.index[1])
        cfg=CapitalConfig(initial_cash=10005)
        out=simulate_capital(partial,signal.reindex(partial.index),cfg,10,5,frame.index)
        self.assertEqual(out['fills'],0)
        self.assertEqual(out['ending_cash'],10005)
        self.assertEqual(out['blocked_orders'][0]['reason'],'missing_session')
        full=simulate_capital(frame,signal,cfg,10,5)
        self.assertAlmostEqual(full['ending_equity'],full['ending_cash']+full['ending_units']*20)
        self.assertGreater(full['slippage_cash'],0)

    def test_summaries_do_not_reuse_invalid_linear_exposure_control(self):
        frame=bars([10]*100)
        flags=pd.DataFrame({'entry':True,'exit':False},index=frame.index)
        out=capital_diagnostics(frame,flags,CapitalConfig())
        for row in out['strategies']['rules']:
            self.assertFalse(row['exposure_control']['matched'])
            self.assertNotIn('cash_path',row)
        json.dumps(_jsafe(out),allow_nan=False)

    def test_cli_capital_mode_runs_without_training_or_benchmark(self):
        frame=bars([10]*100)
        plan=dict(start=str(frame.index[0].date()),development_start=str(frame.index[20].date()),
                  holdout_start=str(frame.index[70].date()),end=str(frame.index[-1].date()),
                  assets=[dict(kind='stock',code='sh600519',adjust='',capital={})])
        with patch('market_research.research.market_validation.read_bars',return_value=frame) as load, \
             patch('market_research.research.market_validation.direction_forecast',side_effect=AssertionError('Unexpected training')):
            result=run_plan(plan,Path('.'),execution_only=True)
        self.assertTrue(result['complete'])
        self.assertEqual(load.call_count,1)
        self.assertEqual(result['assets'][0]['result']['holdout']['account_mode'],'capital')
        self.assertEqual(result['planned_exposure_controls'],0)


if __name__=='__main__':unittest.main()
