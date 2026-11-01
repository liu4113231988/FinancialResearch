import json
import unittest

import numpy as np
import pandas as pd

from market_research.market.capital import CapitalConfig
from market_research.market.execution import compare_probability_execution
from market_research.market.uncertainty import block_intervals, execution_intervals
from market_research.research.market_validation import paired_evidence
from tests.market.test_execution import bars


def account(path, closed=6, initial=None):
    result={'dates':list(range(len(path))), 'closed_trades':closed}
    if initial is None:
        result['equity']=path
    else:
        result.update(equity_cash=np.asarray(path)*initial, config={'initial_cash':initial})
    return result


class UncertaintyTests(unittest.TestCase):
    def test_paired_mean_is_reproducible_and_includes_zero_for_symmetric_data(self):
        values=np.linspace(-.1,.1,80)
        out=block_intervals(values)
        self.assertEqual(out,block_intervals(values))
        self.assertAlmostEqual(out['point_estimate'],0)
        for row in out['intervals']:
            self.assertEqual(row['status'],'estimated')
            self.assertLess(row['lower'],0)
            self.assertGreater(row['upper'],0)

    def test_sample_and_block_gates_have_no_intervals(self):
        for n in (0,19):
            out=block_intervals(np.arange(n))
            self.assertTrue(all(r['status']=='insufficient' and r['lower'] is None for r in out['intervals']))
        out=block_intervals(np.arange(60),blocks=(5,10,20),minimum=60)
        self.assertEqual([r['status'] for r in out['intervals']],['estimated','estimated','insufficient'])

    def test_constant_apparent_advantage_has_no_spurious_certainty(self):
        out=block_intervals([.1]*40)
        self.assertAlmostEqual(out['point_estimate'],.1)
        for row in out['intervals']:
            self.assertEqual(row['status'],'degenerate')
            self.assertIsNone(row['lower'])
            self.assertIsNone(row['upper'])

    def test_invalid_samples_are_rejected_without_deleting_observations(self):
        for values,kwargs in (([np.nan],{}),([np.inf],{}),([[1,2]],{}),
                              ([[0,-1]],{'statistic':'return_gap'}),
                              ([1],{'blocks':(0,)}),([1],{'replicates':True})):
            with self.subTest(values=values,kwargs=kwargs), self.assertRaises(ValueError):
                block_intervals(values,**kwargs)
        out=block_intervals(np.full((80,2),1e100),statistic='return_gap')
        self.assertIsNone(out['point_estimate'])
        self.assertTrue(all(r['status']=='unstable' for r in out['intervals']))
        json.dumps(out,allow_nan=False)

    def test_identical_paired_returns_cancel_without_mutating_capital_path(self):
        path=np.cumprod(1+np.sin(np.arange(80))*.01)
        capital=account(path,initial=100000)
        before=capital['equity_cash'].copy()
        out=execution_intervals(account(path),capital)
        self.assertEqual(out['status'],'degenerate')
        self.assertAlmostEqual(out['point_estimate'],0)
        np.testing.assert_array_equal(capital['equity_cash'],before)

    def test_few_closed_trades_suppress_return_interval_despite_long_history(self):
        out=execution_intervals(account(np.ones(500),closed=0),account(np.linspace(1,2,500)))
        self.assertEqual(out['status'],'sparse_trades')
        self.assertEqual(out['point_estimate'],1)
        self.assertIsNone(out['bootstrap'])

    def test_return_gap_includes_initial_cost_and_uses_same_capital_scale(self):
        a=np.cumprod(1+np.sin(np.arange(80))*.01)*.99
        b=np.cumprod(1+np.cos(np.arange(80))*.01)*.98
        out=execution_intervals(account(a),account(b))
        capital=execution_intervals(account(a,initial=100000),account(b,initial=200000))
        self.assertEqual(out['status'],'estimated')
        self.assertAlmostEqual(out['point_estimate'],b[-1]-a[-1])
        self.assertAlmostEqual(out['bootstrap']['point_estimate'],b[-1]-a[-1])
        for left,right in zip(out['bootstrap']['intervals'],capital['bootstrap']['intervals']):
            self.assertAlmostEqual(left['lower'],right['lower'])
            self.assertAlmostEqual(left['upper'],right['upper'])

    def test_dates_equity_shape_and_initial_cash_are_validated(self):
        good=account([1,1])
        for bad in (account([1]),account([1,0]),account([1,np.nan]),
                    good|{'dates':[2,3]},good|{'equity':[[1,1]]},
                    account([1,1],initial=0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                execution_intervals(good,bad)

    def test_offline_opt_in_preserves_account_results_for_both_modes(self):
        frame=bars(10+np.sin(np.arange(100))*.5)
        probability=pd.Series(np.tile([.8,.2],50),index=frame.index)
        for cfg in (None,CapitalConfig()):
            plain=compare_probability_execution(frame,probability,probability,capital_config=cfg)
            out=compare_probability_execution(frame,probability,probability,capital_config=cfg,uncertainty=True)
            self.assertNotIn('return_uncertainty',plain)
            self.assertEqual(out['strategies'],plain['strategies'])
            self.assertEqual(len(out['return_uncertainty']),3)
            self.assertTrue(all(r['status']=='degenerate' for r in out['return_uncertainty']))

    def test_brier_uses_same_mature_spaced_pair_and_positive_improvement_sign(self):
        frame=bars(np.arange(1,127))
        base=pd.Series(.5,index=frame.index); candidate=pd.Series(.75,index=frame.index)
        candidate.iloc[0]=np.nan
        out=paired_evidence(frame,base,candidate,5,32)
        diagnostic=out['brier_improvement_uncertainty']
        self.assertEqual(out['n'],24)
        self.assertEqual(diagnostic['n'],24)
        self.assertAlmostEqual(diagnostic['point_estimate'],.1875)
        short=paired_evidence(frame.iloc[:10],[.5]*10,[.75]*10,5,32)
        self.assertIsNone(short['p_bonferroni_approx'])
        self.assertTrue(all(r['status']=='insufficient' for r in short['brier_improvement_uncertainty']['intervals']))


if __name__ == '__main__':
    unittest.main()
