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
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from typing import Any, Dict, Iterable, List

import market_research.data.storage as storage
from market_research.serialization import _jsafe

HORIZONS = (5, 20)
GRACE_SESSIONS = 2

# P3-J：台账口径的"今天"用北京时间（与同步调度、`tasks/sync.now()` 一致），
# 不随宿主时区漂移 —— 非东八区部署时 datetime.now() 会让实盘判定/历史窗口
# 判定与同步的"今天"错位一天，把已可见的历史回看误记为实盘。
SHANGHAI = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(SHANGHAI)
# 只记录最近这么多自然日内的“最新收盘”，周末与长假留出余量。
LIVE_MAX_LAG_DAYS = 14
# 规则买卖事件不是方向预测：horizon=0 表示无固定方向周期，结算时按固定
# 5 个会话只记录触发后的收益（observed），不进入方向命中率与 Brier。
RULE_HORIZON = 0
RULE_EVAL_SESSIONS = 5
DIRECTION_THRESHOLD = 0.5
# 须与 market/execution.py 的成交阈值一致，由 tests/predictions 断言防漂移。
ENTRY_PROBABILITY = 0.6
EXIT_PROBABILITY = 0.4
THRESHOLDS = {"direction": DIRECTION_THRESHOLD,
              "entry": ENTRY_PROBABILITY, "exit": EXIT_PROBABILITY}
LABELS = ("correct", "incorrect", "neutral", "observed",
          "blocked", "insufficient")
# 仅从落库 features 派生的诊断分桶，不参与写入与分析结果。
FEATURE_GROUPS = ("vol_bucket", "trend_bucket")
GROUP_FIELDS = ("source", "horizon", "asset_kind", "regime",
                *FEATURE_GROUPS)


def _schema(db) -> None:
    db.execute((files("market_research.data") / "migrations/004_predictions.sql")
               .read_text(encoding="utf-8"))
    # 宽限顺延样本要冻结实际结算会话数；迁移 SQL 不改，幂等补列兼容旧库。
    try:
        columns = {row[1] for row in db.execute(
            "PRAGMA table_info('prediction_records')").fetchall()}
        if "actual_sessions" not in columns:
            db.execute("ALTER TABLE prediction_records "
                       "ADD COLUMN actual_sessions INTEGER")
    except Exception:                                      # noqa: BLE001
        # 并发补列时后到者可能看到“列已存在”；读路径不依赖它，忽略即可。
        pass


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
    today = (now or _now()).date()
    return 0 <= (today - signal_date).days <= LIVE_MAX_LAG_DAYS


def is_historical_window(params, now=None) -> bool:
    """显式结束日早于今天即历史回看，不得写入实盘台账。

    `is_live` 只校验信号日距今的天数：用户把 end 选在两周内时，5 日结果
    其实已能从当时的行情里看到，仍会被当成"实盘最新收盘"。这里从请求
    参数堵住入口，分析/回测窗口的任一显式结束日早于今天就不记账。
    """
    if not isinstance(params, dict):
        return False
    today = (now or _now()).date()
    for key in ("end", "window_end"):
        parsed = _date(params.get(key))
        if parsed is not None and parsed < today:
            return True
    return False


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
    kind = str(result.get("kind") or "")
    code = str(result.get("code") or "")
    if kind not in ("stock", "index") or not code:
        return []
    meta = result.get("meta") or {}
    timing = _market_timing(as_of, meta, now)
    if timing is None:
        return []
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
            params={**timing, "adjust": meta.get("adjust"),
                    "thresholds": dict(THRESHOLDS),
                    "horizon_basis": forecast.get("horizon_basis") or "observed_bars",
                    "feature_set": "technical_v1"}))
    for signal in technical.get("signals") or []:
        if _iso(signal.get("date")) != as_of:
            continue
        action = signal.get("type")
        if action not in ("buy", "sell"):
            continue
        rows.append(_make(
            asset_kind=kind, asset_code=code, asset_name=result.get("name"),
            as_of=as_of, source="rule",
            direction="up" if action == "buy" else "down",
            horizon=RULE_HORIZON, rule_score=_num(signal.get("score")),
            rule_action=action, price_basis=str(meta.get("adjust") or ""),
            regime=regime, features=snapshot, model_version=version,
            data_stamp=stamp,
            params={**timing, "adjust": meta.get("adjust"),
                    "horizon_semantics": "none",
                    "evaluation_sessions": RULE_EVAL_SESSIONS,
                    "rule": "score>=60 & RSI45-70 & close<=boll_upper; "
                            "score<=-20 or close<MA20 & MACD<0",
                    "note": "规则条件首次满足是状态事件，没有 5/20 日方向含义"}))
    return rows


