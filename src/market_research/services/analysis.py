"""Application orchestration, shared by HTTP adapters and worker processes."""
import hashlib
import json
import logging
import pickle
import time

import market_research.audit as audit
import market_research.config as C
from market_research.research.runinfo import config_hash, result_provenance
from market_research.security import current_identity
import market_research.data.marketdata as D
import market_research.tasks.sync as sync
from market_research.tasks import checkpoints
from market_research.tasks.jobs import JOBS
from market_research.market.analysis import analyze_market
CACHE_DIR = C.DATA_DIR / "webcache"
# 当前版本结果缓存按 mtime 淘汰：每次分析都会为新的参数组合写一份 .pkl，
# 不清理会无限累积（版本清扫只删非当前版本）。
RESULT_CACHE_TTL_DAYS = 30

def analyze(**arguments):
    from market_research.funds.analysis import analyze as calculate
    return calculate(**arguments)

def backtest_fund(**arguments):
    from market_research.funds.analysis import backtest_fund as calculate
    return calculate(**arguments)

def refit_single(**arguments):
    from market_research.funds.analysis import refit_single as calculate
    return calculate(**arguments)

def holdings(code, refresh=False, job_id=None, checkpoint=None, *,
             start=None, end=None, auto_equity=None):
    """真实持仓拟合：数据、RBSA、对齐与持仓基准都跑在任务队列里。"""
    from market_research.funds.holdings import fit_fund_holdings, load_industry_map
    from market_research.funds.payloads import holdings_payload

    cp = checkpoint or checkpoints.NullCheckpoint()
    industry_map = cp.load("industry_map")
    if industry_map is None:
        if job_id:
            JOBS.update(job_id, stage="加载申万行业映射", progress=0.12)
        industry_map = load_industry_map(refresh=refresh)
        if industry_map.empty:
            return holdings_payload({"ok": False, "code": code,
                                     "reason": "申万行业映射构造失败（无法取得成分股）"})
        cp.save("industry_map", industry_map)
    if job_id:
        JOBS.update(job_id, stage="加载持仓与行情并拟合", progress=0.4)
    fitted = cp.load("fit")
    if fitted is None:
        fitted = fit_fund_holdings(code, industry_map, refresh=refresh,
                                   start=start, end=end, auto_equity=auto_equity)
        cp.save("fit", fitted)
    return holdings_payload(fitted)


def correlation(**arguments):
    """滚动相关重算（P1-13）：在任务队列内执行，可取消、占计算名额。

    与 analyze 不同，这里不做结果缓存：数据缓存预热后计算只是毫秒级内存
    操作，任务结果本身已随 job 落库（TTL 内可重复读取）。
    """
    from market_research.funds.payloads import correlation_block
    jid = arguments.get("job_id")
    code = arguments["code"]
    start = arguments.get("start")
    end = arguments.get("end")
    win = int(arguments.get("win") or 120)
    if jid:
        JOBS.update(jid, stage="加载数据", progress=0.1)
    panel = D.load_panel(code, start=start or C.START_DATE, end=end,
                         verbose=False)
    if jid and JOBS.is_cancelled(jid):
        # 取消后不再计算；finish() 对已取消任务是无操作（状态机只认 running）
        return {"ok": False, "cancelled": True}
    if jid:
        JOBS.update(jid, stage="计算滚动相关", progress=0.6)
    result = correlation_block(panel, panel["__FUND__"],
                               roll_n=max(20, min(win, 750)))
    return {"ok": True, "code": code, "correlation": result,
            # P2-27/P2-28：轻量接口同样要带出过期/缺失/口径降级
            "data_quality": dict(getattr(panel, "attrs", {}).get("data_quality") or {}),
            "data_warnings": list(getattr(panel, "attrs", {}).get("warnings") or [])}

from market_research.data.files import _atomic_pickle_dump

LOG = logging.getLogger(__name__)
RUNNER = JOBS


def result_cache_path(code, models, params, max_rebal=None, auto_equity=False, data_stamp="", start="", end=""):
    key = json.dumps({"v":C.CACHE_VERSION,"g":config_hash(),"c":code,"m":sorted(models or []),"p":params or {},
                      "r":C.RBSA.get("max_rebal") if max_rebal is None else max_rebal,
                      "a":bool(auto_equity),"d":data_stamp,"s":start or "","e":end or ""},sort_keys=True,ensure_ascii=False)
    return CACHE_DIR / f"result_v{C.CACHE_VERSION}_{code}_{hashlib.md5(key.encode()).hexdigest()[:10]}.pkl"


