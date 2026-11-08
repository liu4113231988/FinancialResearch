from __future__ import annotations
from typing import Dict, Iterable, Optional
import logging
import pandas as pd
import market_research.config as C
from market_research.data import provenance
from market_research.data.http import _retry, _clean_series
from market_research.errors import ProviderContractError

LOG = logging.getLogger(__name__)

def fetch_index_sina(code: str, start: Optional[str] = None) -> pd.Series:
    """新浪指数日线收盘价。code 形如 sh000300 / sz399006。"""
    import akshare as ak

    df = _retry(ak.stock_zh_index_daily, symbol=code, source="index_sina")
    provenance.require_columns(df, ["date", "close"], "index_sina")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["close"].astype(float).sort_index()
    s.name = code
    return _clean_series(s, start)


def fetch_index_sw(code: str, start: Optional[str] = None) -> pd.Series:
    """申万一级行业指数收盘价。code 形如 801010。"""
    import akshare as ak

    df = _retry(ak.index_hist_sw, symbol=code, period="day", source="index_sw")
    has_date = "日期" in df.columns or len(df.columns) >= 2
    has_close = "收盘" in df.columns or "close" in df.columns
    if not (has_date and has_close):
        raise ProviderContractError(
            f"index_sw 返回缺少日期/收盘字段（实际列 {list(df.columns)}）；"
            f"端点 {provenance.CONTRACTS['index_sw']['endpoint']}")
    dcol = "日期" if "日期" in df.columns else df.columns[1]
    ccol = "收盘" if "收盘" in df.columns else "close"
    df[dcol] = pd.to_datetime(df[dcol])
    s = df.set_index(dcol)[ccol].astype(float).sort_index()
    s.name = code
    return _clean_series(s, start)


def _fetch_one(code: str, start: str, end: Optional[str]):
    if code in C.SW_INDUSTRY:
        s = fetch_index_sw(code, start)
    else:
        s = fetch_index_sina(code, start)
    if end:
        s = s[s.index <= pd.Timestamp(end)]
    return code, s


def fetch_universe(codes: Iterable[str], start: str, end: Optional[str] = None,
                   verbose: bool = True, workers: int = 6) -> pd.DataFrame:
    """
    并发抓取整个候选池，返回 宽表 DataFrame(index=日期, columns=指数代码)。

    串行抓 44 个指数约需 45 秒，6 线程并发可压到 8 秒左右。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    codes = list(codes)
    series: Dict[str, pd.Series] = {}
    ok = fail = 0
    errors = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_one, c, start, end): c for c in codes}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                code, s = fut.result()
                if len(s) > 200:
                    series[code] = s
                    ok += 1
                else:
                    fail += 1
                    errors.append(f"{code}: 仅 {len(s)} 行")
                    if verbose:
                        print(f"  [data] {code} {C.NAME_OF.get(code,''):8s} "
                              f"跳过（仅 {len(s)} 行）")
            except Exception as e:      # noqa: BLE001
                fail += 1
                errors.append(f"{code}: {type(e).__name__} {str(e)[:60]}")
                if verbose:
                    print(f"  [data] {code} {C.NAME_OF.get(code,''):8s} "
                          f"失败: {type(e).__name__} {str(e)[:50]}")

    if verbose:
        print(f"  [data] 候选池抓取完成：成功 {ok} / 失败 {fail}")
    if codes and ok == 0:
        # 全部失败通常意味着源站结构/权限变化，必须显式失败而不是返回空表
        raise ProviderContractError(
            "候选指数全部抓取失败，无法判断是网络问题还是源站结构变化："
            + "；".join(errors[:6]) + ("…" if len(errors) > 6 else ""))
    px = pd.DataFrame(series).sort_index()
    if errors:
        px.attrs["fetch_errors"] = errors
        # verbose=False（Web/CLI 默认）时上面的 print 全部不可见，
        # 至少留一条日志说明"为什么少了这些指数"。
        LOG.warning("候选池 %d/%d 个指数抓取失败或样本不足：%s",
                    fail, len(codes),
                    "；".join(errors[:6]) + ("…" if len(errors) > 6 else ""))
    return px
