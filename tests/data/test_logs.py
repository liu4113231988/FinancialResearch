"""P3-D：日志按大小轮转、保留份数可配、任务日志按保留期清理。"""
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import market_research.config as C
from market_research import logs


class LogConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=C.ROOT / 'output')
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_env_overrides_size_backups_level_and_directory(self):
        with patch.dict(os.environ, {
                'FIL_LOG_DIR': str(self.dir),
                'FIL_LOG_MAX_BYTES': '120',
                'FIL_LOG_BACKUPS': '2',
                'FIL_LOG_LEVEL': 'DEBUG'}):
            self.assertEqual(logs.log_dir(), self.dir.resolve())
            self.assertEqual(logs.max_bytes(), 120)
            self.assertEqual(logs.backups(), 2)
            self.assertEqual(logs.level(), logging.DEBUG)

    def test_handler_rotates_by_size_and_keeps_backups(self):
        path = self.dir / 'worker.log'
        handler = logs.RotatingAppendHandler(path, limit=200, keep=2)
        handler.setFormatter(logging.Formatter('%(message)s' + 'x' * 30))
        logger = logging.getLogger('logs-test-rotate')
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            for index in range(20):
                logger.info('line-%02d', index)
        finally:
            logger.removeHandler(handler)
        self.assertTrue(path.exists())
        self.assertTrue(path.with_name('worker.log.1').exists())
        self.assertTrue(path.with_name('worker.log.2').exists())
        self.assertFalse(path.with_name('worker.log.3').exists())
        # 当前文件不超过上限，最新一行仍在当前文件里
        self.assertLess(path.stat().st_size, 200)
        self.assertIn('line-19', path.read_text(encoding='utf-8'))
        # 备份中保留的是较旧内容，顺序向后推进
        self.assertTrue(any('line-' in item.read_text(encoding='utf-8')
                            for item in (path.with_name('worker.log.1'),
                                         path.with_name('worker.log.2'))))

    def test_writes_do_not_hold_file_handle(self):
        """Windows 下常驻句柄会阻塞目录清理；追加后必须能立即删除/重命名。"""
        path = self.dir / 'api.log'
        handler = logs.RotatingAppendHandler(path, limit=1000, keep=2)
        handler.setFormatter(logging.Formatter('%(message)s'))
        record = logging.LogRecord('x', logging.INFO, __file__, 0, 'hello', (), None)
        handler.emit(record)
        renamed = path.with_name('renamed.log')
        path.replace(renamed)                       # 无句柄时在 Windows 也成立
        renamed.unlink()
        self.assertFalse(path.exists())
        self.assertFalse(renamed.exists())

    def test_configure_is_idempotent_and_writes_to_log_dir(self):
        with patch.dict(os.environ, {'FIL_LOG_DIR': str(self.dir)}):
            first = logs.configure('api', limit=500, keep=2)
            second = logs.configure('api', limit=500, keep=2)
            logging.getLogger('probe').warning('contract-line')
        path = self.dir / 'api.log'
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding='utf-8').count('contract-line'), 1)
        installed = [h for h in logging.getLogger().handlers
                     if getattr(h, '_market_research_log', False)]
        self.assertEqual(len(installed), 1)         # 重复 configure 不叠加 handler
        self.assertEqual(first.name, 'api')
        self.assertEqual(second.name, 'api')
        for handler in installed:
            logging.getLogger().removeHandler(handler)

    def test_job_logs_are_pruned_by_age(self):
        old = self.dir / 'job-old.log'
        recent = self.dir / 'job-new.log'
        worker = self.dir / 'worker.log'
        for path in (old, recent, worker):
            path.write_text('entry\n', encoding='utf-8')
        stale = time.time() - 3 * 86400
        os.utime(old, (stale, stale))
        os.utime(worker, (stale, stale))
        removed = logs.prune_job_logs(self.dir, days=1)
        self.assertEqual(removed, ['job-old.log'])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(worker.exists(), '非任务日志不按保留期清理')

    def test_rotate_returns_false_when_under_or_missing(self):
        missing = self.dir / 'none.log'
        self.assertFalse(logs.rotate(missing, limit=10, keep=1))
        small = self.dir / 'small.log'
        small.write_text('abc', encoding='utf-8')
        self.assertFalse(logs.rotate(small, limit=10, keep=1))


if __name__ == '__main__':
    unittest.main()
