"""预测台账的到期判定：方向概率与规则事件按各自口径结算。

口径（已确认）：
  - 方向概率按 5 / 20 周期判定；周期时钟与记录时的 horizon_basis 一致；
  - 规则买卖事件不是方向预测，按固定 5 会话只记录触发后收益（observed）；
  - 到期日之后允许 2 个交易日宽限；
  - 收盘涨算上涨、跌算下跌，平盘记 neutral（不判对错）；不做相对基准；
  - neutral / observed / blocked / insufficient 均不计入方向命中率。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta

import pandas as pd

from market_research.predictions import ledger

FLAT_EPSILON = 1e-9
SETTLEMENT_RULE_VERSION = "market-close-v1"


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


def _normalize(index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(index).normalize()


def _through(prices, today: date):
    return prices.loc[_normalize(prices.index) <= pd.Timestamp(today)]


def _close_series(prices, today: date) -> pd.Series:
    if isinstance(prices, pd.DataFrame):
        prices = prices.get("close")
    if not isinstance(prices, pd.Series):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    close = _through(prices, today).astype(float)
    close = close[(close > 0) & (close < float("inf"))]
    close.index = _normalize(close.index)
    return close


def _grace_expired(grace_end, close, attrs, today: date) -> bool:
    if today <= grace_end.date() or attrs.get("stale") or attrs.get("source_error"):
        return False
    # 缓存的 through 是请求上界，旧缓存可能将它写成抓取日的次日。
    checked = _date(attrs.get("source_checked_through"))
    return ((checked is not None and checked >= grace_end.date())
            or (len(close) > 0 and close.index.max() >= grace_end))


def _clock(now):
    now = now or ledger._now()
    if now.utcoffset() is None:
        raise ValueError("结算时点必须带时区")
    return now.astimezone(ledger.SHANGHAI)


def _session_close(day: date) -> datetime:
    return datetime.combine(day, time(15), ledger.SHANGHAI)


def _market_snapshot(attrs, now):
    if attrs.get("stale") or attrs.get("source_error"):
        return None
    try:
        started = datetime.fromisoformat(attrs["request_started_at"])
        observed = datetime.fromisoformat(attrs["observed_at"])
        end = date.fromisoformat(attrs["observed_data_end"])
        start = date.fromisoformat(attrs.get("observed_data_start", end.isoformat()))
        if started <= observed <= now and start <= end <= observed.astimezone(ledger.SHANGHAI).date():
            return start, end, started, observed
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _evidence_through(prices, through, today: date) -> date:
    last = _date(prices.index.max()) if len(prices) else None
    if last is None:
        return through
    return min(max(last, through), today)


def _settlement_evidence(prices, snapshot, as_of, through, *,
                         evidence_through: date | None = None):
    if snapshot is None:
        return {}
    start, end, started, observed = snapshot
    evidence_through = evidence_through or through
    rows = prices.loc[str(as_of):str(evidence_through)]
    content = pd.util.hash_pandas_object(rows, index=True).values.tobytes()
    columns = list(rows.columns) if isinstance(rows, pd.DataFrame) else [rows.name]
    digest = hashlib.sha256(json.dumps(columns).encode() + content).hexdigest()
    return {"settlement_evidence": {
        "rule_version": SETTLEMENT_RULE_VERSION, "input_sha256": digest,
        "input_from": str(as_of), "input_through": str(evidence_through),
        "decision_through": str(through),
        "request_started_at": started.isoformat(), "observed_at": observed.isoformat(),
        "observed_data_start": start.isoformat(), "observed_data_end": end.isoformat(),
        "source": prices.attrs.get("source"),
        "source_checked_from": prices.attrs.get("source_checked_from"),
        "source_checked_through": prices.attrs.get("source_checked_through"),
    }}


def _absence_verified(snapshot, attrs, target, grace_end) -> bool:
    start, end, started, _ = snapshot
    checked_from = _date(attrs.get("source_checked_from")) or start
    checked_through = _date(attrs.get("source_checked_through")) or end
    return (started >= _session_close(grace_end.date())
            and checked_from <= target.date() <= grace_end.date() <= checked_through)


def _one_price_bar(row) -> bool:
    values = [row.get(key) for key in ("open", "high", "low", "close")]
    if any(pd.isna(value) for value in values):
        return False
    return max(values) - min(values) <= 1e-9


def judge_record(record: dict, prices, sessions, today: date | None = None, *,
                 now: datetime | None = None) -> dict:
    """判定单条记录；pending 可附等待原因。

    `prices` 对股票/指数为含 OHLC 的 DataFrame，对基金为净值 Series。
    """
    now = _clock(now)
    today = min(today, now.date()) if today is not None else now.date()
    as_of = _date(record.get("as_of"))
    horizon = int(record.get("horizon") or 0)
    is_rule = record.get("source") == "rule" and horizon == ledger.RULE_HORIZON
    if is_rule:
        horizon = ledger.RULE_EVAL_SESSIONS
    if as_of is None or horizon <= 0:
        return {"status": "settled", "label": "insufficient",
                "reason": "记录缺少信号日或周期"}
    if as_of > today:
        return {"status": "pending"}
    sessions = _normalize(sessions)
    observed = _horizon_basis(record) == "observed_bars"
    if observed:
        sessions = sessions[sessions <= pd.Timestamp(today)]
    if sessions.empty or not sessions[0].date() <= as_of <= sessions[-1].date():
        return {"status": "pending"}
    position = int(sessions.get_indexer([pd.Timestamp(as_of)])[0])
    if position < 0:
        if observed:
            return {"status": "pending"}
        return {"status": "settled", "label": "insufficient",
                "target_date": None, "reason": "信号日不在交易日历"}
    close = _close_series(prices, today)
    attrs = getattr(prices, "attrs", {})
    market = record.get("asset_kind") in ("stock", "index")
    snapshot = _market_snapshot(attrs, now) if market else None
    if market and snapshot is None:
        return {"status": "pending", "reason": "awaiting_close_evidence"}
    target_position = position + horizon
    if target_position >= len(sessions):
        if observed and pd.Timestamp(as_of) in close.index:
            nominal = _sessions(as_of, today)
            nominal = nominal[nominal >= pd.Timestamp(as_of)]
            grace_position = horizon + ledger.GRACE_SESSIONS
            if (len(nominal) > grace_position and nominal[0] == pd.Timestamp(as_of)
                    and close.index.max() < nominal[horizon]
                    and _grace_expired(nominal[grace_position], close, attrs, today)
                    and (not market or _absence_verified(
                        snapshot, attrs, nominal[horizon], nominal[grace_position]))):
                decision_through = nominal[grace_position].date()
                return {"status": "settled", "label": "blocked",
                        "target_date": nominal[horizon].date().isoformat(),
                        "reason": "no_feed", **_settlement_evidence(
                            prices, snapshot, as_of, decision_through,
                            evidence_through=_evidence_through(
                                prices, decision_through, today))}
        return {"status": "pending"}
    target = sessions[target_position]
    pending = {"status": "pending", "target_date": target.date().isoformat()}
    if today < target.date():
        return pending
    window = sessions[target_position:target_position + ledger.GRACE_SESSIONS + 1]
    candidates = window[window.isin(close.index)]
    base = close.get(pd.Timestamp(as_of))
    if base is None or len(candidates) == 0:
        if (len(window) <= ledger.GRACE_SESSIONS
                or not _grace_expired(window[-1], close, attrs, today)):
            return pending
        if market and not _absence_verified(snapshot, attrs, target, window[-1]):
            return {**pending, "reason": "awaiting_close_evidence"}
        if base is None:
            label = "insufficient"
            reason = "缺少收盘价数据" if close.empty else "信号日缺少有效收盘价"
        elif record.get("asset_kind") == "fund":
            label, reason = "insufficient", "净值缺失超过宽限期"
        else:
            label, reason = "blocked", "missing_session"
        decision_through = window[-1].date()
        return {**pending, "status": "settled", "label": label, "reason": reason,
                **_settlement_evidence(
                    prices, snapshot, as_of, decision_through,
                    evidence_through=_evidence_through(
                        prices, decision_through, today))}

    judge_date = candidates[0]
    if market:
        start, end, started, _ = snapshot
        day = judge_date.date()
        if (not start <= day <= end or not _session_close(day) <= started <= now
                or (observed and judge_date not in _sessions(as_of, today))):
            return {**pending, "reason": "awaiting_close_evidence"}
    value = float(close.loc[judge_date])
    realized = value / float(base) - 1.0
    reason = "grace_session" if judge_date > target else None
    extra = _settlement_evidence(prices, snapshot, as_of, judge_date.date())
    if reason == "grace_session":
        # 顺延样本的实际结算会话数可能与名义 horizon 不同；冻结后不再重算。
        judge_position = int(sessions.get_indexer([judge_date])[0])
        extra["actual_sessions"] = judge_position - position
    if record.get("asset_kind") == "stock" and isinstance(prices, pd.DataFrame):
        row = prices.loc[_normalize(prices.index) == judge_date].iloc[0]
        if _one_price_bar(row):
            return {"status": "settled", "label": "blocked",
                    "target_date": target.date().isoformat(),
                    "judge_date": judge_date.date().isoformat(),
                    "realized_return": realized, "reason": "limit_one_price",
                    **extra}
        if "volume" in prices and pd.notna(row.get("volume")) and float(row["volume"]) <= 0:
            return {"status": "settled", "label": "blocked",
                    "target_date": target.date().isoformat(),
                    "judge_date": judge_date.date().isoformat(),
                    "realized_return": realized, "reason": "zero_volume",
                    **extra}

    verdict = {"status": "settled",
               "target_date": target.date().isoformat(),
               "judge_date": judge_date.date().isoformat(),
               "realized_return": realized, "reason": reason, **extra}
    if is_rule:
        # 状态事件只保留触发后的区间收益，不判定方向对错。
        return {**verdict, "label": "observed",
                "reason": reason or "rule_event_return"}
    if abs(realized) <= FLAT_EPSILON:
        # 平盘对 up/down 一视同仁，避免 down 方向白拿一次命中。
        return {**verdict, "label": "neutral",
                "reason": reason or "unchanged_close"}
    went_up = realized > 0
    correct = went_up if record.get("direction") == "up" else (not went_up)
    return {**verdict, "label": "correct" if correct else "incorrect"}


def _sessions(as_of: date, today: date) -> pd.DatetimeIndex:
    try:
        from market_research.data.providers.calendar import bundled_calendar
        sessions, _info = bundled_calendar(as_of, today)
        return _normalize(sessions)
    except Exception:                                      # noqa: BLE001
        return pd.DatetimeIndex([])


def _horizon_basis(record: dict) -> str:
    """读取写入时冻结的周期时钟；旧记录没有该字段时沿用日历会话。"""
    params = record.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            params = {}
    if not isinstance(params, dict):
        return "calendar_sessions"
    basis = params.get("horizon_basis")
    return basis if basis in ("calendar_sessions", "observed_bars") else "calendar_sessions"


def _sessions_for(record: dict, prices, today: date) -> pd.DatetimeIndex:
    """结算时钟必须与训练时钟一致：有效日线口径按实际报价推进。"""
    as_of = _date(record.get("as_of"))
    if as_of is None:
        return pd.DatetimeIndex([])
    if _horizon_basis(record) == "observed_bars":
        return _close_series(prices, today).index
    return _sessions(as_of, today)


def _load_prices(record: dict, today: date, fetch: bool):
    kind = record.get("asset_kind")
    code = record.get("asset_code")
    basis = record.get("price_basis")
    if basis is None:
        basis = "qfq" if kind == "stock" else ""
    as_of = _date(record.get("as_of"))
    start = (as_of - timedelta(days=30)).isoformat()
    end = today.isoformat()
    if kind == "fund":
        from market_research.data.panels import load_fund_nav_cached
        from market_research.data.providers.funds import (NAV_BASIS_CUM,
                                                          NAV_BASIS_TOTAL)
        # 降级为累计净值口径做出的预测，必须用同一口径结算，不能悄悄换成 total。
        basis = record.get("price_basis") or NAV_BASIS_TOTAL
        if basis not in (NAV_BASIS_TOTAL, NAV_BASIS_CUM):
            basis = NAV_BASIS_TOTAL
        prices = load_fund_nav_cached(code, start=start, refresh=fetch,
                                      basis=basis, allow_fetch=fetch,
                                      fresh=not fetch)
        if prices.attrs.get("nav_basis", basis) != basis:
            raise ValueError("结算净值与记录的价格口径不符")
    elif fetch:
        from market_research.market.analysis import load_bars
        prices = load_bars(kind, code, start, end, basis, refresh=True)
    else:
        from market_research.tasks.sync import cached
        prices = cached(kind, code, basis, start, end, fresh=False)
        if prices is None or prices.empty:
            raise ValueError("本地行情缓存未覆盖判定区间，请先同步或使用 --fetch")
    prices = _through(prices, today)
    if fetch and not prices.empty and not (prices.attrs.get("stale") or prices.attrs.get("source_error")):
        # 只能认证供应商实际返回的数据区间；请求上界不代表来源已覆盖到该日。
        observed_start = _date(prices.attrs.get("observed_data_start"))
        observed_end = _date(prices.attrs.get("observed_data_end"))
        if observed_start is not None:
            prices.attrs["source_checked_from"] = observed_start.isoformat()
        if observed_end is not None:
            prices.attrs["source_checked_through"] = observed_end.isoformat()
    return prices


def settle(limit: int = 500, today: date | None = None, fetch: bool = False, *,
           now: datetime | None = None) -> dict:
    """扫描到期记录。指定日期时按当前可读数据诊断，跳过标签写回。"""
    started_at = _clock(now)
    diagnostic_only = today is not None
    today = today or started_at.date()
    if today > started_at.date():
        raise ValueError("判定日期不能晚于北京时间今天")
    records = ledger.pending_records(limit)
    counts = {"checked": len(records), "pending": 0, "unavailable": 0,
              **{label: 0 for label in ledger.LABELS},
              "unavailable_reasons": [], "cutoff_date": today.isoformat(),
              "checked_at": started_at.isoformat(), "diagnostic_only": diagnostic_only,
              "awaiting_close_evidence": 0}
    cache = {}
    for record in records:
        as_of = _date(record.get("as_of"))
        if as_of is not None and as_of > today:
            counts["pending"] += 1
            continue
        key = (record.get("asset_kind"), record.get("asset_code"),
               record.get("price_basis"))
        if key not in cache:
            try:
                cache[key] = (_load_prices(record, today, fetch), None)
            except Exception as exc:                       # noqa: BLE001
                cache[key] = (None, f"{type(exc).__name__}: {exc}")
        prices, error = cache[key]
        if error is not None:
            # 行情读取失败是临时性的（未同步、库锁、磁盘错误），不能把记录
            # 永久判成 insufficient；保持 pending，等下次数据可用再结。
            counts["unavailable"] += 1
            reason = (f"{record.get('asset_kind')}:{record.get('asset_code')}"
                      f":{record.get('price_basis')} -> {error}")
            if reason not in counts["unavailable_reasons"]:
                counts["unavailable_reasons"].append(reason)
            continue
        sessions = _sessions_for(record, prices, today)
        checked_at = _clock(now) if fetch else started_at
        verdict = judge_record(record, prices, sessions, today, now=checked_at)
        if verdict.get("status") == "pending":
            counts["pending"] += 1
            if verdict.get("reason") == "awaiting_close_evidence":
                counts["awaiting_close_evidence"] += 1
            continue
        if not diagnostic_only:
            updated = ledger.update_record(
                record["id"], judged_at=checked_at.timestamp(), **verdict)
            if not updated:
                # 并发结算时另一进程已写回；本次不重复计数。
                continue
        counts[verdict.get("label")] = counts.get(verdict.get("label"), 0) + 1
    return counts
