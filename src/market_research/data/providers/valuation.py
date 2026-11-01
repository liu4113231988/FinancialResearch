from __future__ import annotations
from typing import Iterable, Optional
import pandas as pd
from market_research.data.http import _retry

# 估值面板列名（= PE_SYMBOL 的值）→ 中证官网（csindex）指数代码。
# ★ 取数源（2026-11-01）：统一只用 `stock_zh_index_value_csindex`（中证官方）。
#   旧实现走乐咕 `stock_index_pe_lg`（月度），当前 akshare 版本已无该接口，
#   pe_panel 实际处于损坏状态；csindex 是官方日频口径且字段规范。
#   覆盖边界：csindex 只收录中证/上证系指数 —— 申万行业（801xxx）与
#   国证/巨潮系列（399xxx）不在其中，估值通道本就只映射这 6 个宽基。
CSINDEX_CODE = {
    "沪深300": "000300", "上证50": "000016", "中证500": "000905",
    "中证1000": "000852", "中证100": "000903", "上证380": "000009",
}


def _csindex_value(df: pd.DataFrame, column: str) -> pd.Series:
    """从 csindex 估值表提取指数数值列（单位随列自定，异常值剔除）。"""
    if column not in df.columns:
        raise ValueError(f"csindex 返回缺少 {column} 列：{list(df.columns)}")
    s = df.copy()
    s["日期"] = pd.to_datetime(s["日期"])
    out = (s.set_index("日期")[column].astype(float).sort_index())
    return out[out > 0]


def _percentile(s: pd.Series, lookback_years: float) -> pd.Series:
    """
    滚动历史分位（0~1）：当前值在最近 lookback_years 年内的相对位置。

    ★ csindex 历史靠本地日积月累（官方文件只留近一个月），早期样本不足
      8 年：min_periods 放宽到"至少 1 年"，让分位尽早可用（偏弱但随积累
      变准）；不足 1 年输出 NaN，估值通道按无估值处理。
    """
    win = int(250 * lookback_years)
    return s.rolling(win, min_periods=min(win // 3, 250)).apply(
        lambda a: float((a <= a[-1]).mean()), raw=True)


def _fetch_csindex(symbol_code: str, column: str,
                   verbose: bool, label: str,
                   lookback_years: float = 8.0) -> Optional[pd.Series]:
    import akshare as ak
    df = _retry(ak.stock_zh_index_value_csindex, tries=2, wait=1.0,
                symbol=symbol_code)
    s = _csindex_value(df, column)
    if s.empty:
        if verbose:
            print(f"  [csindex] {label:8s} 无有效数据")
        return None
    if verbose:
        print(f"  [csindex] {label:8s} {len(s):5d} 期  {s.index[0].date()} ~ "
              f"{s.index[-1].date()}")
    return s


def fetch_pe_panel(symbols: Iterable[str], lookback_years: int = 8,
                   verbose: bool = True) -> pd.DataFrame:
    """
    拉取宽基指数的滚动市盈率历史（csindex 官方**日频**），并转为
    **滚动历史分位**（0~1）。

    用途：判断风格基准的"绝对估值水位"。分位=1 表示处于历史最贵档。
    返回列名为指数中文名（与 `PE_SYMBOL` 的值一致，信号引擎按它取列）。

    ★ 口径说明：旧乐咕通道是月度，滚动分位窗口按 12*lookback_years 期算；
      csindex 是日频，分位窗口按 250*lookback_years 个交易日算 —— 两者
      语义相同（约 8 年），粒度更细。`DATA_CACHE_VERSION` 已 +1 使旧缓存
      失效。估值通道的发布滞后（pe_lag_days，默认 10 日）对日频数据偏保守，
      保持默认不动；要收紧应在显式配置里调，不在此处隐式变更。
    """
    out = {}
    for sym in symbols:
        code = CSINDEX_CODE.get(sym, sym)   # 已传 csindex 代码时直接使用
        try:
            s = _fetch_csindex(code, "市盈率2", verbose, sym, lookback_years)
            if s is None:
                continue
            out[sym] = _percentile(s, lookback_years)
        except Exception as e:      # noqa: BLE001
            if verbose:
                print(f"  [csindex] {sym:8s} 失败 {type(e).__name__} {str(e)[:40]}")
    return pd.DataFrame(out).sort_index()


def fetch_dividend_panel(symbols: Iterable[str], verbose: bool = True,
                         lookback_years: int = 8) -> pd.DataFrame:
    """
    拉取宽基指数的**日频股息率**（csindex `股息率2`，%，转年化小数）。

    覆盖边界（P1-10）：csindex 不含申万行业指数 —— 行业腿的股息仍只能靠
    `C.RBSA["index_dividend_annual"]` 情景参数或个股分红推导；本面板提供的是
    csindex 覆盖范围内（宽基/风格）的真实股息率历史，可作数据锚点。
    返回列名为指数中文名，值为年化小数（0.02 = 2%）。
    """
    out = {}
    for sym in symbols:
        code = CSINDEX_CODE.get(sym, sym)
        try:
            s = _fetch_csindex(code, "股息率2", verbose, sym, lookback_years)
            if s is None:
                continue
            out[sym] = s / 100.0        # csindex 以 % 为单位
        except Exception as e:          # noqa: BLE001
            if verbose:
                print(f"  [csindex] {sym:8s} 失败 {type(e).__name__} {str(e)[:40]}")
    return pd.DataFrame(out).sort_index()
