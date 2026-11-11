"""确定性排序：并列时按名称/代码升序，保证「同输入同输出」（P3-G）。"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def _sortable(value) -> float | None:
    """数值化排序键（P2-42）：缺失或非数值一律返回 None（按缺失排序）。

    排序输入是不可信数据（配置快照、缓存表格都可能混入脏值）；
    直接 float() 抛 ValueError 会被请求线程的处理器误标成 400 参数错误。
    """
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def series_order(series: pd.Series, *, ascending: bool = False) -> list[str]:
    """返回排序后的索引名；数值并列按名称升序，NaN/非数值一律排最后。"""
    def key(pair):
        name, value = pair
        number = _sortable(value)
        missing = number is None
        return (missing, 0.0 if missing else (number if ascending else -number), str(name))

    return [name for name, _ in sorted(series.items(), key=key)]


def ranked_series(series: pd.Series, *, ascending: bool = False) -> pd.Series:
    return series.reindex(series_order(series, ascending=ascending))


def ranked_frame(frame: pd.DataFrame, by: str, *, ascending: bool = True,
                 tie: str | None = "key") -> pd.DataFrame:
    """按 by 排序，缺失值排最后；并列按 tie 列升序（tie 不存在则忽略）。"""
    if frame is None or len(frame) == 0:
        return frame
    work = frame.copy()
    marker = "__ordering_missing__"
    work[marker] = work[by].isna()
    columns, orders = [marker, by], [True, ascending]
    if tie and tie in work.columns and tie != by:
        columns.append(tie)
        orders.append(True)
    work = (work.sort_values(columns, ascending=orders, kind="mergesort")
            .drop(columns=marker))
    return work.reset_index(drop=True)


def rows_order(rows: Iterable[dict], *, value: str, ascending: bool = False,
               tie: str = "code") -> list[dict]:
    """对 list[dict] 按数值字段排序，并列按 tie 字段（缺省时用 name）升序。"""
    def key(row):
        number = _sortable(row.get(value))
        missing = number is None
        name = row.get(tie) or row.get("name") or row.get("名称") or ""
        return (missing, 0.0 if missing else (number if ascending else -number), str(name))

    return sorted(rows, key=key)
