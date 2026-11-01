from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from market_research.market.execution import FEE_SCENARIOS
from market_research.research.market_validation import VARIANTS, run_plan
from tests.market.test_execution import bars


def plan():
    return dict(start='2021-01-01',development_start='2022-01-01',holdout_start='2025-01-01',
                end='2026-10-30',assets=[dict(kind='stock',code='sh600519')])


def comparisons():
    return {'horizons':{str(h):{stage:[{'variant':v,'n':20,'ok':True} for v in VARIANTS[1:]]
                               for stage in ('development','holdout')} for h in (5,20)}}


def execution():
    return {stage:{'strategies':{key:[{'fee_bps':fee,'slippage_bps':slip,'n_sessions':50,'fills':0}
                                    for fee,slip in FEE_SCENARIOS] for key in ('rules','buy_hold')}}
            for stage in ('development','holdout')}


class ValidationCoverageTests(unittest.TestCase):
    def run_result(self,result,*,calibrated=False,execution_only=False):
        manifest=plan()
        if calibrated:
            manifest['calibration_start']='2023-01-01'
        frame=bars([100]*100)
        function='evaluate_execution_asset' if execution_only else 'evaluate_asset'
        with patch('market_research.research.market_validation.read_bars',return_value=frame), \
             patch('market_research.research.market_validation.'+function,return_value=result):
            return run_plan(manifest,Path('.'),execution_only=execution_only)

    def test_one_available_holdout_comparison_is_not_a_complete_asset(self):
        result={'horizons':{'5':{'holdout':[{'variant':'volatility','n':20,'ok':True}]}}}
        report=self.run_result(result)
        self.assertFalse(report['complete'])
        asset=report['assets'][0]
        self.assertEqual(asset['status'],'partial')
        self.assertEqual(asset['coverage']['planned'],16)
        self.assertEqual(asset['coverage']['sufficient'],1)
        self.assertEqual(len(asset['coverage']['incomplete']),15)

    def test_all_planned_comparisons_and_sample_gate_are_required(self):
        report=self.run_result(comparisons())
        self.assertTrue(report['complete'])
        self.assertEqual(report['coverage'],{'unit':'paired_comparisons','planned':16,'sufficient':16,'incomplete':0})
        short=comparisons(); short['horizons']['20']['development'][0].update(n=19,ok=True)
        report=self.run_result(short)
        self.assertFalse(report['complete'])
        gap=report['assets'][0]['coverage']['incomplete'][0]
        self.assertEqual(gap['reason'],'insufficient_samples')
        self.assertEqual(gap['n'],19)

    def test_complete_holdout_cannot_hide_missing_development(self):
        result=comparisons()
        for stages in result['horizons'].values():
            stages.pop('development')
        report=self.run_result(result)
        self.assertFalse(report['complete'])
        self.assertEqual(report['coverage']['sufficient'],8)
        self.assertTrue(all(g['stage']=='development' for g in report['assets'][0]['coverage']['incomplete']))

    def test_duplicate_variant_cannot_replace_a_missing_variant(self):
        result=comparisons(); rows=result['horizons']['5']['holdout']
        rows[1]=deepcopy(rows[0])
        report=self.run_result(result)
        self.assertFalse(report['complete'])
        reasons={r['reason'] for r in report['assets'][0]['coverage']['incomplete']}
        self.assertEqual(reasons,{'duplicate_comparison','missing_comparison'})

    def test_empty_or_partial_calibration_is_not_vacuously_complete(self):
        result=comparisons(); result['calibration']={}
        report=self.run_result(result,calibrated=True)
        self.assertTrue(report['complete'])
        self.assertFalse(report['calibration_complete'])
        self.assertEqual(report['calibration_coverage']['planned'],10)
        self.assertEqual(report['calibration_coverage']['sufficient'],0)
        result['calibration']={str(h):[{'variant':v,'status':'fitted','comparison':{'n':20,'ok':True}}
                                     for v in VARIANTS] for h in (5,20)}
        self.assertTrue(self.run_result(result,calibrated=True)['calibration_complete'])
        result['calibration']['20'][0]={'variant':'base','status':'non_monotone','comparison':None}
        report=self.run_result(result,calibrated=True)
        self.assertFalse(report['calibration_complete'])
        self.assertEqual(report['assets'][0]['calibration_coverage']['incomplete'][0]['reason'],'non_monotone')

    def test_unavailable_asset_retains_all_planned_items(self):
        with patch('market_research.research.market_validation.read_bars',side_effect=ValueError('offline')):
            report=run_plan(plan(),Path('.'))
        self.assertFalse(report['complete'])
        self.assertEqual(report['assets'][0]['status'],'unavailable')
        self.assertEqual(report['coverage']['planned'],16)
        self.assertEqual(report['coverage']['sufficient'],0)
        self.assertEqual(len(report['assets'][0]['coverage']['incomplete']),16)

    def test_execution_requires_both_stages_and_all_fee_scenarios(self):
        result=execution(); result.pop('development')
        report=self.run_result(result,execution_only=True)
        self.assertFalse(report['complete'])
        self.assertEqual(report['coverage']['planned'],12)
        self.assertEqual(report['coverage']['sufficient'],6)
        result=execution(); rows=result['holdout']['strategies']['rules']
        rows[1]=deepcopy(rows[0])
        report=self.run_result(result,execution_only=True)
        self.assertFalse(report['complete'])
        self.assertEqual(report['coverage']['sufficient'],10)

    def test_zero_trades_are_complete_but_one_session_is_not(self):
        self.assertTrue(self.run_result(execution(),execution_only=True)['complete'])
        result=execution(); result['holdout']['strategies']['rules'][0]['n_sessions']=1
        report=self.run_result(result,execution_only=True)
        self.assertFalse(report['complete'])
        self.assertEqual(report['assets'][0]['coverage']['incomplete'][0]['reason'],'insufficient_sessions')


if __name__=='__main__':
    unittest.main()
