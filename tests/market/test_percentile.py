import unittest

import numpy as np
import pandas as pd

from market_research.market.context import percentile,context_features
from market_research.market.technical import indicators
from tests.market.test_technical import sample


class PercentileTests(unittest.TestCase):
    def test_leading_missing_values_are_not_in_the_denominator(self):
        values=pd.Series([np.nan]*14+[2.]*60)
        result=percentile(values)
        self.assertTrue(result.iloc[:-1].isna().all())
        self.assertEqual(result.iloc[-1],1.)

    def test_missing_current_observation_does_not_become_zero_percentile(self):
        values=pd.Series([1.]*70+[np.nan])
        self.assertTrue(pd.isna(percentile(values).iloc[-1]))

    def test_known_rank_uses_only_valid_window_observations(self):
        values=pd.Series([np.nan,1.,np.nan,3.,2.])
        self.assertAlmostEqual(percentile(values,window=5,minimum=3).iloc[-1],2/3)

    def test_infinities_are_missing_and_cannot_satisfy_minimum(self):
        for invalid in (np.inf,-np.inf):
            values=pd.Series([1.,invalid,2.,3.])
            self.assertEqual(percentile(values,window=4,minimum=3).iloc[-1],1.)
            self.assertTrue(pd.isna(percentile(values,window=4,minimum=4).iloc[-1]))
            self.assertTrue(pd.isna(percentile(pd.Series([1.,2.,3.,invalid]),window=4,minimum=3).iloc[-1]))

    def test_window_remains_bounded_and_ties_include_current_value(self):
        self.assertAlmostEqual(percentile(pd.Series([100.,100.,2.,3.]),window=3,minimum=3).iloc[-1],2/3)
        self.assertEqual(percentile(pd.Series([5.,5.,5.]),window=3,minimum=3).iloc[-1],1.)
        self.assertTrue(percentile(pd.Series([np.nan]*100),window=10,minimum=3).isna().all())

    def test_prefix_stability_and_input_are_preserved(self):
        rng=np.random.default_rng(117)
        values=pd.Series(rng.normal(size=200)); values.iloc[[30,100,150]]=np.nan
        before=values.copy()
        full=percentile(values,window=100,minimum=30)
        prior=percentile(values.iloc[:150],window=100,minimum=30)
        pd.testing.assert_series_equal(full.iloc[:150],prior)
        changed=values.copy(); changed.iloc[150:]=1e9
        pd.testing.assert_series_equal(full.iloc[:150],percentile(changed,window=100,minimum=30).iloc[:150])
        pd.testing.assert_series_equal(values,before)

    def test_constant_market_indicators_have_correct_initial_percentiles(self):
        frame=sample(100); frame[['open','close']]=100.; frame['high']=101.; frame['low']=99.
        features=context_features(frame,indicators(frame))
        self.assertTrue(features.atr_percentile.iloc[:72].isna().all())
        self.assertEqual(features.atr_percentile.iloc[72],1.)
        self.assertTrue(features.bandwidth_percentile.iloc[:78].isna().all())
        self.assertEqual(features.bandwidth_percentile.iloc[78],1.)

    def test_unavailable_current_indicators_remain_unavailable_in_features(self):
        frame=sample(200); ind=indicators(frame)
        ind.loc[ind.index[-1],['atr','boll_upper','boll_lower']]=np.nan
        features=context_features(frame,ind)
        self.assertTrue(pd.isna(features.atr_percentile.iloc[-1]))
        self.assertTrue(pd.isna(features.bandwidth_percentile.iloc[-1]))


if __name__=='__main__':
    unittest.main()
