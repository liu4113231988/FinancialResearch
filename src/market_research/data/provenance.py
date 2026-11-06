# -*- coding: utf-8 -*-
"""公开抓取的数据溯源（P2-29）。

每次抓取的「来源 / 端点 / 抓取时间 / 是否降级 / 失败原因」都记在这里，
由 `load_panel` 汇总进 `data_quality.provenance`，再随 meta / 报告 / Web
透出。进程内保留最近一次结果即可，不承担审计职责（审计走 audit.jsonl）。
"""
from __future__ import annotations

import threading
import time

# 供应商契约的机器可读镜像；完整说明见 docs/数据源供应商契约.md。
CONTRACTS = {
    "fund_nav": {
        "endpoint": "akshare fund_open_fund_info_em(累计净值走势)",
        "fields": ["日期", "累计净值"],
    },
    "fund_unit_nav": {
        "endpoint": "akshare fund_open_fund_info_em(单位净值走势)",
        "fields": ["日期", "单位净值"],
    },
    "fund_cum_return": {
        "endpoint": "akshare fund_open_fund_info_em(累计收益率走势)",
        "fields": ["日期", "累计收益率"],
    },
    "index_sina": {
        "endpoint": "akshare stock_zh_index_daily",
        "fields": ["date", "close"],
    },
    "index_sw": {
        "endpoint": "akshare index_hist_sw(period=day)",
        "fields": ["日期", "收盘"],
    },
    "csindex_pe": {
        "endpoint": "akshare stock_zh_index_value_csindex(市盈率2)",
        "fields": ["日期", "市盈率2"],
    },
    "csindex_dividend": {
        "endpoint": "akshare stock_zh_index_value_csindex(股息率2)",
        "fields": ["日期", "股息率2"],
    },
}

_LOCK = threading.Lock()
_STATE = {}


def record(source: str, *, ok: bool, error: str = "",
           degraded: bool = False, detail: str = "") -> None:
    if not source:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        previous = _STATE.get(source) or {}
        _STATE[source] = {
            "source": source,
            "endpoint": (CONTRACTS.get(source) or {}).get("endpoint"),
            "last_fetch_at": now,
            "ok": bool(ok),
            "degraded": bool(degraded or not ok),
            "error": error[:200] or None,
            "calls": int(previous.get("calls", 0)) + 1,
            "detail": detail or previous.get("detail") or None,
        }


def require_columns(frame, columns, source: str) -> None:
    """字段契约校验：缺列/改名时显式失败，绝不按默认列位继续算。"""
    present = list(getattr(frame, "columns", []))
    missing = [c for c in columns if c not in present]
    if missing:
        from market_research.errors import ProviderContractError
        endpoint = (CONTRACTS.get(source) or {}).get("endpoint", "未知端点")
        raise ProviderContractError(
            f"{source} 返回缺少字段 {missing}（实际列 {present}）；端点 {endpoint}。"
            "若源站结构调整，请先更新取数实现与供应商契约文档")


def snapshot() -> list:
    with _LOCK:
        return [dict(value) for _, value in sorted(_STATE.items())]


def reset() -> None:
    with _LOCK:
        _STATE.clear()
