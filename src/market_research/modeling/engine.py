from __future__ import annotations
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import market_research.config as C
from market_research.ordering import ranked_frame
from market_research.modeling.catalog import MODELS, ModelSpec
from market_research.modeling.estimators import fit_window, estimate_equity, _score

# B2/P3-R：现行的评估下限与推荐门槛，待产品/研究确认。
MIN_OOS_SESSIONS = 60
MIN_REFITS = 3
# 最小可拟合门槛：至少一个完整训练窗 + 1 个月样本外。与之匹配的是 RBSA
# 的 W+30；不再额外要求 W+60，短样本允许出诊断结果，但评估状态为不足。
MIN_FIT_EXTRA = 30


def _coverage_summary(paired_dates: pd.DatetimeIndex, rebalances: int,
                      expected_n: int, oos_r2: float, *,
                      error: Optional[str] = None) -> Dict[str, Any]:
    """覆盖率分母为重估限流后的样本外日期轴，含无法评分的日期。"""
    oos_n = len(paired_dates)
    reasons: List[str] = []
    if error:
        reasons.append(error)
    else:
        if oos_n < MIN_OOS_SESSIONS:
            reasons.append(f"样本外仅 {oos_n} 个交易日，低于门槛 {MIN_OOS_SESSIONS}")
        if rebalances < MIN_REFITS:
            reasons.append(f"有效重估窗口仅 {rebalances} 个，低于门槛 {MIN_REFITS}")
        if not np.isfinite(oos_r2):
            reasons.append("样本外 R² 未定义")
    return {
        "oos_n": oos_n,
        "oos_start": paired_dates[0].strftime("%Y-%m-%d") if oos_n else None,
        "oos_end": paired_dates[-1].strftime("%Y-%m-%d") if oos_n else None,
        "oos_gap_n": expected_n - oos_n,
        "oos_coverage": oos_n / expected_n if expected_n else None,
        "rebalance_count": rebalances,
        "assessment_status": ("failed" if error else
                              "insufficient" if reasons else "sufficient"),
        "assessment_reasons": reasons,
    }


def _agg_extras(extras: List[dict]) -> Dict[str, Any]:
    """把各重估窗口的 `extra` 汇总成一份诊断字典。

    数值型默认取中位数；但 `fit_ok`（约束优化是否收敛）取**最小值**：
    10 个窗口里只要 1 个降级，整体就不能声称"全部收敛"，中位数会把这种
    边缘失败抹平（9 个 1.0 + 1 个 0.0 的中位数仍是 1.0）。
    """
    agg: Dict[str, Any] = {}
    if not extras:
        return agg
    for k in set().union(*[set(e.keys()) for e in extras]):
        vals = [e[k] for e in extras if k in e]
        if not vals:
            continue
        v0 = vals[0]
        if isinstance(v0, (bool, np.bool_)):
            agg[k] = None
        elif isinstance(v0, (int, float, np.floating)):
            numeric = [float(v) for v in vals]
            # fit_ok 用最小值：只要有一个窗口降级，就不能声称全部收敛。
            agg[k] = (float(np.nanmin(numeric)) if k == "fit_ok"
                      else float(np.nanmedian(numeric)))
        elif isinstance(v0, (list, tuple)):
            # 只对纯数值列表取中位数（如 top_corr），字符串列表直接丢弃
            flat = [x for v in vals for x in v]
            if flat and all(isinstance(x, (int, float, np.floating))
                            for x in flat):
                agg[k] = float(np.nanmedian([float(x) for x in flat]))
        elif isinstance(v0, str):
            agg[k] = None
    return agg


