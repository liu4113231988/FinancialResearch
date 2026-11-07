"""确定性排序：并列时按名称/代码升序，保证「同输入同输出」（P3-G）。"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def _missing(value) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def series_order(series: pd.Series, *, ascending: bool = False) -> list[str]:
    """返回排序后的索引名；数值并列按名称升序，NaN 一律排最后。"""
    def key(pair):
        name, value = pair
        missing = _missing(value)
        number = 0.0 if missing else float(value)
        return (missing, number if ascending else -number, str(name))

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
        number = row.get(value)
        missing = _missing(number)
        number = 0.0 if missing else float(number)
        name = row.get(tie) or row.get("name") or row.get("名称") or ""
        return (missing, number if ascending else -number, str(name))

    return sorted(rows, key=key)
