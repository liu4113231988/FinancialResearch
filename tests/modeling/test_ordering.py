"""P3-G：并列排名必须给出确定顺序，同输入同输出。"""
import unittest

import numpy as np
import pandas as pd

from market_research.modeling.engine import ModelReport, ModelZoo
from market_research.ordering import ranked_frame, ranked_series, rows_order
from market_research.funds.rbsa import RBSA


def _report(key, r2):
    return ModelReport(
        key=key, name=key.upper(), family='线性', desc='', interpretable=True,
        oos_r2=r2, oos_te=0.1, oos_ic=np.nan, win=0.5, beta=0.8,
        vol_ratio=1.0, fit_r2_mean=0.5, fit_te_mean=0.05,
        latest_weights=pd.Series(dtype=float), weights_last_date=None,
        weights_hist=pd.DataFrame(), syn_ret=pd.Series(dtype=float),
        extra={}, elapsed=1.0)


class OrderingTests(unittest.TestCase):
    def test_ranked_frame_ties_sorted_by_tie_key(self):
        frame = pd.DataFrame({'key': ['c', 'a', 'b'], '样本外R²': [0.5, 0.5, 0.5]})
        ordered = ranked_frame(frame, '样本外R²', ascending=False)
        self.assertEqual(list(ordered['key']), ['a', 'b', 'c'])

    def test_ranked_frame_keeps_nan_last_and_ignores_reordered_input(self):
        left = pd.DataFrame({'key': ['b', 'a', 'c'],
                             'v': [np.nan, 1.0, 3.0]})
        right = pd.DataFrame({'key': ['c', 'a', 'b'],
                              'v': [3.0, 1.0, np.nan]})
        a = ranked_frame(left, 'v', ascending=False)
        b = ranked_frame(right, 'v', ascending=False)
        self.assertEqual(list(a['key']), ['c', 'a', 'b'])
        self.assertEqual(list(b['key']), ['c', 'a', 'b'])

    def test_ranked_series_ties_and_nan(self):
        series = pd.Series({'sh000905': 0.2, 'sh000300': 0.2,
                            'sh000852': np.nan, 'sh000016': 0.5})
        ordered = ranked_series(series)
        self.assertEqual(list(ordered.index),
                         ['sh000016', 'sh000300', 'sh000905', 'sh000852'])

    def test_rows_order_ties_by_code(self):
        rows = [{'code': 'b', 'weight': 0.1}, {'code': 'a', 'weight': 0.1}]
        ordered = rows_order(rows, value='weight')
        self.assertEqual([row['code'] for row in ordered], ['a', 'b'])

    def test_model_rank_is_stable_for_tied_r2(self):
        first = {'z_model': _report('z_model', 0.4),
                 'a_model': _report('a_model', 0.4),
                 'm_model': _report('m_model', 0.7)}
        second = {'a_model': _report('a_model', 0.4),
                  'm_model': _report('m_model', 0.7),
                  'z_model': _report('z_model', 0.4)}
        left = ModelZoo.rank(first)
        right = ModelZoo.rank(second)
        self.assertEqual(list(left['key']), ['m_model', 'a_model', 'z_model'])
        self.assertEqual(list(right['key']), ['m_model', 'a_model', 'z_model'])

    def test_latest_exposure_ties_sorted_by_name(self):
        weights = pd.Series({'sh000905': 0.1, 'sh000300': 0.1})
        exposure = RBSA.latest_exposure(weights)
        names = list(exposure['名称'])
        self.assertEqual(names, sorted(names),
                         '相同权重时按名称升序，而不是输入顺序')


if __name__ == '__main__':
    unittest.main()
