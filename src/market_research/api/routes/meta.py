"""Health and catalogue routes."""
from fastapi import APIRouter, Response
import json
import os
import time
from datetime import date
import market_research.config as C
import market_research.disclosure as disclosure
from market_research.data import storage
from market_research.api.schemas import HealthResponse, ReadyResponse
from market_research.api.version import API_VERSION, SERVICE_VERSION
from market_research.resources import limits_snapshot
from market_research.security import access_mode, mode_description
from market_research.modeling.catalog import MODEL_ORDER, model_catalog
from market_research.market.analysis import STOCKS, INDICES
from market_research.tasks.jobs import JOBS
router=APIRouter()
_STARTED = time.time()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _storage_check() -> dict:
    """P2-25：真的连一次 DuckDB，而不是只看进程还在不在。"""
    started = time.perf_counter()
    try:
        with storage.connection(timeout=2.0) as db:
            db.execute("SELECT 1").fetchone()
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:                               # noqa: BLE001
        return {"ok": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _worker_check() -> dict:
    path = C.DATA_DIR / "worker.pid"
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return {"ok": False, "pid": None, "reason": "worker.pid 不存在或不可读"}
    alive = _pid_alive(pid)
    return {"ok": alive, "pid": pid,
            "reason": None if alive else f"worker 进程 {pid} 已退出"}


def _jobs_check() -> dict:
    try:
        counts = JOBS.stats() if hasattr(JOBS, "stats") else {}
        return {"ok": True, **counts}
    except Exception as exc:                               # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _sync_check() -> dict:
    if os.environ.get("FIL_SYNC_ENABLED", "1") == "0":
        return {"ok": True, "enabled": False}
    path = C.DATA_DIR / "valuation_sync.json"
    if not path.exists():
        return {"ok": False, "enabled": True, "last_success": None,
                "reason": "从未成功同步估值面板"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        last = state.get("last_success")
    except (OSError, ValueError) as exc:
        return {"ok": False, "enabled": True, "last_success": None,
                "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:
        age = (date.today() - date.fromisoformat(str(last)[:10])).days
    except ValueError:
        return {"ok": False, "enabled": True, "last_success": last,
                "reason": f"无法解析同步时间：{last}"}
    limit = C.DATA_STALE_DAYS * 2
    ok = age <= limit
    return {"ok": ok, "enabled": True, "last_success": last, "age_days": age,
            "reason": None if ok else f"估值面板已 {age} 天未成功同步（阈值 {limit} 天）"}


@router.get('/health',response_model=HealthResponse)
def health():
    """存活探针：只证明 API 进程还在，依赖故障看 /api/ready。"""
    return {"ok": True, "version": SERVICE_VERSION, "api_version": API_VERSION,
            "framework": "fastapi",
            "models": len(MODEL_ORDER), "pid": os.getpid(),
            "uptime_s": round(time.time() - _STARTED, 1),
            "environment": C.ENV,
            "access_control": {"mode": access_mode(),
                               "description": mode_description()},
            "probe": "liveness"}


@router.get('/ready',response_model=ReadyResponse)
def ready(response: Response):
    """就绪探针：区分「进程活着」与「依赖不可用」。"""
    checks = {"storage": _storage_check(), "worker": _worker_check(),
              "jobs": _jobs_check(), "sync": _sync_check(),
              "lock": dict(storage.LOCK_STATS),
              "limits": {"ok": True, **limits_snapshot()}}
    required = [checks["storage"], checks["worker"], checks["jobs"]]
    status = "unhealthy" if any(not c["ok"] for c in required) else (
        "degraded" if not checks["sync"]["ok"] else "ok")
    if status == "unhealthy":
        response.status_code = 503
    return {"ok": status != "unhealthy", "status": status, "checks": checks,
            "version": SERVICE_VERSION, "api_version": API_VERSION,
            "uptime_s": round(time.time() - _STARTED, 1),
            "probe": "readiness"}

@router.get('/disclosure')
def disclosure_info():
    """P2-36：Web 页与报告共用的数据/免责声明（文案与位置可配置）。"""
    return {"ok": True, **disclosure.payload()}


@router.get('/catalog')
def catalog():
    known = []
    for d in (C.DEMO_FUNDS, C.EXTENDED_FUNDS):
        for k, v in d.items():
            if not any(x["code"] == k for x in known):
                known.append({"code": k, "name": v})
    return {
        "assets": {"stocks": [{"code": k, "name": v} for k, v in STOCKS.items()],
                   "indices": [{"code": k, "name": v} for k, v in INDICES.items()]},
        "models": model_catalog(),
        "default_models": ["enet_slsqp", "lasso", "ridge", "corr_topk", "pcr"],
        "quick_models": ["enet_slsqp", "corr_topk", "pcr", "rf", "krr"],
        "full_models": MODEL_ORDER,
        "known_funds": known,
        "candidate_pool": {
            "宽基": C.BROAD, "市值风格": C.SIZE, "价值成长": C.STYLE,
            "行业": len(C.SW_INDUSTRY),
        },
        "rbsa": {"window": C.RBSA["window"], "refit": C.RBSA["refit"],
                 "equity_ratio": C.RBSA["equity_ratio"],
                 "min_weight": C.RBSA["min_weight"],
                 "max_rebal": C.RBSA.get("max_rebal"),
                 "auto_equity": C.RBSA.get("auto_equity", False),
                 "equity_bounds": C.RBSA.get("equity_bounds", (0.5, 1.0))},
    }
