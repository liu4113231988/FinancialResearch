from __future__ import annotations
from typing import Optional
import logging
import numpy as np
import pandas as pd
import market_research.config as C
from market_research.data import storage
from market_research.data.cache import _cached, _cache_info_write, _with_info
from market_research.data.providers.funds import NAV_BASIS_CUM, NAV_BASIS_TOTAL, fetch_fund_nav, fetch_fund_total_return
from market_research.data.providers.indices import fetch_universe
from market_research.data.providers.valuation import fetch_pe_panel, fetch_dividend_panel

LOG = logging.getLogger(__name__)

def align_prices(px: pd.DataFrame, nav: pd.Series, max_ffill: int = 5,
                 min_coverage: float = 0.97) -> pd.DataFrame:
    """
    把基金净值与指数价格对齐到共同交易日。

    规则：
      1. 以基金净值的交易日为基准（基金开放日才是可交易日）；
      2. 指数缺失值前向填充，但连续填充不超过 max_ffill 天；
      3. 先剔除区间内覆盖率不足 min_coverage 的指数（如已停编的老指数），
         再掐头去尾保留全部序列均有有效值的区间。
    """
    df = px.reindex(nav.index).ffill(limit=max_ffill)
    df["__FUND__"] = nav.reindex(df.index)

    # 覆盖率过滤：优先剔除"中途停编"的指数，避免整段历史被截断
    warnings = []
    cov = df.notna().mean()
    dropped = cov[(cov < min_coverage) & (df.columns != "__FUND__")].index.tolist()
    if dropped:
        message = (f"覆盖率不足已剔除 {len(dropped)} 个：" +
                   ", ".join(f"{c}({C.NAME_OF.get(c, c)})" for c in dropped))
        # 库函数不要用 print 打日志：调用方（Web/CLI）需要拿到这句话放进 meta
        LOG.warning("%s", message)
        warnings.append(message)
    df = df.drop(columns=dropped)

    # 掐头去尾并剔除中间缺失日（连续缺失超过 max_ffill 的交易日直接放弃）
    out = df.dropna()
    # ★ 降级必须留痕：否则前端/报告只看到"对齐后的干净表"，
    #   不知道口径已经从"全候选池"变成了"剔除后的子集"。
    out.attrs["warnings"] = warnings
    return out


def to_log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """价格 → 对数收益。"""
    r = np.log(df).diff().dropna()
    return r.replace([np.inf, -np.inf], np.nan).dropna()


def _accumulate_panel(name: str, fetch, syms, verbose: bool = True) -> pd.DataFrame:
    """
    累积缓存：csindex 官方估值文件**只保留最近约一个月**的日频数据，
    历史长度必须靠"每次把最近一段合并进本地缓存"日积月累。

    规则：
      * 旧缓存永远保留（断网/失败时返回既有数据，指纹不变、不打扰下游）；
      * 新数据在重叠日期上覆盖旧值（官方会修正当期数值），缺失列沿用旧列；
      * 只有真的合并进新内容才回写 storage（否则空转也会改 stamp）。
    旧乐咕时代的月度历史（若有）与新日频数据同列名合并，一并保留。
    """
    cached = storage.read(name)
    if cached is not None and not isinstance(cached.index, pd.DatetimeIndex):
        cached.index = pd.to_datetime(cached.index)
    # 首次抓全量；已有缓存时优先补缺列（首次失败过的标的），否则抓全量以覆盖修订
    targets = ([s for s in syms if cached is None or s not in cached.columns]
               if cached is not None else list(syms)) or list(syms)
    try:
        fresh = fetch(targets, verbose=verbose)
    except Exception as e:                                    # noqa: BLE001
        if verbose:
            print(f"  [{name}] 抓取失败，沿用本地累积缓存：{type(e).__name__}")
        return cached if cached is not None else pd.DataFrame()
    if fresh is None or fresh.empty:
        return cached if cached is not None else pd.DataFrame()
    if cached is None or cached.empty:
        df = fresh.sort_index()
    else:
        union = cached.index.union(fresh.index)
        df = fresh.reindex(union).combine_first(cached.reindex(union)).sort_index()
    if verbose:
        print(f"  [{name}] 累积 {df.shape}：{df.index[0].date()} ~ "
              f"{df.index[-1].date()}（本次新增 {len(fresh)} 期）")
    storage.write(name, df, _with_info({}, df))
    return df


