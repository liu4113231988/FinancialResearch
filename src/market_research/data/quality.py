"""统一的数据质量与降级口径（P2-27）。

缓存新鲜度、候选指数缺失、净值口径降级、取数失败回退都在这里汇总成同一份
`data_quality`，再随 panel attrs 透传到 meta / CLI / 报告 / Web，避免每个模块
各自解释「这份数据还能不能用」。

status 取值：
    ok        无降级
    partial   有过期或少量缺失，但仍在可分析范围
    degraded  口径降级、缺失过多，或本次取数失败退回了旧缓存
    failed    没有可用数据（调用方通常已直接抛错）
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

import market_research.config as C

STATUS_ORDER = {"ok": 0, "partial": 1, "degraded": 2, "failed": 3}


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


def freshness(data_end=None, as_of=None,
              threshold_days: Optional[int] = None) -> dict:
    """数据末日与参考日（默认今天）之间的新鲜度。"""
    end = _as_date(data_end)
    reference = _as_date(as_of) or date.today()
    threshold = C.DATA_STALE_DAYS if threshold_days is None else int(threshold_days)
    stale_days = (reference - end).days if end else None
    return {
        "data_end": end.isoformat() if end else None,
        "as_of": reference.isoformat(),
        "stale_days": stale_days,
        "stale": bool(end is None or stale_days > threshold),
        "threshold_days": threshold,
    }


def build_quality(*, data_end=None, as_of=None, threshold_days=None,
                  missing_assets=None, n_expected=None,
                  nav_degraded: bool = False, nav_reason: str = "",
                  source_error: str = "", warnings=None,
                  provenance=None) -> dict:
    """把各类降级信号合并成一份可序列化的 data_quality。"""
    fresh = freshness(data_end, as_of, threshold_days)
    missing = [str(code) for code in (missing_assets or [])]
    expected = int(n_expected) if n_expected is not None else None
    ratio = (len(missing) / expected) if expected else None

    reasons = []
    status = "ok"

    def bump(level: str) -> None:
        nonlocal status
        if STATUS_ORDER[level] > STATUS_ORDER[status]:
            status = level

    if fresh["stale"]:
        bump("partial")
        reasons.append(
            f"数据截止 {fresh['data_end'] or '未知'}，距参考日 {fresh['as_of']} "
            f"{fresh['stale_days'] if fresh['stale_days'] is not None else '?'} 天"
            f"（阈值 {fresh['threshold_days']} 天）")
    if missing:
        bump("degraded" if ratio is not None and ratio >= C.DATA_MISSING_RATIO_LIMIT
             else "partial")
        shown = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        reasons.append(f"候选指数缺失 {len(missing)}/{expected or '?'}：{shown}")
    if nav_degraded:
        bump("degraded")
        reasons.append(f"净值口径已降级为累计净值：{nav_reason or '总回报构造失败'}")
    if source_error:
        bump("degraded")
        reasons.append(f"本次取数失败，已回退到本地旧缓存：{source_error}")

    return {
        **fresh,
        "status": status,
        "reasons": reasons,
        "missing_assets": missing,
        "n_missing": len(missing),
        "n_expected": expected,
        "missing_ratio": ratio,
        "nav_degraded": bool(nav_degraded),
        "nav_degraded_reason": (nav_reason or None) if nav_degraded else None,
        "source_error": source_error or None,
        "warnings": list(warnings or []),
        # P2-29：数据来源 / 端点 / 抓取时间 / 是否降级
        "provenance": list(provenance or []),
    }


def quality_reasons(quality: Optional[dict]) -> list:
    return list((quality or {}).get("reasons") or [])