def _market_timing(as_of, meta, now):
    from market_research.data.providers.calendar import bundled_calendar

    signal = _date(as_of)
    if signal is None:
        return None
    # 沪深京竞价收盘时间为北京时间 15:00。只采信收盘后发起的取数请求。
    close = datetime(signal.year, signal.month, signal.day, 15, tzinfo=SHANGHAI)
    if close > now:
        return None
    asset = ((meta.get("data_quality") or {}).get("sources") or {}).get("asset") or {}
    if asset.get("stale") or asset.get("source_error") or _date(asset.get("observed_data_end")) != signal:
        return None
    try:
        started, observed, generated = (datetime.fromisoformat(value) for value in (
            asset.get("request_started_at"), asset.get("observed_at"), meta.get("analysis_generated_at")))
        if not close <= started <= observed <= generated <= now:
            return None
        sessions, _ = bundled_calendar(signal, now.date())
    except (TypeError, ValueError, OSError, ImportError):
        return None
    completed_through = now.date() - timedelta(days=int(now.hour < 15))
    completed = [session.date() for session in sessions if signal <= session.date() <= completed_through]
    if not completed or completed[-1] != signal:
        return None
    return {"analysis_generated_at": generated.isoformat(), "data_observed_at": observed.isoformat(),
            "request_started_at": started.isoformat(), "session_close_at": close.isoformat()}


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
                    "thresholds": dict(THRESHOLDS),
                    "horizon_basis": "observed_bars",
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
        horizon=RULE_HORIZON, rule_score=_num(latest.get("health")),
        rule_action=action,
        price_basis=str(meta.get("nav_basis") or "total"), regime="unknown",
        features=features, model_version=_model_version(provenance),
        data_stamp=provenance.get("data_stamp"),
        params={"model": meta.get("model"),
                "thresholds": meta.get("thresholds"),
                "horizon_semantics": "none",
                "evaluation_sessions": RULE_EVAL_SESSIONS,
                "rule": "health<=exit 卖出；health>=enter 买入；连续确认",
                "note": "健康分是拟合可信度风控，不是市场方向预测"})]


def record_result(operation: str, params: dict, result: dict,
                  *, now=None) -> int:
    """把一次服务层分析结果写入台账；返回新增条数。

    仅接受 analyze（基金方向概率）、backtest（基金规则信号）和 market
    （股票/指数）三种操作；历史区间与离线研究不会走到这里。
    """
    if not isinstance(result, dict):
        return 0
    now = now or _now()
    now = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
    if is_historical_window(params, now):
        return 0
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
    limit = max(1, min(int(limit), 5000))
    with storage.connection() as db:
        _schema(db)
        cursor = db.execute(
            "SELECT * FROM prediction_records WHERE status='pending' "
            f"ORDER BY as_of, asset_kind, asset_code LIMIT {limit}")
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]


def _row_features(row: dict) -> dict:
    """features 在库里是 JSON 字符串；损坏或缺失按空字典处理。"""
    features = row.get("features")
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except (TypeError, ValueError):
            features = None
    return features if isinstance(features, dict) else {}


def _level(value, low, high, labels=("low", "mid", "high")) -> str:
    number = _num(value)
    if number is None:
        return "unknown"
    if number < low:
        return labels[0]
    if number < high:
        return labels[1]
    return labels[2]


