"""预测台账：记录实盘最新收盘的规则信号与方向概率，供事后回验。

只做两件事：
  1. 把一次**实盘最新收盘**的分析结果写成不可变的预测记录；
  2. 提供待结算记录与汇总查询，判定逻辑放在 `judge.py`。

历史区间回看、回测和离线研究一律不写入，避免把“事后拟合”当成预测。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime
from importlib.resources import files
from typing import Any, Dict, Iterable, List

import market_research.data.storage as storage
from market_research.serialization import _jsafe

HORIZONS = (5, 20)
GRACE_SESSIONS = 2
# 只记录最近这么多自然日内的“最新收盘”，周末与长假留出余量。
LIVE_MAX_LAG_DAYS = 14
LABELS = ("correct", "incorrect", "blocked", "insufficient")
GROUP_FIELDS = ("source", "horizon", "asset_kind", "regime")


def _schema(db) -> None:
    db.execute((files("market_research.data") / "migrations/004_predictions.sql")
               .read_text(encoding="utf-8"))


def _date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso(value) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def is_live(as_of, now=None) -> bool:
    """信号日是否为最近的最新收盘（而不是历史回看窗口）。"""
    signal_date = _date(as_of)
    if signal_date is None:
        return False
    today = (now or datetime.now()).date()
    return 0 <= (today - signal_date).days <= LIVE_MAX_LAG_DAYS


def _model_version(provenance: dict) -> str:
    provenance = provenance or {}
    commit = str(provenance.get("git_commit") or "unknown")
    version = str(provenance.get("code_version") or "")
    return f"{commit}:{version}" if version else commit


def _identity(asset_kind, asset_code, as_of, source, horizon) -> str:
    """同资产同收盘同来源同周期只保留首条，重跑分析不会重复记账。"""
    key = "|".join([asset_kind, asset_code, as_of, source, str(horizon)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _make(*, asset_kind, asset_code, asset_name, as_of, source, direction,
          horizon, probability=None, rule_score=None, rule_action=None,
          price_basis=None, regime=None, features=None, model_version=None,
          params=None, data_stamp=None) -> dict:
    return {
        "id": _identity(asset_kind, asset_code, as_of, source, horizon),
        "created_at": time.time(),
        "as_of": as_of,
        "asset_kind": asset_kind,
        "asset_code": asset_code,
        "asset_name": asset_name,
        "source": source,
        "direction": direction,
        "horizon": int(horizon),
        "probability": probability,
        "rule_score": rule_score,
        "rule_action": rule_action,
        "price_basis": price_basis,
        "regime": regime,
        "features": features,
        "model_version": model_version,
        "params": params,
        "data_stamp": data_stamp,
    }


def _insert(rows: Iterable[dict]) -> int:
    rows = [row for row in rows if row]
    if not rows:
        return 0
    columns = ("id", "created_at", "as_of", "asset_kind", "asset_code",
               "asset_name", "source", "direction", "horizon", "probability",
               "rule_score", "rule_action", "price_basis", "regime", "features",
               "model_version", "params", "data_stamp", "status",
               "target_date", "judge_date", "label", "realized_return",
               "judged_at", "reason")
    inserted = 0
    with storage.connection() as db:
        _schema(db)
        for row in rows:
            if db.execute("SELECT 1 FROM prediction_records WHERE id=?",
                          [row["id"]]).fetchone():
                continue
            values = [
                row["id"], row["created_at"], row["as_of"], row["asset_kind"],
                row["asset_code"], row.get("asset_name"), row["source"],
                row["direction"], row["horizon"], row.get("probability"),
                row.get("rule_score"), row.get("rule_action"),
                row.get("price_basis"), row.get("regime"),
                json.dumps(_jsafe(row.get("features")), ensure_ascii=False,
                           allow_nan=False),
                row.get("model_version"),
                json.dumps(_jsafe(row.get("params")), ensure_ascii=False,
                           allow_nan=False),
                row.get("data_stamp"), "pending", None, None, None, None, None,
                None,
            ]
            db.execute(
                "INSERT INTO prediction_records(" + ",".join(columns) + ") "
                "VALUES (" + ",".join("?" for _ in columns) + ")", values)
            inserted += 1
    return inserted


def _regime(context: dict) -> str:
    environment = ((context or {}).get("environment") or "").strip()
    return {"偏强": "strong", "偏弱": "weak",
            "分歧 / 震荡": "mixed"}.get(environment, "unknown")


def _market_snapshot(technical: dict) -> dict:
    latest = technical.get("latest") or {}
    keys = ("ma20", "ma60", "rsi", "dif", "dea", "macd", "atr",
            "boll_upper", "boll_lower", "support", "resistance",
            "volume_ratio", "score")
    return {
        "score": technical.get("score"),
        "action": technical.get("action"),
        "trend": technical.get("trend"),
        "latest": {key: latest.get(key) for key in keys if key in latest},
        "context": technical.get("context"),
        "levels": technical.get("levels"),
        "reasons": technical.get("reasons"),
    }


def _market_rows(result: dict, now) -> List[dict]:
    technical = result.get("technical") or {}
    as_of = _iso(technical.get("as_of"))
    if not is_live(as_of, now):
        return []
    kind = str(result.get("kind") or "")
    code = str(result.get("code") or "")
    if kind not in ("stock", "index") or not code:
        return []
    meta = result.get("meta") or {}
    provenance = meta.get("provenance") or {}
    snapshot = _market_snapshot(technical)
    regime = _regime(technical.get("context"))
    version = _model_version(provenance)
    stamp = provenance.get("data_stamp")
    rows: List[dict] = []
    for forecast in technical.get("forecasts") or []:
        if not forecast.get("ok"):
            continue
        probability = _num(forecast.get("probability"))
        horizon = forecast.get("horizon")
        if probability is None or horizon not in HORIZONS:
            continue
        rows.append(_make(
            asset_kind=kind, asset_code=code, asset_name=result.get("name"),
            as_of=as_of, source="direction",
            direction="up" if probability >= 0.5 else "down",
            horizon=horizon, probability=probability,
            price_basis=str(meta.get("adjust") or ""), regime=regime,
            features={**snapshot, "evaluation": forecast.get("evaluation"),
                      "baseline": forecast.get("baseline"),
                      "training_rows": forecast.get("training_rows")},
            model_version=version, data_stamp=stamp,
            params={"adjust": meta.get("adjust"),
                    "thresholds": {"direction": 0.5},
                    "feature_set": "technical_v1"}))
    for signal in technical.get("signals") or []:
        if _iso(signal.get("date")) != as_of:
            continue
        action = signal.get("type")
        if action not in ("buy", "sell"):
            continue
        for horizon in HORIZONS:
            rows.append(_make(
                asset_kind=kind, asset_code=code, asset_name=result.get("name"),
                as_of=as_of, source="rule",
                direction="up" if action == "buy" else "down",
                horizon=horizon, rule_score=_num(signal.get("score")),
                rule_action=action, price_basis=str(meta.get("adjust") or ""),
                regime=regime, features=snapshot, model_version=version,
                data_stamp=stamp,
                params={"adjust": meta.get("adjust"),
                        "rule": "score>=60 & RSI45-70 & close<=boll_upper; "
                                "score<=-20 or close<MA20 & MACD<0"}))
    return rows


def _fund_forecast_rows(result: dict, now) -> List[dict]:
    forecast = result.get("forecast") or {}
    as_of = _iso(forecast.get("as_of"))
    if not is_live(as_of, now):
        return []
    code = str(result.get("code") or "")
    if not code:
        return []
    meta = result.get("meta") or {}
    provenance = meta.get("provenance") or {}
    rows: List[dict] = []
    for item in forecast.get("horizons") or []:
        if not item.get("ok"):
            continue
        probability = _num(item.get("probability"))
        horizon = item.get("horizon")
        if probability is None or horizon not in HORIZONS:
            continue
        rows.append(_make(
            asset_kind="fund", asset_code=code, asset_name=result.get("name"),
            as_of=as_of, source="direction",
            direction="up" if probability >= 0.5 else "down",
            horizon=horizon, probability=probability,
            price_basis=meta.get("nav_basis") or "total", regime="unknown",
            features={"basis": forecast.get("basis"),
                      "nav_basis": meta.get("nav_basis"),
                      "evaluation": item.get("evaluation"),
                      "baseline": item.get("baseline"),
                      "training_rows": item.get("training_rows"),
                      "minimum_training_rows": item.get("minimum_training_rows")},
            model_version=_model_version(provenance),
            data_stamp=provenance.get("data_stamp"),
            params={"nav_basis": meta.get("nav_basis"),
                    "thresholds": {"direction": 0.5},
                    "feature_set": "fund_nav_direction_v1"}))
    return rows


def _fund_rule_rows(result: dict, now) -> List[dict]:
    meta = result.get("meta") or {}
    current = result.get("current") or {}
    as_of = _iso(current.get("date") or meta.get("end"))
    if not is_live(as_of, now):
        return []
    code = str(meta.get("code") or "")
    if not code:
        return []
    trades = result.get("trades") or []
    if not trades:
        return []
    latest = trades[-1]
    if _iso(latest.get("date")) != as_of:
        return []
    action = "buy" if latest.get("action") == "买入" else (
        "sell" if latest.get("action") == "卖出" else None)
    if action is None:
        return []
    provenance = meta.get("provenance") or {}
    features = {
        "health": _num(latest.get("health")),
        "reason": latest.get("reason"),
        "position": current.get("position"),
        "health_stats": meta.get("health_stats"),
        "thresholds": meta.get("thresholds"),
        "model": meta.get("model"),
    }
    return [_make(
        asset_kind="fund", asset_code=code, asset_name=meta.get("name"),
        as_of=as_of, source="rule",
        direction="up" if action == "buy" else "down",
        horizon=horizon, rule_score=_num(latest.get("health")),
        rule_action=action, price_basis="total", regime="unknown",
        features=features, model_version=_model_version(provenance),
        data_stamp=provenance.get("data_stamp"),
        params={"model": meta.get("model"),
                "thresholds": meta.get("thresholds"),
                "rule": "health<=exit 卖出；health>=enter 买入；连续确认"})
        for horizon in HORIZONS]


def record_result(operation: str, params: dict, result: dict,
                  *, now=None) -> int:
    """把一次服务层分析结果写入台账；返回新增条数。

    仅接受 analyze（基金方向概率）、backtest（基金规则信号）和 market
    （股票/指数）三种操作；历史区间与离线研究不会走到这里。
    """
    if not isinstance(result, dict):
        return 0
    now = now or datetime.now()
    if operation == "market":
        rows = _market_rows(result, now)
    elif operation == "analyze":
        rows = _fund_forecast_rows(result, now)
    elif operation == "backtest":
        rows = _fund_rule_rows(result, now)
    else:
        return 0
    return _insert(rows)


def pending_records(limit: int = 500) -> List[dict]:
    with storage.connection() as db:
        _schema(db)
        cursor = db.execute(
            "SELECT * FROM prediction_records WHERE status='pending' "
            f"ORDER BY as_of, asset_kind, asset_code LIMIT {int(limit)}")
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]


def _records() -> List[dict]:
    with storage.connection() as db:
        _schema(db)
        cursor = db.execute("SELECT * FROM prediction_records")
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]


def update_record(record_id: str, **fields) -> None:
    allowed = {"status", "target_date", "judge_date", "label",
               "realized_return", "judged_at", "reason"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ",".join(f"{key}=?" for key in values)
    with storage.connection() as db:
        _schema(db)
        db.execute(f"UPDATE prediction_records SET {assignments} WHERE id=?",
                   [*values.values(), record_id])


def _stats(rows: List[dict]) -> dict:
    counts = {label: 0 for label in LABELS}
    for row in rows:
        label = row.get("label")
        if label in counts:
            counts[label] += 1
    decided = counts["correct"] + counts["incorrect"]
    brier_values = [
        (float(row["probability"]) - (1.0 if float(row["realized_return"]) > 0 else 0.0)) ** 2
        for row in rows
        if row.get("probability") is not None
        and row.get("realized_return") is not None
        and row.get("label") in ("correct", "incorrect")
    ]
    return {
        "total": len(rows),
        "pending": sum(1 for row in rows if row.get("status") == "pending"),
        **counts,
        "decided": decided,
        "hit_rate": (counts["correct"] / decided) if decided else None,
        "brier": (sum(brier_values) / len(brier_values)) if brier_values else None,
        "probability_rows": len(brier_values),
    }


def summary(group_by: str | None = None) -> dict:
    """命中率、Brier 与分桶汇总；blocked/insufficient 不计入命中率分母。"""
    if group_by not in (None, *GROUP_FIELDS):
        raise ValueError(f"group_by 只支持 {GROUP_FIELDS}")
    rows = _records()
    result = {"group_by": group_by, "overall": _stats(rows)}
    if group_by:
        groups: Dict[Any, List[dict]] = {}
        for row in rows:
            groups.setdefault(row.get(group_by), []).append(row)
        result["groups"] = [
            {"key": key, **_stats(values)}
            for key, values in sorted(groups.items(), key=lambda item: str(item[0]))
        ]
    return result
