# -*- coding: utf-8 -*-
"""
分析窗口解析（2026-10-13）。

背景：分析窗口默认近 5 年（`C.DEFAULT_WINDOW_YEARS`）、下限 2 年
（`C.MIN_WINDOW_YEARS`）。但各研究脚本此前**没有任何入口**能改窗口，
恒用默认值 —— 想跑一次近 2 年的对比只能手动改代码或设环境变量。

本模块提供三件事：

1. `add_window_args(ap)`   给 argparse 统一加 `--window-years / --window-start
                           / --window-end`（不复用 `--start/--end`：`run.py`
                           的 `--start/--end` 是**回测**窗口，语义不同，
                           同名会让两者混为一谈）。
2. `resolve_window(...)`   纯函数，只计算不碰全局状态，便于测试。
3. `apply_window(args)`    解析并**就地覆盖** `C.START_DATE` / `C.END_DATE`。

★ 为什么要"就地覆盖"而不是把窗口逐个传进几十个调用点：
   这些脚本里有大量 `load_panel(code)` / `run_fund(code)` 不显式传窗口的调用，
   逐个改既啰嗦又容易漏。`data.load_panel` 的 `start/end` 默认值已在函数体内
   惰性解析（`start or C.START_DATE`，见 src/data.py 的说明），所以覆盖属性
   就能让它们全部跟随。

   注意这个"惰性解析"是**前提**：Python 的默认参数在函数定义时求值，
   若写成 `def load_panel(..., start=C.START_DATE)`，运行时改属性毫无作用。
"""
from __future__ import annotations

import argparse
import datetime as _dt
from typing import Optional, Tuple

import pandas as pd

import market_research.config as C


def _iso(x, label: str) -> str:
    """
    严格校验 YYYY-MM-DD 并原样返回字符串。

    ★ 不能用 `pd.Timestamp(x)` 判断：pandas 会接受 "2021/10/12"、"2021.10.12"、
      甚至 "Oct 12 2021" 等一堆格式。而下游 `data.fetch_start_for()` 做的是
      **字符串字典序**比较，"2021/10/12" 与 "2016-01-01" 的字典序关系和日期
      先后无关，于是窗口会被静默判成"晚于固定起点"从而走错分支。
      实测（R13）：`resolve_window(start="2021/10/12")` 第一版**没有报错**。
    """
    s = str(x).strip()
    try:
        d = _dt.date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{label} {x!r} 不是 YYYY-MM-DD 格式")
    # ★ 还要挡"基本格式"：Python 3.11+ 的 fromisoformat 接受 "20211012"
    #   （无分隔符）。它解析出来是同一个日期，但下游 fetch_start_for 做的是
    #   **字符串**字典序比较 —— "20211012" 与 "2016-01-01" 的字典序关系和
    #   日期先后毫无关系，窗口会被静默判错。所以要求往返一致。
    if d.isoformat() != s:
        raise ValueError(f"{label} {x!r} 不是 YYYY-MM-DD 格式（应为四位年-两位月-两位日）")
    return s


def parse_window(start=None, end=None, min_days=None, today=None):
    """CLI 与 Web 共用 ISO 日期校验；None 表示沿用默认窗口。"""
    def date_value(value, label):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return pd.Timestamp(_iso(value, label))

    s = date_value(start, "起始日期")
    e = date_value(end, "结束日期")
    limit = pd.Timestamp(
        today if today is not None else _dt.date.today()).normalize()
    # 未来结束日没有任何可用行情，却会让下游按超长区间发起全量抓取
    # （例如 2099-12-31 会遍历全部指数历史）。直接拒绝，不要靠抓取端兜底。
    if e is not None and e > limit:
        raise ValueError(f"结束日期 {e.date()} 不能晚于今天 {limit.date()}")
    effective_end = e if e is not None else limit
    if s is not None:
        if s >= effective_end:
            raise ValueError(f"起始日 {s.date()} 需早于结束日 {effective_end.date()}")
        minimum = C.MIN_WINDOW_DAYS if min_days is None else int(min_days)
        days = int((effective_end - s).days)
        if days < minimum:
            if min_days is not None:
                raise ValueError(f"分析区间只有 {days} 天，最短需 {minimum} 个自然日；请扩大区间。")
            raise ValueError(
                f"分析区间只有 {days} 天（约 {days / 365.25:.1f} 年），"
                f"最短需 {minimum} 天（{C.MIN_WINDOW_YEARS} 年）。"
                "样本太短时拟合窗口与预热会吃掉大部分数据。")
    return (s.strftime("%Y-%m-%d") if s is not None else None,
            e.strftime("%Y-%m-%d") if e is not None else None)