def load_pe_panel(verbose: bool = True) -> pd.DataFrame:
    """
    宽基指数估值面板（csindex 日频市盈率的滚动历史分位，0~1），缓存 `pe_panel`。

    ★ 取数源为 csindex（官方文件只留近一个月），历史靠 `_accumulate_panel`
      日积月累；历史不足时滚动分位按 `min_periods` 输出 NaN，估值通道按
      无估值处理，等积累充足后自然变准。
    """
    from market_research.modeling.signals import PE_SYMBOL, PE_FALLBACK

    syms = sorted(set(PE_SYMBOL.values()) | {PE_FALLBACK})
    return _accumulate_panel("pe_panel", fetch_pe_panel, syms, verbose)


def load_dividend_panel(verbose: bool = True) -> pd.DataFrame:
    """
    宽基指数日频股息率面板（csindex `股息率2`，年化小数），缓存 `div_panel`。

    P1-10 覆盖边界：csindex 不含申万行业指数，行业腿的股息仍由
    `C.RBSA["index_dividend_annual"]` 情景参数承担；本面板是 csindex 覆盖
    范围内（宽基/风格）的真实股息率历史，同样按日累积。
    """
    from market_research.modeling.signals import PE_SYMBOL, PE_FALLBACK

    syms = sorted(set(PE_SYMBOL.values()) | {PE_FALLBACK})
    return _accumulate_panel("div_panel", fetch_dividend_panel, syms, verbose)


def load_index_universe(start: Optional[str] = None,
                        end: Optional[str] = None,
                        refresh: bool = False,
                        verbose: bool = True,
                        allow_fetch: bool = True) -> pd.DataFrame:
    """
    候选指数池的价格宽表（**全局缓存**，与基金无关）。

    ★ 为什么要单独缓存：指数价格对一只基金和对另一只基金是同一份数据。
    早期实现把它塞进 load_panel 的 per-fund 缓存里，于是每分析一只基金
    都要重新抓 47 个指数 —— 扩池到 50 只就是 2350 次网络请求。
    拆出来之后，无论分析多少只基金，指数只抓一次。

    返回列 = 候选指数代码（不含 __FUND__），**未做对齐** —— 对齐依赖具体
    基金的交易日轴，仍由 load_panel 做。

    ★ start/end 默认 None 而不是 `C.START_DATE`：Python 的默认参数在
      **函数定义时**求值，写成 `start=C.START_DATE` 会让运行时修改
      `C.START_DATE`（如 `--window-years 2`）完全无效。None 表示"用当前配置"。
    """
    start = start or C.START_DATE
    end = end if end is not None else C.END_DATE
    cache_name = f"index_universe_{start}" + (f"_to{end}" if end else "")
    meta = {"v": C.DATA_CACHE_VERSION, "n_candidates": len(C.NAME_OF),
            "start": start, "end": end}

    def build() -> pd.DataFrame:
        if verbose:
            print(f"[data] 抓取候选指数池 {len(C.NAME_OF)} 个 ...")
        return fetch_universe(C.NAME_OF.keys(), start, end, verbose=verbose)

    df = _cached(cache_name, build, meta=meta, refresh=refresh,
                 allow_fetch=allow_fetch)
    # ★ 缺失资产必须留痕（N-07）：部分行情失败会静默得到一个"不完整指数池"，
    #   下游的覆盖率过滤会把它们当"中途停编"剔除，掩盖了真实原因是抓取失败。
    #   无条件写入（空列表 = 无缺失），否则"没这个字段"和"没缺失"分不清。
    missing = [c for c in C.NAME_OF if c not in df.columns]
    # 只读模式（allow_fetch=False）下补取同样是联网抓取，必须跳过。
    if missing and not refresh and allow_fetch:
        try:
            add = fetch_universe(missing, start, end, verbose=verbose)
            add = add.loc[:, add.columns.intersection(missing)].dropna(axis=1, how="all")
            if not add.empty:
                df = pd.concat([df, add], axis=1).sort_index()
                storage.write(cache_name, df, _with_info(meta, df))
        except Exception:
            LOG.warning("补取候选指数失败，保留已有行情", exc_info=True)
        missing = [c for c in C.NAME_OF if c not in df.columns]
    _cache_info_write(cache_name, {
        "missing_assets": missing,
        "n_missing": len(missing),
        "n_expected": len(C.NAME_OF)})
    if missing:
        # 用 logging 而不是 print：这条警告会在 GBK 控制台打印 ⚠（U+26A0）时
        # 抛 UnicodeEncodeError，把一次取数变成"程序崩了"。日志处理器会吞掉
        # 编码错误，且这里本来也只是一条降级说明。
        LOG.warning("候选池缺 %d/%d 个指数（%s）——重跑本命令可补齐，已成功的部分不会重复请求",
                    len(missing), len(C.NAME_OF),
                    ", ".join(missing[:6]) + ("…" if len(missing) > 6 else ""))
    return df