def _failed_report(key: str, err: Exception, expected_n: int = 0) -> ModelReport:
    """某个模型失败时返回一个占位报告，保证前端仍能渲染完整表格。"""
    spec = MODELS.get(key, ModelSpec(key, key, "unknown", ""))
    error = f"{type(err).__name__}: {str(err)[:200]}"
    return ModelReport(
        key=key, name=spec.name, family=spec.family, desc=spec.desc,
        interpretable=spec.interpretable,
        oos_r2=float("nan"), oos_te=float("nan"),
        oos_ic=float("nan"), win=float("nan"),
        beta=float("nan"), vol_ratio=float("nan"),
        fit_r2_mean=float("nan"), fit_te_mean=float("nan"),
        latest_weights=pd.Series(dtype=float), weights_last_date=None,
        weights_hist=pd.DataFrame(), syn_ret=pd.Series(dtype=float),
        extra={"error": error, **_coverage_summary(
            pd.DatetimeIndex([]), 0, expected_n, float("nan"), error=error)},
        elapsed=0.0, fit_diag=pd.DataFrame(),
    )


def _worker(key: str, X_all, y_all, assets, dates, rebal_idx, cfg, override):
    """进程池入口：必须是模块级函数，且参数可 pickle。"""
    try:
        return ModelZoo(cfg)._run_one(key, np.asarray(X_all), np.asarray(y_all),
                                      list(assets), pd.DatetimeIndex(dates),
                                      list(rebal_idx), override)
    except Exception as e:                          # noqa: BLE001
        expected_n = len(dates) - rebal_idx[0] if rebal_idx else 0
        return _failed_report(key, e, expected_n)


@dataclass
class ModelReport:
    key: str
    name: str
    family: str
    desc: str
    interpretable: bool
    oos_r2: float                  # 样本外 R²（用 OOS 收益算）
    oos_te: float                  # 年化跟踪误差
    oos_ic: float                  # 合成收益与基金收益的秩相关（应≈1）
    win: float                     # 方向一致率
    beta: float                    # 基金对合成基准的回归斜率（隐含股票仓位）
    vol_ratio: float               # 合成基准波动 / 基金波动
    fit_r2_mean: float             # 各窗口内拟合 R² 均值
    fit_te_mean: float
    latest_weights: pd.Series
    weights_last_date: Optional[pd.Timestamp]
    weights_hist: pd.DataFrame      # 滚动权重（index=重估日）
    syn_ret: pd.Series             # 样本外合成收益
    extra: Dict[str, Any]
    elapsed: float
    fit_diag: pd.DataFrame = field(default_factory=pd.DataFrame)  # 逐窗口 r2/te/alpha
    # P1-25：样本外真实收益（与 syn_ret 同轴），用于跨模型共同日期重算。
    # 默认 None 以兼容旧检查点与测试夹具。
    oos_y: Optional[pd.Series] = None
    # 产品/研究确认前，推荐沿用原来的预测点数和拟合窗口数。
    recommendation_coverage_sufficient: Optional[bool] = None


