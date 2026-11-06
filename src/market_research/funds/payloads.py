"""把计算结果转换为前端图表与诊断字典；不读取行情、不启动任务。"""
from __future__ import annotations
import math
from market_research.serialization import _jnum, _jsafe, _dates
from typing import Any, Dict, List
import numpy as np
import pandas as pd
import market_research.config as C
from market_research.modeling.models import MODELS, ModelReport
from market_research.modeling.signals import health_stats
from market_research.modeling.trends import trend_payload
from market_research.market.technical import series_forecast, MIN_TRAIN








def correlation_block(panel: pd.DataFrame, nav: pd.Series,
                      top_n: int = 15, roll_n: int = 120) -> dict:
    """
    基金与候选指数的相关性分析。

    三个层次：
      1. 全样本相关系数矩阵（基金 × 全部指数）
      2. Top-N 排行榜（按 |相关系数| 排序）
      3. 滚动 roll_n 日相关系数（看风格是否漂移）

    roll_n 可由前端传入（/api/corr 会用它做低成本重算）。
    """
    R = np.log(panel.astype(float)).diff().dropna()
    fund = R["__FUND__"]
    corr = R.drop(columns=["__FUND__"]).corrwith(fund)

    assets = list(corr.index)
    mat = R.drop(columns=["__FUND__"]).corr()
    # 只保留与基金相关的前 top_n 个指数做矩阵展示，避免 43×43 糊成一团
    keep = [c for c in corr.abs().sort_values(ascending=False).index[:top_n]]
    sub = mat.loc[keep, keep].copy()
    # 加上基金这一行/列，方便一眼看出谁最相关
    sub.insert(0, "【基金】", corr.reindex(keep))
    sub.loc["【基金】"] = np.concatenate([[1.0], corr.reindex(keep).values])

    # 滚动 roll_n 日相关系数（对 Top8 指数）
    roll_n = int(max(20, min(int(roll_n), len(R) - 1)))
    roll = {}
    for c in keep[:8]:
        rc = R[c].rolling(roll_n).corr(fund).dropna()
        if len(rc):
            roll[C.NAME_OF.get(c, c)] = {
                "dates": _dates(rc.index),
                "values": [_jnum(v, 4) for v in rc.values],
            }

    top_rows = []
    for code_, v in corr.abs().sort_values(ascending=False).head(top_n).items():
        top_rows.append({
            "code": code_,
            "name": C.NAME_OF.get(code_, code_),
            "layer": C.LAYER_OF.get(code_, "其他"),
            "corr": _jnum(corr[code_], 4),
            "abs": _jnum(abs(corr[code_]), 4),
        })

    # 按大类汇总平均相关性
    layer_corr = {}
    for code_, v in corr.items():
        layer_corr.setdefault(C.LAYER_OF.get(code_, "其他"), []).append(v)
    layer_rows = [{"layer": k,
                   "avg_corr": _jnum(float(np.mean(v)), 4),
                   "n": len(v)}
                  for k, v in layer_corr.items()]
    layer_rows.sort(key=lambda r: -abs(r["avg_corr"] or 0))

    # 风格漂移：基金与 Top3 指数的滚动相关是否发生明显变化
    drift = []
    for c in keep[:3]:
        rc = R[c].rolling(roll_n).corr(fund).dropna()
        if len(rc) < 200:
            continue
        drift.append({
            "name": C.NAME_OF.get(c, c),
            "early": _jnum(rc.iloc[:len(rc) // 3].mean(), 4),
            "late": _jnum(rc.iloc[-len(rc) // 3:].mean(), 4),
        })
    for d in drift:
        d["delta"] = _jnum((d["late"] or 0) - (d["early"] or 0), 4)

    return {
        "matrix": {"cols": [str(c) for c in sub.columns],
                   "rows": [str(i) for i in sub.index],
                   "values": [[_jnum(v, 4) for v in row]
                              for row in sub.values]},
        "top": top_rows,
        "layers": layer_rows,
        "rolling": roll,
        "drift": drift,
        "roll_window": roll_n,
        "n_assets": len(assets),
    }


def nav_block(panel: pd.DataFrame, nav: pd.Series) -> dict:
    """
    净值区块：基金净值曲线、月度收益热力图、回撤曲线。

    注意：开放式基金只有净值没有成交量，因此这里给的是净值 K 线
    （以每日净值构造 开=高=低=收 的退化形式），不是交易所 K 线。
    前端会用「收盘净值 + 涨跌幅柱」的方式呈现，更符合净值语义。
    """
    nav = nav.dropna()
    rets = nav.pct_change().fillna(0.0)
    dd = nav / nav.cummax() - 1.0

    # 月度收益热力图（年 × 月）
    m = nav.resample("ME").last()
    mret = m.pct_change()
    mret.iloc[0] = np.nan
    years_list = sorted({d.year for d in mret.index})
    monthly = {}
    for d, v in mret.items():
        if pd.isna(v):
            continue
        monthly.setdefault(str(d.year), {})[str(d.month)] = _jnum(v, 5)

    # 滚动 60 日波动 vs 全样本
    vol60 = rets.rolling(60).std() * np.sqrt(252)
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    ann = float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1)
    avol = float(rets.std() * np.sqrt(252))
    return {
        "dates": _dates(nav.index),
        "nav": [_jnum(v, 5) for v in nav.values],
        "ret": [_jnum(v, 5) for v in rets.values],
        "drawdown": [_jnum(v, 5) for v in dd.values],
        "vol60": [_jnum(v, 5) for v in vol60.values],
        "monthly": monthly,
        "years": [str(y) for y in years_list],
        "stats": {
            "start": nav.index[0].strftime("%Y-%m-%d"),
            "end": nav.index[-1].strftime("%Y-%m-%d"),
            "n_days": int(len(nav)),
            "latest": _jnum(nav.iloc[-1], 5),
            "total_return": _jnum(nav.iloc[-1] / nav.iloc[0] - 1, 5),
            "years_span": _jnum(years, 2),
            "ann_return": _jnum(ann, 5),
            "ann_vol": _jnum(avol, 5),
            "max_drawdown": _jnum(dd.min(), 5),
            # 夏普比率 = (年化收益 - 无风险) / 年化波动，无风险取 0
            "sharpe": _jnum(ann / avol if avol > 1e-9 else np.nan, 5),
            "calmar": _jnum(ann / abs(dd.min()) if dd.min() < -1e-9 else np.nan, 5),
            "max_vol60": _jnum(vol60.max(), 5),
            "current_vol60": _jnum(vol60.iloc[-1], 5),
        },
    }


def fund_forecast_payload(nav: pd.Series, horizons=(5, 20),
                          max_points: int = 400) -> dict:
    """基金净值的方向概率。

    与模型详情的「趋势色带」是两件事：色带只描述已经发生的形态，
    这里给出的是**未来 H 日上涨的模型概率**。底层复用股票/指数同一套
    `direction_forecast`（标签按成熟期截断、每 21 日滚动重训），
    样本不足时显式返回不可用，不补 0.5 或历史均值。
    """
    s = pd.Series(nav).astype(float).dropna()
    if len(s) < MIN_TRAIN + 65:
        return {"basis": "fund_nav", "available": False, "dates": [],
                "horizons": [], "reason": "净值样本不足以训练方向模型",
                "disclaimer": "模型参考值，非投资建议"}
    try:
        step = max(1, len(s) // max_points)
        items = []
        sampled = None
        for h in horizons:
            f = series_forecast(s, h)
            ser = pd.Series(f["series"], index=s.index, dtype=float)
            sl = ser.iloc[::step]
            if len(ser) and sl.index[-1] != ser.index[-1]:
                sl = pd.concat([sl, ser.iloc[-1:]])
            sampled = sl if sampled is None else sampled
            ev = f["evaluation"] or {}
            items.append({
                "horizon": int(h),
                "ok": bool(f["ok"]),
                "probability": _jnum(f["probability"], 4),
                "baseline": _jnum(f["baseline"], 4),
                "training_rows": int(f["training_rows"] or 0),
                "minimum_training_rows": int(f["minimum_training_rows"] or 0),
                "evaluation": _jsafe(ev),
                "advantage": bool(ev.get("advantage", False)),
                "reason": f["reason"],
                "series": [_jnum(v, 4) for v in sl.values],
            })
        return {
            "basis": "fund_nav",
            "available": any(i["ok"] for i in items),
            "as_of": s.index[-1].strftime("%Y-%m-%d"),
            "dates": _dates(sampled.index) if sampled is not None else [],
            "horizons": items,
            "disclaimer": "基于历史净值动量的模型参考值，非买卖建议，也不代表未来收益。",
        }
    except Exception as exc:                              # noqa: BLE001
        return {"basis": "fund_nav", "available": False, "dates": [],
                "horizons": [], "reason": f"方向模型计算失败：{exc}",
                "disclaimer": "模型参考值，非投资建议"}


def holdings_payload(out: dict) -> dict:
    """真实持仓拟合的序列化结果（CLI / API / 报告共用）。

    保留全部披露约束：只用半年报/年报、Q2 滞后 62 天 / Q4 滞后 90 天、
    共同行业归一、持仓基准要求 ≥60 个共同交易日。任何一项不满足都走
    `ok=False` 并给出原因，不静默降级。
    """
    if not out or not out.get("ok"):
        return {"ok": False, "code": (out or {}).get("code"),
                "reason": (out or {}).get("reason", "持仓拟合失败"),
                "constraints": _HOLDING_CONSTRAINTS}
    cmp_ = out.get("compare") or {}
    stats = cmp_.get("stats") if isinstance(cmp_.get("stats"), pd.DataFrame) else None
    bench = out.get("benchmark") or {}
    diag = bench.get("diagnostics")
    return {
        "ok": True,
        "code": out["code"],
        "name": out.get("name"),
        "summary": {
            "n_periods": int(out.get("n_periods") or 0),
            "mad_mean": _jnum(out.get("mad_mean"), 4),
            "corr_mean": _jnum(out.get("corr_mean"), 4),
            "corr_first": _jnum(out.get("corr_first"), 4),
            "corr_last": _jnum(out.get("corr_last"), 4),
            "corr_valid": int(out.get("corr_valid") or 0),
            "broad_exposure": _jnum(out.get("broad_exposure"), 4),
            "unmapped_mean": _jnum(out.get("unmapped_mean"), 4),
            "coverage_mean": _jnum(out.get("coverage_mean"), 4),
            "dividend_yield": _jnum(out.get("dividend_yield"), 4),
            "elapsed": _jnum(out.get("elapsed"), 1),
        },
        "notes": {
            "broad": out.get("broad_note"),
            "coverage": out.get("coverage_note"),
            "unmapped_not_cash": "未映射权重是抓不到行业的股票仓位，不是现金。",
            "benchmark_compare": "持仓基准与 RBSA 基准频率/信息集不同，"
                                 "年化更高不等于更好。",
            "benchmark_residual": "未映射股票与行业面板缺失的权重按 0 收益处理，"
                                  "不再按现金利率计息；只有明确现金/债券部分计息。",
        },
        "periods": _jsafe(out["periods"].to_dict("records"))
        if isinstance(out.get("periods"), pd.DataFrame) else [],
        "by_industry": _jsafe(_industry_rows(out.get("by_industry"))),
        "detail": _jsafe(out["detail"].to_dict("records"))
        if isinstance(out.get("detail"), pd.DataFrame) else [],
        "compare": {
            "ok": bool(cmp_.get("ok")),
            "reason": cmp_.get("reason"),
            "n_days": int(cmp_.get("n_days") or 0),
            "stats": _jsafe(stats.to_dict("records")) if stats is not None else [],
        },
        "benchmark": {
            "available": bool(bench.get("available")),
            "missing_industries": list(bench.get("missing_industries") or []),
            "residual_treatment": bench.get("residual_treatment", ""),
            "note": bench.get("note", ""),
            "diagnostics": _jsafe(diag.to_dict("records"))
            if isinstance(diag, pd.DataFrame) else [],
        },
        "constraints": _HOLDING_CONSTRAINTS,
    }


def _industry_rows(df) -> list:
    """按行业汇总表转记录；索引名缺失时统一补成「行业」，避免接口出现 `index`。"""
    if not isinstance(df, pd.DataFrame):
        return []
    out = df.reset_index()
    if "index" in out.columns:
        out = out.rename(columns={"index": "行业"})
    return out.to_dict("records")


_HOLDING_CONSTRAINTS = [
    "只用半年报/年报（Q2/Q4）的完整持仓，季报前十大不参与",
    "披露滞后：Q2 季末 +62 天、Q4 季末 +90 天，从披露之后起算收益",
    "RBSA 与真实持仓只对齐共同行业，并归一到 100%；宽基暴露单独报告",
    "持仓基准与基金/合成基准要求 ≥60 个共同交易日",
]


def _weights_payload(w: pd.Series, min_w: float = 0.005,
                     top_n: int = 15) -> dict:
    """
    序列化权重用于条形图。

    **必须过滤掉权重≈0 的项**：线性约束模型会把落选指数置零，
    但 Series 里仍保留这些列。若不过滤，43 个指数会挤进图表，
    大部分是零高度的柱，前端完全读不出重点。

    ★ 不输出"被丢掉的项数"：旧字段 `n_zero` 名字与实际含义不符（它统计的是
      **低于展示阈值**的项，不是恰好为 0 的项），而且全仓（前端、报告、测试）
      没有任何消费方。需要时可由 `n - len(items)` 得到，不必再存一份。
    """
    if w is None or len(w) == 0:
        return {"items": [], "sum": 0.0, "n": 0}
    w = w[w > 0]
    if w.empty:
        return {"items": [], "sum": 0.0, "n": 0}
    wv = w[w > min_w].head(top_n)
    items = []
    for code_, v in wv.items():
        items.append({
            "code": str(code_),
            "name": C.NAME_OF.get(code_, str(code_)),
            "layer": C.LAYER_OF.get(code_, "其他"),
            "weight": _jnum(v, 5),
        })
    return {"items": items, "sum": _jnum(w.sum(), 4), "n": int(len(w))}


def _weights_hist_payload(wh: pd.DataFrame, assets_limit: int = 8,
                         max_points: int = 120) -> dict:
    """滚动权重历史：Top-N 资产的权重随时间变化（堆叠面积图）。"""
    if wh is None or wh.empty:
        return {"dates": [], "series": [], "assets": []}
    total_w = wh.abs().sum(axis=0).sort_values(ascending=False)
    top = list(total_w.head(assets_limit).index)
    step = max(1, len(wh) // max_points)
    sl = wh.iloc[::step][top].tail(max_points)
    return {
        "dates": _dates(sl.index),
        "assets": [C.NAME_OF.get(c, str(c)) for c in top],
        "series": [[_jnum(v, 5) for v in sl[c].values] for c in top],
    }


def _alpha_stats(fund_line: pd.Series, syn_line: pd.Series) -> dict:
    """
    量化"基金 − 合成基准"的累计差距，把它拆成可解释的 alpha。

    fund_line / syn_line 都是**归一到起点 1** 的净值（与图上两条线一致）。
    因此 log 差就是累计超额，起点为 0。

    为什么必须单独算：
      R² 0.8~0.93 时日收益拟合已经很好，但每年几个百分点的系统性缺口
      复利 9 年就会让两条线差出 60% 以上（实测 110013 落后 60%、
      519697 落后 66%）。用户看到的是"拟合崩了"，真实原因是**基金的 alpha
      不在指数组合的张成空间里**。这里把它变成数字，让差距可被解释。

    注意：年化 alpha 里**包含**指数价格指数不含股息带来的偏差（约 2~3%/年），
    因此它是"表观 alpha 上界"，不是纯粹的选股能力。剥离股息需配置
    `RBSA["index_dividend_annual"]`。
    """
    try:
        from market_research.research.robustness import _nw_mean_t
    except Exception:                                        # noqa: BLE001
        _nw_mean_t = None

    empty = pd.Series(dtype=float)
    out = {"alpha_cum": empty, "stats": {}}
    try:
        lf = np.log(fund_line.astype(float).replace(0, np.nan)).dropna()
        ls = np.log(syn_line.astype(float).replace(0, np.nan)).dropna()
        common = lf.index.intersection(ls.index)
        if len(common) < 30:
            return out
        lf, ls = lf.reindex(common), ls.reindex(common)
        alpha_cum = lf - ls
        # 日度 alpha = 基金对数收益 − 合成对数收益
        d = (lf.diff() - ls.diff()).dropna()
        if len(d) < 30:
            return {"alpha_cum": alpha_cum, "stats": {}}
        ann = float(d.mean() * 252)
        sd = float(d.std(ddof=1))
        t = None
        if _nw_mean_t is not None:
            try:
                t = float(_nw_mean_t(d.values, lags=5))
            except Exception:                                # noqa: BLE001
                t = None
        if t is None and sd > 1e-12:
            t = float(d.mean() / (sd / np.sqrt(len(d))))
        n_years = len(d) / 252.0
        return {
            "alpha_cum": alpha_cum,
            "stats": {
                "年化alpha": _jnum(ann, 4),
                "alpha_t值": _jnum(t, 3),
                "累计对数超额": _jnum(float(alpha_cum.iloc[-1]), 4),
                "累计倍数差": _jnum(float(np.exp(alpha_cum.iloc[-1])), 3),
                "年数": _jnum(n_years, 2),
                # 表观性提醒：指数是价格指数，股息会被算进 alpha
                "含股息偏差": True,
            },
        }
    except Exception:                                        # noqa: BLE001
        return out


def model_payload(rep: ModelReport, nav_oos: pd.Series,
                  max_points: int = 400) -> dict:
    """单个模型的完整可视化数据。"""
    syn = rep.syn_ret.dropna()
    syn_nav = np.exp(syn.cumsum())
    # 把基金净值与合成基准都归一到起点=1，才能在同一坐标轴上比较形状。
    # 注意：绝不能跨各自起点归一后再比，否则会抹掉真实的收益差。
    fund_aligned = nav_oos.reindex(syn_nav.index)
    fund_line = fund_aligned / fund_aligned.dropna().iloc[0] if \
        len(fund_aligned.dropna()) else fund_aligned
    syn_line = syn_nav / syn_nav.iloc[0] if len(syn_nav) else syn_nav

    step = max(1, len(syn_nav) // max_points)
    idx = syn_nav.index[::step]
    # 趋势状态：必须在**日频**序列上判定，再把结果按绘图下标采样下发。
    # 若直接在降采样序列上判"最近 10 日"，实际是"10 个采样点 ≈ 40 个交易日"，
    # 语义会完全失真（step 实测约 4）。
    trend = trend_payload(syn_nav, idx)

    # ---------------- alpha 分解
    # ★ 图上看"基金起飞、合成基准横盘"时，容易被误读成拟合失败。实际上：
    #   R² 高 = 风格暴露拟合得好；两条线的累计差距 = **风格之外的部分**，
    #   主要是基金的 alpha（选股超额），其次是指数价格指数不含股息造成的偏差。
    #   这里把它单独量化出来，让差距可被解释而不是被当成 bug。
    alpha_stats = _alpha_stats(fund_line, syn_line)

    return {
        "key": rep.key, "name": rep.name, "family": rep.family,
        "desc": rep.desc, "interpretable": rep.interpretable,
        "metrics": {
            "oos_r2": _jnum(rep.oos_r2, 4),
            "oos_te": _jnum(rep.oos_te, 4),
            "oos_ic": _jnum(rep.oos_ic, 4),
            "win": _jnum(rep.win, 4),
            "beta": _jnum(rep.beta, 4),
            "vol_ratio": _jnum(rep.vol_ratio, 4),
            "fit_r2_mean": _jnum(rep.fit_r2_mean, 4),
            "fit_te_mean": _jnum(rep.fit_te_mean, 4),
            "elapsed": _jnum(rep.elapsed, 2),
            "n_weights": int((rep.latest_weights > 0).sum())
            if rep.latest_weights is not None else 0,
        },
        "extra": _jsafe({k: v for k, v in rep.extra.items() if v is not None}),
        "latest_weights": _weights_payload(rep.latest_weights),
        "weights_last_date": (rep.weights_last_date.strftime("%Y-%m-%d")
                              if rep.weights_last_date is not None else None),
        "weights_hist": _weights_hist_payload(rep.weights_hist),
        "curve": {
            "dates": _dates(idx),
            "fund": [_jnum(v, 5) for v in fund_line.reindex(idx)],
            "synth": [_jnum(v, 5) for v in syn_line.reindex(idx)],
            # 累计 alpha（对数尺度，起点 0）：与 fund/synth 同一下标采样，
            # 前端可直接在拟合曲线旁叠加这条线。
            "alpha": [_jnum(v, 5) for v in
                      alpha_stats["alpha_cum"].reindex(idx)],
            # 趋势状态按绘图下标一一对应（长度 = len(dates)）
            "trend": trend,
        },
        "alpha": alpha_stats["stats"],
        "residual": {
            # ★ 残差必须与图上两条线同尺度：fund/synth 都已归一到起点 1，
            #   若这里用未归一的 fund_aligned - syn_nav，得到的是「净值水平差」
            #   （实测 1.65~3.28，全部为正），图上会被误读成"长期正偏离"。
            "dates": _dates(syn.index[::step]),
            "value": [_jnum(v, 5) for v in
                      (fund_line - syn_line).reindex(
                          syn.index[::step]).values],
        },
    }


def build_insights(code: str, name: str, navb: dict, corr: dict,
                   models: Dict[str, ModelReport], rank: pd.DataFrame,
                   payload: Dict[str, dict]) -> dict:
    """
    基于真实数据生成结构化结论。分「可靠结论」与「不可靠结论」两侧写，
    避免把拟合优度当成预测能力。
    """
    st = navb["stats"]
    out: Dict[str, Any] = {}

    # ---------- 1. 风格穿透（拟合层面，成立）
    if not rank.empty:
        best_row = rank.iloc[0]
        bm = payload.get(best_row["key"], {})
        lw = (bm.get("latest_weights") or {}).get("items", [])
        top_txt = "、".join(f"{r['name']} {r['weight']:.0%}" for r in lw[:3]
                           if r["weight"] and r["weight"] > 0.02)
        # 若最佳模型是黑箱，其权重只是重要性排序，必须降级表述
        blackbox = not bm.get("interpretable", True)
        if blackbox:
            tail = ("该模型是<b>黑箱模型</b>，下面权重是特征重要性排序，"
                    "<b>不能当作真实持仓</b>；它胜出说明非线性成分重要，"
                    "但风格暴露仍应以可解释模型（线性约束类）为准。")
        else:
            tail = "这意味着它的收益来源高度集中，可被上述赛道指数直接复制。"
        out["penetration"] = {
            "ok": True,
            "model": best_row["模型"],
            "text": (f"滚动样本外拟合显示，{name} 可被少数几个指数解释："
                     f"<b>{top_txt}</b>。该模型样本外 R² = "
                     f"<b>{best_row['样本外R²']:.3f}</b>，"
                     f"年化跟踪误差 {best_row['样本外TE']:.1%}。" + tail),
        }
        # 结论：低 TE = 好解释
        iqr = rank["样本外TE"].quantile([0.25, 0.75])
        out["te_spread"] = {
            "text": (f"模型间的跟踪误差从 {rank['样本外TE'].min():.1%} 到 "
                     f"{rank['样本外TE'].max():.1%}，"
                     f"四分位区间 {iqr[0.25]:.1%}~{iqr[0.75]:.1%}。"
                     f"差异主要来自模型的正则化强度与非线性程度，"
                     f"而非数据差异——因为所有模型用的是同一份切分。"),
        }

        # ---------- 1b. 合成基准与基金净值的累计差距 = alpha（不是拟合失败）
        # 用户最容易误读的一处：图上"基金起飞、合成基准横盘"看起来像崩了，
        # 但 R² 高说明风格暴露是对的，差距是**风格之外的 alpha**。
        a = (payload.get(best_row["key"], {}) or {}).get("alpha") or {}
        ann, mult = a.get("年化alpha"), a.get("累计倍数差")
        if ann is not None and mult is not None and abs(float(mult) - 1.0) > 0.15:
            tv = a.get("alpha_t值")
            sig = (tv is not None and abs(float(tv)) > 1.96)
            sig_txt = (
                f"其 t 值 <b>{float(tv):.2f}</b>"
                + ("（|t|>1.96，统计上显著）" if sig else
                   "（|t|&lt;1.96，<b>统计上不显著</b>，只能当点估计看）")
                if tv is not None else "")
            out["alpha_gap"] = {
                "warn": float(mult) < 1.0,
                "text": (
                    f"<b>为什么两条线会越走越远：</b>该基金与合成基准的累计差距"
                    f"折合年化 <b>{float(ann):+.1%}</b>（{a.get('年数')} 年累计 "
                    f"<b>{float(mult):.2f}×</b>）。{sig_txt}<br/>"
                    f"这不是拟合失败 —— 样本外 R² 仍有 "
                    f"<b>{best_row['样本外R²']:.3f}</b>，说明<b>风格暴露识别是对的</b>；"
                    f"差距来自<b>风格之外的 alpha</b>（基金选股超额），"
                    f"指数组合在数学上无法复制它。"),
            }
            # 口径差是**可修**的那一部分：实测对低 alpha 基金几乎能完全抹平
            out["alpha_fix"] = {
                "text": (
                    f"<b>其中一部分是口径差，可以修：</b>基金侧用总回报（含分红再投），"
                    f"而候选指数是<b>价格指数（不含股息）</b>，这部分会被算进 alpha。"
                    f"把 <code>RBSA[\"index_dividend_annual\"]</code> 填一个年化股息率"
                    f"（如 <code>0.02</code>）即可还回指数侧 —— 实测对部分基金"
                    f"几乎能完全抹平差距（110022 合成累计 1.66→2.00，基金实际 1.96；"
                    f"000001 1.22→1.39，基金实际 1.39）。<br/>"
                    f"<b>不建议</b>为了追上差距去调大 Σw：实测放开仓位上限后 R² 反而"
                    f"全面变差（0.83→0.77）。"),
            }

    # ---------- 2. 风格漂移（相关性层面）
    if corr.get("drift"):
        d = corr["drift"]
        shifted = [r for r in d if r["delta"] is not None and abs(r["delta"]) > 0.10]
        if shifted:
            txt = "；".join(f"与{r['name']}的 120 日滚动相关从 "
                           f"{r['early']:+.2f} 变为 {r['late']:+.2f}"
                           for r in d)
            out["drift"] = {
                "warn": bool(shifted),
                "text": (f"相关性并非恒定：{txt}。"
                         + ("<b>存在风格漂移，说明基金经理近期调仓改变了赛道暴露，"
                            "固定权重的合成基准会滞后。</b>" if shifted else
                            "<b>漂移幅度均在 0.10 以内，风格暴露相对稳定。</b>")),
            }

    # ---------- 3. 拟合 ≠ 预测（关键提醒）
    if not rank.empty:
        lin = rank[rank["可解释"] == "是"]
        blk = rank[rank["可解释"] == "否"]
        parts = []
        if len(lin):
            parts.append(f"线性约束类模型（{len(lin)} 个）样本外 R² "
                         f"{lin['样本外R²'].min():.3f}~{lin['样本外R²'].max():.3f}")
        if len(blk):
            parts.append(f"黑箱模型（{len(blk)} 个）样本外 R² "
                         f"{blk['样本外R²'].min():.3f}~{blk['样本外R²'].max():.3f}")
        overfit = []
        if len(blk):
            for k in blk["key"]:
                p = payload.get(k, {})
                fi = p.get("metrics", {}).get("fit_r2_mean")
                oo = p.get("metrics", {}).get("oos_r2")
                if fi is None or oo is None:
                    continue
                gap = fi - oo
                if gap > 0.08:
                    overfit.append((gap, f"{p['name']}（窗口内 {fi:.3f} → 样本外 "
                                         f"{oo:.3f}，差 {gap:+.3f}）"))
        # 可解释模型也可能过拟合，一并检查
        if len(lin):
            for k in lin["key"]:
                p = payload.get(k, {})
                fi = p.get("metrics", {}).get("fit_r2_mean")
                oo = p.get("metrics", {}).get("oos_r2")
                if fi is None or oo is None:
                    continue
                gap = fi - oo
                if gap > 0.08:
                    overfit.append((gap, f"{p['name']}（窗口内 {fi:.3f} → 样本外 "
                                         f"{oo:.3f}，差 {gap:+.3f}）"))
        overfit.sort(reverse=True)
        top_of = [t for _, t in overfit[:2]]
        out["overfit"] = {
            "warn": bool(overfit),
            "text": ("；".join(parts) + "。"
                     + ("<b>" + "；".join(top_of) + " 存在过拟合："
                        "训练集拟合度高但样本外塌陷，这类模型的权重"
                        "不能当作真实持仓看。</b>" if overfit else
                        "<b>各模型训练/样本外差距均在 0.08 以内，控制良好。</b>")),
        }

    # ---------- 4. 仓位假设失配（最容易误读的一类低 R²）
    # 关键：线性约束模型强制 Σw=equity_ratio，因此它的隐含 beta 直接反映
    # 「假设与实际的偏离」；黑箱模型不做仓位约束，beta 恒等于 1，无诊断价值。
    # 所以只用**可解释模型**的 beta 做判断。
    lin_keys = [x["key"] for x in rank.to_dict("records")
                if x.get("可解释") == "是" and x["key"] in models]
    lin_betas = [float(models[k].beta) for k in lin_keys
                 if models[k].beta is not None and np.isfinite(models[k].beta)]
    if lin_betas:
        b = float(np.median(lin_betas))
        eq = C.RBSA["equity_ratio"]
        vr = next((models[k].vol_ratio for k in lin_keys
                   if models[k].vol_ratio is not None
                   and np.isfinite(models[k].vol_ratio)), None)
        vr = float(vr) if vr is not None else None
        if b < eq * 0.7:
            out["exposure_gap"] = {
                "warn": True,
                "text": (
                    f"<b>该基金的实际股票仓位约 {b:.0%}</b>"
                    f"（用线性约束模型的回归斜率估计），"
                    f"远低于框架假设的 {eq:.0%}。"
                    f"合成基准因此承担了过多波动"
                    + (f"（是基金自身波动的 {vr:.1f} 倍）" if vr else "") + "，"
                    f"<b>这会系统性压低线性模型的 R²</b>。"
                    f"因此下面的低 R² <b>不代表风格识别失败</b>，"
                    f"而是说明该基金大概率做了对冲、分档或仓位择时。"),
            }
            out["exposure_fix"] = {
                "text": (
                    f"要让 R² 反映「风格识别能力」而非「仓位差异」，"
                    f"应把约束改成 <code>Σw ≈ {b:.2f}</code>（按隐含 beta 缩放），"
                    f"或改用<b>不约束仓位</b>的纯风格回归。"
                    f"本页面为保持跨基金可比，仍统一用 {eq:.0%}；"
                    f"此时<b>建议直接参考排名靠前的黑箱模型</b>——"
                    f"它们不受仓位假设影响。"),
            }
        else:
            out["exposure_gap"] = {
                "warn": False,
                "text": (f"线性约束模型反推出的实际股票仓位约 {b:.0%}，"
                         f"与框架假设的 {eq:.0%} 基本吻合，"
                         f"说明该基金是常规满仓运作，R² 具备横向可比性。"),
            }

    # ---------- 5. 候选池覆盖度
    layers = corr.get("layers") or []
    if layers:
        top = layers[0]
        out["coverage"] = {
            "text": (f"候选池共 {corr.get('n_assets', 0)} 个指数，"
                     f"覆盖宽基 / 市值风格 / 价值成长 / 申万一级行业四层。"
                     f"相关度最高的层级是 {top['layer']}"
                     f"（平均相关 {top['avg_corr']:+.3f}），"
                     f"这直接决定了拟合出的风格权重的可信区间。"),
        }
    else:
        # 候选池为空（数据未取到/全部剔除）时，旧写法 corr['layers'][0] 直接 IndexError
        out["coverage"] = {
            "text": (f"候选池共 {corr.get('n_assets', 0)} 个指数，"
                     "但本次没有可用的层级相关度（数据为空或已全部剔除），"
                     "风格权重的可信区间无法评估。"),
        }

    # ---------- 5. 走势描述（只描述，不预测）
    nav = np.array([v for v in navb["nav"] if v is not None], dtype=float)
    if len(nav) > 250:
        r = np.diff(np.log(nav))
        s: List[str] = []
        for lbl, n in (("近1月", 21), ("近3月", 63), ("近6月", 126),
                       ("近1年", 252)):
            if len(r) > n:
                s.append(f"{lbl} {float(np.exp(r[-n:].sum()) - 1):+.1%}")
        out["trend"] = {
            "text": (f"近期动量：{'，'.join(s)}。"
                     f"当前 60 日年化波动 {st.get('current_vol60')}，"
                     f"处于历史区间"
                     f"{'偏高' if (st.get('current_vol60') or 0) > (st.get('max_vol60') or 1) * 0.7 else '中性偏低'}"
                     f"（历史峰值 {st.get('max_vol60')}）。"
                     f"<b>此处只做状态描述，不给出买卖建议。</b>"),
        }
    return out


def _backtest_payload(code: str, name: str, model_key: str, auto_equity: bool,
                      bt: dict, health: pd.DataFrame, decision: pd.Series,
                      elapsed: float, valuation: dict | None = None,
                      meta_extra: dict | None = None) -> dict:
    eq = bt["equity"]
    _mn = bt.get("main_name", "择时策略(融合)")
    hs = health_stats(health)
    exit_s = float(C.SIGNAL["health_exit"])
    h = health["health_score"].dropna() if "health_score" in health else pd.Series(dtype=float)
    h_last = float(h.iloc[-1]) if len(h) else None
    pos = int(decision.iloc[-1]) if len(decision) else None
    # 健康分缺失时不把回测仓位的默认值当成"当前建议"，否则页面会显示一个
    # 看起来正常的仓位。缺数据就必须显式不可用。
    if h_last is None:
        pos = None
    last_dt = decision.index[-1] if len(decision) else eq.index[-1]
    notes: List[str] = []
    if h_last is None:
        notes.append("健康分全部缺失，无法给出建议仓位。")
    if hs.get("n_below_exit", 0) == 0 and hs.get("gap_to_exit") is not None:
        notes.append(f"健康分从未触及离场线（阈值 {exit_s:+.2f}，最低 {hs['min']:+.2f}），"
                     "门控实际不可触发，当前建议等价于买入持有。")
    if float(hs.get("unreliable_ratio") or 0.0) >= 0.5:
        notes.append(f"拟合在 {hs['unreliable_ratio']:.0%} 的交易日不可信，卖出被停用。")
    if len(bt["trades"]) == 0:
        notes.append("回测区间内没有产生任何交易。")
    unavailable = h_last is None or pos is None
    degraded = bool(unavailable or float(hs.get("unreliable_ratio") or 0.0) >= 0.5
                    or (hs.get("n_below_exit", 0) == 0))
    current = {
        "date": last_dt.strftime("%Y-%m-%d"),
        "available": not unavailable,
        "degraded": degraded,
        "position": pos,
        "position_pct": (1.0 if pos == 1 else 0.0) if pos is not None else None,
        "state": ("不可用" if unavailable else ("持有" if pos == 1 else "空仓")),
        "health": _jnum(h_last, 4),
        "distance_to_exit": (_jnum(h_last - exit_s, 3)
                             if h_last is not None else None),
        "notes": notes,
        "disclaimer": "模型参考值，非投资建议；决策在 t 日收盘做出、t+1 生效。",
    }
    return {
        "meta": {
            "code": code, "name": name,
            "model": model_key, "model_name": MODELS[model_key].name,
            "auto_equity": bool(auto_equity),
            "start": bt["window_start"].strftime("%Y-%m-%d"),
            "end": bt["window_end"].strftime("%Y-%m-%d"),
            "elapsed": round(elapsed, 2),
            "thresholds": {"exit": C.SIGNAL["health_exit"],
                           "enter": C.SIGNAL["health_enter"]},
            # 门控诊断：零交易时必须让用户看到"健康分离离场线还有多远"，
            # 否则"无触发交易"会被误读成"信号判断为一直持有"。
            "no_trades": len(bt["trades"]) == 0,
            "health_stats": _jsafe(hs),
            # P1-20/P2-27：口径与数据质量随回测结果一起展示
            **(meta_extra or {}),
        },
        "current": current,
        "valuation": _jsafe(valuation or {}),
        "variants": [
            {"name": k,
             "note": (valuation or {}).get("note") if "估值" in k else None,
             "degraded": bool((valuation or {}).get("degraded")) if "估值" in k else False}
            for k in (bt.get("timing_contrib_all") or {})
        ],
        "equity": {
            "dates": _dates(eq.index),
            "series": {k: [_jnum(v, 5) for v in eq[k]] for k in eq.columns},
        },
        "metrics": _jsafe(bt["metrics"].reset_index().to_dict("records")),
        "health": {
            "dates": _dates(health.index),
            "score": [_jnum(v, 4) for v in health["health_score"]],
            "r2": [_jnum(v, 4) for v in health["fit_r2"]],
            "te_ratio": [_jnum(v, 4) for v in health["te_ratio"]],
            "alpha": [_jnum(v, 4) for v in health["active_alpha"]],
            # alpha 的 t 值才是真正进入健康分的分量（原始 alpha 噪声太大）
            "alpha_t": [_jnum(v, 4) for v in health["alpha_t"]],
            "position": [int(v) for v in decision.tolist()],
        },
        "trades": [{
            "date": t["date"].strftime("%Y-%m-%d"),
            "exec_date": t["exec_date"].strftime("%Y-%m-%d"),
            "action": t["action"], "health": round(t["health"], 3),
            "reason": t["reason"],
        } for t in bt["trades"]],
        "summary": {
            "ic": _jnum(bt["ic"]), "icir": _jnum(bt["icir"]),
            "turnover": _jnum(bt["turnover"], 2),
            "avg_position": _jnum(bt["avg_position"], 3),
            "win_long": _jnum(bt["win_long"], 3),
            # N-02：胜率的分母口径必须一起给出去，否则"99.17%"会被误读成
            # "几乎全对"（旧实现把尾部无效前瞻标签算进了分母）。
            "n_valid_label": bt.get("n_valid_label"),
            "control_name": bt["control_name"],
            "timing_contrib": _jsafe(bt["timing_contrib"]),
            # 逐方案的对照名与净贡献：只有方案 vs 它自己的对照才是有效比较
            "controls": _jsafe(bt["controls"]),
            "timing_contrib_all": _jsafe(bt["timing_contrib_all"]),
            "avg_position_all": _jsafe(bt["avg_position_all"]),
            # N-03：夏普有两个口径，都要给（几何 vs 算术）
            "sharpe_geometric": _jnum(
                bt["metrics"].loc[_mn, "夏普"], 3)
            if _mn in bt["metrics"].index else None,
            "sharpe_arithmetic": _jnum(
                bt["metrics"].loc[_mn, "算术夏普"], 3)
            if _mn in bt["metrics"].index else None,
        },
    }
