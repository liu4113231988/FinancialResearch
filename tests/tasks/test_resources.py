"""P3-I：进程级资源上限（并发度、内存、超时配置）与明确错误。"""
import os
import unittest
from contextlib import nullcontext
from unittest.mock import patch

import market_research.resources as resources
from market_research.settings import Settings
from market_research.tasks import worker


class WorkerBudgetTests(unittest.TestCase):
    def test_auto_workers_uses_cpu_budget(self):
        with patch.object(resources, 'cpu_limit', return_value=4), \
             patch.dict(os.environ, {'FIL_JOB_WORKERS': '0',
                                     'FIL_JOB_WORKERS_MAX': '0'}):
            self.assertEqual(resources.model_workers(10), 3)
            self.assertEqual(resources.model_workers(2), 2)

    def test_auto_workers_never_below_one(self):
        with patch.object(resources, 'cpu_limit', return_value=1), \
             patch.dict(os.environ, {'FIL_JOB_WORKERS': '0',
                                     'FIL_JOB_WORKERS_MAX': '0'}):
            self.assertEqual(resources.model_workers(10), 1)

    def test_explicit_workers_and_global_cap(self):
        with patch.object(resources, 'cpu_limit', return_value=8), \
             patch.dict(os.environ, {'FIL_JOB_WORKERS': '6',
                                     'FIL_JOB_WORKERS_MAX': '2'}):
            self.assertEqual(resources.model_workers(10), 2)
            self.assertEqual(Settings.load().job_workers, 6)
            self.assertEqual(Settings.load().job_workers_max, 2)

    def test_timeout_and_memory_defaults_are_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FIL_JOB_MEMORY_MB', None)
            os.environ.pop('FIL_JOB_TIMEOUT', None)
            settings = Settings.load()
            self.assertEqual(settings.job_memory_mb, 0, '默认不限制内存')
            self.assertEqual(settings.job_timeout, 900, '单任务时长上限默认 900s')

    def test_memory_limit_behavior_per_platform(self):
        if os.name == 'nt':
            self.assertEqual(resources.apply_memory_limit(128), 128 * 1024 * 1024,
                             'Windows 由 Job Object 限制，此处只返回配额')
            return
        import resource
        before = resource.getrlimit(resource.RLIMIT_AS)
        try:
            limit = resources.apply_memory_limit(256)
            current = resource.getrlimit(resource.RLIMIT_AS)[0]
            self.assertEqual(limit, 256 * 1024 * 1024)
            self.assertEqual(current, limit)
        finally:
            resource.setrlimit(resource.RLIMIT_AS, before)

    def test_memory_error_failure_is_actionable(self):
        job = {'id': 'job-mem', 'token': 't', 'status': 'running',
               'operation': 'analyze', 'arguments': {}, 'started': 1}
        with patch.object(worker.JOBS, 'get', return_value=job), \
             patch.object(worker, 'contain_children', lambda: None), \
             patch.object(worker, 'bind_attempt', lambda token: nullcontext()), \
             patch.object(worker.JOBS, 'fail') as fail, \
             patch('market_research.services.analysis.execute',
                   side_effect=MemoryError()):
            worker.execute('job-mem', 't')
        message = fail.call_args.args[1]
        self.assertIn('FIL_JOB_MEMORY_MB', message,
                      '内存超限必须给出可操作的错误，而不是通用异常名')
        self.assertEqual(fail.call_args.kwargs['token'], 't')


if __name__ == '__main__':
    unittest.main()
