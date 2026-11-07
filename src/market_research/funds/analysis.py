# -*- coding: utf-8 -*-
"""
分析引擎：为 Web 前端组装一份完整的、可序列化的分析结果。

一次 analyze() 调用产出四组内容：
    1. K 线/净值序列（基金 + 各模型合成基准）
    2. 与候选指数的相关性分析（矩阵 + Top 榜 + 滚动相关性）
    3. 各机器学习模型的拟合结果（样本外评估 + 滚动权重）
    4. 基于上述证据生成的自然语言分析结论

设计要点
--------
* 结果以 JSON 友好的字典返回，数值统一转成原生类型（NaN → None）。
* 数据与拟合结果都可缓存，避免每次请求都重算（重算是分钟级操作）。
* 允许只跑部分模型（前端勾选），也允许对单个模型重算并替换（重学习）。
"""
from __future__ import annotations

import math
import time
import warnings
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import market_research.config as C
import market_research.data.marketdata as D
from market_research.data.files import _atomic_pickle_dump
from market_research.modeling.backtest import run_backtest, score_to_position
from market_research.modeling.models import MODELS, MODEL_ORDER, ModelZoo
from market_research.modeling.signals import SignalEngine, build_decision
from market_research.tasks.checkpoints import NullCheckpoint

CACHE_DIR = C.DATA_DIR / "webcache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def nav_basis_meta(attrs: dict) -> dict:
    """把净值口径转成结果 meta；累计净值不得继续宣称总回报口径。"""
    attrs = attrs or {}
    nav_basis = str(attrs.get("nav_basis") or "total")
    nav_degraded = bool(attrs.get("nav_degraded"))
    nav_reason = str(attrs.get("nav_degraded_reason") or "")
    if nav_basis == "cum":
        data_basis = ("累计净值（total 构造失败，已降级）；不可按总回报解读"
                      if nav_degraded else
                      "累计净值（未还原分红/拆分）；仅形态与相对强弱参考，收益口径偏低")
    else:
        data_basis = "事后月度锚点校准；尚非历史时点可得数据"
    return {"data_basis": data_basis,
            "nav_basis": nav_basis,
            "nav_degraded": nav_degraded,
            "nav_degraded_reason": nav_reason or None}


def result_completeness(*, payload: dict, correlation: dict, forecast: dict,
                        valuation: dict, drift: dict,
                        quality: dict | None = None) -> dict:
    """结果完整性契约（P2-31）：完成/部分/失败 + 缺失项 + 原因。

    各接口与报告统一读取同一份结构；可选章节（估值/方向概率/漂移）缺失
    记为 unavailable 并计入 partial，不影响已产出数值。
    """
    missing: list = []
    sections: dict = {}

    models = list(payload or {})
    failed = [k for k in models
              if (payload[k].get("extra") or {}).get("error")
              or (payload[k].get("metrics") or {}).get("oos_r2") is None]
    sections["models"] = {
        "status": ("failed" if models and len(failed) == len(models)
                   else "partial" if failed else "ok"),
        "completed": len(models) - len(failed),
        "total": len(models),
    }
    for key in failed:
        reason = (payload[key].get("extra") or {}).get("error") or "样本外评估缺失"
        missing.append({"section": "models", "item": key, "reason": str(reason)[:200]})

    def optional(section: str, ok: bool, reason: str) -> None:
        sections[section] = {"status": "ok" if ok else "unavailable"}
        if not ok:
            missing.append({"section": section, "item": section, "reason": reason})

    optional("correlation", bool((correlation or {}).get("top")),
             "无可用候选指数或收益重叠不足")
    optional("forecast", bool((forecast or {}).get("available")),
             (forecast or {}).get("reason") or "方向概率不可用")
    optional("valuation", bool((valuation or {}).get("available")),
             (valuation or {}).get("note") or (valuation or {}).get("reason") or "估值数据不可用")
    optional("style_drift", bool((drift or {}).get("ok")),
             (drift or {}).get("reason") or "风格漂移未计算")

    data_status = str((quality or {}).get("status") or "ok")
    sections["data"] = {"status": data_status}

    fatal = sections["models"]["status"] == "failed"
    status = "failed" if fatal else (
        "partial" if missing or data_status not in ("ok",) else "ok")
    reasons = [m["reason"] for m in missing]
    if data_status not in ("ok",):
        reasons.extend((quality or {}).get("reasons") or [])
    return {"status": status, "ok": status != "failed",
            "sections": sections, "missing": missing, "reasons": reasons}


