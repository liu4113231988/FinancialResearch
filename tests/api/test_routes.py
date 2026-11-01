"""DuckDB 迁移、事务与路由资源生命周期的离线回归。"""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import market_research.config as C
import market_research.data.marketdata as D
from market_research.errors import CacheMiss
from market_research.tasks.jobs import JobStore, JobRunner
import market_research.services.analysis as web
from market_research.api.app import app
from fastapi.testclient import TestClient


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        class TestRunner(JobRunner):
            def submit(self, code, models, operation, arguments, key=None):
                return super().submit(code, models, lambda jid:web.execute(operation,arguments,jid), key=key)
        self.runner = TestRunner(JobStore(), max_jobs=1)
        self.patch = patch.object(web, "RUNNER", self.runner)
        self.patch.start()
        self.jobs = patch.object(web, "JOBS", self.runner.store)
        self.jobs.start()

    def tearDown(self):
        self.jobs.stop()
        self.patch.stop()

    def test_cache_identity(self):
        args = ("110022", ["ridge"], {})
        self.assertNotEqual(web.result_cache_path(*args, None), web.result_cache_path(*args, 0))

    def test_changed_data_does_not_cache_old_result_under_new_stamp(self):
        with patch.object(web, "fund_cache_path", side_effect=[Path("old.pkl"), Path("new.pkl")]), patch.object(web, "analyze", return_value={"meta": {}}), patch.object(web, "_atomic_pickle_dump") as save:
            self.assertEqual(web.execute("analyze", {}, "test"), {"meta": {}})
            save.assert_not_called()

    def test_cached_requests_do_not_consume_slots(self):
        from unittest.mock import Mock
        import io, pickle
        path = Mock()
        path.exists.return_value = True
        path.open.side_effect = lambda *a: io.BytesIO(pickle.dumps({"meta": {}}))
        with patch.object(web, "result_cache_path", return_value=path), patch.object(D, "data_stamp", return_value="test"):
            for _ in range(3):
                self.assertEqual(self.client.post("/api/analyze", json={"code": "110022"}).status_code, 200)
        self.assertTrue(self.runner._slots.acquire(False))
        self.runner._slots.release()

    def test_jobs_merge_only_identical_parameters(self):
        with patch("market_research.tasks.jobs.Thread"), patch.object(D, "data_stamp", return_value="test"):
            body = {"code": "110022", "models": ["ridge"], "params": {"ridge": {"alpha_scale": 1}}}
            first = self.client.post("/api/analyze", json=body)
            same = self.client.post("/api/analyze", json=body)
            self.assertEqual(first.json()["job_id"], same.json()["job_id"])
            body["params"]["ridge"]["alpha_scale"] = 2
            self.assertEqual(self.client.post("/api/analyze", json=body).status_code, 429)
            self.assertEqual(self.client.post("/api/refit", json={"code": "110022", "model": "ridge"}).status_code, 429)
            self.assertEqual(self.client.post("/api/backtest", json={"code": "110022"}).status_code, 429)

    def test_all_calculation_routes_execute_background_work(self):
        with tempfile.TemporaryDirectory(dir=C.ROOT / "output") as directory:
            with patch.object(web, "result_cache_path", return_value=Path(directory) / "result.pkl"), patch.object(D, "data_stamp", return_value="test"):
                for route, function, body in (
                    ("analyze", "analyze", {"code": "110022", "models": ["ridge"]}),
                    ("refit", "refit_single", {"code": "110022", "model": "ridge"}),
                    ("backtest", "backtest_fund", {"code": "110022"}),
                ):
                    with self.subTest(route=route), patch.object(web, function, return_value={"meta": {}, "case": route}) as work:
                        response = self.client.post("/api/" + route, json=body)
                        self.assertEqual(response.status_code, 200)
                        jid = response.json()["job_id"]
                        for thread in threading.enumerate():
                            if thread.name == "job-" + jid:
                                thread.join(timeout=5)
                        result = self.client.get("/api/result/" + jid)
                        self.assertEqual(result.status_code, 200)
                        self.assertEqual(result.json()["result"]["case"], route)
                        work.assert_called_once()
                        self.assertTrue(self.runner._slots.acquire(False))
                        self.runner._slots.release()

    def test_non_object_body(self):
        for route in ("analyze", "refit", "backtest"):
            self.assertEqual(self.client.post("/api/" + route, json=[1]).status_code, 400)

    def test_correlation_cold_cache_executes_as_queue_job(self):
        # P1-13 端到端：冷缓存 → 202 + 队列任务 → worker 执行 corr → 结果可读
        rng = np.random.default_rng(7)
        index = pd.date_range("2024-01-01", periods=60, freq="B")
        panel = pd.DataFrame({"__FUND__": np.exp(np.cumsum(rng.normal(0, .01, 60))),
                              "801010": np.exp(np.cumsum(rng.normal(0, .01, 60)))},
                             index=index)
        # 第一次调用（请求线程，只读）抛 CacheMiss；第二次（队列 worker）拿到面板
        with patch.object(D, "load_panel",
                          side_effect=[CacheMiss("冷缓存"), panel]) as load:
            response = self.client.get("/api/corr",
                                       params={"code": "110022", "win": 30})
            self.assertEqual(response.status_code, 202)
            jid = response.json()["job_id"]
            self.assertIs(load.call_args_list[0].kwargs["allow_fetch"], False)
            for thread in threading.enumerate():
                if thread.name == "job-" + jid:
                    thread.join(timeout=5)
            result = self.client.get("/api/result/" + jid)
        self.assertEqual(result.status_code, 200)
        self.assertIn("correlation", result.json()["result"])
        self.assertTrue(self.runner._slots.acquire(False))
        self.runner._slots.release()


