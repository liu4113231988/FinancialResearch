"""Versioned vendor session calendar; never infer sessions from weekdays."""
from functools import lru_cache
import hashlib
from importlib.resources import files
import json

import pandas as pd


@lru_cache(maxsize=1)
def _calendar_resource():
    import akshare
    contents = files('akshare').joinpath('file_fold/calendar.json').read_bytes()
    sessions = pd.DatetimeIndex(pd.to_datetime(json.loads(contents), format='%Y%m%d'))
    if sessions.empty or sessions.hasnans or not sessions.is_unique or not sessions.is_monotonic_increasing:
        raise ValueError('供应商日历日期无效、重复或未升序')
    return sessions, {'source':'AKShare bundled file_fold/calendar.json','version':akshare.__version__,
                      'sha256':hashlib.sha256(contents).hexdigest(),
                      'coverage_start':str(sessions[0].date()),'coverage_end':str(sessions[-1].date())}


def bundled_calendar(start, end):
    sessions, info = _calendar_resource()
    if sessions[0] > pd.Timestamp(start) or sessions[-1] < pd.Timestamp(end):
        raise ValueError('供应商日历未覆盖完整请求区间')
    return sessions.copy(), dict(info)