class ModelZoo:
    """
    在同一数据切分上滚动评估多个模型，输出可排名、可切换的结果集合。

    关键点：所有模型共享同一份 rebal_idx 与窗口切分，因此横向排名公平。
    """

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or C.RBSA
        self.max_rebal = int((cfg or C.RBSA).get("max_rebal", 0)) or None
        # "linspace" = 按样本总长度均匀抽稀（快，但历史点会随追加数据漂移）
        # "calendar" = 固定日历锚点（前缀稳定，研究结论应使用它）
        self.rebal_mode = str((cfg or C.RBSA).get("rebal_mode", "linspace"))

    @staticmethod
    def _rebal_idx(n: int, W: int, refit: int, max_rebal: Optional[int] = None
                   ) -> List[int]:
        """
        构造滚动重估点，必要时**均匀抽稀**到 max_rebal 个。

        为什么需要抽稀：样本越长，重估点越多，总耗时线性增长。
        早期版本 2621 个交易日产生 119 个重估点，其中最贵的 RF 要跑 34 秒，
        导致 Web 首屏等待过长。

        抽稀对模型**公平性无影响**：所有模型共享同一份 rebal_idx，
        切分完全一致，横向排名依然可比。代价是合成基准的更新频率变低，
        但这只影响"基准有多新鲜"，不影响"这个模型能不能拟合"。
        """
        idx = list(range(W, n, refit))
        if max_rebal and len(idx) > max_rebal:
            import numpy as _np
            sel = _np.linspace(0, len(idx) - 1, max_rebal).round().astype(int)
            idx = [idx[i] for i in dict.fromkeys(sel.tolist())]
        return idx

    @staticmethod
    def _rebal_calendar(n: int, W: int, refit: int,
                        max_rebal: Optional[int] = None,
                        dates=None, freq: str = "QE") -> List[int]:
        """
        **固定日历**重估点（P0-04 修复路径①）。

        `_rebal_idx` 用 `linspace(0, len-1, max_rebal)` 均匀抽稀，采样位置由
        **样本总长度**决定 —— 追加 100 行数据，历史重估点里就有 52/60 个发生
        位移，历史输出被未来数据改写（实测）。

        这里改为按**自然日历锚点**（默认每季度末）取"锚点之后的第一个 refit 点"。
        锚点只由日期决定，追加未来数据不会移动历史锚点 → 前缀稳定。
        代价：每季一个点的密度低于 linspace 的 60 点，长样本下更粗。

        dates 为完整日期索引；freq 传 pandas 偏移别名（"QE" 季、"ME" 月）。
        """
        idx = list(range(W, n, refit))
        if not idx or dates is None:
            return idx
        di = pd.DatetimeIndex(dates)
        if len(di) < n:                       # 保险：dates 与 n 不匹配则退回原法
            return ModelZoo._rebal_idx(n, W, refit, max_rebal)
        try:
            anchors = pd.date_range(di[0], di[-1], freq=freq)
        except Exception:                     # noqa: BLE001
            return ModelZoo._rebal_idx(n, W, refit, max_rebal)
        out = []
        for a in anchors:
            j = int(np.searchsorted(di.values, np.datetime64(a), side="right"))
            k = int(np.searchsorted(idx, j, side="left"))
            if k < len(idx):
                out.append(idx[k])
        out = sorted(set(out))
        # 仍需限流时**保留最新 max_rebal 个锚点、从头部丢弃**（P1 修复轮）：
        # 等距 stride 采样在样本变长后步长会变，已选锚点集合随之改变 ——
        # 同样不是前缀稳定。保留尾部则保证"已选过的历史点永远还在"
        # （新集合是旧集合的超集），只有最老的点会被挤出窗口。
        if max_rebal and len(out) > max_rebal:
            out = out[-int(max_rebal):]
        return out

    # -------------------------------------------------------------- 主入口
    def run(self, px: pd.DataFrame, model_keys: List[str],
            fund_col: str = "__FUND__", params: Optional[dict] = None,
            progress=None, workers: int = 0,
            max_rebal: Optional[int] = None) -> Dict[str, ModelReport]:
        """
        px        : 对齐后的价格面板
        model_keys: 要评估的模型 key 列表
        params    : {model_key: {超参}} 覆盖
        progress  : 回调 progress(model_key, done, total, oos_r2)
        workers   : 并行进程数；0 = 自动（受 CPU 核数与模型数限制）
        max_rebal : 重估点上限，None = 用配置默认值

        模型之间完全独立，因此用进程池并行。注意：**并行压缩的是总墙钟，
        不是关键路径**——当某一个模型（如 RF）占了大部分耗时，
        并行化对它无能为力。此时应调小 max_rebal。
        """
        cfg = self.cfg
        params = params or {}
        # P1-25：`load_panel` 会在 attrs 里附带未经全区间删列/删行的原始候选
        # 价格（causal_pool）。研究路径必须用它，否则"某个指数在尾部才缺数"
        # 会改写早期窗口的可用因子集。没有该属性时退回旧的对齐面板。
        causal = px.attrs.get("causal_pool") if isinstance(px, pd.DataFrame) else None
        source = causal if isinstance(causal, pd.DataFrame) and fund_col in causal.columns else px
        causal_used = source is causal
        assets = [c for c in source.columns if c != fund_col]
        if source is causal:
            R = np.log(source[assets + [fund_col]].astype(float)).diff()
            # 只按**基金收益**确定样本日期轴；候选指数缺日保留为 NaN，
            # 由每个重估窗口自行判断覆盖率，不做全区间删行。
            R = R.loc[R[fund_col].notna()]
        else:
            P = np.log(source.astype(float))
            R = P.diff().dropna()
        dates = R.index
        X_all = R[assets].values
        y_all = R[fund_col].values

        # ★ 指数股息调整（P0-01 遗留 / alpha 分解配套）
        #   基金侧现在是**总回报**（含分红再投），而候选指数是**价格指数**
        #   （不含成分股股息）。这个口径差全部落进残差、被当成 alpha，
        #   实测约 2~3%/年，复利 9 年就是 20~30% 的"伪 alpha"。
        #   填 `RBSA["index_dividend_annual"]` 即可把它还回指数侧；
        #   默认 0 = 保持原行为，不改变任何现有结果。
        div = float(cfg.get("index_dividend_annual", 0.0) or 0.0)
        if abs(div) > 1e-9:
            X_all = X_all + div / 252.0

        n = len(dates)
        W = cfg["window"]
        if n < W + MIN_FIT_EXTRA:
            raise ValueError(
                f"样本不足：仅 {n} 行，滚动拟合至少需要 {W}+{MIN_FIT_EXTRA}。"
                f"更短的历史无法产生评估结果。")
        mr = max_rebal if max_rebal is not None else self.max_rebal
        # rebal_mode="calendar" → 固定日历锚点，追加数据不改变历史重估点（P0-04）
        if str(cfg.get("rebal_mode", self.rebal_mode)) == "calendar":
            rebal_idx = self._rebal_calendar(n, W, cfg["refit"], mr, dates)
        else:
            rebal_idx = self._rebal_idx(n, W, cfg["refit"], mr)
        expected_n = n - rebal_idx[0] if rebal_idx else 0

        keys = list(model_keys)
        if not keys:
            return {}

        if workers == 0:
            from market_research.resources import model_workers
            workers = model_workers(len(keys))
        use_pool = workers > 1 and len(keys) > 1

        out: Dict[str, ModelReport] = {}
        if use_pool:
            try:
                from concurrent.futures import ProcessPoolExecutor, as_completed
                ctx = __import__("multiprocessing").get_context("spawn")
                with ProcessPoolExecutor(max_workers=workers,
                                         mp_context=ctx) as ex:
                    futs = {
                        ex.submit(_worker, key, X_all, y_all, assets,
                                  dates, rebal_idx, cfg, params.get(key)): key
                        for key in keys
                    }
                    done = 0
                    for fut in as_completed(futs):
                        key = futs[fut]
                        try:
                            out[key] = fut.result()
                        except Exception as e:      # noqa: BLE001
                            out[key] = _failed_report(key, e, expected_n)
                        done += 1
                        if progress:
                            progress(key, done, len(keys), out[key].oos_r2)
                for rep in out.values():
                    rep.extra["causal_pool_used"] = causal_used
                    rep.extra["causal_required"] = bool(
                        cfg.get("require_causal_panel", False))
                self._attach_common_oos(out)
                return out
            except Exception as e:                  # noqa: BLE001
                print(f"[models] 进程池不可用（{type(e).__name__}: {str(e)[:60]}），"
                      f"退回串行")
                out = {}

        for i, key in enumerate(keys, 1):
            try:
                rep = self._run_one(key, X_all, y_all, assets, dates,
                                    rebal_idx, params.get(key))
            except Exception as e:                  # noqa: BLE001
                rep = _failed_report(key, e, expected_n)
            out[key] = rep
            if progress:
                progress(key, i, len(keys), rep.oos_r2)
        for rep in out.values():
            rep.extra["causal_pool_used"] = causal_used
            rep.extra["causal_required"] = bool(
                cfg.get("require_causal_panel", False))
        self._attach_common_oos(out)
        return out

    # -------------------------------------------------------------- 单模型滚动
    def _run_one(self, key: str, X_all, y_all, assets, dates, rebal_idx,
                 override: Optional[dict]) -> ModelReport:
        t0 = time.time()
        cfg = self.cfg
        rows, fit_r2s, fit_tes, extras = [], [], [], []
        diag_rows, equities = [], []
        n_fallback = 0          # 黑箱预测失败、回退成重要性权重的窗口数
        paired_refits = 0
        syn = pd.Series(index=dates[rebal_idx[0]:], dtype=float)
        last_w = None
        last_date = None
        spec = MODELS[key]
        auto_equity = bool(cfg.get("auto_equity", False))
        bounds = tuple(cfg.get("equity_bounds", (0.2, 1.0)))
        eq_start = float(cfg.get("equity_ratio", C.RBSA["equity_ratio"]))

        min_coverage = float(cfg.get("min_coverage", 0.97))
        for k, i in enumerate(rebal_idx):
            lo, hi = i - cfg["window"], i
            Xw, yw = X_all[lo:hi], y_all[lo:hi]
            if len(yw) < cfg["min_obs"]:
                continue
            # P1-25：只用**本窗口**的可得性选因子。未来才出现的缺数不得
            # 改写历史窗口的候选集；不再在全区间先删列。
            finite = np.isfinite(Xw)
            coverage = finite.mean(axis=0) if len(Xw) else np.zeros(len(assets))
            sel = np.flatnonzero(coverage >= min_coverage)
            if sel.size == 0:
                continue
            sel_rows = np.isfinite(yw) & finite[:, sel].all(axis=1)
            if int(sel_rows.sum()) < cfg["min_obs"]:
                continue
            Xw_fit, yw_fit = Xw[sel_rows][:, sel], yw[sel_rows]
            cols = [assets[j] for j in sel]
            if auto_equity and spec.interpretable:
                # 只有可解释模型（Σw 有经济含义）才谈得上"实际仓位"；
                # 黑箱模型的权重只是重要性排序，缩放它没有意义。
                er, fr = estimate_equity(
                    lambda E: fit_window(key, Xw_fit, yw_fit, cols, override,
                                         equity_ratio=E),
                    Xw_fit, yw_fit, cols, bounds=bounds,
                    grid=tuple(cfg.get("equity_grid",
                                       (0.20, 0.30, 0.40, 0.50,
                                        0.60, 0.70, 0.80, 0.95))),
                    method=str(cfg.get("equity_method", "grid")))
                equities.append(er)
            else:
                # ★ 之前这里是 None，导致 fit_window 回落到全局
                # C.RBSA["equity_ratio"]，也就是 ModelZoo(cfg) 传入的
                # equity_ratio **完全被忽略** —— 一个怎么改都不生效的配置项。
                er = eq_start
                fr = fit_window(key, Xw_fit, yw_fit, cols, override,
                                equity_ratio=er)
            # P1-25：窗口内若因缺报价删过行，TE 也不能按逐日 252 直接年化。
            window_periods = 252.0
            if len(Xw_fit) > 1:
                span = len(pd.bdate_range(dates[lo], dates[hi - 1]))
                if span > len(Xw_fit):
                    window_periods = 252.0 * len(Xw_fit) / span
                    _, fr.te = _score(fr.pred, yw_fit,
                                      periods_per_year=window_periods)
            w_sel = fr.weights.reindex(cols).fillna(0.0).values

            # 权重覆盖到下一个重估点：抽稀后相邻点间隔可能远大于 refit，
            # 若只覆盖固定 refit 天会丢掉中间大段样本
            next_i = rebal_idx[k + 1] if k + 1 < len(rebal_idx) else len(dates)
            j_end = min(next_i, len(dates))
            X_next = X_all[i:j_end][:, sel]
            degraded = ""
            # 只有**真正被赋予权重**的因子缺报价才使该日不可用；
            # 权重为 0 的候选缺数不应污染合成收益。
            relevant = np.abs(w_sel) > 0
            missing = (~np.isfinite(X_next[:, relevant]).all(axis=1)
                       if relevant.any() else np.zeros(len(X_next), dtype=bool))
            X_next_clean = np.where(np.isfinite(X_next), X_next, 0.0)
            if fr.predictor is not None:
                # 黑箱模型：用「上一窗口拟合好的估计器」预测下一段，
                # 这是真正的样本外（前向）预测。
                seg = np.full(len(X_next), np.nan)
                ok = ~missing
                if ok.any():
                    try:
                        pred = np.asarray(fr.predictor(X_next[ok]), dtype=float)
                        if not np.all(np.isfinite(pred)):
                            raise ValueError("预测含非有限值")
                        seg[ok] = pred
                        if missing.any():
                            degraded = "missing_factor_days"
                    except Exception as e:      # noqa: BLE001
                        # ★ 回退必须如实记录：把重要性权重当线性持仓得到的收益
                        #   不能伪装成黑箱模型的原生样本外结果（P0-05）。
                        seg[ok] = X_next_clean[ok] @ w_sel
                        degraded = f"blackbox_fallback:{type(e).__name__}"
                        n_fallback += 1
            else:
                # 线性约束模型：合成收益 = X @ w，权重和 = 股票仓位
                seg = X_next_clean @ w_sel
            seg[missing] = np.nan
            syn.loc[dates[i:j_end]] = seg
            paired_refits += int((np.isfinite(seg) & np.isfinite(y_all[i:j_end])).any())

            rows.append({"date": dates[i - 1], **fr.weights.to_dict()})
            fit_r2s.append(fr.r2)
            fit_tes.append(fr.te)
            # P1-25：逐窗审计信息 —— 训练起止、入选/剔除因子及覆盖率、
            # 输入指纹。指纹只截断前 16 位，用于同一窗口的可复现核对，
            # 不是安全哈希。
            dropped_names = [
                f"{assets[j]}:{coverage[j]:.2f}"
                for j in np.flatnonzero(coverage < min_coverage)]
            fingerprint = hashlib.sha256(
                np.ascontiguousarray(
                    np.column_stack([Xw_fit, yw_fit])).tobytes()
            ).hexdigest()[:16]
            diag_rows.append({
                "date": dates[i - 1], "r2": fr.r2, "te": fr.te,
                "alpha": fr.extra.get("alpha"),
                "train_start": dates[lo].strftime("%Y-%m-%d"),
                "train_end": dates[hi - 1].strftime("%Y-%m-%d"),
                "te_ppy": float(window_periods),
                "selected": ",".join(cols),
                "dropped": ",".join(dropped_names),
                "fingerprint": fingerprint,
                "n_factors": int(sel.size),
                "n_rows": int(sel_rows.sum()),
                "dropped_factors": int(len(assets) - sel.size),
                "degraded": degraded or None})
            if fr.extra:
                extras.append(fr.extra)
            last_w, last_date = fr.weights, dates[i - 1]

        syn_raw = syn
        syn = syn_raw.dropna()
        # 每个滚动窗口都因 len(yw) < min_obs 被跳过时 rows/diag_rows 为空，
        # 后面的 set_index("date") 会抛 KeyError 而不是给出可读的失败原因。
        # 统一转成 ValueError，交给调用方的 _failed_report 记录。
        if not rows:
            raise ValueError(
                "无有效拟合窗口（全部 len(yw) < min_obs），样本不足以拟合")
        y_oos = pd.Series(y_all, index=dates).reindex(syn.index)
        # P1-25：合成收益可能因必需因子缺报价留下空档。年化 TE 不能把
        # 不连续样本当逐日序列，按"实际样本数 / 日历内交易日数"折算年化频率。
        oos_periods = 252.0
        if len(syn) > 1:
            span = len(pd.bdate_range(syn.index[0], syn.index[-1]))
            if span > len(syn):
                oos_periods = 252.0 * len(syn) / span
        oos_r2, oos_te = _score(syn.values, y_oos.values,
                                periods_per_year=oos_periods)
        ic = float(syn.corr(y_oos, method="spearman"))
        win = float((np.sign(syn.values) == np.sign(y_oos.values)).mean())

        # 隐含股票仓位：把基金收益对合成基准收益做无约束回归，斜率即 beta。
        # 关键诊断 —— 若beta 远小于 equity_ratio（0.9），说明基金实际仓位远低于
        # 假设值，Σw=0.9 的合成基准就会系统性高估波动，导致 R² 被压低。
        # 典型场景：做了大量对冲/低仓位运作的"趋势型"基金。
        var_x = float(np.var(syn.values, ddof=1))
        beta = (float(np.cov(y_oos.values, syn.values)[0, 1] / var_x)
                if var_x > 1e-18 else float("nan"))
        vol_ratio = (float(syn.std() / y_oos.std())
                     if y_oos.std() > 1e-12 else float("nan"))

        wdf = pd.DataFrame(rows).set_index("date").fillna(0.0)
        wdf = wdf.loc[:, (wdf.abs().sum(0) > 0)]

        fit_diag = pd.DataFrame(diag_rows).set_index("date")

        # 各参数的中位值（诊断用）
        agg_extra: Dict[str, Any] = {}
        if equities:
            agg_extra["equity_used"] = float(np.median(equities))
        # ★ 降级必须可观测：黑箱回退窗口数 / 有效窗口数（P0-05）
        if diag_rows:
            agg_extra["fallback_windows"] = int(n_fallback)
            agg_extra["fallback_ratio"] = float(n_fallback) / len(diag_rows)
        if extras:
            agg_extra.update(_agg_extras(extras))

        # B2/P3-R：覆盖元数据放在 extras 聚合之后，避免被逐窗指标覆盖。
        # 页面/API/导出据此区分"已算出"与"评估充分"。
        paired_dates = syn.index[np.isfinite(syn.values) & np.isfinite(y_oos.values)]
        agg_extra.update(_coverage_summary(
            paired_dates, paired_refits, len(syn_raw), oos_r2))
        agg_extra["oos_periods_per_year"] = float(oos_periods)
        agg_extra["te_basis"] = ("calendar_adjusted"
                                 if abs(oos_periods - 252.0) > 1e-9 else "daily")

        return ModelReport(
            key=key, name=spec.name, family=spec.family, desc=spec.desc,
            interpretable=spec.interpretable,
            oos_r2=oos_r2, oos_te=oos_te, oos_ic=ic, win=win,
            beta=beta, vol_ratio=vol_ratio,
            fit_r2_mean=float(np.mean(fit_r2s)) if fit_r2s else np.nan,
            fit_te_mean=float(np.mean(fit_tes)) if fit_tes else np.nan,
            latest_weights=last_w if last_w is not None else pd.Series(dtype=float),
            weights_last_date=last_date,
            weights_hist=wdf, syn_ret=syn,
            extra=agg_extra, elapsed=time.time() - t0,
            fit_diag=fit_diag, oos_y=y_oos,
            recommendation_coverage_sufficient=(
                len(syn) >= MIN_OOS_SESSIONS and len(diag_rows) >= MIN_REFITS),
        )

    # -------------------------------------------------------------- 排名
    @staticmethod
    def _attach_common_oos(reports: Dict[str, ModelReport]) -> None:
        """跨模型共同样本外日期与可比指标。

        各模型自己的 OOS 序列可能因因子缺报价而空档长度不同；直接比较各自
        全段的 R² 不是同一道题。这里取交集，并在交集中重算 R²/TE。
        """
        usable = [r.syn_ret for r in reports.values()
                  if isinstance(r.syn_ret, pd.Series) and len(r.syn_ret)]
        if not usable:
            return
        common = usable[0].index
        for series in usable[1:]:
            common = common.intersection(series.index)
        common = common.sort_values()
        for r in reports.values():
            s = r.syn_ret
            n = int(len(common)) if isinstance(s, pd.Series) and len(s) else 0
            r.extra["common_oos_n"] = n
            r.extra["common_oos_r2"] = None
            r.extra["common_oos_te"] = None
            if not n:
                continue
            r.extra["common_oos_start"] = common[0].strftime("%Y-%m-%d")
            r.extra["common_oos_end"] = common[-1].strftime("%Y-%m-%d")
            y = getattr(r, "oos_y", None)
            if isinstance(y, pd.Series) and len(y):
                r2, te = _score(s.reindex(common).values,
                                y.reindex(common).values)
                r.extra["common_oos_r2"] = r2
                r.extra["common_oos_te"] = te

    @staticmethod
    def blocking_reasons(r: ModelReport) -> List[str]:
        """模型不能进入推荐位的原因（原始诊断仍保留）。"""
        reasons: List[str] = []
        if r.extra.get("error"):
            reasons.append("拟合失败")
        try:
            finite_r2 = bool(np.isfinite(float(r.oos_r2)))
        except (TypeError, ValueError):
            finite_r2 = False
        if not finite_r2:
            reasons.append("样本外 R² 未定义")
        coverage_ok = r.recommendation_coverage_sufficient
        if coverage_ok is None:
            coverage_ok = r.extra.get("assessment_status") == "sufficient"
        if not coverage_ok:
            reasons.append("样本外覆盖不足")
        fit_ok = r.extra.get("fit_ok")
        degraded = int(r.extra.get("fallback_windows") or 0) > 0
        if fit_ok is not None and np.isfinite(fit_ok) and float(fit_ok) < 1.0 - 1e-9:
            degraded = True
        if degraded:
            reasons.append("存在降级拟合窗口")
        if (r.extra.get("causal_required")
                and r.extra.get("causal_pool_used") is False):
            reasons.append("未使用因果面板")
        common_n = r.extra.get("common_oos_n")
        if common_n is not None and int(common_n) < MIN_OOS_SESSIONS:
            reasons.append("跨模型共同 OOS 不足")
        return reasons

    @staticmethod
    def rank(reports: Dict[str, ModelReport], key: str = "样本外R²") -> pd.DataFrame:
        """按指定指标生成排名表（默认样本外 R²，越大越好）。

        `可推荐` 是**推荐门禁**，不改变排序：失败、覆盖不足、降级拟合或
        旧检查点（无因果面板）仍出现在表里，但不能被 best/insights 选中。
        """
        rows = []
        for k, r in reports.items():
            status = r.extra.get("assessment_status")
            blockers = ModelZoo.blocking_reasons(r)
            rows.append({
                "key": k, "模型": r.name, "流派": r.family,
                "可解释": "是" if r.interpretable else "否",
                "样本外R²": r.oos_r2, "样本外TE": r.oos_te,
                "窗口内R²": r.fit_r2_mean, "窗口内TE": r.fit_te_mean,
                "方向一致率": r.win, "隐含仓位": r.beta, "耗时": r.elapsed,
                # 覆盖诊断与推荐门禁分别展示。
                "样本外天数": r.extra.get("oos_n"),
                "样本外起始": r.extra.get("oos_start"),
                "样本外结束": r.extra.get("oos_end"),
                "样本外覆盖率": r.extra.get("oos_coverage"),
                "共同OOS天数": r.extra.get("common_oos_n"),
                "共同OOS R²": r.extra.get("common_oos_r2"),
                "有效重估": r.extra.get("rebalance_count"),
                "评估状态": ("有效" if status == "sufficient" else
                             "不足" if status == "insufficient" else
                             "失败" if status == "failed" else "未知"),
                "评估原因": r.extra.get("assessment_reasons") or [],
                "可推荐": "否" if blockers else "是",
                "阻断原因": "；".join(blockers) or None,
            })
        df = pd.DataFrame(rows)
        if key not in df.columns:
            key = "样本外R²"
        asc = key in ("样本外TE", "窗口内TE", "耗时")
        return ranked_frame(df, key, ascending=asc)