def _vol_bucket(features: dict) -> str:
    """把已冻结的波动率连续值离散化，优先 0~1 分位，其次同量纲比值。"""
    latest = features.get("latest")
    latest = latest if isinstance(latest, dict) else {}
    context = features.get("context")
    context_latest = context.get("latest") if isinstance(context, dict) else None
    context_latest = (context_latest if isinstance(context_latest, dict)
                      else {})
    for key in ("atr_percentile", "bandwidth_percentile"):
        if _num(context_latest.get(key)) is not None:
            return _level(context_latest[key], 0.33, 0.66)
    upper, lower, ma20 = (_num(latest.get("boll_upper")),
                          _num(latest.get("boll_lower")),
                          _num(latest.get("ma20")))
    if None not in (upper, lower, ma20) and ma20 > 0:
        return _level((upper - lower) / ma20, 0.04, 0.10)
    atr, ma20 = _num(latest.get("atr")), _num(latest.get("ma20"))
    if atr is not None and ma20 is not None and ma20 > 0:
        return _level(atr / ma20, 0.015, 0.04)
    return "unknown"


def _trend_bucket(features: dict) -> str:
    """趋势分（−100~100）为主；缺失时退回最新 MACD 柱的符号。"""
    score = _num(features.get("score"))
    if score is not None:
        if score < -20:
            return "down"
        if score > 20:
            return "up"
        return "flat"
    latest = features.get("latest")
    latest = latest if isinstance(latest, dict) else {}
    macd = _num(latest.get("macd"))
    if macd is not None:
        return "up" if macd > 0 else ("down" if macd < 0 else "flat")
    return "unknown"


def _with_feature_buckets(rows: List[dict]) -> List[dict]:
    """只追加诊断分桶键，不改动落库字段，也不改变默认汇总口径。"""
    for row in rows:
        features = _row_features(row)
        row["vol_bucket"] = _vol_bucket(features)
        row["trend_bucket"] = _trend_bucket(features)
    return rows


def _records() -> List[dict]:
    with storage.connection() as db:
        _schema(db)
        cursor = db.execute("SELECT * FROM prediction_records")
        columns = [item[0] for item in cursor.description]
        rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    return _with_feature_buckets(rows)