def _fetch_window(params):
    """复现 load_panel 的取数窗口：抓取起点 + 结束日（含全局配置回落）。"""
    start = params.get("start") or C.START_DATE
    end = params.get("end")
    if end is None:
        end = C.END_DATE
    return D.fetch_start_for(start), end


def fund_cache_path(params, data_stamp=None):
    fs, end = _fetch_window(params)
    stamp = data_stamp if data_stamp is not None else D.data_stamp(
        exclude_stocks=True, fund_code=params["code"], fetch_start=fs, end=end)
    return result_cache_path(params["code"],params["models"],params["params"],params.get("max_rebal"),params.get("auto_equity",False),
                             stamp,
                             params.get("start"),params.get("end"))


def _data_stamp_for(operation, params):
    """结果所依赖的数据版本（P2-34）。

    基金类操作收窄到本基金本窗口的缓存（与结果缓存键同一口径），
    市场/持仓类操作依赖全局数据集，用全局指纹。
    """
    code = params.get("code")
    if operation in ("analyze", "backtest", "refit", "corr") and code:
        fs, end = _fetch_window(params)
        return D.data_stamp(exclude_stocks=True, fund_code=code,
                            fetch_start=fs, end=end)
    return D.data_stamp()


_CLEANED = False


def _cleanup_stale_results() -> None:
    """
    结果缓存清理：
      1. 删除**非当前版本**的 .pkl（config.py 曾声称"旧版缓存会自动清理"
         但没有任何代码做清理 —— v7/v8 长期滞留）；
      2. 当前版本超过 `RESULT_CACHE_TTL_DAYS` 未再命中的也删除 ——
         每次分析都为新的参数组合写一份缓存，不清理会无限累积。
    结果都可重算，删除只影响下次首跑耗时；数据缓存（fund_nav 等）不在此列。
    """
    global _CLEANED
    if _CLEANED:
        return
    _CLEANED = True
    prefix = f"result_v{C.CACHE_VERSION}_"
    try:
        now = time.time()
        for path in CACHE_DIR.glob("result_v*.pkl"):
            if not path.name.startswith(prefix):
                path.unlink(missing_ok=True)      # 非当前版本
            elif (now - path.stat().st_mtime
                  > RESULT_CACHE_TTL_DAYS * 86400):
                path.unlink(missing_ok=True)      # 当前版本但长期未用
    except OSError:
        LOG.warning("清理旧结果缓存失败（不影响本次分析）", exc_info=True)


def submit(operation, params):
    _cleanup_stale_results()
    if operation == "analyze":
        basis = params.get("nav_basis")
        if basis not in (D.NAV_BASIS_TOTAL, D.NAV_BASIS_CUM):
            basis = D.NAV_BASIS_TOTAL
        sync.register("fund", params["code"], basis)
        path = fund_cache_path(params)
        if not params.get("refresh") and path.exists():
            try:
                with path.open("rb") as file:
                    payload=pickle.load(file)
                payload.setdefault("meta",{})["from_cache"]=True
                # 清理按 mtime 判"长期未用"，命中只读不会更新 mtime，
                # 热点缓存每天被读也会在 TTL 后被误删。命中即续期。
                try:
                    path.touch()
                except OSError:
                    LOG.warning("结果缓存命中时间刷新失败（不影响本次结果）",
                                exc_info=True)
                return {"ok":True,"result":payload,"cached":True}
            except Exception:
                LOG.exception("结果缓存读取失败，将重新分析")
        identity = path.name
    else:
        identity = operation
    key=json.dumps([identity,params],sort_keys=True,ensure_ascii=False)
    models=params.get("models") or [params.get("model") or params.get("kind",operation)]
    jid,merged=RUNNER.submit(params["code"],models,operation,params,key=key)
    return {"ok":True,"job_id":jid,"cached":False,"merged":merged}


def _audit_summary(operation, params, result):
    """审计只写白名单摘要：不落参数原文与行情数值（P2-26）。"""
    detail = {"operation": operation, "code": params.get("code"),
              "job_id": params.get("job_id")}
    identity = current_identity()
    if identity.authenticated:
        detail["actor"] = identity.subject
        detail["tenant"] = identity.tenant
    if isinstance(result, dict):
        detail["status"] = result.get("status")
        completeness = result.get("completeness")
        if isinstance(completeness, dict):
            detail["completeness"] = completeness.get("status")
            detail["missing"] = [item.get("item") or item.get("section")
                                 for item in (completeness.get("missing") or [])][:8]
        models = result.get("models")
        if isinstance(models, dict):
            detail["models"] = len(models)
        if "ok" in result:
            detail["ok"] = bool(result["ok"])
        if result.get("reason"):
            detail["reason"] = str(result["reason"])[:120]
    return detail


