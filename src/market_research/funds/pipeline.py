# -*- coding: utf-8 -*-
"""单只基金的端到端流水线：数据 → 风格拟合 → 信号 → 回测。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import market_research.audit as audit
import market_research.config as C
import market_research.data.marketdata as D
from market_research.modeling.backtest import run_backtest, score_to_position
from market_research.funds.rbsa import RBSA
from market_research.modeling.signals import SignalEngine, build_decision, health_stats


def _completeness(res: dict) -> dict:
    """报告路径的完整性契约（P2-31），与 Web 结果同构。"""
    missing: list = []
    sections: dict = {}

    def check(name: str, ok: bool, reason: str, optional: bool = False) -> None:
        sections[name] = {"status": "ok" if ok else
                          ("unavailable" if optional else "failed")}
        if not ok:
            missing.append({"section": name, "item": name, "reason": reason})

    check("nav", len(res["nav"]) > 0, "净值序列为空")
    check("exposure", len(res["exposure"]) > 0, "风格暴露为空")
    check("health", len(res["health"]) > 0, "健康分为空")
    check("backtest", len(res["backtest"]["equity"]) > 0, "回测净值为空")
    check("valuation", bool(res["valuation"].get("available")),
          "估值数据不可用", optional=True)
    check("style_drift", bool(res["style_drift"].get("ok")),
          res["style_drift"].get("reason") or "风格漂移未计算", optional=True)

    data_status = str((res.get("data_quality") or {}).get("status") or "ok")
    sections["data"] = {"status": data_status}
    fatal = any(s["status"] == "failed" for s in sections.values())
    status = "failed" if fatal else (
        "partial" if missing or data_status not in ("ok",) else "ok")
    reasons = [m["reason"] for m in missing]
    if data_status not in ("ok",):
        reasons.extend((res.get("data_quality") or {}).get("reasons") or [])
    return {"status": status, "ok": status != "failed",
            "sections": sections, "missing": missing, "reasons": reasons}


def run_fund(code: str, name: str = "", refresh: bool = False,
             verbose: bool = True, start_date: Optional[str] = None,
             end_date: Optional[str] = None,
             window_start: Optional[str] = None) -> dict:
    """
    window_start / end_date 是**分析窗口**（默认近 5 年，见 C.DEFAULT_WINDOW_YEARS）。
    start_date / end_date 是**回测**窗口 —— 两者不同：回测区间之前的数据要留给
    模型做拟合与预热，所以面板不能按 start_date 裁剪。

    ★ 旧实现只把 start_date 传给 run_backtest，面板恒用默认窗口，
      于是 `run.py --start` 改的只是回测段，拟合窗口永远不变。
    """
    name = name or C.DEMO_FUNDS.get(code, code)
    if verbose:
        print(f"\n{'='*78}\n>>> {code} {name}\n{'='*78}")

    panel = D.load_panel(code, start=(window_start or C.START_DATE),
                         end=end_date, refresh=refresh, verbose=False)
    nav = panel["__FUND__"]
    quality = dict(getattr(panel, "attrs", {}).get("data_quality") or {})
    # P1-20：净值口径（总回报 / 累计净值降级）必须随结果一起输出
    from market_research.funds.analysis import nav_basis_meta
    nav_meta = nav_basis_meta(getattr(panel, "attrs", {}))
    if verbose:
        print(f"[data] {panel.shape[0]} 交易日 × {panel.shape[1]-1} 条指数  "
              f"{panel.index[0].date()} ~ {panel.index[-1].date()}")
        print(f"[口径] 净值口径："
              f"{'总回报' if nav_meta['nav_basis'] == 'total' else '累计净值（降级）'}"
              f" — {nav_meta['data_basis']}")
        if quality and quality.get("status") != "ok":
            # P2-27：数据过期/缺失/口径降级/取数回退统一在这里提示
            print(f"[警示] 数据质量 {quality.get('status')}："
                  + "；".join(quality.get("reasons") or []))

    # ---------- 1. 风格拟合
    rbsa = RBSA()
    fit = rbsa.fit_rolling(panel, verbose=verbose)

    # 合成基准净值：与基金在样本外起点对齐（便于价差比较）
    syn_ret = fit["syn_ret"]
    syn_nav = RBSA.synth_nav(syn_ret, start_value=1.0)
    nav_oos = nav.reindex(syn_nav.index)

    # ---------- 2. 信号
    pe_pct = D.load_pe_panel(verbose=False)
    engine = SignalEngine()
    sig = engine.compute(nav_oos, syn_nav, fit["weights"], pe_pct)

    # 拟合健康度通道 + 二值买卖决策
    health = engine.fit_health_channel(nav_oos, syn_ret, fit["diag"],
                                       fit["weights"])
    dec_pos, dec_trades = build_decision(health)

    # ---------- 3. 回测（含单通道归因 + 对照实验）
    vt = engine.vol_target_channel(syn_nav)
    # P2-21：估值通道不可用时不能把 NaN 仓位塞进变体（会把回测净值污染成
    # NaN）；与 Web 回测同一判断口径，不可用就不生成该曲线并显式标注。
    valuation = dict(sig.attrs.get("valuation") or {})
    variants = {
        "仅趋势通道": score_to_position(sig["trend_score"]),
        "仅相对偏离通道": score_to_position(sig["dev_score"]),
        "拟合健康决策": dec_pos,
        "波动率目标": vt["vol_target_pos"].reindex(nav_oos.index).ffill().fillna(1.0),
    }
    if valuation.get("available") and sig["val_score"].notna().any():
        variants["仅赛道估值通道"] = score_to_position(sig["val_score"])
    else:
        valuation.setdefault("note", "估值通道不可用，未生成该对照曲线")
        valuation["excluded"] = True
    bt = run_backtest(nav_oos, sig, fund_name=name, variants=variants,
                      start_date=start_date, end_date=end_date,
                      decision_trades=dec_trades)

    # ---------- 4. 打包
    latest_w = fit["weights"].iloc[-1]
    latest_w = latest_w[latest_w > 0].sort_values(ascending=False)
    exposure = RBSA.latest_exposure(latest_w)
    snap = SignalEngine.snapshot(sig, name)

    if verbose:
        print(f"\n[结果] 最新风格暴露：")
        print(exposure.to_string(index=False))
        print(f"\n[结果] 当前决策：{json.dumps(snap, ensure_ascii=False, indent=2)}")
        if valuation.get("degraded"):
            assets = "、".join(
                f"{a['name']}({a['code']}, {a['weight']:.1%})"
                for a in (valuation.get("fallback_assets") or [])[:8])
            cov = valuation.get("coverage")
            print(f"\n[警示] 估值口径：{valuation.get('note') or '部分资产使用宽基兜底'}"
                  f"；真实 PE 覆盖率={cov:.0%}" if cov is not None else
                  f"\n[警示] 估值口径：{valuation.get('note') or '部分资产使用宽基兜底'}")
            if assets:
                print(f"       兜底资产（按权重）：{assets}")
        elif valuation.get("excluded"):
            print(f"\n[警示] 估值通道未生成对照曲线："
                  f"{valuation.get('note') or '不可用'}")
        print(f"\n[结果] 绩效：")
        print(bt["metrics"].to_string(float_format=lambda x: f"{x:.4f}"))
        print(f"       RankIC={bt['ic']:.4f}  ICIR={bt['icir']:.3f}  "
              f"多头胜率={bt['win_long']:.1%}  年化换手={bt['turnover']:.1f}x  "
              f"平均仓位={bt['avg_position']:.0%}")
        if len(bt["groups"]):
            print("\n       分组前瞻收益：")
            print(bt["groups"].to_string(float_format=lambda x: f"{x:.4%}"))
        # 各方案 vs 它自己的恒定仓位对照（不再共用主策略那一份）
        print("\n       逐方案择时净贡献（各自对照，仓位已中性化）：")
        for v, tv in bt["timing_contrib_all"].items():
            if not tv:
                continue
            print(f"         {v:14s} 对照={bt['controls'].get(v)}  "
                  f"超额夏普={tv['夏普']:+.3f}  超额年化={tv['年化收益']:+.2%}  "
                  f"平均仓位={bt['avg_position_all'].get(v, float('nan')):.0%}")

        if bt["trades"]:
            print(f"\n       健康门控交易（{len(bt['trades'])} 笔，次日生效）：")
            for t in bt["trades"]:
                print(f"         {t['exec_date'].date()}  {t['action']}  "
                      f"health={t['health']:+.2f}  {t['reason']}")
        else:
            # 零交易时必须明确告警：该方案已退化为买入持有，
            # 它与对照的差额只是仓位差，不是择时能力。
            st = health_stats(health)
            print(f"\n       [警告] 健康门控全程未触发（{len(bt['trades'])} 笔交易）"
                  f"→ 「拟合健康决策」≡ 买入持有。")
            print(f"               健康分 最低={st['min']:+.3f} "
                  f"5%分位={st['p05']:+.3f} 中位={st['median']:+.3f} "
                  f"离场线={st['exit']:+.2f}  距离场线还差 {st['gap_to_exit']:+.3f}")
            print("               该方案的绩效差异仅来自仓位水平，"
                  "不得作为择时能力的证据。")

    # ---------- 3.5 风格漂移（2026-10-12）
    # 经理变更无法检测（免费源只有现任、无任职日期），改为检测**风格本身**
    # 是否漂移。报告路径此前拿不到这个字段（只有 Web 前端有），这里补上。
    try:
        from market_research.research.experiments import style_drift as _style_drift
        drift = _style_drift(fit["weights"], nav=nav_oos, syn_nav=syn_nav,
                             name_of=getattr(C, "NAME_OF", {}))
    except Exception as e:                                   # noqa: BLE001
        drift = {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    if verbose and drift.get("ok"):
        print(f"\n[结果] 风格漂移：{drift['verdict']}")

    res = {
        "code": code, "name": name,
        "panel": panel, "nav": nav,
        "weights_hist": fit["weights"], "fit_diag": fit["diag"],
        "style_drift": drift,
        "syn_nav": syn_nav, "syn_ret": syn_ret,
        "signal": sig, "health": health,
        # 健康门控的仓位列：下游要做成本敏感性（E10）等需要原始仓位序列，
        # 而 backtest 只返回收益率序列，拿不到仓位。
        "decision_pos": dec_pos, "decision_trades": dec_trades,
        "backtest": bt,
        "exposure": exposure, "snapshot": snap,
        "valuation": valuation,
        "data_quality": quality,
        # P1-20：与 Web 结果同构的净值口径标识（data_basis / nav_basis / 降级原因）
        "meta": dict(nav_meta),
    }
    completeness = _completeness(res)
    res.update(status=completeness["status"], ok=completeness["ok"],
               completeness=completeness)
    # P2-26：CLI/报告链路同样留审计轨迹（只写状态与缺失项，不写行情数值）
    audit.record("analysis.run", ok=completeness["ok"],
                 subject=f"fund:{code}",
                 detail={"code": code, "window": [str(nav.index[0].date()),
                                                 str(nav.index[-1].date())],
                         "status": completeness["status"],
                         "missing": [item["item"] for item in
                                     completeness["missing"]][:8],
                         "data_quality": (quality or {}).get("status")})
    return res


def save_result(res: dict, outdir: Optional[Path] = None) -> Path:
    outdir = Path(outdir or C.RES_DIR / f"{res['code']}_{res['name']}")
    outdir.mkdir(parents=True, exist_ok=True)
    res["weights_hist"].to_csv(outdir / "style_weights.csv", encoding="utf-8-sig")
    res["signal"].to_csv(outdir / "signals.csv", encoding="utf-8-sig")
    res["backtest"]["equity"].to_csv(outdir / "equity.csv", encoding="utf-8-sig")
    res["backtest"]["metrics"].to_csv(outdir / "metrics.csv", encoding="utf-8-sig")
    if len(res["backtest"]["groups"]):
        res["backtest"]["groups"].to_csv(outdir / "ic_groups.csv", encoding="utf-8-sig")
    pd.DataFrame([res["snapshot"]]).to_csv(outdir / "decision.csv",
                                           index=False, encoding="utf-8-sig")
    meta = {
        "code": res["code"], "name": res["name"],
        "日期区间": [str(res["nav"].index[0].date()), str(res["nav"].index[-1].date())],
        "样本外天数": int(res["backtest"]["metrics"]["样本天数"].iloc[0]),
        "RankIC": None if pd.isna(res["backtest"]["ic"]) else round(res["backtest"]["ic"], 4),
        "ICIR": None if pd.isna(res["backtest"]["icir"]) else round(res["backtest"]["icir"], 3),
        "多头胜率": round(res["backtest"]["win_long"], 4),
        "年化换手": round(res["backtest"]["turnover"], 2),
        "平均仓位": round(res["backtest"]["avg_position"], 4),
        "健康门控交易数": len(res["backtest"]["trades"]),
        "健康分最低": round(health_stats(res["health"])["min"], 4),
        "离场线": C.SIGNAL["health_exit"],
        "数据质量": res.get("data_quality") or {},
        "完整性": res.get("completeness") or {},
    }
    (outdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return outdir
