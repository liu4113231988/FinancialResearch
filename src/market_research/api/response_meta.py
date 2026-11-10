# -*- coding: utf-8 -*-
"""统一 API meta 信封（P2-36）。

所有字段都能离线确定；data_as_of/data_latency 只从响应内已有的数据质量
快照提取，不猜测联网日期，也不把缺失值伪装成当日数据。
"""
from __future__ import annotations

from typing import Any

import market_research.config as C
from market_research import data_license, disclosure
from market_research.research.runinfo import config_hash

MODEL_LIMITS = {
    "scope": "internal_research",
    "tradeable_signal": False,
    "known_limitations": [
        "历史研究未建立稳定的日频方向预测优势",
        "方向概率、规则条件与仓位状态均为研究诊断",
        "公开抓取数据可能延迟、缺失或存在口径差异",
    ],
}


def _quality_from(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("data_quality", "quality"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    for key in ("meta", "result", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _quality_from(nested)
            if found:
                return found
    return {}


def build(payload: Any = None) -> dict[str, Any]:
    quality = _quality_from(payload)
    return {
        "disclaimer": disclosure.text(),
        "disclosure_scope": disclosure.scope(),
        "disclosure_position": disclosure.position(),
        "data_as_of": quality.get("data_end"),
        "data_latency": {
            "status": quality.get("status", "unknown"),
            "stale_days": quality.get("stale_days"),
            "threshold_days": quality.get("threshold_days", getattr(C, "DATA_STALE_DAYS", None)),
            "as_of": quality.get("as_of"),
        },
        "model_limits": MODEL_LIMITS,
        "data_source_license": data_license.status(),
        "config_hash": config_hash(),
    }


def enrich(payload: Any) -> Any:
    """保留调用方已有的 meta 字段，只补齐信封要求的缺省键。"""
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload)
    existing = merged.get("meta")
    meta = dict(existing) if isinstance(existing, dict) else {}
    for key, value in build(payload).items():
        meta.setdefault(key, value)
    merged["meta"] = meta
    return merged