def execute(operation, params, jid):
    started = time.monotonic()
    try:
        result = _dispatch(operation, params, jid)
    except Exception as exc:
        audit.record("analysis.run", ok=False,
                     subject=f"{operation}:{params.get('code', '')}",
                     detail={"operation": operation, "code": params.get("code"),
                             "job_id": jid},
                     error=f"{type(exc).__name__}: {str(exc)[:200]}",
                     duration_s=time.monotonic() - started)
        raise
    detail = _audit_summary(operation, {**params, "job_id": jid}, result)
    audit.record("analysis.run", ok=bool(result.get("ok", True)),
                 subject=f"{operation}:{params.get('code', '')}",
                 detail=detail, duration_s=time.monotonic() - started)
    return result


def _stamp(result, data_stamp=None):
    """P2-34：给所有操作结果挂可追溯指纹；缓存文件里也保留当时的版本。"""
    if isinstance(result, dict):
        meta = result.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            result["meta"] = meta
        meta["provenance"] = result_provenance(data_stamp)
    return result


_CHECKPOINT_OPERATIONS = ("analyze", "refit", "backtest", "holdings")


def _checkpoint_for(operation, params, jid, stamp):
    """P3-F：为长任务建阶段检查点；行情/相关性等短任务用空实现。"""
    if not jid or operation not in _CHECKPOINT_OPERATIONS:
        return checkpoints.NullCheckpoint()
    fingerprint = {"operation": operation, "stamp": stamp,
                   "config_hash": config_hash(),
                   "args": {key: value for key, value in params.items()
                            if key != "job_id"}}
    return checkpoints.StageCheckpoint(jid, fingerprint)


def _dispatch(operation, params, jid):
    stamp_before = _data_stamp_for(operation, params)
    cp = _checkpoint_for(operation, params, jid, stamp_before)
    result = _dispatch_stage(operation, params, jid, cp, stamp_before)
    cp.clear()                       # 只有整体成功才丢弃阶段产物
    return result


def _record_predictions(operation, params, result):
    """把实盘最新收盘的预测写入台账；失败不影响分析结果。"""
    try:
        from market_research.predictions.ledger import record_result
        record_result(operation, params, result)
    except Exception:
        LOG.warning("预测台账写入失败，不影响分析结果", exc_info=True)


def _register_result_nav_basis(params, result):
    """按分析实际使用的净值口径登记同步资产；register 幂等。"""
    code = (params or {}).get("code")
    if not code:
        return
    basis = str(((result or {}).get("meta") or {}).get("nav_basis")
                or (params or {}).get("nav_basis") or D.NAV_BASIS_TOTAL)
    if basis not in (D.NAV_BASIS_TOTAL, D.NAV_BASIS_CUM):
        basis = D.NAV_BASIS_TOTAL
    try:
        sync.register("fund", code, basis)
    except Exception:                                      # noqa: BLE001
        LOG.warning("同步资产登记失败，不影响分析结果", exc_info=True)


def _dispatch_stage(operation, params, jid, checkpoint, stamp_before):
    if operation == "analyze":
        initial_path = fund_cache_path(params, stamp_before)
        result=_stamp(analyze(**params,job_id=jid,checkpoint=checkpoint), stamp_before)
        try:
            if fund_cache_path(params, _data_stamp_for(operation, params)) == initial_path:
                _atomic_pickle_dump(result,initial_path)
            else:
                LOG.info("分析期间行情发生变化，跳过结果缓存写入")
        except Exception:
            LOG.exception("分析完成，但缓存写入失败")
        _record_predictions("analyze", params, result)
        _register_result_nav_basis(params, result)
        return result
    if operation == "market":
        result = _stamp(analyze_market(**params,job_id=jid), stamp_before)
        _record_predictions("market", params, result)
        return result
    if operation == "corr":
        return _stamp(correlation(**params, job_id=jid), stamp_before)
    if operation == "refit":
        JOBS.update(jid,stage="重新学习模型",progress=.2)
        arguments=dict(params)
        arguments["model_key"]=arguments.pop("model")
        return _stamp(refit_single(**arguments,checkpoint=checkpoint), stamp_before)
    if operation == "backtest":
        arguments=dict(params)
        arguments["model_key"]=arguments.pop("model")
        arguments["start_date"]=arguments.pop("start",None)
        arguments["end_date"]=arguments.pop("end",None)
        result = _stamp(backtest_fund(**arguments,job_id=jid,checkpoint=checkpoint),
                        stamp_before)
        _record_predictions("backtest", params, result)
        return result
    if operation == "holdings":
        return _stamp(holdings(**params, job_id=jid, checkpoint=checkpoint),
                      stamp_before)
    raise ValueError("未知任务操作")
