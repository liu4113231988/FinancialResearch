import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_research.market.technical import (indicators, wilder, features_for,
    direction_forecast, compare_forecasts, analyze_technical,
    evidence_state, EVIDENCE_STATES, EVIDENCE_LABELS)
from market_research.funds.payloads import _jsafe


def sample(n=430):
    rng = np.random.default_rng(117)
    close = 100 * np.exp(np.cumsum(rng.normal(.0002, .012, n)))
    return pd.DataFrame({"open":close, "high":close+1, "low":close-1, "close":close, "volume":1000.}, index=pd.bdate_range('2023-01-01',periods=n))


class TechnicalTests(unittest.TestCase):
    def test_wilder_arithmetic_seed(self):
        result = wilder(pd.Series(np.arange(1.,17.)))
        self.assertTrue(result.iloc[:13].isna().all())
        self.assertAlmostEqual(result.iloc[13],7.5)
        self.assertAlmostEqual(result.iloc[14],(7.5*13+15)/14)

    def test_rsi_flat_and_one_way_prices(self):
        for close, expected in [(np.full(80,100.),50), (np.arange(100.,180.),100), (np.arange(180.,100.,-1),0)]:
            frame=sample(80)
            frame['close']=close; frame['high']=close+1; frame['low']=close-1
            result=indicators(frame)
            self.assertTrue(result.rsi.iloc[:14].isna().all())
            self.assertAlmostEqual(result.rsi.iloc[-1],expected)
            self.assertTrue(result.score.iloc[:64].isna().all())
            if expected==50:
                self.assertEqual(result.boll_upper.iloc[-1],100.)
                self.assertEqual(result.boll_lower.iloc[-1],100.)
                self.assertEqual(result.score.iloc[-1],0.)
                self.assertFalse(result.exit.iloc[-1])

    def test_atr_includes_price_gaps_and_prior_extrema(self):
        frame=sample(40)
        frame[['open','close']]=100.;frame['high']=101.;frame['low']=99.
        frame.iloc[14,frame.columns.get_indexer(['open','close','high','low'])]=[103.,103.,104.,102.]
        result=indicators(frame)
        self.assertAlmostEqual(result.atr.iloc[14],(13*2+4)/14)
        self.assertEqual(result.resistance.iloc[20],104.)
        self.assertTrue(pd.isna(result.resistance.iloc[19]))
        self.assertEqual(result.volume_ratio.iloc[-1],1.)

    def test_prefix_indicators_and_forecasts_do_not_see_future(self):
        frame=sample(430)
        prefix=frame.iloc[:385]
        full_ind=indicators(frame); prior_ind=indicators(prefix)
        pd.testing.assert_frame_equal(full_ind.loc[prefix.index],prior_ind)
        full=direction_forecast(frame,features_for(frame,full_ind),5,frame.index[0])
        prior=direction_forecast(prefix,features_for(prefix,prior_ind),5,frame.index[0])
        np.testing.assert_allclose(full['series'][:385],prior['series'],equal_nan=True,rtol=1e-10)

    def test_training_target_matures_before_prediction_and_evaluation_spacing(self):
        frame=sample()
        positions={d:i for i,d in enumerate(frame.index)}
        horizon=20
        class Spy:
            def fit(self,x,y):
                self.last=positions[x.index[-1]]
                self.base=float(y.mean())
                return self
            def predict_proba(self,x):
                assert self.last+horizon<=positions[x.index[0]],'future target leaked'
                return np.array([[.3,.7]])
        with patch('market_research.market.technical.make_pipeline',side_effect=lambda *args:Spy()):
            out=direction_forecast(frame,features_for(frame,indicators(frame)),horizon,frame.index[0])
        self.assertTrue(out['ok'])
        self.assertLessEqual(out['evaluation']['n'],len(frame)//horizon)
        self.assertFalse(out['evaluation']['ok'])

    def test_short_history_no_forecast_and_json_finite(self):
        frame=sample(40)
        result=analyze_technical(frame,adjust='')
        self.assertFalse(result['ready'])
        self.assertTrue(all(not f['ok'] for f in result['forecasts']))
        self.assertTrue(any('不复权' in x for x in result['notices']))
        json.dumps(_jsafe(result),allow_nan=False)

    def test_notices_disclose_thresholds_and_evidence_limits(self):
        result = analyze_technical(sample(), adjust='qfq')
        notes = " ".join(result['notices'])
        self.assertIn("0.5", notes)
        self.assertIn("60%", notes)
        self.assertIn("40%", notes)
        self.assertIn("未建立稳定的日频方向预测优势", notes)
        json.dumps(_jsafe(result), allow_nan=False)

    def test_evaluation_adds_flat_audit_without_changing_main_label(self):
        # 每 7 个交易日才跳一档：5 日周期里既有平盘也有上涨，两种口径都能算。
        n = 520
        close = 100. + np.arange(n) // 7
        frame = pd.DataFrame({"open": close, "high": close, "low": close,
                              "close": close, "volume": 1000.},
                             index=pd.bdate_range('2023-01-02', periods=n))
        out = direction_forecast(frame, features_for(frame, indicators(frame)),
                                 5, frame.index[0])
        ev = out['evaluation']
        self.assertEqual(ev['label_policy'], 'flat_as_down')
        self.assertGreater(ev['flat_n'], 0)
        self.assertGreater(ev['n_excluding_flat'], 0)
        self.assertEqual(ev['n'], ev['flat_n'] + ev['n_excluding_flat'])
        self.assertTrue(np.isfinite(ev['brier_excluding_flat']))
        # 主指标仍按"平盘算下跌"计算；新增字段只是并列对照，不替换主口径。
        self.assertNotEqual(ev['n_excluding_flat'], ev['n'])
        self.assertTrue(0. <= ev['accuracy_excluding_flat'] <= 1.)
        json.dumps(_jsafe(out), allow_nan=False)

    def test_signals_are_first_condition_and_use_past_only(self):
        frame=sample()
        ind=indicators(frame)
        np.testing.assert_array_equal(ind.buy_event,ind.entry & ~ind.entry.shift(fill_value=False))
        np.testing.assert_array_equal(ind.sell_event,ind.exit & ~ind.exit.shift(fill_value=False))
        result=analyze_technical(frame,display_start=frame.index[-100],adjust='hfq')
        self.assertEqual(len(result['series']['dates']),100)
        # P3-A：窗口内全部触发都返回，不再只留最近 12 条
        self.assertEqual(len(result['signals']),result['signal_total'])
        self.assertEqual(result['signals_truncated'],0)
        self.assertTrue(all(s['date']>=frame.index[-100].date().isoformat()
                            for s in result['signals']))
        self.assertTrue(any('不能直接' in x for x in result['notices']))
        json.dumps(_jsafe(result),allow_nan=False)


class C2EvaluationContractTests(unittest.TestCase):
    """C2/P3-P：可计算 != 评估充分；少样本主指标留空，诊断字段保留。"""

    def test_evidence_states_never_claim_verified(self):
        self.assertEqual(len(EVIDENCE_STATES), 4)
        self.assertEqual(set(EVIDENCE_LABELS), set(EVIDENCE_STATES))
        self.assertEqual(evidence_state(computable=False,
                                        assessment_status='sufficient'),
                         'not_computable')
        self.assertEqual(evidence_state(computable=True,
                                        assessment_status='insufficient'),
                         'insufficient')
        self.assertEqual(evidence_state(computable=True,
                                        assessment_status='sufficient'),
                         'historical_diagnostic')
        # 进入预注册验收也不输出"已验证"，只是独立验证待完成
        self.assertEqual(evidence_state(computable=True,
                                        assessment_status='sufficient',
                                        verification_started=True),
                         'independent_verification_pending')
        self.assertNotIn('verified', ' '.join(EVIDENCE_STATES))

    @staticmethod
    def _seed99_frame(n=800):
        rng = np.random.default_rng(99)
        close = 100 * np.exp(np.cumsum(rng.normal(.0002, .012, n)))
        return pd.DataFrame({"open": close * .999, "high": close * 1.01,
                             "low": close * .99, "close": close, "volume": 1000.},
                            index=pd.bdate_range('2020-01-01', periods=n))

    def test_seed99_20day_computable_but_assessment_insufficient(self):
        frame = self._seed99_frame()
        out = direction_forecast(frame, features_for(frame, indicators(frame)),
                                 20, frame.index[-252])
        ev = out['evaluation']
        self.assertTrue(out['computable'])
        self.assertEqual(out['ok'], out['computable'])
        self.assertEqual(ev['n'], 12)          # 252 展示窗内只有 12 个非重叠起点
        self.assertEqual(ev['assessment_status'], 'insufficient')
        self.assertEqual(out['evidence_state'], 'insufficient')
        self.assertIsNone(ev['primary'])
        self.assertIsNone(ev['advantage'])
        self.assertGreater(out['training_rows'], 0)
        self.assertEqual(sum(ev['class_distribution'].values()), ev['n'])
        self.assertEqual(ev['start'], '2022-02-07')
        # 主指标留空，但研究诊断仍可显式保留
        self.assertTrue(np.isfinite(ev['brier']))
        self.assertTrue(np.isfinite(ev['baseline_brier']))

    def test_no_mature_sample_status_and_empty_distribution(self):
        frame = sample(40)
        out = direction_forecast(frame, features_for(frame, indicators(frame)),
                                 5, frame.index[0])
        ev = out['evaluation']
        self.assertEqual(ev['n'], 0)
        self.assertEqual(ev['assessment_status'], 'insufficient')
        self.assertEqual(out['evidence_state'], 'not_computable')
        self.assertIsNone(ev['primary'])
        self.assertIsNone(ev['advantage'])
        self.assertEqual(ev['class_distribution'], {'up': 0, 'down': 0, 'flat': 0})

    def test_comparison_primary_empty_when_common_sample_insufficient(self):
        frame = self._seed99_frame()
        ind = indicators(frame)
        base = direction_forecast(frame, features_for(frame, ind), 20,
                                  frame.index[-252])
        candidate = {'series': [0.5] * len(base['series'])}
        out = compare_forecasts(frame, base, candidate, 20, frame.index[-252])
        self.assertEqual(out['n'], 12)
        self.assertEqual(out['assessment_status'], 'insufficient')
        self.assertEqual(out['evidence_state'], 'insufficient')
        self.assertIsNone(out['primary'])
        self.assertEqual(sum(out['class_distribution'].values()), out['n'])
        # 兼容字段仍给出，供研究诊断；不得据此宣称优势
        self.assertTrue(np.isfinite(out['brier_improvement']))

    def test_comparison_primary_present_when_sample_sufficient(self):
        frame = self._seed99_frame()
        ind = indicators(frame)
        base = direction_forecast(frame, features_for(frame, ind), 5,
                                  frame.index[0])
        candidate = {'series': [0.5] * len(base['series'])}
        out = compare_forecasts(frame, base, candidate, 5, frame.index[0])
        self.assertGreater(out['n'], 20)
        self.assertEqual(out['assessment_status'], 'sufficient')
        self.assertEqual(out['evidence_state'], 'historical_diagnostic')
        self.assertIsNotNone(out['primary'])
        self.assertAlmostEqual(out['primary']['brier_improvement'],
                               out['brier_improvement'])


class C2AcceptanceExtensionTests(unittest.TestCase):
    """C2/P3-P 第6条验收：台账驱动、极端概率、日期交集、负对照与固定种子。"""

    @staticmethod
    def _frame(n=800, seed=99):
        rng = np.random.default_rng(seed)
        close = 100 * np.exp(np.cumsum(rng.normal(.0002, .012, n)))
        return pd.DataFrame({"open": close * .999, "high": close * 1.01,
                             "low": close * .99, "close": close, "volume": 1000.},
                            index=pd.bdate_range('2020-01-01', periods=n))

    def test_verification_flag_promotes_label_in_both_layers(self):
        frame = self._frame()
        ind = indicators(frame)
        feats = features_for(frame, ind)
        plain = direction_forecast(frame, feats, 5, frame.index[0])
        self.assertNotEqual(plain['evidence_state'], 'independent_verification_pending')
        self.assertEqual(plain['evaluation']['evidence_state'], plain['evidence_state'])
        pending = direction_forecast(frame, feats, 5, frame.index[0],
                                     verification_started=True)
        expected = evidence_state(computable=pending['computable'],
                                  assessment_status=pending['evaluation']['assessment_status'],
                                  verification_started=True)
        self.assertEqual(pending['evidence_state'], expected)
        self.assertEqual(pending['evidence_label'], EVIDENCE_LABELS[expected])
        # 页面读 evaluation、导出读顶层，两层必须是同一结论
        self.assertEqual(pending['evaluation']['evidence_state'], pending['evidence_state'])
        self.assertEqual(pending['evaluation']['evidence_label'],
                         pending['evidence_label'])
        self.assertEqual(expected, 'independent_verification_pending'
                         if pending['evaluation']['assessment_status'] == 'sufficient'
                         else 'insufficient')

    def test_verification_flag_never_upgrades_insufficient_samples(self):
        frame = self._frame()
        out = direction_forecast(frame, features_for(frame, indicators(frame)),
                                 20, frame.index[-252], verification_started=True)
        self.assertEqual(out['evaluation']['assessment_status'], 'insufficient')
        self.assertEqual(out['evidence_state'], 'insufficient')

    def test_extreme_probabilities_keep_brier_finite_and_hand_checkable(self):
        from market_research.research.market_validation import paired_evidence
        frame = self._frame(n=400)
        forward = frame.close.shift(-5) / frame.close - 1
        target = (forward > 0).astype(float).where(forward.notna())
        flat = pd.Series(0.5, index=frame.index)
        perfect = target.copy()          # 概率取 0/1 端点
        inverted = 1.0 - target
        good = paired_evidence(frame, flat, perfect, 5, 4)
        bad = paired_evidence(frame, flat, inverted, 5, 4)
        self.assertGreater(good['n'], 20)
        self.assertAlmostEqual(good['brier_improvement_uncertainty']['point_estimate'],
                               0.25, places=6)
        # 完全取反的 0/1 概率：平方误差由 0.25 升到 1.00，配对差 -0.75
        self.assertAlmostEqual(bad['brier_improvement_uncertainty']['point_estimate'],
                               -0.75, places=6)
        self.assertEqual(good['n'], bad['n'])

    def test_date_intersection_change_drops_rows_instead_of_filling(self):
        from market_research.research.market_validation import paired_evidence
        frame = self._frame(n=400)
        forward = frame.close.shift(-5) / frame.close - 1
        target = (forward > 0).astype(float).where(forward.notna())
        flat = pd.Series(0.5, index=frame.index)
        full = paired_evidence(frame, flat, target.copy(), 5, 4)
        # 候选缺 60 个日期：应改用交集，而不是补 0.5 或沿用旧分母
        gapped = target.copy()
        gapped.iloc[100:160] = np.nan
        reduced = paired_evidence(frame, flat, gapped, 5, 4)
        self.assertLess(reduced['n'], full['n'])
        self.assertIsNotNone(reduced['n'])
        self.assertAlmostEqual(
            reduced['brier_improvement_uncertainty']['point_estimate'], 0.25, places=6)

    def test_fixed_seed_reproduces_forecast_and_intervals(self):
        from market_research.research.market_validation import paired_evidence
        frame = self._frame()
        feats = features_for(frame, indicators(frame))
        first = direction_forecast(frame, feats, 5, frame.index[0])
        second = direction_forecast(frame, feats, 5, frame.index[0])
        for key in ('probability', 'baseline', 'evidence_state', 'training_rows'):
            self.assertEqual(first[key], second[key])
        np.testing.assert_allclose(first['series'], second['series'], equal_nan=True)
        flat = pd.Series(0.5, index=frame.index)
        candidate = pd.Series(np.random.default_rng(7).uniform(.2, .8, len(frame)),
                              index=frame.index)
        a = paired_evidence(frame, flat, candidate, 5, 4)
        b = paired_evidence(frame, flat, candidate, 5, 4)
        self.assertEqual(a['n'], b['n'])
        self.assertEqual(a['brier_improvement_uncertainty'], b['brier_improvement_uncertainty'])

    def test_constant_price_input_stays_finite(self):
        frame = self._frame(n=200)
        frame[['open', 'high', 'low', 'close']] = 100.0
        out = direction_forecast(frame, features_for(frame, indicators(frame)), 5,
                                 frame.index[0], verification_started=True)
        self.assertIn(out['evidence_state'], EVIDENCE_STATES)
        if out['computable']:
            self.assertTrue(np.isfinite(out['probability']))
            self.assertGreaterEqual(out['probability'], 0.0)
            self.assertLessEqual(out['probability'], 1.0)

    def test_assessment_threshold_flips_at_twenty_starts(self):
        frame = self._frame()
        base = direction_forecast(frame, features_for(frame, indicators(frame)),
                                  5, frame.index[0])
        length, last = len(base['series']), len(base['series']) - 6
        seen = {}
        for size in range(80, 120):
            start = last - size
            series = [0.5 if start <= i < last else None for i in range(length)]
            out = compare_forecasts(frame, base, {'series': series}, 5,
                                    frame.index[0])
            if out['n'] in (19, 20) and out['n'] not in seen:
                seen[out['n']] = (out['assessment_status'], out['primary'])
            if len(seen) == 2:
                break
        self.assertEqual(seen.get(19, (None,))[0], 'insufficient')
        self.assertIsNone(seen[19][1])
        self.assertEqual(seen[20][0], 'sufficient')
        self.assertIsNotNone(seen[20][1])

    def test_block_intervals_widen_for_correlated_series(self):
        from market_research.market.uncertainty import block_intervals
        rng = np.random.default_rng(5)
        n = 240
        independent = rng.normal(0.01, 0.05, n)
        noise = rng.normal(0, 0.03, n)
        correlated = np.zeros(n)
        for i in range(1, n):
            correlated[i] = .8 * correlated[i - 1] + noise[i]
        correlated += 0.01
        iid = block_intervals(independent)
        ar = block_intervals(correlated)
        iid5 = next(i for i in iid['intervals'] if i['block'] == 5)
        ar5 = next(i for i in ar['intervals'] if i['block'] == 5)
        self.assertEqual(iid5['status'], 'estimated')
        self.assertEqual(ar5['status'], 'estimated')
        self.assertGreater(ar5['upper'] - ar5['lower'], iid5['upper'] - iid5['lower'])

    def test_negative_control_only_claims_advantage_when_interval_excludes_zero(self):
        from market_research.research.market_validation import frequency_baseline_evidence
        frame = self._frame()
        cutoff = frame.index[-300].date().isoformat()
        for seed in (7, 11, 13):
            probs = pd.DataFrame({'base': np.random.default_rng(seed).uniform(0., 1., len(frame))},
                                 index=frame.index)
            result = frequency_baseline_evidence(frame, probs, cutoff, 5, 4)
            self.assertTrue(result['ok'])
            block = result['variants']['base']
            advantage = block['advantage_vs_frequency']
            estimated = [i for i in block['uncertainty']['intervals']
                         if i['status'] == 'estimated']
            if advantage is True:
                self.assertTrue(estimated)
                self.assertTrue(all(i['lower'] > 0 for i in estimated))
            elif advantage is False:
                self.assertTrue(estimated)
                self.assertTrue(all(i['upper'] < 0 for i in estimated))
            else:
                self.assertIsNone(advantage)


if __name__=='__main__':unittest.main()
