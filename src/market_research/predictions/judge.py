"""预测台账的到期判定：方向概率与规则事件按各自口径结算。

口径（已确认）：
  - 方向概率按 5 / 20 周期判定；周期时钟与记录时的 horizon_basis 一致；
  - 规则买卖事件不是方向预测，按固定 5 会话只记录触发后收益（observed）；
  - 到期日之后允许 2 个交易日宽限；
  - 收盘涨算上涨、跌算下跌，平盘记 neutral（不判对错）；不做相对基准；
  - neutral / observed / blocked / insufficient 均不计入方向命中率。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import pandas as pd

from market_research.predictions import ledger

FLAT_EPSILON = 1e-9


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


def _close_series(prices):
    if isinstance(prices, pd.DataFrame):
        if "close" not in prices:
            return None
        return prices["close"].astype(float)
    if isinstance(prices, pd.Series):
        return prices.astype(float)
    return None


def _one_price_bar(row) -> bool:
    values = [row.get(key) for key in ("open", "high", "low", "close")]
    if any(pd.isna(value) for value in values):
        return False
    return max(values) - min(values) <= 1e-9


def judge_record(record: dict, prices, sessions, today=None) -> dict:
    """判定单条记录；返回需要写回的字段，pending 只返回状态。

    `prices` 对股票/指数为含 OHLC 的 DataFrame，对基金为净值 Series。
    """
    today = today or date.today()
    as_of = _date(record.get("as_of"))
    horizon = int(record.get("horizon") or 0)
    is_rule = record.get("source") == "rule" and horizon == ledger.RULE_HORIZON
    if is_rule:
        horizon = ledger.RULE_EVAL_SESSIONS
    if as_of is None or horizon <= 0:
        return {"status": "settled", "label": "insufficient",
                "reason": "记录缺少信号日或周期"}
    sessions = _normalize(sessions)
    position = int(sessions.get_indexer([pd.Timestamp(as_of)])[0])
    if position < 0:
        return {"status": "settled", "label": "insufficient",
                "target_date": None, "reason": "信号日不在交易日历"}
    target_position = position + horizon
    if target_position >= len(sessions):
        # observed_bars 的会话轴就是报价轴；退市/清算/断供会令目标会话
        # 永远不出现。仅在信号日仍有报价、行情末日早于名义目标日、当前
        # 日期已超宽限期且目标后确认无报价时，保守终判 blocked/no_feed。
        if _horizon_basis(record) == "observed_bars":
            nominal = pd.bdate_range(
                as_of, periods=horizon + ledger.GRACE_SESSIONS + 1)
            nominal_target = nominal[horizon]
            nominal_grace_end = nominal[horizon + ledger.GRACE_SESSIONS]
            close = _close_series(prices)
            if close is not None and not close.empty:
                axis = _normalize(close.index)
                later = axis[(axis >= nominal_target)
                             & (axis <= nominal_grace_end)]
                today_date = _date(today)
                if (pd.Timestamp(as_of) in axis
                        and axis.max() < nominal_target
                        and today_date is not None
                        and today_date > nominal_grace_end.date()
                        and len(later) == 0):
                    return {"status": "settled", "label": "blocked",
                            "target_date": nominal_target.date().isoformat(),
                            "reason": "no_feed"}
        return {"status": "pending"}
    target = sessions[target_position]
    if today < target.date():
        return {"status": "pending", "target_date": target.date().isoformat()}
    if target_position + ledger.GRACE_SESSIONS < len(sessions):
        grace_end = sessions[target_position + ledger.GRACE_SESSIONS]
    else:
        grace_end = pd.bdate_range(target, periods=ledger.GRACE_SESSIONS + 1)[-1]

    close = _close_series(prices)
    if close is None or close.empty:
        if today <= grace_end.date():
            return {"status": "pending",
                    "target_date": target.date().isoformat()}
        return {"status": "settled", "label": "insufficient",
                "target_date": target.date().isoformat(),
                "reason": "缺少收盘价数据"}
    close = close.copy()
    close.index = _normalize(close.index)
    base = close.get(pd.Timestamp(as_of))
    if base is None or pd.isna(base) or float(base) <= 0:
        # 信号日收盘还没进本地缓存（同步滞后一天很常见）属于暂时缺数，
        # 不能在宽限期内直接判 insufficient，否则记录会被永久定案。
        if today <= grace_end.date():
            return {"status": "pending",
                    "target_date": target.date().isoformat()}
        return {"status": "settled", "label": "insufficient",
                "target_date": target.date().isoformat(),
                "reason": "信号日缺少有效收盘价"}

    candidates = close.index[(close.index >= target) & (close.index <= grace_end)]
    if len(candidates) == 0:
        if today <= grace_end.date():
            return {"status": "pending", "target_date": target.date().isoformat()}
        label = "insufficient" if record.get("asset_kind") == "fund" else "blocked"
        reason = "净值缺失超过宽限期" if label == "insufficient" else "missing_session"
        return {"status": "settled", "label": label,
                "target_date": target.date().isoformat(), "reason": reason}

    judge_date = candidates[0]
    value = float(close.loc[judge_date])
    realized = value / float(base) - 1.0
    reason = "grace_session" if judge_date > target else None
    extra = {}
    if reason == "grace_session":
        # 顺延样本的实际结算会话数可能与名义 horizon 不同；冻结后不再重算。
        judge_position = int(sessions.get_indexer([judge_date])[0])
        if judge_position < 0:
            judge_position = int(sessions.searchsorted(judge_date))
        extra["actual_sessions"] = judge_position - position
    if record.get("asset_kind") == "stock" and isinstance(prices, pd.DataFrame):
        row = prices.loc[judge_date]
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
        return pd.bdate_range(as_of, today)


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
        close = _close_series(prices)
        if close is not None and not close.empty:
            axis = _normalize(close.index)
            if pd.Timestamp(as_of) in axis:
                return axis
    return _sessions(as_of, today)


def _load_prices(record: dict, today: date, fetch: bool):
    kind = record.get("asset_kind")
    code = record.get("asset_code")
    basis = record.get("price_basis")
    if basis is None:
        basis = "qfq" if kind == "stock" else ""
    as_of = _date(record.get("as_of"))
    start = (as_of - timedelta(days=30)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    if kind == "fund":
        from market_research.data.panels import load_fund_nav_cached
        from market_research.data.providers.funds import (NAV_BASIS_CUM,
                                                          NAV_BASIS_TOTAL)
        # 降级为累计净值口径做出的预测，必须用同一口径结算，不能悄悄换成 total。
        basis = record.get("price_basis") or NAV_BASIS_TOTAL
        if basis not in (NAV_BASIS_TOTAL, NAV_BASIS_CUM):
            basis = NAV_BASIS_TOTAL
        nav = load_fund_nav_cached(code, start=start, refresh=fetch,
                                   basis=basis, allow_fetch=fetch,
                                   fresh=not fetch)
        # 该 loader 没有 end 参数：命中同步历史时序列会延伸到判定日之后，
        # 不截断会把未来净值带进收益计算。结算只允许用到今天为止的净值。
        if len(nav):
            nav = nav.loc[_normalize(nav.index) <= pd.Timestamp(today)]
        return nav
    if fetch:
        from market_research.market.analysis import load_bars
        return load_bars(kind, code, start, end, basis, refresh=True)
    from market_research.tasks.sync import cached
    # 结算只需要历史收盘，不能要求“同步到今天”：否则每次结算时本地缓存都
    # 被判定未就绪，记录会被误标成 insufficient 且再无机会重判。
    bars = cached(kind, code, basis, start, end, fresh=False)
    if bars is None or bars.empty:
        raise ValueError("本地行情缓存未覆盖判定区间，请先同步或使用 --fetch")
    return bars


def settle(limit: int = 500, today=None, fetch: bool = False) -> dict:
    """扫描到期记录并写回标签；返回本次结算计数。"""
    today = today or date.today()
    records = ledger.pending_records(limit)
    counts = {"checked": len(records), "pending": 0, "unavailable": 0,
              **{label: 0 for label in ledger.LABELS},
              "unavailable_reasons": []}
    cache = {}
    for record in records:
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
        verdict = judge_record(record, prices, sessions, today)
        if verdict.get("status") == "pending":
            counts["pending"] += 1
            continue
        ledger.update_record(record["id"], judged_at=time.time(), **verdict)
        counts[verdict.get("label")] = counts.get(verdict.get("label"), 0) + 1
    return counts