def load_fund_nav_cached(fund_code: str, start: Optional[str] = None,
                         refresh: bool = False,
                         basis: str = NAV_BASIS_TOTAL,
                         allow_fetch: bool = True) -> pd.Series:
    """
    单只基金的收益序列（独立缓存，~100 KB，避免与指数抓取耦合）。

    basis = NAV_BASIS_TOTAL（默认，P0-01 修正口径）：日频总回报，含分红再投。
            由累计净值 + 单位净值 + 月频累计收益率校准得到。
    basis = NAV_BASIS_CUM：数据源的累计净值直算。**已实测系统性低估
            （14 只基金平均 −23.19%，最差 −76.12%）**，仅用于兼容与对照。

    缓存名带 basis 后缀，两种口径互不覆盖。
    """
    import market_research.tasks.sync as sync
    sync.register("fund", fund_code, basis)
    if not refresh:
        history = sync.cached("fund", fund_code, basis, start, None)
        if history is not None:
            result = history.iloc[:, 0].copy()
            result.name = f"fund_{fund_code}"
            return result
    suffix = "" if basis == NAV_BASIS_CUM else f"_{basis}"
    # None → 当前配置的起点（见 load_index_universe 的说明）
    start = start or C.START_DATE
    cache_name = f"fund_nav_{fund_code}_{start}{suffix}"
    meta = {"v": C.DATA_CACHE_VERSION, "code": fund_code, "start": start,
            "basis": basis}

    class _DegradedNav(Exception):
        """总回报口径不可用。绝不能把退化结果写进 total 缓存名下。"""

    def build() -> pd.DataFrame:
        if basis == NAV_BASIS_TOTAL:
            s = fetch_fund_total_return(fund_code, start)
            # ★ 复查发现的缓存污染：旧实现把退回的 cum 序列原样返回，
            #   _cached 会把它写进 `_total` 后缀的缓存名（meta 还标
            #   basis="total"），之后所有读取持续命中口径错误的缓存。
            #   这里显式拒绝：退回结果只本次内存使用，不落盘。
            if bool(getattr(s, "attrs", {}).get("degraded")):
                raise _DegradedNav(
                    f"{fund_code} 无法构造总回报口径（单位净值或累计收益率锚点缺失）")
            return s.to_frame()
        return fetch_fund_nav(fund_code, start).to_frame()

    try:
        df = _cached(cache_name, build, meta=meta, refresh=refresh,
                     allow_fetch=allow_fetch)
    except _DegradedNav as e:
        print(f"[data] ⚠ {e}；本次退回累计净值口径，结果【未缓存】")
        return fetch_fund_nav(fund_code, start)
    s = df.iloc[:, 0] if isinstance(df, pd.DataFrame) else df
    s.name = f"fund_{fund_code}"
    return s


def fetch_start_for(start: Optional[str] = None) -> str:
    """
    **抓取**起点：取 `start` 与 `C.FETCH_START_DATE` 的较早者。

    ★ 为什么分析与抓取必须用两个起点（2026-10-12 改）：
    缓存名里带 start（`fund_nav_{code}_{start}_total.csv`）。分析窗口改成滚动
    近 N 年之后，如果抓取也跟着滚动，那么每换一次窗口、甚至每天运行都会让
    全部缓存 miss —— 离线环境下等于拿不到任何数据；在线环境下则是每次重抓
    全部指数，白白放大几十倍请求量。

    所以抓取一律用固定长区间（缓存稳定 + 预热期充足），拿到之后再裁剪到
    分析窗口。若用户显式要求比 FETCH_START_DATE 更早的区间，才真正提前抓取。

    ISO 格式（YYYY-MM-DD）字符串可直接字典序比较，等价于日期比较。
    """
    s = str(start) if start else ""
    # 取**较早者**：分析起点晚于固定起点时用固定起点（白赚一段预热数据，
    # 且缓存必然命中）；只有用户显式要求更早的区间才真的提前抓取。
    if not s or s > C.FETCH_START_DATE:
        return C.FETCH_START_DATE
    return s