# 兼容现有脚本导入；实现按职责放在独立模块。
from market_research.tasks.jobs import JOBS, JobStore
from market_research.validation.parameters import _finite, _parse_max_rebal, _parse_auto_equity, _check_params, _parse_window
from market_research.funds.payloads import _jnum, _jsafe, _dates, correlation_block, nav_block, model_payload, build_insights, _backtest_payload, _weights_payload, _weights_hist_payload, _alpha_stats, fund_forecast_payload


def _cache_path(code: str, tag: str) -> Path:
    return CACHE_DIR / f"{code}_{tag}.pkl"


def _cache_load(code: str, tag: str):
    p = _cache_path(code, tag)
    if not p.exists():
        return None
    try:
        import pickle
        with p.open("rb") as f:
            return pickle.load(f)
    except Exception:                      # noqa: BLE001
        return None


def _cache_save(code: str, tag: str, obj):
    try:
        _atomic_pickle_dump(obj, _cache_path(code, tag))
    except Exception:                      # noqa: BLE001
        pass


# ====================================================================== 主流程
def analyze(code: str, models: Optional[List[str]] = None,
            params: Optional[dict] = None, refresh: bool = False,
            job_id: Optional[str] = None,
            max_rebal: Optional[int] = None,
            auto_equity: bool = True,
            start: Optional[str] = None,
            end: Optional[str] = None,
            checkpoint=None) -> dict:
    """
    完整分析流程。models 为空表示全部 11 个模型。

    start / end 是**分析窗口**（默认近 5 年，见 C.DEFAULT_WINDOW_YEARS）。
    抓取仍走固定长区间（C.FETCH_START_DATE），拿到后再裁剪 —— 见
    data.fetch_start_for()：缓存名带 start，让它跟着窗口滚动会让缓存全 miss。
    """
    models = models or MODEL_ORDER
    models = [m for m in models if m in MODELS]
    t0 = time.time()
    cp = checkpoint or NullCheckpoint()

    def prog(stage, pct, detail=""):
        if job_id:
            JOBS.update(job_id, stage=stage, progress=pct, detail=detail)

    # ---------- 0. 数据
    prog("抓取基金与指数数据", 0.05)
    win_start, win_end = _parse_window(start, end)
    panel = cp.load("panel")
    if panel is None:
        panel = D.load_panel(code, start=(win_start or C.START_DATE),
                             end=win_end, refresh=refresh, verbose=False)
        cp.save("panel", panel)
    nav = panel["__FUND__"]
    name = (C.DEMO_FUNDS.get(code) or C.EXTENDED_FUNDS.get(code) or
            _guess_name(code))
    prog("数据就绪", 0.15, f"{panel.shape[0]} 交易日 × {panel.shape[1]-1} 指数")

    # ---------- 1. 净值
    prog("计算净值序列与回撤", 0.22)
    navb = cp.load("nav")
    if navb is None:
        navb = nav_block(panel, nav)
        cp.save("nav", navb)
    # 方向概率：与趋势色带（形态描述）分开发布，样本不足时 available=False
    prog("估计净值方向概率", 0.26)
    forecast = cp.load("forecast")
    if forecast is None:
        forecast = fund_forecast_payload(nav)
        cp.save("forecast", forecast)

    # ---------- 2. 相关性
    prog("计算与指数的相关性", 0.32)
    corr = cp.load("correlation")
    if corr is None:
        corr = correlation_block(panel, nav)
        cp.save("correlation", corr)

    # ---------- 3. 多模型拟合（耗时主体，占 0.35~0.9）
    reps = cp.load("models")
    if reps is None:
        prog("机器学习拟合中", 0.35, f"{len(models)} 个模型")

        def on_progress(key, done, total, r2):
            JOBS.update(job_id, progress=0.35 + 0.5 * done / max(total, 1),
                        stage=f"拟合 {MODELS[key].name}",
                        detail=f"({done}/{total}) 样本外R²="
                               f"{'—' if r2 is None or (isinstance(r2, float) and not math.isfinite(r2)) else f'{r2:.3f}'}")

        zoo_cfg = {**C.RBSA, "auto_equity": bool(auto_equity)}
        zoo = ModelZoo(zoo_cfg)
        reps = zoo.run(panel, models, params=params, max_rebal=max_rebal,
                       progress=on_progress if job_id else None)
        cp.save("models", reps)
    else:
        prog("复用已完成阶段：模型拟合", 0.88, "崩溃重试跳过已完成阶段")
        zoo = ModelZoo({**C.RBSA, "auto_equity": bool(auto_equity)})

    # ---------- 4. 序列化
    prog("组装结果", 0.9)
    R = np.log(panel.astype(float)).diff().dropna()
    nav_oos = nav.reindex(R.index[C.RBSA["window"]:])
    payload = {k: model_payload(r, nav_oos) for k, r in reps.items()}
    rank = ModelZoo.rank(reps)

    # ---------- 4.5 风格漂移（2026-10-12）
    # 分析窗口从固定 10 年改成近 5 年，就是因为这个：十年里风格与经理可能都变过，
    # 混在一起拟合出的画像是历史平均，对当下没指导意义。
    # 经理变更数据取不到（fund_manager_em 只有现任、无任职日期），所以改为
    # 直接检测**风格本身**是否漂移 —— 权重序列是现成产物，且比经理更贴近问题。
    drift: dict = {"ok": False, "reason": "无可用模型结果"}
    try:
        from market_research.research.experiments import style_drift as _style_drift
        best_key = None
        if not rank.empty and "key" in rank.columns:
            for _, top in rank.iterrows():
                if reps[top["key"]].interpretable and pd.notna(top.get("样本外R²", np.nan)):
                    best_key = top["key"]
                    break
        if best_key is None:
            best_key = next((k for k, r in reps.items() if r.interpretable), None)
        if best_key in reps:
            rep = reps[best_key]
            syn_nav_d = np.exp(rep.syn_ret.dropna().cumsum())
            d = _style_drift(rep.weights_hist, nav=nav, syn_nav=syn_nav_d,
                             name_of=getattr(C, "NAME_OF", {}))
            d["model"] = best_key
            d["model_name"] = (MODELS[best_key].name
                               if best_key in MODELS else best_key)
            drift = d
    except Exception as e:                                   # noqa: BLE001
        # 漂移检测是**诊断增强**，失败不能拖垮整个分析
        drift = {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}

    prog("生成分析结论", 0.96)
    insights = build_insights(code, name, navb, corr, reps, rank, payload)

    # 估值口径：用当前"最佳可解释模型"的权重做一次通道，供页面标注兜底。
    valuation: dict = {"available": False, "degraded": False,
                       "fallback_assets": [], "note": None}
    try:
        bk = locals().get("best_key")
        if bk is None:
            bk = next((k for k, r in reps.items() if r.interpretable), None)
        if bk in reps:
            pe_pct = D.load_pe_panel(verbose=False)
            v = SignalEngine().valuation_channel(reps[bk].weights_hist, pe_pct, nav.index)
            valuation = dict(v.attrs.get("valuation") or {})
            valuation["model"] = bk
    except Exception as exc:                            # noqa: BLE001
        valuation = {"available": False, "degraded": True,
                     "fallback_assets": [], "coverage": None,
                     "fallback_weight": None,
                     "note": "估值数据不可用，无法判断行业估值覆盖率",
                     "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}

    completeness = result_completeness(
        payload=payload, correlation=corr, forecast=forecast,
        valuation=valuation, drift=drift,
        quality=dict(getattr(panel, "attrs", {}).get("data_quality") or {}))

    result = {
        "code": code, "name": name,
        "status": completeness["status"], "ok": completeness["ok"],
        "completeness": completeness,
        "models": payload,
        "rank": _jsafe(rank.to_dict("records")),
        "nav": navb,
        "forecast": forecast,
        "valuation": _jsafe(valuation),
        "correlation": corr,
        "insights": insights,
        "style_drift": drift,
        "meta": {
            "n_days": int(panel.shape[0]),
            "n_assets": int(panel.shape[1] - 1),
            "start": panel.index[0].strftime("%Y-%m-%d"),
            "end": panel.index[-1].strftime("%Y-%m-%d"),
            "models": models,
            "params": _jsafe(params or {}),
            "elapsed": round(time.time() - t0, 1),
            "window": C.RBSA["window"],
            "refit": C.RBSA["refit"],
            "max_rebal": (max_rebal if max_rebal is not None
                          else C.RBSA.get("max_rebal")),
            # 前缀稳定性（P0-04/P1 修复轮）：calendar 锚点只由日期决定，
            # 限流也只从头部丢弃（保留尾部）→ 抽稀与否都稳定；
            # linspace 只有完全不抽稀（max_rebal=0）时才稳定。
            "rebal_mode": zoo.rebal_mode,
            "prefix_stable": bool(zoo.rebal_mode == "calendar" or
                                  not (max_rebal if max_rebal is not None
                                       else zoo.max_rebal)),
            "n_rebal": int(len(reps[next(iter(reps))].weights_hist))
            if reps else 0,
            "equity_ratio": C.RBSA["equity_ratio"],
            "auto_equity": bool(auto_equity),
            "best_model": (rank.iloc[0]["key"] if not rank.empty and
                           pd.notna(rank.iloc[0]["样本外R²"]) else None),
            # ---- 分析窗口（2026-10-12 新增）
            # start/end 是**实际生效**的窗口（已裁剪）。它默认近 5 年而不是
            # 原来的固定 2016 起：十年跨度会混入风格漂移与经理变更，
            # 拟合出的画像是历史平均，对当下没有指导意义。
            "window_years": round(
                (panel.index[-1] - panel.index[0]).days / 365.25, 2),
            "window_default_years": C.DEFAULT_WINDOW_YEARS,
            "window_min_years": C.MIN_WINDOW_YEARS,
            **nav_basis_meta(getattr(panel, "attrs", {})),
            # 价格覆盖降级（剔除停编指数、连续缺失截断）必须透传到界面，
            # 否则用户会以为用的是完整候选池
            "data_warnings": list(getattr(panel, "attrs", {}).get("warnings") or []),
            # P2-27：统一的数据质量（stale/partial/degraded + 原因 + 缺失清单）
            "data_quality": dict(getattr(panel, "attrs", {}).get("data_quality") or {}),
            # P2-28：候选池缺失必须显式可见，避免用户以为用了完整候选
            "missing_assets": list(
                getattr(panel, "attrs", {}).get("data_quality", {}).get("missing_assets") or []),
            "n_assets_expected": getattr(panel, "attrs", {}).get(
                "data_quality", {}).get("n_expected"),
            "window_requested_end": win_end,
            "window_requested": win_start,     # None = 用默认滚动窗口
            # 抓取起点与分析起点解耦：缓存名带 start，跟着窗口滚动会让缓存全 miss
            "fetch_start": D.fetch_start_for(win_start or C.START_DATE),
        },
    }
    prog("完成", 1.0)
    return result


# 负缓存（"名录里确实没有这只基金"）的存活时间，秒。24h 足够避免重复打接口，
# 又不会把一次断网/一次输错永久固化。
_NAME_NEG_TTL = 24 * 3600
_NAME_NEG_PREFIX = "__neg__"


def _cached_name(code: str) -> tuple[Optional[str], str]:
    """只读本地名录缓存，返回 `(name, state)`。

    state：
      - `"hit"`     有名字；
      - `"absent"`  名录**确实拉到过**且没有这只基金（负缓存未过期）；
      - `"unknown"` 没查过、负缓存已过期、或旧格式空串 —— 需要联网才能确定。

    ★ 必须把 `"absent"` 与 `"unknown"` 分开：请求线程内的 `/api/name` 不再联网，
      若两者都返回 None，前端就分不清"代码输错了"和"这次没查"，
      只能一律不提示（或一律误报）。
    """
    hit = _cache_load(code, "name")
    # ★ P1-05：负缓存必须有 TTL。旧实现把"网络失败"和"名录里确实没有"都写成
    #   空串并永久记住 —— 一次断网或一次代码输错，之后永远查不到名字。
    if isinstance(hit, str) and hit.startswith(_NAME_NEG_PREFIX):
        try:
            if time.time() - float(hit[len(_NAME_NEG_PREFIX):]) < _NAME_NEG_TTL:
                return None, "absent"           # 负缓存未过期
        except (TypeError, ValueError):
            pass
        return None, "unknown"                  # 负缓存损坏/过期
    if hit is not None and hit != "":
        return hit, "hit"
    return None, "unknown"


def _guess_name(code: str, allow_network: bool = True) -> Optional[str]:
    """
    尝试解析基金中文名；**查不到时返回 None**，不再用代码冒充名字。

    早期实现失败时回退成 code，调用方无法区分"这只基金就叫 110022"和
    "名录里查不到"，于是前端对"代码输错了"完全静默，只能等任务跑完才发现。

    allow_network=False 表示只查本地缓存（请求线程内的 `/api/name` 用）：
    名单接口曾在请求线程里挂起，而这类调用不能取消、也不占任务队列名额。
    """
    name, state = _cached_name(code)
    if state == "hit":
        return name
    if state == "absent":
        return None
    # state == "unknown"：没查过、负缓存过期、或旧格式空串 —— 需要联网
    if not allow_network:
        return None

    nm, listed = None, False        # listed = 名录**成功拉取**（区分网络失败）
    try:
        import akshare as ak
        # ★ 复查发现：这是唯一没走 _retry/http_timeout 的网络调用点 ——
        #   /api/name 和分析首屏都会走到它，挂起会把请求线程卡死。
        from market_research.data.marketdata import http_timeout
        with http_timeout():
            info = ak.fund_name_em()
        listed = True
        col = "基金代码" if "基金代码" in info.columns else info.columns[0]
        namecol = next((c for c in ("基金简称", "名称", "基金名称")
                        if c in info.columns), None)
        m = info[info[col].astype(str).str.zfill(6) == code]
        if len(m) and namecol:
            nm = str(m[namecol].iloc[0])
        elif len(m) and info.shape[1] > 1:
            nm = str(m.iloc[0, 1])
    except Exception:                      # noqa: BLE001
        nm, listed = None, False

    if nm:
        _cache_save(code, "name", nm)
    elif listed:
        # 名录拉到了、只是没有这只基金 → 记负缓存，但**带 TTL**
        _cache_save(code, "name",
                    f"{_NAME_NEG_PREFIX}{time.time():.0f}")
    # 网络失败：什么都不写，下次请求重新尝试
    return nm


def get_fund_name(code: str) -> str:
    return _guess_name(code) or code


# ====================================================================== 局部重算
def refit_single(code: str, model_key: str,
                 params: Optional[dict] = None,
                 max_rebal: Optional[int] = None,
                 auto_equity: bool = True,
                 start: Optional[str] = None,
                 end: Optional[str] = None,
                 checkpoint=None) -> dict:
    """
    只重跑一个模型（"重新学习"按钮）。

    数据走缓存，因此耗时 = 单模型滚动时间，远快于全量重算。

    ★ start/end 必须与主分析一致 —— 否则"重新学习"会用默认窗口（近 5 年）重算，
    而页面其它区块是用户选的窗口，结果混在两个区间上。
    """
    win_start, win_end = _parse_window(start, end)
    cp = checkpoint or NullCheckpoint()
    panel = cp.load("panel")
    if panel is None:
        panel = D.load_panel(code, start=(win_start or C.START_DATE),
                             end=win_end, verbose=False)
        cp.save("panel", panel)
    zoo_cfg = {**C.RBSA, "auto_equity": bool(auto_equity)}
    zoo = ModelZoo(zoo_cfg)
    rep = cp.load("model")
    if rep is None:
        rep = zoo.run(panel, [model_key], params={model_key: params or {}},
                      workers=1, max_rebal=max_rebal)[model_key]
        cp.save("model", rep)
    R = np.log(panel.astype(float)).diff().dropna()
    nav_oos = panel["__FUND__"].reindex(R.index[C.RBSA["window"]:])
    return model_payload(rep, nav_oos)


# ====================================================================== 买卖决策回测


def backtest_fund(code: str, model_key: str = "enet_slsqp",
                  auto_equity: bool = True,
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None,
                  cost_rate: Optional[float] = None,
                  job_id: Optional[str] = None,
                  window_start: Optional[str] = None,
                  window_end: Optional[str] = None,
                  params: Optional[dict] = None,
                  max_rebal: Optional[int] = None,
                  checkpoint=None) -> dict:
    """
    拟合 → 健康度买卖决策 → 指定日期区间的回测。

    线性模型滚动拟合约 1~3 秒（max_rebal=60），因此整个任务通常在数秒内完成；
    依然走任务系统以便前端统一轮询。
    """
    if model_key not in MODELS:
        raise ValueError(f"未知模型：{model_key}")
    t0 = time.time()

    def prog(stage, pct):
        if job_id:
            JOBS.update(job_id, stage=stage, progress=pct)

    # 拟合窗口与回测展示区间分开；预热历史保留在 panel 中。
    # 回测层另检查实际至少 60 个交易日，不把两年拟合下限套到展示区间。
    start_date, end_date = _parse_window(start_date, end_date, min_days=0)
    window_start, window_end = _parse_window(window_start, window_end)
    params = _check_params({model_key: params or {}})

    prog("加载数据", 0.1)
    cp = checkpoint or NullCheckpoint()
    panel = cp.load("panel")
    if panel is None:
        panel = D.load_panel(code, start=window_start or C.START_DATE,
                             end=window_end, verbose=False)
        cp.save("panel", panel)
    nav = panel["__FUND__"]
    name = (C.DEMO_FUNDS.get(code) or C.EXTENDED_FUNDS.get(code)
            or _guess_name(code))

    prog(f"滚动拟合 {MODELS[model_key].name}", 0.25)
    zoo_cfg = {**C.RBSA, "auto_equity": bool(auto_equity)}
    zoo = ModelZoo(zoo_cfg)
    rep = cp.load("model")
    if rep is None:
        rep = zoo.run(panel, [model_key], params=params, workers=1,
                      max_rebal=C.RBSA["max_rebal"] if max_rebal is None else max_rebal)[model_key]
        cp.save("model", rep)

    syn_ret = rep.syn_ret
    syn_nav = np.exp(syn_ret.cumsum())
    nav_oos = nav.reindex(syn_ret.index)

    prog("计算健康度与买卖决策", 0.7)
    engine = SignalEngine()
    trend = engine.trend_channel(syn_nav)
    health = engine.fit_health_channel(nav_oos, syn_ret, rep.fit_diag,
                                       rep.weights_hist)
    decision, trades = build_decision(health)

    # run_backtest 从 sig 取主策略与打分；趋势通道作为对照变体
    sig = pd.DataFrame({"position_final": decision,
                        "score": health["health_score"]})
    variants = {"仅趋势通道": score_to_position(trend["trend_score"])}

    # 赛道估值通道：行业腿没有 PE 数据时会回退沪深300，必须把兜底信息
    # 一起写进 payload，前端/报告据此标注，而不是只在前端写死一句提示。
    valuation = None
    try:
        pe_pct = D.load_pe_panel(verbose=False)
        val = engine.valuation_channel(rep.weights_hist, pe_pct, syn_nav.index)
        valuation = dict(val.attrs.get("valuation") or {})
        # P2-21：通道不可用时不能把 NaN 仓位塞进变体（会把回测净值污染成 NaN），
        # 也不能拿中性 0 冒充。此时不生成该曲线，由 valuation.note/reason 说明。
        if valuation.get("available") and val["val_score"].notna().any():
            variants["仅赛道估值通道"] = score_to_position(val["val_score"])
        else:
            valuation.setdefault("note", "估值通道不可用，未生成该对照曲线")
            valuation["excluded"] = True
    except Exception as exc:                               # noqa: BLE001
        valuation = {"available": False, "degraded": True,
                     "fallback_assets": [], "coverage": None,
                     "fallback_weight": None, "weight": 0.0, "excluded": True,
                     "note": "估值数据不可用，估值通道未参与回测",
                     "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
    bt_cfg = None if cost_rate is None else {"cost_rate": float(cost_rate)}

    prog("执行回测", 0.85)
    bt = run_backtest(nav_oos, sig, cfg=bt_cfg, fund_name=name,
                      variants=variants, main_name="拟合健康策略",
                      start_date=start_date, end_date=end_date,
                      decision_trades=trades)

    return _backtest_payload(code, name, model_key, auto_equity, bt,
                             health, decision, time.time() - t0,
                             valuation=valuation,
                             meta_extra={
                                 **nav_basis_meta(getattr(panel, "attrs", {})),
                                 "data_quality": dict(
                                     getattr(panel, "attrs", {}).get("data_quality") or {}),
                                 "data_warnings": list(
                                     getattr(panel, "attrs", {}).get("warnings") or []),
                             })