def update_record(record_id: str, **fields) -> None:
    allowed = {"status", "target_date", "judge_date", "label",
               "realized_return", "judged_at", "reason", "actual_sessions"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ",".join(f"{key}=?" for key in values)
    with storage.connection() as db:
        _schema(db)
        db.execute(f"UPDATE prediction_records SET {assignments} WHERE id=?",
                   [*values.values(), record_id])


def migrate_legacy(apply: bool = False) -> dict:
    """把口径修正前的规则行（5/20 各一条）合并成新口径的单条状态事件。

    默认只预览；`apply=True` 才写库并删除同组重复行。幂等：迁移后不再有
    `horizon IN (5,20)` 的规则行，重跑返回 deleted=0。建议先备份
    `market.duckdb`：删除的是旧口径重复行，保留行沿用原 id 与已结算收益。
    """
    with storage.connection() as db:
        _schema(db)
        groups = db.execute(
            "SELECT asset_kind, asset_code, as_of, COALESCE(rule_action,'') AS action, "
            "COUNT(*) AS n FROM prediction_records "
            "WHERE source='rule' AND horizon IN (5,20) GROUP BY 1,2,3,4"
        ).fetchall()
        legacy_rows = sum(int(group[4]) for group in groups)
        would_delete = sum(int(group[4]) - 1 for group in groups)
        if not apply:
            return {"dry_run": True, "legacy_rows": legacy_rows,
                    "groups": len(groups), "would_delete": would_delete,
                    "kept": 0, "deleted": 0}
        kept = deleted = 0
        for asset_kind, code, as_of, action, _n in groups:
            rows = db.execute(
                "SELECT id, params, status, label FROM prediction_records "
                "WHERE source='rule' AND horizon IN (5,20) "
                "AND asset_kind=? AND asset_code=? AND as_of=? "
                "AND COALESCE(rule_action,'')=? ORDER BY horizon, created_at",
                [asset_kind, code, as_of, action]).fetchall()
            if not rows:
                continue
            keep = rows[0]
            try:
                params = json.loads(keep[1]) if isinstance(keep[1], str) else {}
            except (TypeError, ValueError):
                params = {}
            params = dict(params) if isinstance(params, dict) else {}
            params.update({"horizon_semantics": "none",
                           "evaluation_sessions": RULE_EVAL_SESSIONS,
                           "migrated_from": "legacy_rule_5_20"})
            label = "observed" if keep[2] == "settled" else keep[3]
            db.execute(
                "UPDATE prediction_records SET horizon=?, params=?, label=? WHERE id=?",
                [RULE_HORIZON,
                 json.dumps(_jsafe(params), ensure_ascii=False, allow_nan=False),
                 label, keep[0]])
            for row in rows[1:]:
                db.execute("DELETE FROM prediction_records WHERE id=?", [row[0]])
                deleted += 1
            kept += 1
    return {"dry_run": False, "legacy_rows": legacy_rows,
            "groups": len(groups), "would_delete": would_delete,
            "kept": kept, "deleted": deleted}


def _stats(rows: List[dict]) -> dict:
    counts = {label: 0 for label in LABELS}
    for row in rows:
        label = row.get("label")
        if label in counts:
            counts[label] += 1
    # 方向命中率与 Brier 只统计有概率的方向预测；规则买卖事件是状态记录，
    # 只单列触发后收益，不参与方向正确性判定（旧版数据也按 source 排除）。
    direction = [row for row in rows
                 if row.get("source") != "rule" and row.get("probability") is not None]
    rules = [row for row in rows if row.get("source") == "rule"]
    # 口径修正前落库的旧规则行是 5/20 各一条；单列出来，避免跨版本汇总被误读。
    legacy = [row for row in rules if row.get("horizon") in HORIZONS]
    decided = sum(1 for row in direction
                  if row.get("label") in ("correct", "incorrect"))
    correct = sum(1 for row in direction if row.get("label") == "correct")
    brier_values = [
        (float(row["probability"]) - (1.0 if float(row["realized_return"]) > 0 else 0.0)) ** 2
        for row in direction
        if row.get("realized_return") is not None
        and row.get("label") in ("correct", "incorrect")
    ]
    rule_returns = [float(row["realized_return"]) for row in rules
                    if row.get("realized_return") is not None]
    grace_settled = sum(1 for row in rows
                        if row.get("reason") == "grace_session")
    no_feed = sum(1 for row in rows if row.get("reason") == "no_feed")
    return {
        "total": len(rows),
        "pending": sum(1 for row in rows if row.get("status") == "pending"),
        **counts,
        "grace_settled": grace_settled,
        "no_feed": no_feed,
        "decided": decided,
        "hit_rate": (correct / decided) if decided else None,
        "hit_rate_basis": "direction_only",
        "brier": (sum(brier_values) / len(brier_values)) if brier_values else None,
        "probability_rows": len(brier_values),
        "rule_events": len(rules),
        "rule_return_mean": ((sum(rule_returns) / len(rule_returns))
                             if rule_returns else None),
        "rule_return_positive_rate": (
            (sum(1 for value in rule_returns if value > 0) / len(rule_returns))
            if rule_returns else None),
        "legacy_rows": len(legacy),
        "current_total": len(rows) - len(legacy),
        "note": "hit_rate/Brier 只统计方向概率；规则事件单列触发后收益，"
                "neutral/observed/blocked/insufficient 均不计入命中率分母。"
                "legacy_rows 是口径修正前的旧规则行（5/20 各一条），"
                "current_total 已将其排除。"
                "grace_settled 是宽限期顺延结算样本，实际会话数冻结在 "
                "actual_sessions，仅作诊断单列，不改变默认命中率分母。"
                "blocked 按 label 计数；no_feed 按 reason 计数，代表行情末日"
                "早于目标日、超宽限期仍确认无后续报价的终判样本。",
    }


def summary(group_by: str | None = None) -> dict:
    """方向命中率、Brier 与规则事件归因的分桶汇总。"""
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
