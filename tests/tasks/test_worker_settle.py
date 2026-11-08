"""worker 内置台账结算调度：开关、节流与失败隔离的离线回归。"""
import os
import unittest
from unittest.mock import MagicMock, patch

from market_research.tasks import worker


class SettleIntervalTests(unittest.TestCase):
    def test_default_and_floor(self):
        with patch.dict(os.environ, {'FIL_PREDICTION_SETTLE_MINUTES': '0'}, clear=False):
            self.assertEqual(worker._settle_interval_seconds(), 60.)
        with patch.dict(os.environ, {'FIL_PREDICTION_SETTLE_MINUTES': 'bad'}, clear=False):
            self.assertEqual(worker._settle_interval_seconds(), 3600.)
        with patch.dict(os.environ, {'FIL_PREDICTION_SETTLE_MINUTES': '15'}, clear=False):
            self.assertEqual(worker._settle_interval_seconds(), 900.)


class SettleScheduleTests(unittest.TestCase):
    def test_disabled_or_once_never_launches(self):
        with patch.object(worker, 'launch') as launch:
            with patch.dict(os.environ, {'FIL_PREDICTION_SETTLE_ENABLED': '0'}, clear=False):
                self.assertEqual(worker._advance_settle(None, 0.), (None, 0.))
            self.assertEqual(worker._advance_settle(None, 0., once=True), (None, 0.))
        launch.assert_not_called()

    def test_launches_cli_settle_once_while_running(self):
        child = MagicMock()
        child.poll.return_value = None
        with patch.object(worker, 'launch', return_value=child) as launch, \
             patch.object(worker.time, 'time', return_value=1000.):
            first, nxt = worker._advance_settle(None, 900.)
            second, _ = worker._advance_settle(first, nxt)
        self.assertIs(first, child)
        self.assertIs(second, child)
        self.assertEqual(nxt, 900.)
        self.assertEqual(launch.call_count, 1)
        launch.assert_called_with('market_research.cli', 'predictions', 'settle',
                                  log_name='prediction-settle.log')

    def test_finished_child_reschedules(self):
        finished = MagicMock()
        finished.poll.return_value = 0
        with patch.object(worker, 'launch') as launch, \
             patch.object(worker.time, 'time', return_value=1000.):
            child, nxt = worker._advance_settle(finished, 900.)
        # 子进程结束后先等一个间隔，避免异常退出时反复重启。
        self.assertIsNone(child)
        self.assertEqual(nxt, 4600.)
        launch.assert_not_called()

    def test_launch_failure_does_not_raise(self):
        with patch.object(worker, 'launch', side_effect=OSError('spawn failed')), \
             patch.object(worker.time, 'time', return_value=1000.), \
             patch.object(worker, 'LOG'):
            child, nxt = worker._advance_settle(None, 0.)
        self.assertIsNone(child)
        self.assertEqual(nxt, 4600.)


class ChildStopTests(unittest.TestCase):
    """109：僵尸子进程的 wait 超时不得炸掉整个监督循环。"""
    def test_timeout_is_logged_and_swallowed(self):
        import subprocess
        child = MagicMock()
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired(cmd="job", timeout=10)
        with patch.object(worker, 'terminate_tree'), patch.object(worker, 'LOG'):
            self.assertFalse(worker._stop_child(child, 'job-1', timeout=0.01))

    def test_terminate_failure_does_not_raise(self):
        child = MagicMock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with patch.object(worker, 'terminate_tree', side_effect=OSError('denied')), \
             patch.object(worker, 'LOG'):
            self.assertTrue(worker._stop_child(child, 'job-1'))

    def test_finished_child_is_not_terminated_again(self):
        child = MagicMock()
        child.poll.return_value = 0
        with patch.object(worker, 'terminate_tree') as kill:
            self.assertTrue(worker._stop_child(child, 'job-1'))
        kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
