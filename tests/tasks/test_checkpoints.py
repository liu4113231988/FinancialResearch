"""P3-F：阶段检查点让失败重试跳过已完成阶段，且状态不一致时回退从头执行。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.config as C
import market_research.funds.analysis as FA
import market_research.services.analysis as services
from market_research.tasks.checkpoints import StageCheckpoint


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=C.ROOT / 'output')
        self.base = Path(self.temp.name) / 'checkpoints'

    def tearDown(self):
        self.temp.cleanup()

    def test_stage_roundtrip_and_clear(self):
        cp = StageCheckpoint('job-1', {'v': 1}, base=self.base)
        cp.save('panel', {'rows': 10})
        self.assertEqual(cp.load('panel'), {'rows': 10})
        cp.clear()
        self.assertFalse(cp.dir.exists())

    def test_fingerprint_change_discards_stages(self):
        first = StageCheckpoint('job-2', {'stamp': 'a', 'args': {'start': '2020'}},
                                base=self.base)
        first.save('models', {'r2': 0.5})
        second = StageCheckpoint('job-2', {'stamp': 'b', 'args': {'start': '2020'}},
                                base=self.base)
        self.assertIsNone(second.load('models'),
                          '数据版本变化后不能复用旧阶段产物')
        self.assertFalse((second.dir / 'models.pkl').exists())

    def test_corrupt_stage_is_dropped_not_used(self):
        cp = StageCheckpoint('job-3', {'v': 1}, base=self.base)
        (cp.dir / 'panel.pkl').write_bytes(b'not-a-pickle')
        self.assertIsNone(cp.load('panel'))
        self.assertFalse((cp.dir / 'panel.pkl').exists())

    def test_expired_checkpoint_directories_are_pruned(self):
        import os, time
        stale = StageCheckpoint('old-job', {'v': 1}, base=self.base)
        old = time.time() - 10 * 86400
        os.utime(stale.dir, (old, old))
        cp = StageCheckpoint('new-job', {'v': 1}, base=self.base)
        self.assertFalse(stale.dir.exists())
        self.assertTrue(cp.dir.exists())


class DispatchResumeTests(unittest.TestCase):
    """_dispatch 在失败后保留检查点、成功后清理。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=C.ROOT / 'output')
        self.root = Path(self.temp.name)
        self.calls = {'panel': 0, 'models': 0}
        self.state = {'fail': True}

    def tearDown(self):
        self.temp.cleanup()

    def test_retry_reuses_completed_stages(self):
        def fake_analyze(code, checkpoint=None, **_):
            cp = checkpoint
            panel = cp.load('panel')
            if panel is None:
                self.calls['panel'] += 1
                panel = {'code': code}
                cp.save('panel', panel)
            models = cp.load('models')
            if models is None:
                self.calls['models'] += 1
                models = {'r2': 0.4}
                cp.save('models', models)
            if self.state['fail']:
                self.state['fail'] = False
                raise RuntimeError('中途失败')
            return {'ok': True, 'sections': models}

        params = {'code': '110022', 'models': ['enet_slsqp'], 'params': {},
                  'refresh': True, 'auto_equity': True, 'start': None, 'end': None}
        checkpoint_dir = self.root / 'checkpoints'
        (self.root / 'cache').mkdir(parents=True, exist_ok=True)
        with patch.object(services, 'analyze', fake_analyze), \
             patch.object(services, '_data_stamp_for', return_value='stamp-x'), \
             patch.object(services, 'CACHE_DIR', self.root / 'cache'), \
             patch.object(C, 'DATA_DIR', self.root), \
             patch.dict('os.environ', {'FIL_CHECKPOINT_DIR': str(checkpoint_dir)}):
            with self.assertRaises(RuntimeError):
                services._dispatch('analyze', params, 'job-retry')
            self.assertTrue((checkpoint_dir / 'job-retry').exists(),
                            '失败后保留检查点供下一次尝试')
            result = services._dispatch('analyze', params, 'job-retry')
        self.assertTrue(result['ok'])
        self.assertEqual(self.calls['panel'], 1, '重试不应重新加载数据')
        self.assertEqual(self.calls['models'], 1, '重试不应重新拟合模型')
        self.assertFalse((checkpoint_dir / 'job-retry').exists(),
                         '成功后清理检查点')