def add_window_args(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """给 argparse 加统一的窗口参数（返回同一个 parser 以便链式调用）。"""
    g = ap.add_argument_group(
        "分析窗口",
        f"默认近 {C.DEFAULT_WINDOW_YEARS} 年，下限 {C.MIN_WINDOW_YEARS} 年。"
        "十年跨度会混入风格漂移与经理变更，拟合出的画像是历史平均，"
        "对当下没有指导意义。")
    g.add_argument("--window-years", type=float, default=None, metavar="N",
                   help=f"分析窗口年数（默认 {C.DEFAULT_WINDOW_YEARS}，"
                        f"最小 {C.MIN_WINDOW_YEARS}）")
    g.add_argument("--window-start", default=None, metavar="YYYY-MM-DD",
                   help="分析窗口起始日；与 --window-years 二选一，"
                        "给了则本项优先")
    g.add_argument("--window-end", default=None, metavar="YYYY-MM-DD",
                   help="分析窗口结束日，默认取到最新")
    return ap


def _start_from_end(end: str, years: float) -> str:
    """
    以显式结束日为锚向前推 `years` 年（P2-19）。

    整数年按日历回退（闰年 2/29 由 pandas DateOffset 自动落到 2/28），
    小数部分按 365.25 天/年折算成日。没有这个锚点，"年数 + 历史截止日"
    这组参数无法表达一个固定的历史窗口 —— 旧实现从"今天"回推，
    `years=2, end=2024-01-01` 会算出晚于 end 的起点然后报错。
    """
    e = pd.Timestamp(end)
    whole = int(years)
    start = e - pd.DateOffset(years=whole)
    frac = float(years) - whole
    if frac > 0:
        start = start - pd.Timedelta(days=int(round(frac * 365.25)))
    return start.strftime("%Y-%m-%d")


def resolve_window(years: Optional[float] = None,
                   start: Optional[str] = None,
                   end: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    把窗口参数解析成 `(start, end)`，并校验 2 年下限。

    优先级：`start` > `years` > 当前配置（`C.START_DATE`）。
    年数路径的锚点（P2-19）：
      * 显式 `end` → 以 `end` 为锚向前推年数（`years=2, end=2024-01-01`
        即 2022-01-01 ~ 2024-01-01，历史窗口可精确复现）；
      * 无 `end`   → 锚定今天，或环境变量 `FIL_START_DATE`。
    `FIL_START_DATE` **只在无显式 `end` 的年数路径生效**：它锁定的是
    "默认滚动起点"；显式 `end` 是更具体的用户契约，优先于环境变量。
    `end` 为 None 表示取到最新。
    校验不通过抛 `ValueError`（由调用方决定是退出还是转 4xx）。
    """
    if start:
        s = _iso(start, "窗口起始日")
    elif years is not None:
        years = float(years)
        if years <= 0:
            raise ValueError(f"窗口年数需为正数，收到 {years}")
        if end:
            s = _start_from_end(_iso(end, "窗口结束日"), years)
        else:
            s = C._rolling_start(years)
    else:
        s = C.START_DATE

    return parse_window(s, end)


def apply_window(args, verbose: bool = True) -> Tuple[str, Optional[str]]:
    """
    解析 args 上的窗口参数，就地覆盖 `C.START_DATE` / `C.END_DATE` 并返回之。

    失败时抛 `ValueError` —— 由调用方打印并退出（研究脚本）或转 4xx（Web）。
    """
    s, e = resolve_window(getattr(args, "window_years", None),
                          getattr(args, "window_start", None),
                          getattr(args, "window_end", None))
    C.START_DATE = s
    C.END_DATE = e
    # ★ 运行指纹记录的是 DEFAULT_WINDOW_YEARS，而不是这次实际用的窗口。
    #   不写回的话，`--window-years 2` 跑出来的报告会声称自己是 5 年窗口。
    try:
        from datetime import date
        start_d = date.fromisoformat(s)
        end_d = date.fromisoformat(e) if e else date.today()
        C.DEFAULT_WINDOW_YEARS = round((end_d - start_d).days / 365.25, 2)
    except Exception:                                        # noqa: BLE001
        pass
    if verbose:
        print(f"[窗口] 分析窗口 {s} ~ {e or '最新'}"
              + (f"（{getattr(args, 'window_years', None)} 年）"
                 if getattr(args, "window_years", None) else ""))
    return s, e
