"""跨市场相关性与方向提示；所有可交易口径只用美股前一交易日收盘。"""
from __future__ import annotations

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.serialization import _jsafe

RULE_VERSION = "cross_market_v1"
MIN_SAMPLES = 60
WINDOWS = (60, 120)
WEAK_THRESHOLD = 0.20
CONFIDENT_THRESHOLD = 0.35


def _close_series(frame):
    if frame is None:
        return pd.Series(dtype=float)
    series = frame["close"] if isinstance(frame, pd.DataFrame) and "close" in frame else frame
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    return pd.to_numeric(series, errors="coerce").dropna().astype(float).sort_index()


def _normalize_index(series: pd.Series) -> pd.Series:
    """统一为 tz-naive、纳秒精度的 DatetimeIndex。

    `merge_asof` 要求两侧键的**时间单位完全一致**；供应商返回的索引可能是
    `datetime64[us]` 或 `datetime64[s]`（pandas 2.x 起可保留单位），
    与本地行情 `datetime64[ns]` 直接合并会抛 "incompatible merge keys"，
    随后被上层的宽泛 try/except 静默降级成"跨市场不可用"。
    这里显式归一，避免把数据格式差异误判为数据缺失。
    """
    index = pd.DatetimeIndex(pd.to_datetime(series.index))
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    index = index.as_unit("ns") if hasattr(index, "as_unit") else index
    return series.set_axis(index)


def _lagged_pair(domestic_returns: pd.Series, us_returns: pd.Series) -> pd.DataFrame:
    """把美股收益按严格早于本地交易日的最近一条对齐，避免同日未来信息。"""
    left = pd.DataFrame({"date": pd.to_datetime(domestic_returns.index),
                         "domestic": domestic_returns.to_numpy()}).sort_values("date")
    right = pd.DataFrame({"date": pd.to_datetime(us_returns.index),
                          "us_date": pd.to_datetime(us_returns.index),
                          "us": us_returns.to_numpy()}).sort_values("date")
    merged = pd.merge_asof(left, right, on="date", direction="backward",
                           allow_exact_matches=False)
    return merged.dropna(subset=["domestic", "us"]).set_index("date")


def _correlation(pair: pd.DataFrame, window: int | None) -> float | None:
    """窗口相关；窗口内样本不足 window 时返回 None。

    不能只按 MIN_SAMPLES 放行：100 个共同样本算出的"120 日相关"会在字段名上
    冒充满窗，后续置信度也无法按窗口长度判断。
    """
    if window is None:
        if len(pair) < MIN_SAMPLES:
            return None
        sample = pair
    else:
        if len(pair) < max(window, MIN_SAMPLES):
            return None
        sample = pair.tail(window)
    value = sample["domestic"].corr(sample["us"])
    return float(value) if np.isfinite(value) else None


def _hedge_hint(rho: float | None, us_return: float | None, n: int) -> dict:
    if rho is None or us_return is None or n < MIN_SAMPLES:
        return {"signal": "unavailable", "confidence": "none",
                "hint": "共同样本不足 60 个，暂不判断方向。"}
    strength = abs(rho)
    if strength < WEAK_THRESHOLD:
        return {"signal": "mixed", "confidence": "low",
                "hint": "滞后相关性较弱，不给出方向性判断。"}
    follow = rho * us_return
    signal = "tailwind" if follow > 0 else "headwind"
    confidence = "medium" if strength >= CONFIDENT_THRESHOLD and n >= 120 else "low"
    if signal == "tailwind":
        hint = ("美股上一交易日方向与该资产的滞后相关方向一致，对本地多头偏顺风；"
                "这只是历史相关观察，不构成买卖建议。")
    else:
        hint = ("美股上一交易日方向对该资产偏不利，对本地多头构成逆风提示；"
                "如需对冲，应先核验工具成本、跟踪误差与相关性稳定性，本提示不构成投资建议。")
    return {"signal": signal, "confidence": confidence, "hint": hint}


def cross_market_block(domestic_close: pd.Series, start: str, end: str) -> dict:
    """生成美股三大指数与本地资产的相关性对比及方向提示。"""
    from market_research.tasks import sync

    close = _close_series(domestic_close)
    domestic = _normalize_index(close.pct_change().dropna())
    domestic = domestic.loc[str(start):str(end)]
    items = []
    for code, name in C.US_INDEX.items():
        item = {"code": code, "name": name, "available": False,
                "corr_lag_60": None, "corr_lag_120": None,
                "corr_lag_full": None, "corr_same_60": None,
                "latest_us_date": None, "latest_us_return": None,
                "n_lagged": 0, "corr_window_used": None, "n_window_used": 0,
                "signal": "unavailable", "confidence": "none",
                "hint": "美股指数本地缓存不可用；请先在数据同步中补齐。"}
        try:
            frame = sync.cached("us_index", code, "", start, end)
        except Exception:                                  # noqa: BLE001
            frame = None
        us = _close_series(frame)
        if not us.empty:
            us = us.loc[str(start):str(end)]
            us_returns = _normalize_index(us.pct_change().dropna())
            pair = _lagged_pair(domestic, us_returns)
            same = pd.concat([domestic.rename("domestic"),
                              us_returns.rename("us")], axis=1,
                             join="inner").dropna()
            item.update(available=not pair.empty,
                        corr_lag_60=_correlation(pair, 60),
                        corr_lag_120=_correlation(pair, 120),
                        corr_lag_full=_correlation(pair, None),
                        corr_same_60=_correlation(same, 60),
                        n_lagged=len(pair))
            if not pair.empty:
                latest = pair.tail(1).iloc[0]
                item["latest_us_date"] = pd.Timestamp(latest["us_date"]).strftime("%Y-%m-%d")
                item["latest_us_return"] = float(latest["us"])
                # 置信度必须按**实际用于相关性的窗口样本数**判断：
                # 旧写法传 len(pair)，在只有 60 日相关可用时会被 180 个共同样本
                # 误升为 medium。也避免 `0.0 or ...` 把合法的零相关当缺失。
                rho120, rho60 = item["corr_lag_120"], item["corr_lag_60"]
                if rho120 is not None:
                    rho, window_used = rho120, 120
                elif rho60 is not None:
                    rho, window_used = rho60, 60
                else:
                    rho, window_used = None, None
                n_used = min(len(pair), window_used) if window_used else 0
                item["corr_window_used"] = window_used
                item["n_window_used"] = n_used
                item.update(_hedge_hint(rho, item["latest_us_return"], n_used))
        items.append(item)
    notes = [
        "可交易口径只使用美股前一交易日（严格早于本地交易日）的收盘收益；同日相关仅供探索，不可用于当日决策。",
        "相关性不等于因果，也不保证方向延续；样本不足、相关性弱或数据未同步时明确标为不判断。",
        "方向提示是固定启发式（相关系数符号 × 上一交易日美股收益符号），未做样本外检验；"
        "置信度按实际参与相关性的窗口样本数判定，60 日窗口不会因共同样本更长而升级。",
        "美股指数为价格指数，不含股息；与 A 股价格指数或基金净值口径并不完全一致。",
    ]
    return _jsafe({"ok": any(item["available"] for item in items),
                   "rule_version": RULE_VERSION,
                   "window": {"start": str(start), "end": str(end),
                              "n_domestic_returns": int(len(domestic))},
                   "items": items, "notes": notes})
