"""Shared forecast targets and clocks; missing quotes are never filled."""
import numpy as np
import pandas as pd


def horizon_data(close, horizon, calendar=None):
    if isinstance(horizon,bool) or not isinstance(horizon,int) or horizon<1:
        raise ValueError('预测周期需为正整数')
    index=close.index
    if not isinstance(index,pd.DatetimeIndex) or index.hasnans or not index.equals(index.normalize()) or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError('预测日期需有效、唯一且升序')
    axis=index
    if calendar is not None:
        if not isinstance(calendar,pd.DatetimeIndex) or calendar.empty or calendar.hasnans or not calendar.equals(calendar.normalize()) or not calendar.is_unique or not calendar.is_monotonic_increasing or calendar.tz!=index.tz:
            raise ValueError('预测日历需有效、唯一、升序且与行情时区一致')
        if not index.isin(calendar).all():
            raise ValueError('预测行情包含日历外日期')
        axis=calendar[(calendar>=index.min())&(calendar<=index.max())]
    aligned=close.reindex(axis)
    returns=(aligned.shift(-horizon)/aligned-1).reindex(index)
    returns=returns.where(np.isfinite(returns))
    return returns,axis.get_indexer(index),axis