class ReadOnlyRouteTests(unittest.TestCase):
    """请求线程内的端点只读缓存，不替用户联网取数（P1-13）。"""

    def setUp(self):
        self.client = TestClient(app)

    def test_correlation_cold_cache_defers_to_queue(self):
        # 冷缓存不再 409：取数与计算交回任务队列（可取消、占名额），
        # 请求线程仍然只读（allow_fetch=False），返回 202 + job_id。
        with patch.object(D, "load_panel", side_effect=CacheMiss("冷缓存")) as load, \
             patch.object(web, "RUNNER") as runner:
            # services.submit 解包 RUNNER.submit 的 (jid, merged)
            runner.submit.return_value = ("job123", False)
            response = self.client.get("/api/corr",
                                       params={"code": "110022", "win": 60})
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["job_id"], "job123")
        self.assertIs(load.call_args.kwargs["allow_fetch"], False)
        self.assertEqual(runner.submit.call_args.args[2], "corr")
        arguments = runner.submit.call_args.args[3]
        self.assertEqual(arguments["code"], "110022")
        self.assertEqual(arguments["win"], 60)

    def test_correlation_reports_queue_exhaustion(self):
        from market_research.errors import JobsBusy
        with patch.object(D, "load_panel", side_effect=CacheMiss("冷缓存")), \
             patch.object(web, "RUNNER") as runner:
            runner.submit.side_effect = JobsBusy("待处理任务已达上限")
            response = self.client.get("/api/corr", params={"code": "110022"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("上限", response.json()["error"])

    def test_correlation_computes_from_already_loaded_panel(self):
        rng = np.random.default_rng(7)
        index = pd.date_range("2024-01-01", periods=60, freq="B")
        panel = pd.DataFrame({"__FUND__": np.exp(np.cumsum(rng.normal(0, .01, 60))),
                              "801010": np.exp(np.cumsum(rng.normal(0, .01, 60)))},
                             index=index)
        with patch.object(D, "load_panel", return_value=panel) as load:
            response = self.client.get("/api/corr", params={"code": "110022", "win": 30})
        self.assertEqual(response.status_code, 200)
        self.assertIs(load.call_args.kwargs["allow_fetch"], False)
        self.assertIn("correlation", response.json())

    def test_name_route_never_blocks_on_lookup(self):
        with patch("market_research.funds.analysis._cached_name",
                   return_value=(None, "unknown")) as cached:
            response = self.client.get("/api/name/110022")
        self.assertEqual(response.status_code, 200)
        # 未查 = found 为 None；前端只在 found=False 时提示"代码可能有误"
        self.assertIsNone(response.json()["found"])
        self.assertTrue(response.json()["deferred"])
        cached.assert_called_once_with("110022")

    def test_name_route_still_reports_a_known_absent_code(self):
        with patch("market_research.funds.analysis._cached_name",
                   return_value=(None, "absent")):
            response = self.client.get("/api/name/999999")
        self.assertIs(response.json()["found"], False)
        self.assertFalse(response.json()["deferred"])


class NameLookupTests(unittest.TestCase):
    def test_read_only_lookup_never_reaches_the_network(self):
        from market_research.funds.analysis import _guess_name
        with patch("market_research.funds.analysis._cache_load", return_value=None), \
             patch("market_research.data.marketdata.http_timeout",
                   side_effect=AssertionError("请求线程内不应联网")):
            self.assertIsNone(_guess_name("110022", allow_network=False))

    def test_cached_name_separates_absent_from_unknown(self):
        from market_research.funds import analysis
        future = f"{analysis._NAME_NEG_PREFIX}{analysis.time.time():.0f}"
        with patch.object(analysis, "_cache_load", return_value="易方达消费行业"):
            self.assertEqual(analysis._cached_name("110022"), ("易方达消费行业", "hit"))
        with patch.object(analysis, "_cache_load", return_value=future):
            self.assertEqual(analysis._cached_name("999999"), (None, "absent"))
        expired = f"{analysis._NAME_NEG_PREFIX}{analysis.time.time() - analysis._NAME_NEG_TTL - 60:.0f}"
        with patch.object(analysis, "_cache_load", return_value=expired):
            self.assertEqual(analysis._cached_name("999999"), (None, "unknown"))


if __name__ == "__main__":
    unittest.main()