def load_panel(fund_code: str, start: Optional[str] = None,
               end: Optional[str] = None, refresh: bool = False,
               verbose: bool = True,
               nav_basis: str = NAV_BASIS_TOTAL,
               allow_fetch: bool = True) -> pd.DataFrame:
    """
    返回对齐后的价格面板：列 = 候选指数 + __FUND__，**已裁剪到 [start, end]**。

    存储分层（★ 面板本身**不再缓存**，即时重建）：

        指数池  index_universe_{fs}.csv         全局一份（与基金无关）
        净值    fund_nav_{code}_{fs}.csv         每只一份（~100 KB）
        面板    = 两者 → align_prices() 即时重建（纯内存，<100 ms）

    为什么不缓存面板：面板里 95% 以上的列是指数，逐基金缓存会让同样的指数
    数据存 N 份（1 MB × 基金数）。拆成两个原子数据源之后，存储与抓取量都从
    O(基金 × 指数) 降到 O(基金 + 指数)；对齐是毫秒级内存操作，重建不亏。

    ★ `start` 是**分析**起点，抓取用 fetch_start_for(start)（通常更早）。
    对齐按**完整抓取区间**做（覆盖率筛选需要全样本），之后再裁剪到分析窗口 ——
    顺序反过来会让覆盖率统计只看到窗口内的样本，把"曾经存在过的指数"误判为
    缺失而剔除。

    ★ `allow_fetch=False` 是**只读**模式：净值或指数池缓存任一缺失就抛
      `CacheMiss`，不会替调用方联网取数。供 URL 请求线程内的轻量接口
      （`/api/corr`）使用 —— 请求线程里的抓取既不可取消，也不占任务队列名额。
    """
    start = start or C.START_DATE
    end = end if end is not None else C.END_DATE
    fs = fetch_start_for(start)
    nav = load_fund_nav_cached(fund_code, fs, refresh=refresh, basis=nav_basis,
                               allow_fetch=allow_fetch)
    # ★ P1-15：净值必须先截到分析结束日再做对齐与覆盖率筛选。
    #   指数池按 end 截止；若净值延伸到 end 之后，那些日期的指数缺失会
    #   稀释覆盖率分母，把完整覆盖历史窗口的指数误判为"中途停编"而剔除
    #   （历史窗口的分析结果随"今天抓到的净值长度"漂移）。截断只影响
    #   对齐/筛选，起始侧的预热区间（fs < start）照旧保留。
    if end is not None:
        nav = nav.loc[nav.index <= pd.Timestamp(end)]
    if verbose:
        tag = "总回报" if nav_basis == NAV_BASIS_TOTAL else "累计净值(旧口径)"
        print(f"[data] 基金 {fund_code} {tag} {len(nav)} 行  "
              f"{nav.index[0].date()} ~ {nav.index[-1].date()}")
    px = load_index_universe(fs, end, refresh=refresh, verbose=verbose,
                             allow_fetch=allow_fetch)
    out = align_prices(px, nav)
    warnings = list(out.attrs.get("warnings") or [])

    # 裁剪到分析窗口
    if start:
        out = out.loc[out.index >= pd.Timestamp(start)]
    if end:
        out = out.loc[out.index <= pd.Timestamp(end)]
    out.attrs["warnings"] = warnings
    if out.empty:
        raise ValueError(
            f"{fund_code} 在 {start or '全区间'} ~ {end or '最新'} 内没有可用数据"
            f"（抓取区间 {fs} ~ {end or '最新'}，请放宽分析窗口或检查代码）")
    if out.shape[1] <= 1:
        # ★ P1-15：只剩 __FUND__ 说明所有候选指数都没通过覆盖率过滤，
        #   下游 RBSA 无从拟合，必须明确报错而不是静默返回单列面板。
        raise ValueError(
            f"{fund_code} 在 {start or '全区间'} ~ {end or '最新'} 内没有任何候选指数"
            f"存活（对齐后 {len(out)} 个交易日，覆盖率过滤详见警告："
            f"{warnings or '无'}）")
    if verbose:
        print(f"[data] 对齐后：{out.shape[0]} 交易日 × {out.shape[1]} 条序列 "
              f"({out.index[0].date()} ~ {out.index[-1].date()})"
              + (f"  [分析窗口 {start} 起]" if start else ""))
    return out
