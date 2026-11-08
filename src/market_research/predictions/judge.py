"""预测台账的到期判定：同一套口径结算方向概率与规则信号。

口径（已确认）：
  - 5 日与 20 日两个周期都判定；
  - 到期日之后允许 2 个交易日宽限；
  - 纯方向：收盘涨算上涨，不设幅度阈值，不做相对基准；
  - blocked / insufficient 不计入命中率，但保留收益用于事后归因。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pandas as pd

from market_research.predictions import ledger


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
        return {"status": "settled", "label": "insufficient",
                "target_date": target.date().isoformat(),
                "reason": "缺少收盘价数据"}
    close = close.copy()
    close.index = _normalize(close.index)
    base = close.get(pd.Timestamp(as_of))
    if base is None or pd.isna(base) or float(base) <= 0:
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
    if record.get("asset_kind") == "stock" and isinstance(prices, pd.DataFrame):
        row = prices.loc[judge_date]
        if _one_price_bar(row):
            return {"status": "settled", "label": "blocked",
                    "target_date": target.date().isoformat(),
                    "judge_date": judge_date.date().isoformat(),
                    "realized_return": realized, "reason": "limit_one_price"}
        if "volume" in prices and pd.notna(row.get("volume")) and float(row["volume"]) <= 0:
            return {"status": "settled", "label": "blocked",
                    "target_date": target.date().isoformat(),
                    "judge_date": judge_date.date().isoformat(),
                    "realized_return": realized, "reason": "zero_volume"}

    went_up = realized > 0
    correct = went_up if record.get("direction") == "up" else (not went_up)
    return {"status": "settled",
            "label": "correct" if correct else "incorrect",
            "target_date": target.date().isoformat(),
            "judge_date": judge_date.date().isoformat(),
            "realized_return": realized, "reason": reason}


def _sessions(as_of: date, today: date) -> pd.DatetimeIndex:
    try:
        from market_research.data.providers.calendar import bundled_calendar
        sessions, _info = bundled_calendar(as_of, today)
        return _normalize(sessions)
    except Exception:                                      # noqa: BLE001
        return pd.bdate_range(as_of, today)


def _load_prices(record: dict, today: date, fetch: bool):
    kind = record.get("asset_kind")
    code = record.get("asset_code")
    basis = record.get("price_basis") or ("qfq" if kind == "stock" else "")
    as_of = _date(record.get("as_of"))
    start = (as_of - timedelta(days=30)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    if kind == "fund":
        from market_research.data.panels import load_fund_nav_cached
        return load_fund_nav_cached(code, start=start, end=end,
                                    refresh=fetch, basis="total",
                                    allow_fetch=fetch)
    if fetch:
        from market_research.market.analysis import load_bars
        return load_bars(kind, code, start, end, basis, refresh=True)
    from market_research.tasks.sync import cached
    bars = cached(kind, code, basis, start, end)
    if bars is None or bars.empty:
        raise ValueError("本地行情缓存未覆盖判定区间，请先同步或使用 --fetch")
    return bars


def settle(limit: int = 500, today=None, fetch: bool = False) -> dict:
    """扫描到期记录并写回标签；返回本次结算计数。"""
    today = today or date.today()
    records = ledger.pending_records(limit)
    counts = {"checked": len(records), "pending": 0, "correct": 0,
              "incorrect": 0, "blocked": 0, "insufficient": 0}
    cache = {}
    for record in records:
        key = (record.get("asset_kind"), record.get("asset_code"),
               record.get("price_basis"))
        if key not in cache:
            try:
                cache[key] = (_load_prices(record, today, fetch), None)
            except Exception as exc:                       # noqa: BLE001
                cache[key] = (None, str(exc))
        prices, error = cache[key]
        if error is not None:
            ledger.update_record(record["id"], status="settled",
                                 label="insufficient", judged_at=time.time(),
                                 reason=f"行情读取失败：{error}")
            counts["insufficient"] += 1
            continue
        sessions = _sessions(_date(record.get("as_of")), today)
        verdict = judge_record(record, prices, sessions, today)
        if verdict.get("status") == "pending":
            counts["pending"] += 1
            continue
        ledger.update_record(record["id"], judged_at=time.time(), **verdict)
        counts[verdict.get("label")] = counts.get(verdict.get("label"), 0) + 1
    return counts
