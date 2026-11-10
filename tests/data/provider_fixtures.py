# -*- coding: utf-8 -*-
"""脱敏供应商契约 fixture 的加载与离线重放（P2-35）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_research.data.provenance import (CONTRACTS, FIELD_ALIASES,
                                             require_columns)

MANIFEST = (Path(__file__).resolve().parents[1] /
            "fixtures" / "providers" / "contracts.json")


def load_manifest(path: Path | None = None) -> dict:
    with (path or MANIFEST).open(encoding="utf-8") as stream:
        return json.load(stream)


def sources(path: Path | None = None) -> list[str]:
    return sorted(load_manifest(path)["sources"])


def replay(source: str, path: Path | None = None) -> pd.DataFrame:
    """把合成契约行重放成满足真实字段校验的 DataFrame。"""
    manifest = load_manifest(path)
    try:
        info = manifest["sources"][source]
    except KeyError as error:
        raise KeyError(f"没有 {source} 的契约 fixture") from error
    frame = pd.DataFrame(info["rows"])
    require_columns(frame, CONTRACTS[source]["fields"], source)
    frame[info["date_field"]] = pd.to_datetime(frame[info["date_field"]])
    frame[info["numeric_field"]] = pd.to_numeric(
        frame[info["numeric_field"]], errors="raise")
    return frame


def _validate_live_observation(source: str, obs: dict) -> None:
    """允许脱敏观测指纹，禁止原始响应或未登记别名混进 fixture 库。"""
    fields = CONTRACTS[source]["fields"]
    if obs.get("endpoint") != CONTRACTS[source]["endpoint"]:
        raise ValueError(f"{source} live_observation 端点与契约不一致")
    if obs.get("contract_fields") != fields:
        raise ValueError(f"{source} live_observation 契约字段与 CONTRACTS 不一致")
    if obs.get("raw_response_included") is not False:
        raise ValueError(f"{source} live_observation 不得包含原始响应")
    for forbidden in ("rows", "data", "raw", "sample", "values", "response"):
        if forbidden in obs:
            raise ValueError(f"{source} live_observation 禁止写入 {forbidden}")
    observed = obs.get("fields")
    if (not isinstance(observed, list) or len(observed) != len(fields)
            or not all(isinstance(name, str) for name in observed)):
        raise ValueError(f"{source} live_observation 列名与契约不一致")
    aliases = obs.get("field_aliases_applied") or {}
    if not isinstance(aliases, dict) or not set(aliases) <= set(fields):
        raise ValueError(f"{source} live_observation 别名映射不合法")
    for field, alias in aliases.items():
        if alias not in FIELD_ALIASES.get(source, {}).get(field, ()):
            raise ValueError(f"{source} live_observation 含未登记的别名 {alias}")


def validate_manifest(path: Path | None = None) -> list[str]:
    manifest = load_manifest(path)
    if manifest.get("schema_version") != 1:
        raise ValueError("不支持的 fixture schema_version")
    if manifest.get("raw_data_included") is not False:
        raise ValueError("fixture 库不得包含原始第三方响应")
    available = sorted(manifest["sources"])
    missing = sorted(set(CONTRACTS) - set(available))
    unknown = sorted(set(available) - set(CONTRACTS))
    if missing or unknown:
        raise ValueError(f"fixture 与供应商契约不一致：缺 {missing}，多 {unknown}")
    for source in available:
        info = manifest["sources"][source]
        if info["endpoint"] != CONTRACTS[source]["endpoint"]:
            raise ValueError(f"{source} endpoint 与契约不一致")
        if info["fields"] != CONTRACTS[source]["fields"]:
            raise ValueError(f"{source} fields 与契约不一致")
        frame = replay(source, path)
        dates = frame[info["date_field"]]
        values = frame[info["numeric_field"]]
        if not dates.is_monotonic_increasing or not dates.is_unique:
            raise ValueError(f"{source} 日期必须严格递增且无重复")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"{source} 数值必须为有限值且非 NaN")
        if source != "fund_cum_return" and not (values > 0).all():
            raise ValueError(f"{source} 价格/净值/比率必须为正")
        observation = info.get("live_observation")
        if observation is not None:
            if not isinstance(observation, dict):
                raise ValueError(f"{source} live_observation 必须是对象")
            _validate_live_observation(source, observation)
    return available