class FakeReport:
    interpretable = True
    name = 'ENet'
    family = '线性'
    oos_r2 = 0.4
    oos_te = 0.1
    fit_r2_mean = 0.5
    fit_te_mean = 0.05
    win = 0.6
    beta = 0.8
    elapsed = 1.0
    weights_hist = pd.DataFrame({'sh000300': [0.5]})
    syn_ret = pd.Series([0.01])


class FakeZoo:
    rebal_mode = 'calendar'
    max_rebal = 60

    def __init__(self, cfg=None):
        pass

    def run(self, panel, models, params=None, max_rebal=None, workers=None,
            progress=None):
        return {key: FakeReport() for key in models}

    @staticmethod
    def rank(reports, key='样本外R²'):
        return pd.DataFrame([{'key': k, '样本外R²': 0.4} for k in reports])


class AnalyzeStageWiringTests(unittest.TestCase):
    """真实 analyze() 在阶段间读写检查点。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=C.ROOT / 'output')
        self.base = Path(self.temp.name)
        self.calls = {'panel': 0, 'models': 0, 'nav': 0, 'corr': 0}
        self.fail_insights = True
        index = pd.bdate_range('2023-01-02', periods=260)
        self.panel = pd.DataFrame(
            {'__FUND__': np.linspace(1.0, 1.2, len(index)),
             'sh000300': np.linspace(1.0, 1.1, len(index))}, index=index)
        self.panel.attrs['data_quality'] = {'status': 'ok'}
        self.panel.attrs['nav_basis'] = 'total'

    def tearDown(self):
        self.temp.cleanup()

    def _patches(self):
        calls = self.calls

        def load_panel(code, **kwargs):
            calls['panel'] += 1
            return self.panel

        def nav_block(panel, nav):
            calls['nav'] += 1
            return {'points': 2}

        def corr_block(panel, nav):
            calls['corr'] += 1
            return {'top': []}

        def insights(*args, **kwargs):
            if self.fail_insights:
                self.fail_insights = False
                raise RuntimeError('组装结论时崩溃')
            return []

        class Zoo(FakeZoo):
            def run(self, *args, **kwargs):
                calls['models'] += 1
                return super().run(*args, **kwargs)

        return [
            patch.object(FA.D, 'load_panel', load_panel),
            patch.object(FA, 'nav_block', nav_block),
            patch.object(FA, 'correlation_block', corr_block),
            patch.object(FA, 'fund_forecast_payload', lambda nav: {'available': False}),
            patch.object(FA, 'model_payload',
                         lambda rep, nav: {'extra': {}, 'metrics': {'oos_r2': 0.4}}),
            patch.object(FA, 'build_insights', insights),
            patch.object(FA, 'ModelZoo', Zoo),
            patch.object(FA.D, 'load_pe_panel',
                         side_effect=RuntimeError('无估值数据')),
            patch.object(FA.JOBS, 'update', lambda *a, **k: None),
            patch('market_research.research.experiments.style_drift',
                  side_effect=RuntimeError('跳过漂移诊断')),
        ]

    def test_analyze_resumes_after_failure_without_recomputing(self):
        fingerprint = {'operation': 'analyze', 'stamp': 's1',
                       'args': {'code': '110022', 'models': ['enet_slsqp']}}
        patches = self._patches()
        for item in patches:
            item.start()
        try:
            cp = StageCheckpoint('job-analyze', fingerprint, base=self.base)
            with self.assertRaises(RuntimeError):
                FA.analyze(code='110022', models=['enet_slsqp'], params={},
                           job_id='job-analyze', checkpoint=cp)
            self.assertEqual(self.calls['panel'], 1)
            self.assertEqual(self.calls['models'], 1)

            cp = StageCheckpoint('job-analyze', fingerprint, base=self.base)
            result = FA.analyze(code='110022', models=['enet_slsqp'], params={},
                                job_id='job-analyze', checkpoint=cp)
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(self.calls['panel'], 1, '数据阶段不应重跑')
        self.assertEqual(self.calls['models'], 1, '模型阶段不应重跑')
        self.assertEqual(self.calls['nav'], 1, '净值阶段不应重跑')
        self.assertEqual(self.calls['corr'], 1, '相关性阶段不应重跑')
        self.assertTrue(result['ok'])


if __name__ == '__main__':
    unittest.main()
