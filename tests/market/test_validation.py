import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
import pandas as pd
from tests.market.test_technical import sample
from market_research.market.technical import direction_forecast, compare_forecasts
from market_research.market.targets import horizon_data
from market_research.research.market_validation import (validate_plan, feature_variants,
    evaluate_asset, run_plan, paired_evidence, frequency_baseline_evidence, VARIANTS)
from market_research.market.context import market_regimes


class ValidationTests(unittest.TestCase):
    def test_frozen_holdout_never_trains_on_test_labels(self):
        bars = sample(430); cutoff = bars.index[360]
        features = pd.DataFrame({'x': 1.}, index=bars.index)
        fitted = []
        class Spy:
            def fit(self, x, y):
                fitted.append(x.index[-1]); self.prior = y.mean(); return self
            def predict_proba(self, x):
                return np.array([[1-self.prior, self.prior]])
        with patch('market_research.market.technical.make_pipeline', side_effect=lambda *a: Spy()):
            first = direction_forecast(bars, features, 20, bars.index[0], training_end=str(cutoff.date()))
            changed = bars.copy(); changed.loc[changed.index > cutoff, 'close'] *= 100
            second = direction_forecast(changed, features, 20, bars.index[0], training_end=str(cutoff.date()))
        self.assertEqual(max(fitted), bars.index[340])
        np.testing.assert_allclose(first['series'], second['series'], equal_nan=True)

    def test_environment_partition_is_exhaustive_and_uses_signal_date(self):
        bars = sample(100)
        context = pd.DataFrame({'benchmark_gap60': [1.,-1.,1.,np.nan]*25,
                                'benchmark_return20': [1.,-1.,-1.,np.nan]*25}, index=bars.index)
        self.assertEqual(market_regimes(context).iloc[:4].tolist(), ['strong','weak','mixed','unknown'])
        out = compare_forecasts(bars, {'series':[.6]*100}, {'series':[.7]*100}, 5, bars.index[0], context)
        self.assertEqual(sum(r['n'] for r in out['regimes']), out['n'])

    def test_missing_group_does_not_silently_become_baseline(self):
        bars = sample(100).drop(columns='volume')
        variants, _ = feature_variants(bars, None)
        self.assertTrue(variants['volume']['volume_ratio'].isna().all())
        self.assertTrue(variants['relative']['relative20'].isna().all())
        self.assertFalse(variants['base'].iloc[-1].isna().any())
        available, _ = feature_variants(sample(100), None)
        self.assertIn('volume_ratio', available['volume'])
        self.assertNotIn('amount_ratio', available['volume'])

    def test_split_excludes_targets_crossing_development_boundary(self):
        bars = sample(200)
        plan = dict(start=str(bars.index[0].date()), development_start=str(bars.index[40].date()),
                    holdout_start=str(bars.index[120].date()), end=str(bars.index[-1].date()))
        with patch('market_research.research.market_validation.direction_forecast', return_value={'series':[.6]*200}) as forecast:
            out = evaluate_asset(bars, bars.close, plan, 16)
        for call in forecast.call_args_list:
            self.assertEqual(call.kwargs['training_end'], str((bars.index[120]-pd.Timedelta(days=1)).date()))
        for horizon in (5,20):
            for row in out['horizons'][str(horizon)]['development']:
                self.assertLessEqual(pd.Timestamp(row['end']), bars.index[119-horizon])
                self.assertEqual(pd.Timestamp(row['execution']['end']),bars.index[119])
                self.assertEqual(row['execution']['strategies']['base'][0]['fills'],1)

    def test_invalid_manifest_and_missing_assets_are_retained(self):
        plan = dict(start='2021-01-01', development_start='2023-01-01', holdout_start='2025-01-01', end='2026-10-30',
                    assets=[dict(kind='stock',code='sh600519')])
        with self.assertRaises(ValueError): validate_plan(plan | {'holdout_start': '2020-01-01'})
        with self.assertRaises(ValueError): validate_plan(plan | {'assets':plan['assets']*2})
        with patch('market_research.research.market_validation.read_bars', side_effect=ValueError('missing')):
            report = run_plan(plan, Path('.'))
        self.assertFalse(report['complete'])
        self.assertEqual(report['planned_comparisons'],16)
        self.assertEqual(report['assets'][0]['status'],'unavailable')

    def test_small_sample_does_not_report_inferential_p_value(self):
        bars = sample(50)
        out = paired_evidence(bars, [.6]*50, [.9]*50, 5, 32)
        self.assertIsNone(out['p_bonferroni_approx'])

    def test_frequency_baseline_uses_training_labels_and_shared_dates(self):
        bars = sample(300)
        cutoff = str(bars.index[200].date())
        train_returns, _, _ = horizon_data(bars.close.loc[:cutoff], 5, None)
        train_labels = (train_returns > 0).astype(float).where(train_returns.notna())
        prior = float(train_labels.dropna().mean())
        probabilities = pd.DataFrame({name: [.6] * len(bars) for name in VARIANTS},
                                     index=bars.index)
        probabilities['relative'] = probabilities['relative'].where(
            bars.index != bars.index[220])
        probabilities['all'] = prior             # 与基线完全相同 → 不得宣称优势
        out = frequency_baseline_evidence(bars, probabilities, cutoff, 5, 16)
        self.assertTrue(out['ok'])
        self.assertAlmostEqual(out['prior'], prior)
        self.assertEqual(out['training_labels'], int(train_labels.notna().sum()))
        self.assertEqual(set(out['variants']), set(VARIANTS))
        samples = {item['n'] for item in out['variants'].values()}
        self.assertEqual(len(samples), 1)        # 所有变体共用同一组日期
        self.assertEqual(samples.pop(), out['common_n'])
        self.assertGreater(out['common_raw_n'], out['common_n'])
        self.assertLess(out['common_n'], 280)    # 固定非重叠起点，不重复取样
        self.assertIsNone(out['variants']['all']['advantage_vs_frequency'])
        self.assertIsNone(out['variants']['all']['uncertainty']['intervals'][0]['lower'])
        # 手算点估计：正号代表候选 Brier 更小（与 fixed 非重叠起点一致）
        actual, clock, _ = horizon_data(bars.close, 5, None)
        common = actual.notna() & probabilities.notna().all(axis=1)
        index = bars.index[common]
        keep, last = [], -5
        for i, position in enumerate(clock[bars.index.get_indexer(index)]):
            if position >= last + 5:
                keep.append(i); last = position
        y = (actual[index[keep]] > 0).astype(float)
        expected = float(((prior - y) ** 2 - (.6 - y) ** 2).mean())
        self.assertEqual(len(y), out['common_n'])
        self.assertAlmostEqual(out['variants']['base']['brier_gap'], expected)

    def test_evaluate_asset_exposes_frequency_comparison(self):
        bars = sample(300)
        plan = dict(start=str(bars.index[0].date()),
                    development_start=str(bars.index[40].date()),
                    holdout_start=str(bars.index[200].date()),
                    end=str(bars.index[-1].date()))
        with patch('market_research.research.market_validation.direction_forecast',
                   return_value={'series': [.6] * len(bars)}):
            out = evaluate_asset(bars, bars.close, plan, 16)
        for horizon in (5, 20):
            freq = out['frequency_baseline'][str(horizon)]
            self.assertTrue(freq['ok'])
            self.assertEqual(set(freq['variants']), set(VARIANTS))
            rows = out['horizons'][str(horizon)]['development']
            self.assertTrue(all(row['frequency']['prior'] == freq['prior']
                                for row in rows))
            self.assertTrue(all(row['frequency']['candidate']['n'] == freq['common_n']
                                for row in rows))


if __name__ == '__main__': unittest.main()
