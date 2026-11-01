# -*- coding: utf-8 -*-
"""
基金真实持仓 vs RBSA 拟合持仓的对齐（2026-10-13）。

为什么要有这个模块
----------------
RBSA 用指数收益回归推断"这只基金在赌哪些行业"，权重可解释，但它终究是
**推断** —— 没有直接证据说拟合出的 37% 食品饮料就是真实的 37%。
季报 / 半年报的持仓明细是唯一的**直接 ground truth**。

本模块做两件事：

  1. `align_vs_rbsa()`    **验证**：逐行业对比 RBSA 权重与真实持仓暴露，
                         量化偏差。回答"RBSA 说的和真实的一致吗"。
  2. `holding_benchmark()` **替代**：只用真实持仓构造基准组合，
                         与 RBSA 合成基准比收益。回答"如果直接拿真实持仓
                         当基准，还 ML 拟合干什么"。

数据源与关键限制（每条都会写进输出，不能隐瞒）
------------------------------------------
* `ak.fund_portfolio_hold_em(symbol, date)` 按**年**取，返回该年各季度明细。
* **季报（Q1/Q3）只披露前十大重仓**：实测 110022 每期仅 11~16 只、
  占净值 77~87%；**半年报（Q2）与年报（Q4）才是完整持仓**（48~100 只、
  87~91%）。因此本模块**只做半年度对齐**（`semiannual_exposure` 只取
  Q2/Q4），季度数据一律不参与 —— 用不完整的前十大做聚合会系统性低估
  小仓位行业，得出的"偏差"是披露规则造成的，不是模型造成的。
* **披露滞后**：Q2 持仓 7 月才公布、Q4 持仓次年 1 月才公布。
  事后验证不受影响，但 `holding_benchmark` 必须从**披露之后**起算，
  否则等于用未来信息构造历史基准（前瞻偏差），比出来的收益不可信。
* 行业口径为**申万一级**（`801xxx`），与项目的 31 个候选行业指数同源，
  不需要任何口径转换。映射表由 31 个指数的成分股并集构造（实测 5221 只）。
* RBSA 权重里还有 16 个**宽基**指数（沪深300 等），持仓只有行业、
  宽基是跨行业的，两者无法一一对应。因此对齐只覆盖**行业部分**，
  宽基暴露单独报告（见 `align_vs_rbsa` 的 `broad_note`）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import market_research.config as C
from market_research.data.files import _atomic_csv_write

LOG = logging.getLogger(__name__)

# 披露滞后（自然日）。★ 完整持仓（全部持仓明细）只出现在**半年报**与**年报**：
#   半年报法定 8/31 前披露（季末 +62 天），年报次年 3/31 前（季末 +90 天）。
#   旧实现统一用 45 天，等于让 Q2/Q4 的持仓提前 17/45 天可用 —— 会引入前瞻偏差。
#   Q1/Q3 只有前十大（季报，季末后 15 个工作日内），用 31 天保守代替。
DISCLOSURE_LAG_DAYS = 45  # 仅当季末月份无法识别时的兜底

_LAG_BY_MONTH = {6: 62, 12: 90, 3: 31, 9: 31}


def disclosure_lag_days(qend) -> int:
    """某期持仓的法定披露滞后（自然日）。"""
    try:
        month = pd.Timestamp(qend).month
    except Exception:                                        # noqa: BLE001
        return DISCLOSURE_LAG_DAYS
    return _LAG_BY_MONTH.get(month, DISCLOSURE_LAG_DAYS)

# 缓存版本：改行业口径或持仓列解析方式时必须 +1
HOLDINGS_CACHE_VERSION = 1


# ====================================================================== 行业映射
# 缺失行业重试冷却期（秒）：失败行业不能永久冻结（P1-11），但离线环境下
# 每次调用都重打 31 个网络请求会拖垮所有调用方，故按冷却期节流补取。
INDUSTRY_RETRY_COOLDOWN_SECONDS = 3600


def _industry_index_codes() -> Dict[str, str]:
    """候选池里的申万一级行业指数（代码 801xxx）。"""
    return {k: v for k, v in C.NAME_OF.items() if str(k).startswith("801")}


def _industry_map_meta_fp() -> Path:
    return C.DATA_DIR / f"industry_map_v{HOLDINGS_CACHE_VERSION}_meta.json"


def _fetch_industry_constituents(code: str, name: str) -> pd.DataFrame:
    import akshare as ak
    d = ak.index_component_sw(symbol=code)
    col = "证券代码" if "证券代码" in d.columns else d.columns[1]
    return pd.DataFrame({"code": d[col].astype(str).str.zfill(6),
                         "industry": name})


def load_industry_map(refresh: bool = False,
                      verbose: bool = True) -> pd.Series:
    """
    股票代码 → 申万一级行业名 的映射（Series，index=6 位代码，value=行业名）。

    构造方式：遍历候选池的 31 个申万一级行业指数，取各自成分股，取并集。
    实测 5221 只股票、约 14 秒（一次性，之后走缓存）。

    ★ 为什么不用东财/中信行业：口径与候选指数（申万）不一致，转换表要人工
      维护且容易错。申万成分股与候选指数同源，零转换成本。

    ★ 不完整映射不得永久冻结（P1-11）：meta 文件记录缺失行业，缓存命中时
      若存在缺失行业，按 `INDUSTRY_RETRY_COOLDOWN_SECONDS` 冷却期节流补取
      并合并；`refresh=True` 无条件重抓全部行业。旧实现一次失败的结果会被
      "非空缓存直接命中"永久保留。注意：这只解决**覆盖不完整**；
      映射本身的幸存者偏差（当前成分回溯历史）仍是数据缺口。
    """
    fp = C.DATA_DIR / f"industry_map_v{HOLDINGS_CACHE_VERSION}.csv"
    meta_fp = _industry_map_meta_fp()
    inds = _industry_index_codes()

    meta = {}
    if meta_fp.exists():
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            meta = {}
    cached = None
    if fp.exists():
        try:
            s = pd.read_csv(fp, index_col=0, dtype=str).iloc[:, 0].dropna()
            if len(s):
                cached = s
        except Exception:                                     # noqa: BLE001
            cached = None                      # 缓存损坏则重建

    if refresh:
        to_fetch = list(inds)
    elif cached is None:
        # 缓存缺失/损坏：无论 meta 怎么说都重建；但刚失败过的场景仍受
        # 冷却期约束 —— 离线环境下否则每次调用都重打全部行业请求。
        if meta.get("last_retry_at"):
            try:
                elapsed = (pd.Timestamp.today()
                           - pd.Timestamp(meta["last_retry_at"])).total_seconds()
                if elapsed < INDUSTRY_RETRY_COOLDOWN_SECONDS:
                    LOG.warning("行业映射上次构建失败，冷却期内不再重试"
                                "（%d 秒后自动重试）",
                                INDUSTRY_RETRY_COOLDOWN_SECONDS)
                    return pd.Series(name="industry", dtype=object)
            except Exception:                                     # noqa: BLE001
                pass
        to_fetch = list(inds)
    else:
        # 无 meta 的旧缓存无法得知当初抓了哪些行业：视为可能不完整，
        # 全量补取一次并写 meta（离线失败也会留下缺失状态与冷却时间）。
        missing = set(meta.get("missing_industries", inds))
        to_fetch = sorted(missing)
        if meta.get("last_retry_at"):
            try:
                elapsed = (pd.Timestamp.today()
                           - pd.Timestamp(meta["last_retry_at"])).total_seconds()
                if elapsed < INDUSTRY_RETRY_COOLDOWN_SECONDS:
                    to_fetch = []            # 冷却期内：先用现有缓存
            except Exception:                                 # noqa: BLE001
                pass
        if not to_fetch:
            return cached

    frames = []
    failed = []
    if to_fetch:
        t0 = time.time()
        for code in to_fetch:
            try:
                frames.append(_fetch_industry_constituents(code, inds[code]))
            except Exception as e:                            # noqa: BLE001
                failed.append(code)
                LOG.warning("行业 %s %s 成分股获取失败：%s",
                            code, inds.get(code, code), type(e).__name__)
            time.sleep(0.15)                  # 轻微限速，避免被限流
        if verbose:
            print(f"[holdings] 行业映射补取 {len(to_fetch)} 个行业"
                  f"（{time.time() - t0:.1f}s）")
    # 新抓取在前：同名股票以最新一次抓取的行业为准（keep=first）
    if cached is not None and not refresh:
        frames.append(cached.rename("industry").to_frame().reset_index()
                      .rename(columns={cached.index.name or "index": "code"}))
    merged = (pd.concat(frames, ignore_index=True)
              if frames else pd.DataFrame(columns=["code", "industry"]))
    m = (merged.drop_duplicates("code", keep="first")
         .set_index("code")["industry"])
    m.name = "industry"
    _atomic_csv_write(m.to_frame(), fp)
    _write_industry_map_meta(meta_fp, failed, list(inds))
    if failed:
        LOG.warning("行业映射缺 %d/%d 个行业（%s）：现有部分已缓存可用，"
                    "冷却期 %d 秒后自动补取",
                    len(failed), len(inds), ", ".join(failed[:6])
                    + ("…" if len(failed) > 6 else ""),
                    INDUSTRY_RETRY_COOLDOWN_SECONDS)
    if verbose:
        print(f"[holdings] 行业映射：{len(m)} 只股票 / "
              f"{len(inds) - len(failed)}/{len(inds)} 个行业"
              + (f"，缺 {len(failed)} 个待补取" if failed else ""))
    return m


def _write_industry_map_meta(meta_fp: Path, failed, inds) -> None:
    meta = {
        "missing_industries": sorted(str(c) for c in failed),
        "n_industries": len(inds),
        "last_retry_at": pd.Timestamp.today().isoformat(),
    }
    meta_fp.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                       encoding="utf-8")


# ====================================================================== 基金持仓
def _holdings_meta_fp(code: str) -> Path:
    return C.DATA_DIR / f"fund_holdings_{code}_v{HOLDINGS_CACHE_VERSION}_meta.json"


def _write_holdings_meta(meta_fp: Path, fetched, failed, wanted) -> None:
    """记录已成功抓取（含合法空结果）与失败的年份，供下次增量补取。"""
    meta = {
        "fetched_years": sorted(int(y) for y in fetched),
        "failed_years": sorted(int(y) for y in failed),
        "start_year": int(wanted[0]),
        "end_year": int(wanted[-1]),
        "refreshed_at": pd.Timestamp.today().isoformat(),
    }
    meta_fp.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                       encoding="utf-8")


def load_fund_holdings(code: str, refresh: bool = False,
                       verbose: bool = True,
                       start_year: Optional[int] = None) -> pd.DataFrame:
    """
    基金全部持仓明细（按年抓取后合并）。

    返回列：股票代码 / 股票名称 / 占净值比例 / 持股数 / 持仓市值 / 季度 /
            期数(如 2026Q2) / 季末(date) / 完整(bool)

    `完整` = 该期是半年报/年报（全部持仓）还是季报（仅前十大）。
    下游**只应使用 `完整 == True`** 的期数做聚合。

    ★ 缓存是**增量的**（P1-18）：meta 文件记录已成功抓取的年份（含合法的
      空结果）与失败年份。请求窗口内未被覆盖的年份（含上次失败的）会自动
      补取并与缓存合并；失败年份**不会伪装成完整历史**，每次调用重试并在
      警告中留痕。`refresh=True` 才无条件重抓整个窗口。
    """
    if start_year is None:
        start_year = int(pd.Timestamp(C.START_DATE).year)
    end_year = int(pd.Timestamp.today().year)
    wanted = list(range(start_year, end_year + 1))
    fp = C.DATA_DIR / f"fund_holdings_{code}_v{HOLDINGS_CACHE_VERSION}.csv"
    meta_fp = _holdings_meta_fp(code)

    meta = {}
    if meta_fp.exists():
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            meta = {}                        # meta 损坏等价于无 meta：全部补取
    fetched = set(meta.get("fetched_years", []))

    cached = None
    if fp.exists():
        try:
            df = pd.read_csv(fp)
            if len(df):
                # ★ 必须重新规整类型：CSV 没有类型信息，"600519" 会被读成 int，
                #   丢掉前导零后与 industry_map 的字符串索引完全对不上 ——
                #   实测症状是"781 条持仓全部未映射"，而联网直取时 100% 命中。
                cached = _retype_holdings(df)
        except Exception:                                     # noqa: BLE001
            cached = None

    if refresh:
        to_fetch = list(wanted)
        fetched = set()          # 强刷后重建 meta：旧成功记录不再豁免本窗口
    else:
        to_fetch = [y for y in wanted if y not in fetched]
        if cached is not None and not to_fetch:
            return cached

    frames = []
    newly_failed = []
    if to_fetch:
        if verbose:
            print(f"[holdings] {code} 抓取 {to_fetch[0]}~{to_fetch[-1]} 年持仓"
                  f"（共 {len(to_fetch)} 年）")
        import akshare as ak
        for yr in to_fetch:
            try:
                d = ak.fund_portfolio_hold_em(symbol=code, date=str(yr))
                # ★ 旧写法 `d is None or len(d)` 会把 None 也 append 进 frames，
                #   后面 pd.concat 直接 TypeError。必须只收非空表。
                if d is not None and len(d):
                    frames.append(d)
                fetched.add(yr)
            except Exception as e:                            # noqa: BLE001
                newly_failed.append(yr)
                LOG.warning("%s %s 年持仓获取失败：%s", code, yr, type(e).__name__)
            time.sleep(0.15)                  # 轻微限速，避免被限流

    failed = ((set(meta.get("failed_years", [])) - set(to_fetch))
              | set(newly_failed))
    if not frames and cached is None:
        # 一期成功的历史都没有：也要写 meta（成功年份不再重复打网络），
        # 但失败年份不写入 fetched，下次调用自动重试。
        _write_holdings_meta(meta_fp, fetched - set(newly_failed), failed, wanted)
        return pd.DataFrame()

    merged = cached
    if frames:
        fresh = _normalize_holdings(pd.concat(frames, ignore_index=True))
        merged = fresh if (merged is None or merged.empty) \
            else pd.concat([merged, fresh], ignore_index=True)
    out = pd.DataFrame()
    if merged is not None and len(merged):
        out = (merged.drop_duplicates(subset=["季末", "股票代码"], keep="last")
               .sort_values("季末").reset_index(drop=True))
        _atomic_csv_write(out, fp)
    _write_holdings_meta(meta_fp, fetched - set(newly_failed), failed, wanted)
    still_failed = sorted(failed & set(wanted))
    if still_failed:
        # 失败年份不得伪装成完整历史：明确留痕，下次调用自动重试。
        LOG.warning("%s 持仓 %s 年抓取失败，历史不完整；下次调用将重试这些年份",
                    code, still_failed)
    return _retype_holdings(out) if len(out) else pd.DataFrame()


def _retype_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """
    把（缓存读回或新建的）持仓表规整成统一类型。

    CSV 往返会丢类型：股票代码变 int（丢前导零）、比例列变 str、季末变 str。
    不规整的后果是 `股票代码.map(industry_map)` 全军覆没，
    而且**不报错** —— 只是静默地什么都映射不上。
    """
    df = df.copy()
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    for c in ("占净值比例", "持股数", "持仓市值"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "季末" in df.columns:
        df["季末"] = pd.to_datetime(df["季末"], errors="coerce")
    if "完整" in df.columns:
        df["完整"] = df["完整"].astype(bool)
    return df


def _normalize_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """把原始明细规整成统一列，并解析季度标签 → 期数 / 季末 / 完整标记。"""
    out = pd.DataFrame(index=df.index)
    out["股票代码"] = df.get("股票代码", pd.Series(index=df.index,
                                                  dtype=str)).astype(str).str.zfill(6)
    out["股票名称"] = df.get("股票名称", pd.Series(index=df.index, dtype=str))
    # 占净值比例可能是 "9.90" 或 9.9；脏值一律转 NaN，绝不静默当 0
    pct = pd.to_numeric(df.get("占净值比例"), errors="coerce")
    out["占净值比例"] = pct
    out["持股数"] = pd.to_numeric(df.get("持股数"), errors="coerce")
    out["持仓市值"] = pd.to_numeric(df.get("持仓市值"), errors="coerce")
    q = df.get("季度", pd.Series(index=df.index, dtype=str)).astype(str)
    out["季度"] = q
    out["期数"] = q.str.extract(r"(\d{4})年(\d)季度")[0] + \
                 "Q" + q.str.extract(r"(\d{4})年(\d)季度")[1]
    ye = pd.to_numeric(q.str.extract(r"(\d{4})年(\d)季度")[0], errors="coerce")
    qq = pd.to_numeric(q.str.extract(r"(\d{4})年(\d)季度")[1], errors="coerce")
    # 季末：Q2=6/30、Q4=12/31（只保留这两期，其它在下游过滤）
    out["季末"] = [
        pd.Timestamp(int(y), 6, 30) if k == 2 else
        (pd.Timestamp(int(y), 12, 31) if k == 4 else pd.NaT)
        for y, k in zip(ye, qq)
    ]
    out["季末"] = pd.to_datetime(out["季末"])
    # 完整持仓：半年报(Q2)与年报(Q4)披露全部，季报(Q1/Q3)只有前十大
    out["完整"] = qq.isin([2, 4])
    return out.dropna(subset=["季末"])


def _map_industries(holdings: pd.DataFrame, industry_map: pd.Series,
                    only_full: bool = True):
    """统一过滤口径，返回 `(已映射, 未映射)` 两张持仓表。

    两张表都保留，是因为"被丢弃的未映射权重"必须能被统计出来：
    暴露表只含能映射到申万一级行业的持仓，未映射的新股/北交所/停牌股
    同样是股票仓位，不能说成现金。
    """
    if holdings is None or holdings.empty:
        return pd.DataFrame(), pd.DataFrame()
    h = holdings[holdings["完整"]] if only_full else holdings
    h = h[h["占净值比例"].notna()].copy()
    if h.empty:
        return pd.DataFrame(), pd.DataFrame()
    # 防御性再规整一次：调用方可能直接传入未经 _retype_holdings 的表
    h["股票代码"] = h["股票代码"].astype(str).str.zfill(6)
    h["industry"] = h["股票代码"].map(industry_map)
    return h[h["industry"].notna()], h[h["industry"].isna()]


def semiannual_exposure(holdings: pd.DataFrame,
                        industry_map: pd.Series,
                        only_full: bool = True) -> pd.DataFrame:
    """
    按申万一级行业聚合的**半年度**暴露（index=季末, columns=行业, values=占净值比例%）。

    only_full=True 时只取 Q2/Q4（完整持仓）。这是唯一诚实的口径 ——
    Q1/Q3 只有前十大，聚合出来的小仓位行业会被系统性低估。

    ★ 返回表**只含能映射的行业**，行合计因此小于 100%。差额里有现金/债券，
      也有抓不到行业的股票 —— 两者必须靠 `exposure_coverage()` 区分，
      否则"真实暴露合计 87~91%"会被误读成"其余都是现金"。
    """
    mapped, unmapped = _map_industries(holdings, industry_map, only_full)
    if len(unmapped):
        # 走日志而不是 print：这条警告含 ⚠（U+26A0），在 GBK 控制台会抛
        # UnicodeEncodeError，把一次聚合变成崩溃。
        LOG.warning("%d 条持仓未映射到申万一级行业（权重合计 %.2f%%），多为新股/"
                    "北交所/停牌股；该权重不计入暴露表，用 exposure_coverage() "
                    "查看每期覆盖率", len(unmapped),
                    float(unmapped["占净值比例"].sum()))
    if mapped.empty:
        return pd.DataFrame()
    exp = (mapped.groupby(["季末", "industry"])["占净值比例"].sum()
           .unstack("industry").sort_index())
    return exp


def exposure_coverage(holdings: pd.DataFrame, industry_map: pd.Series,
                      only_full: bool = True) -> pd.DataFrame:
    """
    每期持仓的映射覆盖率（index=季末，单位=百分点）。

    列：`已映射权重` / `未映射权重` / `合计` / `覆盖率`。

    ★ 为什么单独输出：`semiannual_exposure` 丢掉未映射行后，行合计（实测
      87~91%）同时包含"现金+债券"和"抓不到行业的股票"。覆盖不足的期次，
      结构比（`align_vs_rbsa` 把共同行业归一到 100%）会把未映射部分
      按比例摊到其它行业上，偏差方向因此不可知 —— 有了本表就能标注。
    """
    mapped, unmapped = _map_industries(holdings, industry_map, only_full)
    frames = []
    if mapped is not None and not mapped.empty:
        frames.append(mapped.groupby("季末")["占净值比例"].sum()
                      .rename("已映射权重"))
    if unmapped is not None and not unmapped.empty:
        frames.append(unmapped.groupby("季末")["占净值比例"].sum()
                      .rename("未映射权重"))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).fillna(0.0).sort_index()
    # ★ P1-14：全映射（无未映射行）或全未映射（无已映射行）时，concat 只会
    #   生成其中一列；无条件相加两列会 KeyError，使全覆盖基金的持仓研究
    #   直接失败。非空结果必须始终补齐两列。
    out = out.reindex(columns=["已映射权重", "未映射权重"], fill_value=0.0)
    out["合计"] = out["已映射权重"] + out["未映射权重"]
    out["覆盖率"] = np.where(out["合计"] > 0,
                           out["已映射权重"] / out["合计"], np.nan)
    return out


# ====================================================================== 对齐
def _rbsa_at(weights: pd.DataFrame, when: pd.Timestamp) -> pd.Series:
    """取 `when` 当日或之前最近一次重估的权重。"""
    idx = weights.index
    if len(idx) == 0:
        return pd.Series(dtype=float)
    pos = idx.searchsorted(when, side="right") - 1
    if pos < 0:
        return pd.Series(dtype=float)
    return weights.iloc[pos]


def _exposure_to_codes(exposure: pd.DataFrame) -> pd.DataFrame:
    """
    把暴露表的列从**行业名**统一成**指数代码**（已是代码则原样返回）。

    ★ 为什么必须转：`semiannual_exposure` 产出的是行业名（"食品饮料"），
      而 RBSA 权重与 panel 的列都是指数代码（"801120"）。两者直接
      `intersection` / `in` 会**静默得到空集** —— 不报错，只是什么都对不上。
    """
    name2code = {v: k for k, v in _industry_index_codes().items()}
    return exposure.rename(columns=lambda c: name2code.get(str(c), str(c)))


def align_vs_rbsa(weights: pd.DataFrame, exposure: pd.DataFrame,
                  industry_map: Optional[pd.Series] = None,
                  coverage: Optional[pd.DataFrame] = None) -> dict:
    """
    逐行业对比 RBSA 权重与真实持仓暴露。

    口径说明（必须看清，否则数字会被误读）：
      · RBSA 权重含 16 个宽基指数，这里**只取行业部分**（801xxx）并归一到 100%；
      · 真实暴露同样归一到 100%（原始合计约 87~91%）；
      · 两边都归一化后比的是**结构**（"37% 的股票仓位给了食品饮料"），
        不是绝对仓位。
      · ★ 那 87~91% 的差额里既有现金/债券，也有**抓不到行业的股票**。
        传入 `coverage=exposure_coverage(...)` 后，逐期结果会多出
        `未映射权重` / `持仓合计` 两列，汇总里给 `unmapped_mean` 与
        `coverage_note`；不传则这两列为 NaN、`coverage_note` 明确说明无法区分。

    返回 dict，`ok=False` 表示样本不足以判断（不抛异常）。
    """
    inds = _industry_index_codes()
    if weights is None or weights.empty or exposure is None or exposure.empty:
        return {"ok": False, "reason": "RBSA 权重或真实持仓为空"}
    exposure = _exposure_to_codes(exposure)
    if coverage is not None and not coverage.empty:
        coverage = coverage.reindex(exposure.index)
    else:
        coverage = None

    def _cov(qend, column):
        if coverage is None or column not in coverage.columns:
            return np.nan
        return float(coverage[column].get(qend, np.nan))

    rows, detail = [], []
    for qend in exposure.index:
        w = _rbsa_at(weights, qend)
        if w.empty:
            continue
        w_ind = w[[c for c in w.index if str(c).startswith("801")]]
        w_broad = w[[c for c in w.index if not str(c).startswith("801")]]
        e = exposure.loc[qend].dropna()
        common = w_ind.index.intersection(e.index)
        if len(common) < 2:
            continue
        ws = w_ind[common] / w_ind[common].sum() if w_ind[common].sum() > 0 else w_ind[common]
        es = e[common] / e[common].sum()
        dev = (ws - es)
        rows.append({
            "季末": qend,
            "披露约": qend + pd.Timedelta(days=disclosure_lag_days(qend)),
            "RBSA重估日": weights.index[weights.index.searchsorted(
                qend, side="right") - 1],
            "共同行业数": len(common),
            "平均绝对偏差": float(dev.abs().mean()),
            "最大绝对偏差": float(dev.abs().max()),
            "最大偏差行业": str(dev.abs().idxmax()),
            "权重相关": float(ws.corr(es)) if len(common) > 2 else np.nan,
            "RBSA宽基暴露": float(w_broad.sum()),
            "真实暴露合计": float(e.sum()),
            # 未映射权重与持仓合计来自 exposure_coverage()：用来区分
            # "真实暴露合计 < 100%" 里的现金/债券与"抓不到行业的股票"
            "未映射权重": _cov(qend, "未映射权重"),
            "持仓合计": _cov(qend, "合计"),
        })
        for c in common:
            detail.append({"季末": qend, "行业": inds.get(c, c),
                           "RBSA权重": float(ws[c]), "真实暴露": float(es[c]),
                           "偏差": float(ws[c] - es[c])})

    if not rows:
        return {"ok": False,
                "reason": "没有可对比的期数（RBSA 权重重估日晚于所有持仓季末，"
                          "或共同行业不足）"}

    df = pd.DataFrame(rows).sort_values("季末")
    dd = pd.DataFrame(detail)
    # 汇总：跨期平均
    agg = (dd.groupby("行业")
           .agg(RBSA权重=("RBSA权重", "mean"), 真实暴露=("真实暴露", "mean"),
                偏差=("偏差", "mean"), 平均绝对偏差=("偏差", lambda s: s.abs().mean()))
           .sort_values("平均绝对偏差", ascending=False))
    broad = float(df["RBSA宽基暴露"].mean())
    broad_note = (
        f"RBSA 拟合出的宽基暴露平均 {broad:.1%} —— 这部分**无法与持仓对应**"
        f"（宽基跨行业），对齐只覆盖行业部分。若该值很大，说明模型把一部分"
        f"行业暴露记在了宽基上，行业层面的偏差会被低估。"
    ) if broad > 1e-6 else "RBSA 未拟合出宽基暴露，行业层面可直接对比。"
    unmapped_mean = float(df["未映射权重"].mean())
    total_mean = float(df["持仓合计"].mean())
    if np.isfinite(unmapped_mean) and unmapped_mean > 0:
        share = (unmapped_mean / total_mean) if total_mean > 0 else np.nan
        coverage_note = (
            f"每期平均有 {unmapped_mean:.1f} 个百分点持仓未映射到申万一级行业"
            f"（占持股合计 {share:.1%}）—— 结构比只在共同行业上归一到 100%，"
            f"未映射越重，该期的行业偏差越可能被**低估**，不能当成现金。"
        )
    elif np.isfinite(unmapped_mean):
        coverage_note = "持仓全部映射到申万一级行业，结构比不受未映射权重影响。"
    else:
        coverage_note = ("未传入 exposure_coverage()，无法区分现金/债券与"
                         "抓不到行业的股票。")
    return {
        "ok": True,
        "periods": df,
        "detail": dd,
        "by_industry": agg,
        "n_periods": int(len(df)),
        "mad_mean": float(df["平均绝对偏差"].mean()),
        "corr_mean": float(df["权重相关"].mean()),
        "broad_exposure": broad,
        "broad_note": broad_note,
        "unmapped_mean": unmapped_mean,
        "coverage_note": coverage_note,
    }


# ====================================================================== 持仓基准
def holding_benchmark(exposure: pd.DataFrame, panel: pd.DataFrame,
                      lag_days: Optional[int] = None,
                      cash_rate: float = 0.015,
                      dividend_yield: Optional[float] = None) -> pd.Series:
    """
    只用真实持仓构造的基准组合**日收益**序列（index=交易日）。

    做法：每期行业暴露 × 对应行业指数日收益，加权求和；未覆盖部分
    （现金/债券/未映射行业）按 `cash_rate` 年化计息 —— 旧实现记 0 收益，
    比"空仓也有货币收益"的真实口径更差，会把基金经理的现金管理算成 alpha。

    ★ 收益口径（P1-16）：返回**简单日收益**，权重按**每日再平衡**处理
      （半年度调仓只改权重向量；逐日不追踪漂移，这是显式约定）。股票腿用
      `pct_change()` 的简单收益加权，现金与股息腿也是简单日收益 —— 旧实现
      股票腿用对数收益、现金/股息腿用简单收益，`compare_benchmarks` 又统一
      `exp(sum)`，既混合口径也不等于按权重持有资产的组合收益。

    ★ 指数口径：panel 里的行业指数是**价格指数**（不含股息），
      而基金净值是**总回报**。缺的这段股息此前只能标注（每年约 1.5~2.5%），
      会让基准系统性偏低、把股息算成基金的"选股能力"。
      `dividend_yield` 就是这条腿：股票权重部分按该年化值逐日计息。
      None = 跟随 `C.RBSA["index_dividend_annual"]`（默认 0，即保持历史口径）。
      管线没有逐行业股息数据，这是一个**显式情景参数**，不是精确修正；
      实际取值写入返回序列的 `attrs`，便于结果落盘时留痕。
    ★ **披露滞后必须处理**：Q2 完整持仓见半年报（8/31，季末 +62 天），
      Q4 见年报（次年 3/31，季末 +90 天）。因此该期暴露从
      `季末 + disclosure_lag_days(季末)` 起生效，而不是 6/30。
      否则等于用未来信息构造历史基准，比出来的收益不可信。
      `lag_days` 显式传入时对所有期使用同一数值（仅用于敏感性分析）。
    """
    if exposure is None or exposure.empty or panel is None or panel.empty:
        return pd.Series(dtype=float)
    if dividend_yield is None:
        dividend_yield = float(C.RBSA.get("index_dividend_annual", 0.0) or 0.0)
    dividend_yield = float(dividend_yield)
    if not np.isfinite(dividend_yield) or dividend_yield < 0:
        raise ValueError("dividend_yield 需为有限非负数值（年化，如 0.02）")
    exposure = _exposure_to_codes(exposure)
    cols = [c for c in exposure.columns if c in panel.columns]
    if not cols:
        return pd.Series(dtype=float)

    rets = panel[cols].astype(float).pct_change()
    daily_div = (1.0 + dividend_yield) ** (1.0 / 252.0) - 1.0
    parts = []
    for qend in exposure.index:
        lag = int(lag_days) if lag_days is not None else disclosure_lag_days(qend)
        start = qend + pd.Timedelta(days=lag)
        # ★ 单位换算：exposure 的"占净值比例"是**百分点**（37.03 = 37.03%），
        #   而 rets 是小数日收益。不除 100 会让每日收益被放大近百倍
        #   （实测症状：年化 −95%、年化波动 1650%）。
        w = exposure.loc[qend, cols].fillna(0.0) / 100.0
        stock_weight = float(w.sum())
        if stock_weight <= 0:
            continue
        seg = rets.loc[rets.index >= start]
        if seg.empty:
            continue
        # 未覆盖部分（现金/债券/未映射行业）按年化 cash_rate 计息，
        # 否则空仓期记 0 收益，等于把货币收益白送成基金经理的 alpha。
        cash_weight = max(0.0, 1.0 - stock_weight)
        daily_cash = (1.0 + float(cash_rate)) ** (1.0 / 252.0) - 1.0
        # 该期暴露从披露日一直生效，直到被**更晚一期**覆盖（下方按期末顺序赋值，
        # 后者覆盖前者）—— 这正是"每半年按最新披露调仓"的行为。
        parts.append((seg.index,
                      (seg * w).sum(axis=1) + cash_weight * daily_cash
                      + stock_weight * daily_div))
    if not parts:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(sorted(set().union(*[p[0] for p in parts])))
    out = pd.Series(0.0, index=idx)
    for i, v in parts:
        out.loc[i] = v
    out = out.dropna().sort_index()
    out.attrs["dividend_yield"] = dividend_yield
    out.attrs["cash_rate"] = float(cash_rate)
    out.attrs["returns"] = "simple"
    out.attrs["rebalance"] = "daily"
    return out


def compare_benchmarks(fund_nav: pd.Series, rbsa_syn_nav: pd.Series,
                       holding_ret: pd.Series) -> dict:
    """
    三条曲线的对比：基金实际 / RBSA 合成基准 / 持仓基准。

    都在**同一段共同区间**上比较，且持仓基准已按披露滞后起算，
    所以这是一个"可实现"的比较，不是事后最优的比较。

    ★ 收益口径（P1-16）：全部统一为**简单日收益**。净值序列用 `pct_change`
      转日收益（对持有者而言这就是简单收益），持仓基准本身已是简单日收益；
      区间收益 = ∏(1+r) − 1，年化按几何折算。旧实现把简单/对数收益混在
      `exp(sum)` 里，不等于按权重持有资产的组合收益。

    ★ 信息比率（P1-17）：列名 `信息比率IR` = 超额日收益均值 / 跟踪误差
      × √252，超额对象由 `IR参照` 列声明 —— 前三行参照**基金**，
      "持仓−RBSA（跟踪差）"行参照 **RBSA 合成基准**（旧实现名为"相对基金IR"
      却只算序列自身的收益波动比，基金自己的这一列也非零）。
      约定：超额恒为 0（含参照自身）输出 0；超额恒定非零（零跟踪误差）
      时 IR 无定义，输出 NaN。
    """
    if holding_ret is None or holding_ret.empty:
        return {"ok": False, "reason": "持仓基准序列为空"}
    idx = holding_ret.index
    f = pd.Series(fund_nav).astype(float).pct_change().reindex(idx).dropna()
    s = pd.Series(rbsa_syn_nav).astype(float).pct_change().reindex(idx).dropna()
    common = f.index.intersection(s.index)
    if len(common) < 60:
        return {"ok": False,
                "reason": f"共同区间只有 {len(common)} 个交易日，不足以比较"}
    f, s, h = f.loc[common], s.loc[common], holding_ret.loc[common]
    n = len(common)

    def _ir(ex) -> float:
        sd = float(ex.std(ddof=1))
        mean = float(ex.mean())
        # 零跟踪误差需按容差判断：恒定超额序列的 std 会因浮点求均值引入
        # ~1e-18 的噪声而"非零"，直接除会把 IR 抬到 1e16 量级。
        if sd <= 1e-12:
            return 0.0 if abs(mean) <= 1e-12 else np.nan
        return float(mean / sd * np.sqrt(252))

    def _growth(r) -> float:
        g = float(np.prod(1.0 + np.asarray(r, dtype=float)))
        return g if g > 0 else np.nan

    def _stat(r, name, ref, ref_name):
        g = _growth(r)
        return {
            "基准": name,
            "区间收益": g - 1.0,
            "年化收益": float(g ** (252.0 / n) - 1.0) if np.isfinite(g) else np.nan,
            "年化波动": float(r.std(ddof=1) * np.sqrt(252)),
            "与基金相关": float(r.corr(f)),
            "信息比率IR": _ir(r - ref),
            "IR参照": ref_name,
        }

    rows = [_stat(f, "基金实际", f, "基金"),
            _stat(s, "RBSA合成基准", f, "基金"),
            _stat(h, "持仓基准", f, "基金")]
    # 持仓基准 vs RBSA 基准：跟踪差异（参照是 RBSA，不是基金）
    d = h - s
    hg, sg = _growth(h), _growth(s)
    rel = hg / sg if np.isfinite(hg) and np.isfinite(sg) and sg > 0 else np.nan
    rows.append({
        "基准": "持仓−RBSA（跟踪差）",
        "区间收益": rel - 1.0,
        "年化收益": float(rel ** (252.0 / n) - 1.0) if np.isfinite(rel) else np.nan,
        "年化波动": float(d.std(ddof=1) * np.sqrt(252)),
        "与基金相关": float(d.corr(f)),
        "信息比率IR": _ir(d),
        "IR参照": "RBSA合成基准",
    })
    return {"ok": True, "stats": pd.DataFrame(rows), "n_days": int(n)}
