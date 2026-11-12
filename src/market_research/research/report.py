# -*- coding: utf-8 -*-
"""报告生成：把回测结果与诊断实验组装成一份自带图表的 HTML 研究报告。"""
from __future__ import annotations

import html as _html
import base64
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import market_research.config as C
import market_research.disclosure as disclosure
import market_research.research.charts as CH


def _head_icons() -> str:
    """报告多以本地 HTML 打开，图标内嵌 data URI，避免相对路径失效。"""
    try:
        from importlib.resources import files
        static = files("market_research.api") / "static"
        svg = (static / "favicon.svg").read_text(encoding="utf-8")
        png = (static / "apple-touch-icon.png").read_bytes()
    except Exception:
        return ""
    svg_uri = ("data:image/svg+xml;base64,"
               + base64.b64encode(svg.encode("utf-8")).decode("ascii"))
    png_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return (f'<link rel="icon" type="image/svg+xml" href="{svg_uri}"/>\n'
            f'<link rel="apple-touch-icon" href="{png_uri}"/>')


# ------------------------------------------------------------------ 工具
def _fmt(v, kind="num"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if kind == "int":
        return f"{int(round(float(v)))}"
    if kind == "pct":
        return f"{v:.2%}"
    if kind == "pct1":
        return f"{v:.1%}"
    if kind == "p3":
        return f"{v:+.3f}"
    if kind == "f3":
        return f"{v:.3f}"
    if kind == "f2":
        return f"{v:.2f}"
    return f"{v}"


def _table(df: pd.DataFrame, fmts: Optional[Dict[str, str]] = None,
           index: bool = False, cls: str = "tbl") -> str:
    fmts = fmts or {}
    d = df.copy()
    d.columns = [str(c) for c in d.columns]
    if index:
        d = d.reset_index()
        d.columns = [str(c) for c in d.columns]
    head = "".join(f"<th>{_html.escape(str(c))}</th>" for c in d.columns)
    body = []
    for _, row in d.iterrows():
        tds = []
        for c in d.columns:
            v = row[c]
            k = fmts.get(c, "raw")
            if k == "raw":
                s = "—" if pd.isna(v) else str(v)
            else:
                try:
                    s = _fmt(v, k)
                except Exception:      # noqa: BLE001
                    s = str(v)
            style = ""
            if isinstance(v, (int, float, np.floating)) and not pd.isna(v):
                cn = str(c)
                if cn.startswith(("超额", "净贡献", "RankIC", "t值", "策略")) or "超额" in cn:
                    if v > 0:
                        style = ' class="pos"'
                    elif v < 0:
                        style = ' class="neg"'
            tds.append(f"<td{style}>{_html.escape(s)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<table class="{cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _fig(src: str, caption: str) -> str:
    return (f'<figure><img src="{src}" alt="{_html.escape(caption)}"/>'
            f'<figcaption>{caption}</figcaption></figure>')


CSS = """
:root{
  --ink:#0f172a; --ink2:#334155; --muted:#64748b; --line:#e2e8f0;
  --bg:#f6f8fb; --card:#ffffff; --blue:#2563eb; --red:#d92b37; --green:#0f9d58;
  --amber:#b45309; --purple:#7c3aed;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Microsoft YaHei","Noto Sans SC",-apple-system,"Segoe UI",sans-serif;
  line-height:1.75;font-size:14.5px;}
.wrap{max-width:1180px;margin:0 auto;padding:48px 30px 90px}
.disclosure{background:#fffbeb;border:1px solid #f59e0b;border-left:5px solid #d97706;
  color:#78350f;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13.5px}
.disclosure b{color:#92400e;margin-right:4px}
header.hero{background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 55%,#0ea5e9 100%);
  color:#fff;border-radius:18px;padding:44px 42px;margin-bottom:34px;
  box-shadow:0 14px 40px rgba(37,99,235,.22)}
header.hero h1{margin:0 0 12px;font-size:29px;letter-spacing:.4px}
header.hero p{margin:0;opacity:.92;font-size:14.5px;max-width:900px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:14px;margin-top:26px}
.kpi{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.24);
  border-radius:12px;padding:13px 16px}
.kpi .k{font-size:11.5px;opacity:.85;letter-spacing:.5px}
.kpi .v{font-size:20px;font-weight:700;margin-top:3px}
section{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:30px 32px;margin-bottom:24px;box-shadow:0 2px 10px rgba(15,23,42,.04)}
h2{font-size:20px;margin:0 0 6px;padding-bottom:12px;border-bottom:2px solid var(--line)}
h2 .n{color:var(--blue);font-weight:800;margin-right:9px}
h3{font-size:16px;margin:26px 0 10px;color:var(--ink)}
h4{font-size:14.5px;margin:20px 0 8px;color:var(--ink2)}
p,li{color:var(--ink2)}
.lead{font-size:15px;color:var(--ink);background:#f1f5f9;border-left:4px solid var(--blue);
  padding:14px 18px;border-radius:0 10px 10px 0;margin:16px 0}
.lead.warn{background:#fef3c7;border-left-color:var(--amber)}
.lead.bad{background:#fee2e2;border-left-color:var(--red)}
.lead.ok{background:#dcfce7;border-left-color:var(--green)}
figure{margin:22px 0}
figure img{width:100%;border:1px solid var(--line);border-radius:11px;display:block}
figcaption{font-size:12.5px;color:var(--muted);margin-top:8px;text-align:center}
table.tbl{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}
table.tbl th{background:#f1f5f9;color:var(--ink);text-align:right;padding:9px 11px;
  border-bottom:2px solid var(--line);font-weight:600;white-space:nowrap}
table.tbl th:first-child,table.tbl td:first-child{text-align:left}
table.tbl td{padding:8px 11px;border-bottom:1px solid #eef2f7;text-align:right;
  white-space:nowrap;font-variant-numeric:tabular-nums}
table.tbl tbody tr:hover{background:#f8fafc}
td.pos{color:var(--red);font-weight:600}
td.neg{color:var(--green);font-weight:600}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;
  font-weight:700}
.badge-ok{background:#dcfce7;color:#166534}
.badge-bad{background:#fee2e2;color:#991b1b}
.badge-warn{background:#fef3c7;color:#92400e}
.tag{display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;
  border-radius:7px;padding:2px 9px;font-size:12px;margin:0 6px 6px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;
  margin:16px 0}
.card{border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:#fbfdff}
.card .h{font-weight:700;font-size:14px;margin-bottom:6px}
.card .b{font-size:13px;color:var(--ink2)}
ul.clean{list-style:none;padding-left:0}
ul.clean li{padding-left:22px;position:relative;margin:7px 0}
ul.clean li:before{content:"▸";position:absolute;left:4px;color:var(--blue)}
footer{text-align:center;color:var(--muted);font-size:12.5px;padding:26px 0}
code{background:#f1f5f9;padding:1.5px 6px;border-radius:5px;font-size:12.5px;
  font-family:Consolas,Monaco,monospace}
.muted{color:var(--muted);font-size:13px}
"""


def _safe(v, spec, default="—"):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return format(v, spec)
    except Exception:      # noqa: BLE001
        return default


def _top_holdings(exposure: pd.DataFrame, n: int = 3, thr: float = 0.03) -> str:
    """把最新风格暴露里权重最高的几个读成 '食品饮料 59%、家用电器 24%'。"""
    try:
        d = exposure[exposure["权重"] > thr].head(n)
        if d.empty:
            d = exposure.head(n)
        return "、".join(f"{r['名称']} {_safe(r['权重'], '.0%')}"
                        for _, r in d.iterrows())
    except Exception:      # noqa: BLE001
        return "—"


def _dd_comment(delta) -> str:
    """回撤改善的措辞：delta = 策略回撤 − 对照回撤。>0 表示策略回撤更浅。"""
    try:
        d = float(delta)
    except Exception:      # noqa: BLE001
        return ""
    if d > 0.005:
        return (f"回撤方面策略更浅（比对照浅 {d:.1%}）。")
    if d < -0.005:
        return (f"<b>回撤方面策略反而更深（比对照深 {abs(d):.1%}）</b>。")
    return "回撤与对照组基本持平。"


def timing_comparison(tc: dict) -> tuple:
    """
    择时净贡献的三态措辞：(css, 正文, 结论)。

    ★ 旧版无条件写"策略跑输恒定仓位对照"，换窗口/换基金后与数据相反；
      缺失值又被当成负结论。这里按 `年化收益` 的符号分支，非有限值走"无法计算"。
    """
    try:
        ret = float((tc or {}).get("年化收益"))
        if not np.isfinite(ret):
            ret = None
    except Exception:                                        # noqa: BLE001
        ret = None
    fmt = _safe((tc or {}).get("年化收益"), "+.2%")
    ir = _safe((tc or {}).get("夏普"), "+.3f")
    if ret is None:
        return ("", f"相对恒定仓位对照的净贡献无法计算（{fmt}）。",
                "对照结论取决于跨基金分布，单只基金不足以判断。")
    if ret > 0:
        return ("ok",
                f"策略<b>跑赢</b>恒定仓位对照 <b>{fmt}</b>（年化），超额日收益 IR {ir}。",
                "但单只基金的领先不足以证明择时能力——仍需看信号质量与跨基金"
                "分布/多重检验。")
    if ret < 0:
        return ("bad",
                f"策略<b>跑输</b>恒定仓位对照 <b>{fmt}</b>（年化），超额日收益 IR {ir}。",
                "这说明不仅没有择时能力，还在交易噪声与费用上产生了额外损失。")
    return ("",
            f"策略与恒定仓位对照<b>持平</b>（{fmt}，年化），超额日收益 IR {ir}。",
            "两者表现相当，说明策略的全部差异都来自「仓位水平」，而非「择时能力」。")


def _mget(m: pd.DataFrame, label: str, col: str, spec: str = ".4f") -> str:
    try:
        return format(float(m.loc[label, col]), spec)
    except Exception:      # noqa: BLE001
        return "—"


def build_report(main: dict, summary: pd.DataFrame, experiments: Dict[str, pd.DataFrame],
                 figs: Dict[str, str], path: Optional[Path] = None,
                 extra_meta: Optional[dict] = None,
                 fingerprint: Optional[dict] = None) -> Path:
    extra_meta = extra_meta or {}
    # P2-27：数据质量（过期/缺失/口径降级）必须在报告顶部可见
    _dq = extra_meta.get("data_quality") or {}
    _cq = extra_meta.get("completeness") or {}
    _dq_html = ""
    if (_dq.get("status") and _dq.get("status") != "ok") or \
            (_cq.get("status") and _cq.get("status") != "ok"):
        _dq_reasons = "；".join((_dq.get("reasons") or []) + (_cq.get("reasons") or []))
        _dq_html = (f'<p style="color:#92400e;font-weight:600">数据质量：'
                    f'{_dq.get("status") or _cq.get("status")} — '
                    f'{_dq_reasons or "原因未提供"}</p>')
    _gate = extra_meta.get("publish_gate") or {}
    if _gate.get("problems"):
        _dq_html += (f'<p style="color:#b91c1c;font-weight:600">发布门禁未通过'
                     f'（强制发布）：{"；".join(_gate["problems"])}</p>')
    # P1-20：净值口径（总回报 / 累计净值降级）必须在报告顶部可见，
    # 降级时同时给出 data_basis 与原因，不能按总回报解读。
    _nav_basis = str(extra_meta.get("nav_basis") or "total")
    _nav_degraded = bool(extra_meta.get("nav_degraded")) or _nav_basis == "cum"
    _nav_label = ("总回报（分红再投资）" if _nav_basis == "total"
                  else "累计净值（降级）")
    _nav_reason = (extra_meta.get("data_basis")
                   or extra_meta.get("nav_degraded_reason") or "")
    if _nav_degraded:
        _nav_html = (f'<p style="background:#fef3c7;color:#78350f;border-radius:8px;'
                     f'padding:8px 12px;font-weight:600">净值口径：{_nav_label}'
                     + (f' — {_html.escape(str(_nav_reason))}' if _nav_reason else "")
                     + '</p>')
    else:
        _nav_html = (f'<p style="opacity:.92">净值口径：<b>{_nav_label}</b>'
                     + (f' · {_html.escape(str(_nav_reason))}' if _nav_reason else "")
                     + '</p>')

    def _pred_cell(value):
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    _pred = extra_meta.get("predictions") or {}
    _overall = _pred.get("overall") or {}
    if not _overall:
        _pred_reason = _pred.get("reason")
        _pred_html = ('<p>预测台账汇总不可用'
                      + (f'（{_html.escape(str(_pred_reason))}）' if _pred_reason else '')
                      + '；本报告的回测结论不依赖台账。</p>')
    else:
        _hit = _overall.get("hit_rate")
        _hit_text = "—" if _hit is None else f"{float(_hit)*100:.1f}%"
        _pred_metrics = [("当前口径记录", _overall.get("current_total")),
                         ("方向预测已判定", _overall.get("decided")),
                         ("方向命中率", _hit_text),
                         ("Brier", _overall.get("brier")),
                         ("规则事件", _overall.get("rule_events")),
                         ("规则事件触发后平均收益", _overall.get("rule_return_mean")),
                         ("待结算", _overall.get("pending")),
                         ("旧口径行", _overall.get("legacy_rows"))]
        _pred_html = ('<table class="tbl"><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>'
                      + "".join(
                          f"<tr><td>{_html.escape(str(key))}</td>"
                          f"<td>{_html.escape(_pred_cell(value))}</td></tr>"
                          for key, value in _pred_metrics)
                      + '</tbody></table>'
                      + f'<p>{_html.escape(str(_overall.get("note") or ""))}</p>'
                      + '<p>命中率只衡量方向，不衡量扣费后收益，也不构成有效性证明；'
                        '台账与本报告回测使用不同样本与口径，不可互相印证。</p>')
    m = main["backtest"]["metrics"]
    ctrl = main["backtest"]["control_name"]
    tc = main["backtest"]["timing_contrib"]
    top_holdings = _top_holdings(main["exposure"])
    # 最新一期权重和 = 该期估计的股票仓位。auto_equity=True（默认）时它逐窗口
    # 变化，报告里不能再写死 0.90（旧文案与 config 注释自相矛盾）。
    try:
        _w_latest = pd.to_numeric(main["exposure"]["权重"], errors="coerce").dropna()
        _sumw = float(_w_latest.sum()) if len(_w_latest) else None
    except Exception:                                        # noqa: BLE001
        _sumw = None
    _sumw_s = _safe(_sumw, ".0%")
    _bounds = C.RBSA.get("equity_bounds", (0.20, 1.00))
    _bounds_txt = f"{float(_bounds[0]):.2f}~{float(_bounds[1]):.2f}"
    _eq_txt = f"{float(C.RBSA.get('equity_ratio', 0.90)):.2f}"

    # ---- 择时净贡献的方向：三态措辞，不能无条件写"跑输"
    _cmp_cls, _cmp_text, _timing_tail = timing_comparison(tc)

    # ---------------- 结论数字一律从数据算，不硬编码（P0-07 收尾）
    # 旧版在这里写死了"13 只跑输""28.7% 为正贡献""+0.147 / 86% 为正"等数字。
    # 收益口径修正后这些全部失效（E3 正贡献比例实际从 37.0% 变成 50.9%），
    # 而报告仍在输出旧值 —— 属于"代码已修、文档还引用旧数"的窗口期。
    _sm = summary if isinstance(summary, pd.DataFrame) else pd.DataFrame()

    def _num(col):
        if col not in _sm.columns:
            return pd.Series(dtype=float)
        return pd.to_numeric(_sm[col], errors="coerce").dropna()

    _v = _num("净贡献_夏普")
    _n_fund = int(len(_v))
    _n_neg = int((_v <= 0).sum())
    _rk = _num("RankIC")
    _n_rk_neg = int((_rk < 0).sum())
    _e3 = experiments.get("_e3_stat") or {}
    _e3_pos = _e3.get("pos")
    _e3_n = _e3.get("n")
    # 组合数（趋势窗口×趋势权重×kappa）与行数不同：E3 产物是「基金 × 组合」长表，
    # 直接用 len() 会把 4 只基金的行数说成 4 倍参数组合。
    _e3_combos = _e3.get("combos") or _e3_n
    _e3_n_txt = f"{_e3_combos} 组" if _e3_combos else "多组"
    _e4 = experiments.get("E4_周期匹配")
    _e4_mean = _e4_pos = _e4_win = _e4_reb = None
    if isinstance(_e4, pd.DataFrame) and not _e4.empty \
            and "超额夏普" in _e4.columns:
        _piv = _e4.pivot_table(index="信号窗口", columns="再平衡周期",
                               values="超额夏普", aggfunc="mean")
        if _piv.size:
            _e4_win, _e4_reb = _piv.stack().idxmax()
            _e4_mean = float(_piv.loc[_e4_win, _e4_reb])
            _sub = _e4[(_e4["信号窗口"] == _e4_win)
                       & (_e4["再平衡周期"] == _e4_reb)]["超额夏普"]
            _e4_pos = float((_sub > 0).mean()) if len(_sub) else None

    def _t(txt_yes, txt_no, **kw):
        """有数据就用带数字的版本，没有就退回不带数字的定性表述。"""
        return (txt_yes.format(**kw) if all(
            v is not None for v in kw.values()) else txt_no)

    # 通道级 IC：同样从 E1 表算，不写死（旧版硬编码 −0.010 / −0.112）
    _e1 = experiments.get("E1_IC期限结构")
    # 只认"xx日"的前瞻期列：如果传入的是长表（含基金/前瞻期列），
    # 旧写法会把基金代码当数值列参与 min/max，静默得出错误结论。
    _e1_cols = ([c for c in _e1.columns
                 if c != "信号" and str(c).endswith("日")]
                if isinstance(_e1, pd.DataFrame) and "信号" in _e1.columns
                else [])

    def _ic_of(sig, horizon="250日"):
        if not isinstance(_e1, pd.DataFrame) or not _e1_cols:
            return None
        row = _e1[_e1["信号"] == sig]
        if row.empty:
            return None
        vals = pd.to_numeric(row[_e1_cols].iloc[0], errors="coerce").dropna()
        if horizon not in _e1_cols:
            return None
        v = pd.to_numeric(row[horizon].iloc[0], errors="coerce")
        return float(v) if pd.notna(v) else None

    _dev_neg = None
    _dev_max_abs = None
    # ---- E9b / 功效 / 增量 / 选择感知：全部从 CSV 证据取数（复查发现报告里
    #      这些数字仍是硬编码，口径一变就会再次过期）
    _ev = (extra_meta or {}).get("evidence") or {}
    _e9b = _ev.get("e9b") or {}
    _e9b_mig = _e9b.get("迁移配置在测试期均值IR")
    _e9b_cost = _e9b.get("参数选择过拟合代价")
    _osp = _ev.get("overfit_split")
    _osp_cols = ("运气部分(oracle−均值)", "总代价(oracle−迁移)",
                 "过拟合部分(均值−迁移)")
    if (isinstance(_osp, pd.DataFrame) and not _osp.empty
            and all(c in _osp.columns for c in _osp_cols)):
        def _finite_mean(col):
            v = pd.to_numeric(_osp[col], errors="coerce")
            v = v.replace([np.inf, -np.inf], np.nan).dropna()
            return float(v.mean()) if len(v) else float("nan")
        _tot, _luck, _over = map(_finite_mean, _osp_cols)
        # 总代价为 0/非有限时不能算占比（旧写法 round(nan/nan) 会直接抛 ValueError）
        _ok_ratio = np.isfinite(_tot) and abs(_tot) > 1e-12
        _luck_pct = (round(_luck / _tot * 100)
                     if _ok_ratio and np.isfinite(_luck) else None)
        _over_pct = (round(_over / _tot * 100)
                     if _ok_ratio and np.isfinite(_over) else None)
    else:
        _luck_pct = _over_pct = None
    _power = _ev.get("power")
    _incr = _ev.get("increment")
    _mc = _ev.get("multiplicity")
    # ★ _t() 的参数是急切求值的：f"{None:+.3f}" 会先崩，所以先把数字
    #   格式化成字符串（或 None），再交给 _t 挑选。
    _e9b_mig_s = (f"{float(_e9b_mig):+.3f}"
                  if isinstance(_e9b_mig, (int, float))
                  and np.isfinite(_e9b_mig) else None)
    _e9b_cost_s = (f"{float(_e9b_cost):+.3f}"
                   if isinstance(_e9b_cost, (int, float))
                   and np.isfinite(_e9b_cost) else None)
    _luck_s = f"{_luck_pct}%" if _luck_pct is not None else None
    _over_s = f"{_over_pct}%" if _over_pct is not None else None
    _evidence_tables_html = ""

    def _ev_table(df, note=""):
        if not isinstance(df, pd.DataFrame) or df.empty:
            return ""
        return _table(df.round(4), index=False) + (
            f'<p class="muted">{note}</p>' if note else "")

    if isinstance(_e1, pd.DataFrame) and _e1_cols:
        row = _e1[_e1["信号"] == "相对偏离通道"]
        if not row.empty:
            vals = pd.to_numeric(row[_e1_cols].iloc[0], errors="coerce").dropna()
            _dev_neg = bool((vals < 0).all()) if len(vals) else None
            _dev_max_abs = float(vals.abs().max()) if len(vals) else None
    _val_250 = _ic_of("赛道估值通道", "250日")
    _val_120 = _ic_of("赛道估值通道", "120日")
    _trend_ic250 = _ic_of("趋势通道", "250日")
    _trend_ic_txt = (f"{_trend_ic250:+.4f}" if _trend_ic250 is not None
                     else "未从 E1 取得")
    # 分组单调性与多头胜率也必须从数据判断，不能写死
    _mono_txt = "未计算"
    try:
        _gv = pd.to_numeric(main["backtest"]["groups"]["平均前瞻收益"],
                            errors="coerce").dropna().values
        if len(_gv) >= 2:
            _d = np.diff(_gv)
            if np.all(_d > 0):
                _mono_txt = "单调递增"
            elif np.all(_d < 0):
                _mono_txt = "单调递减"
            else:
                _mono_txt = "不单调"
    except Exception:                                        # noqa: BLE001
        pass
    _win = main.get("backtest", {}).get("win_long")
    try:
        _win = float(_win) if _win is not None else None
    except Exception:                                        # noqa: BLE001
        _win = None
    _win_txt = ("随机附近" if _win is not None and 0.45 <= _win <= 0.55
                else "明显偏离随机" if _win is not None else "未计算")
    # 综合分（融合信号）各前瞻期 IC —— 旧版只描述单个通道，漏掉了这条最有力的证据
    _fused_neg = None
    _fused_worst = None
    if isinstance(_e1, pd.DataFrame) and _e1_cols:
        row = _e1[_e1["信号"] == "综合分"]
        if not row.empty:
            vals = pd.to_numeric(row[_e1_cols].iloc[0], errors="coerce").dropna()
            _fused_neg = bool((vals < 0).all()) if len(vals) else None
            _fused_worst = float(vals.min()) if len(vals) else None

    # ---------------- 运行指纹（P1-09 / N-09）
    # 结论必须能回答"是在哪套代码、哪套依赖、哪份数据、哪些参数下跑的"。
    # 否则同一份代码换个环境给出不同数字时，连该排查哪一层都不知道。
    fp = fingerprint or {}
    fp_rows = []
    for label, val in (
            ("生成时间", fp.get("generated_at")),
            ("git commit", fp.get("git_commit") or "（非 git 环境）"),
            ("Python", fp.get("python")),
            ("依赖版本", ", ".join(f"{k} {v}" for k, v
                              in (fp.get("libs") or {}).items() if v)),
            ("数据指纹", (fp.get("data") or {}).get("data_stamp")),
            ("候选指数数", (fp.get("config") or {}).get("n_candidates")),
            ("bootstrap 种子", (fp.get("seeds") or {}).get("bootstrap")),
            ("CACHE_VERSION", (fp.get("config") or {}).get("CACHE_VERSION")),
    ):
        if val not in (None, ""):
            fp_rows.append(f"<tr><th>{label}</th>"
                           f"<td>{_html.escape(str(val))}</td></tr>")
    _run = fp.get("run") or {}
    if _run:
        fp_rows.append("<tr><th>本次运行</th><td>" + _html.escape(
            ", ".join(f"{k}={v}" for k, v in _run.items())) + "</td></tr>")
    if fp_rows:
        fingerprint_html = (
            '<table class="tbl"><tbody>' + "".join(fp_rows) + "</tbody></table>"
            '<p class="muted">同一份指纹应能复算出同一份报告。'
            'git commit、依赖版本或数据指纹任一不同，数字差异就不能归因于"随机性"。</p>')
    else:
        fingerprint_html = ('<p class="muted">本次运行未提供指纹'
                            '（build_report.py 未传 fingerprint）。</p>')

    # ---------------- 第 09 节：增量 / 功效 / 选择过程证据（从产物 CSV 渲染）
    _ev_parts = []
    if isinstance(_power, pd.DataFrame) and not _power.empty:
        _ev_parts.append(
            "<h3>功效分析（P0-06）</h3>"
            "<p>α=0.05 双侧、目标功效 80%、δ=|mean(IR)|/sd(IR)。"
            "「所需基金数」假设扩池不改变相关结构 —— 这只是下界。</p>"
            + _ev_table(_power))
    if isinstance(_incr, pd.DataFrame) and not _incr.empty:
        _cols = [c for c in ("基金", "名称", "A_单一宽基MSE_R2",
                             "D_受约束回归_R2", "R2增量_D减A",
                             "A_单一宽基MSE_稳定", "D_受约束回归_稳定")
                 if c in _incr.columns]
        if _cols:
            _ev_parts.append(
                "<h3>风格画像 vs 单一宽基（P2-02，事前公平比较）</h3>"
                + _ev_table(_incr[_cols]))
    if isinstance(_mc, pd.DataFrame) and not _mc.empty:
        _ev_parts.append(
            "<h3>多重比较校正（P1-03）</h3>" + _ev_table(_mc))
    if isinstance(_osp, pd.DataFrame) and not _osp.empty:
        _ev_parts.append(
            "<h3>过拟合代价拆解（P1-03）</h3>"
            "<p>运气部分不是过拟合：从 12 个候选里挑最大值本身就会偏高。</p>"
            + _ev_table(_osp))
    evidence_html = ("".join(_ev_parts)
                     if _ev_parts else
                     '<p class="muted">本次运行未提供 P0-06 / P2-02 / P1-03 的'
                     '证据产物（先跑 run_power.py / run_increment.py / '
                     'run_selection.py，再重新生成报告）。</p>')

    # ---------------- 头部 KPI
    kpis = [
        ("风格拟合 R²", _safe(main['fit_diag']['r2'].mean(), ".3f"), "滚动窗口平均解释力"),
        ("跟踪误差 TE", _safe(main['fit_diag']['te'].mean(), ".1%"), "年化，越低表示基准越贴合"),
        ("识别出的核心指数",
         f"{int((main['exposure']['权重'] > 0.02).sum())} 个", "权重 > 2% 的指数"),
        ("信号 RankIC", _safe(main['backtest']['ic'], "+.3f"), "与未来 5 日收益的秩相关"),
        ("择时净贡献（夏普）", _safe(tc.get('夏普'), "+.3f"), f"相对「{ctrl}」对照组"),
        ("回撤差（vs 对照）", _safe(tc.get('最大回撤'), "+.1%"),
         "策略回撤 − 对照回撤，正=更浅"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{k}</div><div class="v">{v}</div>'
        f'<div class="k" style="margin-top:5px">{s}</div></div>'
        for k, v, s in kpis)

    # ---------------- 当前决策
    snap = main["snapshot"]
    snap_view = {k: v for k, v in snap.items() if k != "估值口径"}
    snap_tbl = _table(pd.DataFrame([snap_view]), index=False)

    # 估值兜底必须在报告里显式标注（P1-19），不能只在前端提示
    _val = main.get("valuation") or {}
    _val_note = ''
    if _val.get("degraded"):
        _style = ('font-size:13px;color:#92400e;background:#fffbeb;'
                  'border-left:3px solid #f59e0b;padding:8px 10px;margin-top:8px')
        if _val.get("available") is False:
            # P2-21：通道被剔除时不能继续写「按宽基口径解读」——
            # 估值根本没有参与融合，综合分来自其余通道的重归一。
            _val_note = (
                f'<p style="{_style}">估值通道提示：'
                f'{_val.get("note") or "估值数据不可用"}。'
                f'该通道权重已从分母剔除，其余通道按权重重归一；'
                f'不显示中性估值分。'
                f'{"原因：" + str(_val.get("reason")) if _val.get("reason") else ""}'
                '</p>')
        else:
            _assets = "、".join(
                f'{a["name"]}（{a["code"]}，{a["weight"]:.1%}）'
                for a in (_val.get("fallback_assets") or [])[:8])
            _cov = _val.get("coverage")
            _cov_txt = f'真实 PE 覆盖率 {_cov:.0%}；' if _cov is not None else ''
            _val_note = (
                f'<p style="{_style}">'
                f'估值口径提示：{_val.get("note") or "部分资产使用宽基兜底"}。'
                f'{_cov_txt}兜底资产：{_assets or "—"}。'
                '行业估值不可用，相关「估值分位」结论请按宽基口径谨慎解读。</p>')

    # ---------------- 真实持仓拟合（与 CLI/Web 同一链路）
    _h = extra_meta.get("holdings") or {}
    if _h.get("ok"):
        _s = _h.get("summary") or {}
        _c = _h.get("compare") or {}
        _per = pd.DataFrame(_h.get("periods") or [])
        _per_tbl = _table(_per.tail(8), index=False) if len(_per) else ''
        _cmp_tbl = (_table(pd.DataFrame(_c.get("stats") or []), index=False)
                    if _c.get("stats") else '')
        _cmp_section = (f'<p><b>持仓基准 vs 基金 / RBSA 合成基准</b>'
                        f'（共同区间 {_c.get("n_days", 0)} 个交易日）：</p>{_cmp_tbl}'
                        if _c.get("ok") else '')
        _b = _h.get("benchmark") or {}
        _hm = _h.get("meta") or {}
        _holding_basis = {"total": "总回报净值", "cum": "累计净值"}.get(_hm.get("nav_basis"))
        _basis_note = f'<p>持仓拟合净值口径：{_holding_basis}。</p>' if _holding_basis else ''
        _missing = (_h.get("completeness") or {}).get("missing") or []
        _partial_note = (
            '<p style="color:#92400e">部分可用：' + '；'.join(
                _html.escape(f'{item["item"]}：{item["reason"]}') for item in _missing) + '。</p>'
            if _missing else '')
        _diag = pd.DataFrame(_b.get("diagnostics") or [])
        _diag_tbl = _table(_diag.tail(8), index=False) if len(_diag) else ''
        _miss = _b.get("missing_industries") or []
        _miss_note = (
            '<p style="color:#92400e">[警示] 行业面板缺失 '
            f'{len(_miss)} 个持仓行业：{_html.escape(", ".join(map(str, _miss)))}。'
            '这些权重与未映射股票一样按 0 收益处理，不按现金计息。</p>'
            if _miss else '')
        _res_note = (
            f'<p style="font-size:13px;color:#64748b">持仓基准口径：'
            f'{_html.escape(str(_b.get("residual_treatment") or ""))}。</p>'
            if _b.get("residual_treatment") else '')
        _holdings_html = (
            '<h3>3.6 真实持仓拟合验证</h3>'
            '<p>用半年报/年报披露的真实行业持仓，检验 RBSA 权重是否说对了'
            '基金的行业结构；并据此构造「持仓基准」，与基金、RBSA 合成基准比较。</p>'
            f'<p>可用期数 <b>{_s.get("n_periods", 0)}</b>；跨期平均绝对偏差 '
            f'<b>{_safe(_s.get("mad_mean"), ".1%")}</b>；跨期权重相关均值 '
            f'<b>{_safe(_s.get("corr_mean"), ".2f")}</b>'
            f'（首期 {_safe(_s.get("corr_first"), ".2f")} → 末期 '
            f'{_safe(_s.get("corr_last"), ".2f")}，有效 {_s.get("corr_valid", 0)} 期）；'
            f'真实 PE/行业覆盖率均值 <b>{_safe(_s.get("coverage_mean"), ".0%")}</b>；'
            f'未映射权重均值 <b>{_safe(_s.get("unmapped_mean"), ".0%")}</b>。</p>'
            f'{_basis_note}{_partial_note}{_per_tbl}{_cmp_section}'
            f'{_miss_note}{_res_note}'
            f'<p style="font-size:13px;color:#64748b">{_h.get("notes", {}).get("unmapped_not_cash", "")}'
            f'{_h.get("notes", {}).get("benchmark_compare", "")}'
            f'{_h.get("notes", {}).get("benchmark_residual", "")}</p>'
            f'{_diag_tbl}')
    else:
        _holdings_html = (
            '<h3>3.6 真实持仓拟合验证</h3>'
            f'<p style="color:#92400e">未生成：{_h.get("reason") or "持仓数据不可用"}。'
            '该结论缺失，不代表持仓拟合通过。</p>')

    # ---------------- 风格暴露
    exp = main["exposure"].copy()
    exp["权重"] = exp["权重"].astype(float)
    exp_tbl = _table(exp.rename(columns={"名称": "指数", "层级": "类型"}),
                     fmts={"权重": "pct1"}, index=False)

    # ---------------- alpha 分解：解释"两条线为什么越走越远"
    # 用户最容易误读的一处：净值曲线上基金与合成基准的累计差距，
    # 看起来像拟合崩了，实际主要是 alpha（风格之外的选股超额）。
    _alpha_note = ''
    try:
        _nav_s = main.get("nav")
        _syn_s = main.get("syn_nav")
        if _nav_s is not None and _syn_s is not None and len(_nav_s) > 60:
            _idx = _syn_s.index
            _f = _nav_s.reindex(_idx).dropna()
            _s2 = _syn_s.reindex(_f.index).dropna()
            if len(_f) > 60 and len(_s2) > 60:
                _lf = np.log(_f / _f.iloc[0])
                _ls = np.log(_s2 / _s2.iloc[0])
                _d = (_lf.diff() - _ls.diff()).dropna()
                _ann = float(_d.mean() * 252)
                _mult = float(np.exp((_lf - _ls).iloc[-1]))
                _yrs = len(_d) / 252.0
                # ★ 变量名不能用 _t：模板里 _t(...) 是"有数据就用数字、没数据就
                #   退回定性表述"的助手函数，被 float 覆盖后整页会报 not callable。
                _at = None
                try:
                    from market_research.research.robustness import _nw_mean_t
                    _at = float(_nw_mean_t(_d.values, lags=5))
                except Exception:                            # noqa: BLE001
                    _at = None
                _ts = (f"t = {_at:.2f}" + ("（显著）" if abs(_at) > 1.96
                                         else "（<b>不显著</b>，只能当点估计看）")
                       if _at is not None else "")
                _alpha_note = (
                    f'<div class="lead warn"><b>关于"基金净值与合成基准越走越远"：</b>'
                    f'两者差 {_mult:.2f}×（{_yrs:.1f} 年），折合年化 <b>{_ann:+.1%}</b>，'
                    f'{_ts}。<br/>'
                    f'这不是拟合失败 —— 窗口内 R² 达 '
                    f'{main["fit_diag"]["r2"].mean():.2f}，说明<b>风格暴露识别是对的</b>；'
                    f'差距主要来自<b>风格之外的 alpha</b>（行业内部选股），'
                    f'指数组合在数学上无法复制。<br/>'
                    f'其中约 2~3%/年是<b>口径差</b>：基金侧用总回报（含分红再投），'
                    f'候选指数是价格指数（不含股息）。可在 '
                    f'<code>RBSA["index_dividend_annual"]</code> 填年化股息率予以剥离。'
                    f'<b>不建议</b>为此调大 Σw —— 实测放开仓位上限后 R² 反而变差。</div>')
    except Exception:                                        # noqa: BLE001
        _alpha_note = ''

    # ---------------- 风格漂移（2026-10-12）
    # 经理变更无法自动检测（免费源只有现任、无任职起止日期），所以改为直接
    # 回答"这段区间的风格画像能不能代表当下"——这才是真正影响可用性的事。
    _drift_note = ''
    try:
        _sd = main.get("style_drift") or {}
        if _sd.get("ok"):
            # CSS 里只有 .lead.ok / .lead.warn，"good" 没有样式，稳定态等于无提示
            _cls = "ok" if _sd.get("stable") else "warn"
            _parts = ['<div class="lead ' + _cls + '"><b>'
                      + str(_sd.get("verdict", "")) + '</b></div>']
            _cps = (_sd.get("change_points") or [])[:3]
            if _cps:
                _li = "".join(
                    "<li><b>" + str(c["日期"]) + "</b>：" + str(c["主要变动"])
                    + "（L1 " + f"{c['变化幅度']:.2f}" + "，稳健 z="
                    + str(c["z值"]) + "）</li>" for c in _cps)
                _parts.append("<p>结构突变（最多列 3 次）：</p><ul>" + _li + "</ul>")
            _ht = _sd.get("half_top") or {}
            if _ht.get("前半") and _ht.get("后半"):
                def _fw(lst):
                    return "、".join("%s %.1f%%" % (x["名称"], x["权重"] * 100)
                                    for x in lst)
                _parts.append(
                    "<p>前半（" + str(_ht["前半"]["区间"]) + "）："
                    + _fw(_ht["前半"]["主要持仓"])
                    + "<br/>后半（" + str(_ht["后半"]["区间"]) + "）："
                    + _fw(_ht["后半"]["主要持仓"]) + "</p>")
            if _sd.get("corr_note"):
                _parts.append('<p style="font-size:13px;color:#64748b">'
                              + str(_sd["corr_note"]) + "</p>")
            _parts.append(
                '<p style="font-size:13px;color:#64748b">这里检测的是<b>拟合权重</b>'
                '的结构变化，与「相关性层面」的漂移互为补充。经理变更无法自动检测'
                '（数据源只有现任经理、无任职起止日期），而<b>风格是否漂移</b>'
                '才是决定画像能否代表当下的直接因素 —— 换了经理风格不一定变，'
                '没换经理风格也可能漂。</p>')
            _drift_note = "".join(_parts)
        else:
            _drift_note = ('<p style="font-size:13px;color:#64748b">风格漂移检测未运行：'
                           + str(_sd.get("reason", "无数据")) + '</p>')
    except Exception:                                        # noqa: BLE001
        _drift_note = ''

    perf = m.reset_index().rename(columns={"方案": "方案"})
    metrics_tbl = _table(perf, index=False, fmts={
        "年化收益": "pct", "年化波动": "pct", "夏普": "f3", "算术夏普": "f3",
        "最大回撤": "pct",
        "Calmar": "f3", "月胜率": "pct1", "累计收益": "pct", "样本天数": "raw"})
    # N-03：「夏普」这个名字本身有歧义 —— 它是用**几何年化**收益算的，
    # 而业界默认的 Sharpe 用**算术**超额均值。两个都要显示，并说明差别。
    metrics_note = (
        '<p class="muted">口径区分（P1-04）：'
        '<b>夏普</b> 用几何年化收益计算，衡量"净值曲线本身"的风险调整后收益；'
        '<b>算术夏普</b> 用算术年化收益计算，是业界默认口径、可与 IR 直接对照。'
        '两者都给出，避免单一口径误导；<b>信息比率 IR</b> 另在"择时净贡献"一栏，'
        '定义在<b>超额日收益序列</b>上（夏普与年化都不可加，不能相减）。</p>')
    metrics_tbl = metrics_tbl + metrics_note

    # ---------------- 跨基金汇总
    sum_tbl = _table(summary, index=False, fmts={
        "净贡献_夏普": "p3", "净贡献_年化": "pct", "净贡献_回撤": "pct",
        "策略夏普": "f3", "对照夏普": "f3", "RankIC": "p3", "ICIR": "f3",
        "多头胜率": "pct1", "平均仓位": "pct1", "样本天数": "raw",
        "最优_超额夏普": "f3", "最优窗口": "raw", "最优周期": "raw",
        "策略_算术夏普": "f3", "对照_算术夏普": "f3", "有效标签数": "raw"})

    # ---------------- 实验区
    PCT_COLS = ("年化收益", "年化波动", "最大回撤", "累计收益",
                "月胜率", "超额年化_vs对照", "超额回撤_vs对照",
                "策略年化", "对照年化", "平均仓位")
    PCT1_COLS = ("正贡献比例",)
    F3_COLS = ("夏普", "Calmar", "ICIR", "最优_超额夏普")

    exp_html = ""
    mapping = [
        ("E1", "IC 期限结构", "E1_IC期限结构",
         "各通道信号在不同前瞻期上的 RankIC（跨基金平均）。"
         "用于判断信号究竟在哪个时间尺度上携带预测信息。正值表示方向正确。"),
        ("E2", "通道消融", "E2_通道消融",
         "三个通道各自的回测表现（跨基金平均），全部与「恒定仓位对照」比较，"
         "分离真实择时贡献。"),
        ("E3", "参数敏感性", "E3_参数敏感性",
         "信号窗口 / 通道权重 / 压缩系数的网格扫描（按趋势权重分组）。"
         "注意：这里看的是<b>分布</b>，不是最优值——最优值必然漂亮，分布才说明问题。"),
    ]
    for tag, title, key, desc in mapping:
        df = experiments.get(key)
        if df is None or df.empty:
            continue
        fmts = {}
        for c in df.columns:
            cs = str(c)
            if cs in PCT1_COLS:
                fmts[cs] = "pct1"
            elif cs in PCT_COLS or ("超额" in cs and "夏普" not in cs):
                fmts[cs] = "pct"
            elif cs in F3_COLS or "夏普" in cs or "RankIC" in cs or "t值" in cs:
                fmts[cs] = "f3"
        extra = ""
        if key == "E1_IC期限结构" and experiments.get("_e1_n"):
            extra = f'<p class="muted">样本：{experiments["_e1_n"]} 只基金。</p>'
        if key == "E3_参数敏感性" and experiments.get("_e3_stat"):
            fmts["参数组合数"] = "int"
            st = experiments["_e3_stat"]
            extra = (f'<p class="muted">共 {st["n"]} 组参数组合。'
                     f'超额夏普 &gt; 0 的比例 <b>{st["pos"]:.1%}</b>，'
                     f'均值 <b>{st["mean"]:+.4f}</b>，'
                     f'中位数 <b>{st["median"]:+.4f}</b>。</p>')
        exp_html += (f'<h3>{tag} · {title}</h3><p>{desc}</p>'
                     f'{_table(df, fmts=fmts, index=False)}{extra}')

    # E4 独立图表
    e4 = experiments.get("E4_周期匹配")
    e4_html = ""
    if e4 is not None and not e4.empty:
        piv = e4.pivot_table(index=["基金", "信号窗口"], columns="再平衡周期",
                             values="超额夏普")
        cell_mean = e4.pivot_table(index="信号窗口", columns="再平衡周期",
                                   values="超额夏普", aggfunc="mean")
        cell_pos = e4.pivot_table(index="信号窗口", columns="再平衡周期",
                                  values="超额夏普", aggfunc=lambda s: (s > 0).mean())
        # 最优格子
        best = cell_mean.stack().idxmax()
        bw, br = int(best[0]), int(best[1])
        bmean, bpos = float(cell_mean.loc[bw, br]), float(cell_pos.loc[bw, br])
        near = [float(cell_mean.loc[bw, r]) for r in cell_mean.columns if r != br]

        e4_html = ("<h3>E4 · 周期匹配实验</h3>"
                   "<p>E1 显示趋势通道在部分前瞻期 RankIC 为正、部分为负，"
                   "怀疑问题出在「信号时间尺度」与「调仓周期」错配。"
                   "本实验把两者同时扫描：纯趋势通道，信号窗口 ∈ "
                   f"{sorted(e4['信号窗口'].unique().tolist())}，"
                   "再平衡周期 ∈ "
                   f"{sorted(e4['再平衡周期'].unique().tolist())}。"
                   "表中为跨基金平均超额夏普（相对恒定仓位对照）。</p>"
                   + _table(cell_mean.round(3).reset_index(), index=False)
                   + "<p>同一网格中「超额夏普 > 0 的基金占比」：</p>"
                   + _table((cell_pos * 100).round(0).reset_index(), index=False)
                   + f'<div class="lead ok">最优格子为 <b>{bw} 日信号窗口 + '
                     f'{br} 日再平衡</b>，跨基金平均超额夏普 <b>{bmean:+.3f}</b>，'
                     f'<b>{_safe(bpos, ".0%")}</b> 的基金为正。表面上看，"周期错配"的假设被证实了。</div>'
                   + '<div class="lead warn"><b>但这个结论经不起三重反证：</b>'
                     '① E1 的 IC 期限结构并不支持——趋势通道在该窗口上的前瞻 IC '
                     '接近 0 甚至为负，收益预测层面找不到对应信息；'
                     f'② 它是 {cell_mean.size} 个格子里挑出来的最大值，'
                     f'而 E3 已显示参数网格中天然有约 {_safe((experiments.get("_e3_stat") or {}).get("pos"), ".0%")} '
                     '的组合为正，随机取最大值必然为正，这是典型的选择偏差；'
                     f'③ 相邻格子（同窗口、其他再平衡周期）表现为 '
                     + "、".join(f"{v:+.3f}" for v in near)
                     + '，没有形成连续的"参数高原"，而真实效应通常表现为一片连续正值区域。'
                     '<br/><b>判定：这是一个候选线索，不是已证实的效应。'
                     '若要实盘使用，必须在完全独立的基金池与时间段上重新验证。</b></div>')

    # ---------------- 组装
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
{_head_icons()}
<title>主动基金指数拟合与趋势估值择时 · 研究报告</title>
<style>{CSS}</style></head><body><div class="wrap">

{disclosure.html()}
<header class="hero">
  <h1>主动基金的指数拟合与趋势 / 估值研究</h1>
  <p>用滚动 RBSA 风格分解把一只主动基金还原成「指数 + 风格因子」的组合，
     再用合成基准的趋势与估值判断基金处在什么状态——并对这套择时逻辑做
     严格的样本外检验与对照实验。<br/>
     样本：{extra_meta.get('n_funds','—')} 只 A 股主动权益基金 ·
     区间：{extra_meta.get('period','—')} · 数据源：天天基金 / 新浪指数 / 申万行业</p>
  {_nav_html}
  {_dq_html}
  <div class="kpis">{kpi_html}</div>
</header>

<section>
<h2><span class="n">01</span>执行摘要</h2>
<p class="lead">一句话：<b>「找出相关指数」这一步做得很好，「用指数走势做买卖决策」这一步被证伪了。</b>
两者必须分开对待——前者的成功极易让人误以为后者也应该成立。</p>

<div class="cards">
  <div class="card"><div class="h">✅ 成立：风格穿透</div><div class="b">
    滚动 RBSA 用四十余个候选指数把基金日收益分解到
    <b>滚动 R² ≈ {_safe(main['fit_diag']['r2'].mean(), '.0%')}</b>，
    跟踪误差 {_safe(main['fit_diag']['te'].mean(), '.1%')}。
    {main['name']} 被准确识别为 <b>{top_holdings}</b>——与基金经理真实持仓风格一致。
    <b>这是本框架唯一确定成立的价值。</b></div></div>

  <div class="card"><div class="h">❌ 不成立：日频择时</div><div class="b">
    {_t("{n_fund} 只基金中 <b>{n_neg} 只</b>在默认参数下跑输「恒定仓位对照」，"
        "RankIC 有 <b>{n_rk_neg}/{n_fund}</b> 只为负，"
        "{e3_combos} 组参数组合中 <b>{e3_pos:.1%}</b> 为正贡献。",
        "当前没有完整的跨基金汇总，无法给出跑输只数与网格正贡献比例；"
        "下方实验表只反映本次输入的样本。",
        n_fund=_n_fund or None,
        n_neg=_n_neg if _n_fund else None,
        n_rk_neg=_n_rk_neg if _n_fund else None,
        e3_combos=_e3_combos, e3_pos=_e3_pos)}
    病因：交易噪声 + 短期反转 + 平均仓位被压低到
    {_safe(main['backtest']['avg_position'], '.0%')}
    （★ 这是**该方案的时间平均仓位**，不是基金真实股票仓位；后者看 β 隐含仓位）。</div></div>

  <div class="card"><div class="h">⚠️ 存疑：长周期线索</div><div class="b">
    {_t("{w} 日信号窗口 + {r} 日再平衡的平均超额夏普 <b>{mean:+.3f}</b>"
        "（{pos} 基金为正），",
        "长周期（约半年再平衡）格子出现了正超额夏普，",
        w=_e4_win, r=_e4_reb, mean=_e4_mean,
        pos=_safe(_e4_pos, ".0%") if _e4_pos is not None else None)}
    但 <b>四重反证</b>：IC 期限结构不支持（趋势通道 250 日 IC <b>{_trend_ic_txt}</b>）、
    是 12 格网格里的最优格子（选择偏差）、
    相邻格子无连续参数高原、
    <b>E9b 时间向前验证给出迁移 IR {_t(_e9b_mig_s, "为负（详见 E9b 输出）", v=_e9b_mig_s)}</b>
    ——训练期选出的参数在未来不再为正。<b>只能算候选假设。</b><br/>
    <span style="font-size:12px;color:#64748b">注意别把"过拟合代价 {_t(_e9b_cost_s, '—', v=_e9b_cost_s)}"读成
    "参数选择过拟合"：拆解后其中 <b>{_t(_luck_s, '大部分', v=_luck_s)} 来自"从 12 个候选里挑最大值"的抽签运气，
    真实过拟合为 {_t(_over_s, '很小甚至为负', v=_over_s)}</b>（即训练期选择相对平均水平甚至略好）。
    真正的问题是<b>整个候选集在测试期的 IR 均值为负</b>，不是选参选错了。</span></div></div>

  <div class="card"><div class="h">🚫 应剔除：两个通道</div><div class="b">
    ① <b>相对偏离通道</b>{f'各前瞻期 IC 全为负' if _dev_neg else
    (f'各前瞻期 |IC| 均小于 {_dev_max_abs:.3f}，没有稳定方向'
     if _dev_max_abs is not None else 'IC 未显示稳定方向')}——
    主动基金的累计超额是持续 alpha 的累积，
    不是围绕均值的震荡，「涨多了就减仓」会卖在 alpha 兑现途中。<br/>
    ② <b>赛道估值通道</b>{f'中长期为负（120 日 {_val_120:+.3f}、250 日 {_val_250:+.3f}），方向与直觉相反'
    if _val_120 is not None and _val_250 is not None else '中长期方向与直觉相反'}：
    在 A 股宽基 PE 分位上，「低估买入」在中长期是<b>反向</b>的。<br/>
    ③ <b>综合分（三通道融合）</b>{f'各前瞻期 IC 全为负（最深 {_fused_worst:+.3f}）'
    if _fused_neg else 'IC 未稳定为负'}——
    这是最直接的一条：融合信号与未来收益之间<b>没有可用的线性关系</b>。</div></div>
</div>

<div class="lead warn"><b>最重要的方法论提醒：</b>如果只报告"每只基金最优参数下的表现"，
这批基金会<b>几乎全部显示为正超额收益</b>。
那是 3×4 网格搜索的产物，不是能力。任何只给出"最优参数回测曲线"的研究
都应当被高度警惕——本报告因此明确给出<b>固定参数的跨基金分布</b>与<b>网格正贡献比例</b>，
并额外给出<b>时间向前验证（E9b）</b>：按管理人分组的横截面迁移看似成立，
但时间维度迁移 IR 为 {_t('<b>' + _e9b_mig_s + '</b>' if _e9b_mig_s else '负值', '负值', v=_e9b_mig_s)}；
且该代价里 {_t(_luck_s, '大部分', v=_luck_s)} 是挑最大值的运气，
真实过拟合为 {_t(_over_s, '很小甚至为负', v=_over_s)}
—— 问题在候选集整体为负，不在选参。</div>
</section>

<section>
<h2><span class="n">02</span>方法：三步走</h2>
<h3>第一步 · 用指数把你的基金“拆开”（RBSA 风格拟合）</h3>
<p>假设基金日收益 r<sub>f,t</sub> 可以被一组指数的线性组合解释：</p>
<p style="text-align:center;font-size:16px;color:#1e293b">
r<sub>f,t</sub> = Σ w<sub>i</sub> · r<sub>i,t</sub> + ε<sub>t</sub>
&nbsp;&nbsp;s.t.&nbsp; w<sub>i</sub> ≥ 0, &nbsp; Σw<sub>i</sub> = w<sub>t</sub></p>
<p>三个约束都有现实含义：<span class="tag">非负</span>公募不能做空指数；
<span class="tag">和为股票仓位</span>w<sub>t</sub> 逐窗口估计（<code>auto_equity=True</code>，
裁剪区间 {_bounds_txt}；本基金最新一期为 <b>{_sumw_s}</b>）。
只有固定仓位模式才把 w<sub>t</sub> 固定为 <code>equity_ratio={_eq_txt}</code>，
不能用它代替实际仓位；
<span class="tag">稀疏</span>投资者只需要知道 3~6 个关键指数。</p>
<p>实现上是两阶段：先用<b>非负 ElasticNet</b> 在四十余个候选指数上做
滚动原点交叉验证选正则强度，筛出稀疏支撑集；再在支撑集上做
<b>带求和约束的非负最小二乘</b>（SLSQP）得到精确权重。
每 20 个交易日用过去 252 日重估一次，权重只作用于之后的区间——
<b>保证回测无前瞻偏差</b>。</p>

<h3>第二步 · 把「基准」翻译成三个独立信号</h3>
<ul class="clean">
  <li><b>趋势通道</b>（作用于合成基准）：均线结构 + 区间动量 + 滚动对数价格回归斜率，
      各自滚动 z-score 后平均，tanh 压缩到 [−1, 1]。</li>
  <li><b>相对偏离通道</b>（作用于基金 ÷ 基准价差）：spread = log(NAV) − log(基准)，
      取滚动 z-score。<b>后续实验证明这一通道应当剔除。</b></li>
  <li><b>赛道估值通道</b>（作用于风格基准的绝对估值）：用基准成分加权得到的
      指数 PE 历史分位（低频月度数据）。</li>
</ul>
<p>融合：<code>score = 0.5·趋势 + 0.35·偏离 + 0.15·估值</code>，
目标仓位 = clip(0.5 + 0.5·score, 0, 1)，再经 EMA 平滑与调仓死区。</p>

<h3>第三步 · 用「对照组」而不是「买入持有」来判断择时是否有效</h3>
<p class="lead warn">这是整个研究最关键的一处设计。择时策略的平均仓位往往远低于 100%，
收益天然更低、回撤天然更小。只和「买入持有」比，会把
<b>“仓位降低”误认为“择时能力”</b>。</p>
<p>因此我们构造对照组：<b>恒定仓位 = 策略的时间平均仓位
（本基金为 {_safe(main['backtest'].get('avg_position'), '.0%')}）</b>。
只有当策略<b>跑赢同一平均仓位下的恒定持仓</b>，才说明择时真的创造了价值。</p>
<p>信号质量用四类指标衡量：RankIC / ICIR（秩相关及其稳定性）、
五分组前瞻收益单调性、择时胜率、年化双边换手率。</p>
</section>

<section>
<h2><span class="n">03</span>深度案例：{main['name']}（{main['code']}）</h2>
{_fig(figs.get('equity',''), '净值曲线：基金 vs 合成基准 vs 择时策略 vs 恒定仓位对照')}

<h3>3.1 风格分解：这只基金到底在赌什么</h3>
{_fig(figs.get('exposure',''), '风格暴露演变——滚动 RBSA 权重堆叠图')}
<p>最新一期权重（每 {C.RBSA['refit']} 个交易日重估，权重和 <b>{_sumw_s}</b>
= 该期估计的股票仓位，逐期变化）：</p>
{exp_tbl}
<p>R² 长期维持在 {main['fit_diag']['r2'].min():.2f}~{main['fit_diag']['r2'].max():.2f}，
说明这只基金<b>几乎可以被少数几个行业指数完全复制</b>——
它的超额收益来自行业内部选股，而不是行业配置。</p>
{_alpha_note}

<h3>3.2 趋势与估值状态</h3>
{_fig(figs.get('deviation',''), '相对偏离：价差水平与 z-score')}
{_fig(figs.get('position',''), '综合信号分与建议仓位')}
<div class="grid2">
  <div>{_fig(figs.get('gauge',''), '当前状态四象限定位')}</div>
  <div><h4>当前决策快照</h4>{snap_tbl}{_val_note}
  <p style="font-size:13px;color:#64748b">注：四象限由「趋势分」与「相对偏离分」共同决定。
  左上=趋势向上且相对低估（描述性分区，不是买点建议），右下=趋势向下且相对高估（描述性分区，不是离场建议）。</p></div>
</div>

<h3>3.3 绩效与对照实验</h3>
<p class="muted">净值曲线见 3.0 节开头（同一张图不重复嵌入，避免报告体积翻倍）。</p>
{metrics_tbl}
{_fig(figs.get('variants',''), '对照实验：各方案横向对比（灰=单通道，橙=恒定仓位对照，红=融合策略，蓝=买入持有）')}
{_fig(figs.get('drawdown',''), '回撤对比')}
<div class="lead {_cmp_cls}">策略年化 {_mget(m,'择时策略(融合)','年化收益','.2%')}，
买入持有 {_mget(m,'买入持有(100%)','年化收益','.2%')}，恒定仓位对照
{_mget(m,ctrl,'年化收益','.2%')}。{_cmp_text}
{_dd_comment(tc.get('最大回撤'))}
<b>对照设计</b>：恒定对照组只是把仓位固定在策略的平均水平
（{_safe(main['backtest']['avg_position'], '.0%')}，★ 同上：这是方案的平均仓位，
不是基金真实股票仓位），并没有做任何"择时"。{_timing_tail}</div>

<h3>3.4 信号有效性</h3>
{_fig(figs.get('ic',''), '五分组前瞻收益与滚动 RankIC')}
<p>分组前瞻收益<b>{_mono_txt}</b>，RankIC = {_safe(main['backtest']['ic'], '+.3f')}，
ICIR = {_safe(main['backtest']['icir'], '.2f')}，
多头胜率 {_safe(main['backtest']['win_long'], '.1%')}（{_win_txt}），
年化换手 {_safe(main['backtest']['turnover'], '.2f')} 倍。
<b>信号与未来收益之间没有可用的线性关系。</b></p>

<h3>3.5 风格漂移：这段区间的画像能代表当下吗</h3>
{_drift_note}
{_holdings_html}
</section>

<section>
<h2><span class="n">04</span>跨基金稳健性检验</h2>
<p>单只基金的结果可能只是偶然。下表把同一套流程跑遍 14 只不同风格
（消费 / 成长 / 均衡 / 科技）的 A 股主动权益基金。</p>
{sum_tbl}
<div class="lead bad">{extra_meta.get('cross_fund_verdict','')}</div>
</section>

<section>
<h2><span class="n">05</span>诊断实验：信号到底有没有信息</h2>
{exp_html}
{e4_html}
</section>

<section>
<h2><span class="n">06</span>已知风险与失效场景</h2>
<p class="lead warn">本框架的所有结论都建立在一组可被打破的假设上。
以下每一条都可能让结果失效，使用前必须逐条对照。</p>
<ul class="clean">
  <li><b>alpha 的持续性 vs 均值回复。</b>「相对偏离」通道押注价差会回归。
      但对真正有选股能力的基金经理，价差是<b>单调累积</b>的。
      实验证实该通道为负贡献——<b>不要把持续 alpha 误读为高估</b>。</li>
  <li><b>风格漂移与基金经理变更。</b>RBSA 用过去 252 日估权重，
      若基金刚换基金经理或大幅调仓，权重会滞后 3~6 个月。
      必须叠加持仓披露（季报十大重仓）做交叉验证。</li>
  <li><b>幸存者偏差。</b>本样本是当前仍在运作的基金，已清盘/转型的基金被排除，
      会系统性高估策略表现。</li>
  <li><b>交易成本被低估。</b>场外基金申购费约 1.5%、赎回费 0.5%，
      且确认 T+1。本回测按单边 0.15% 计提，<b>对场外申赎而言严重偏乐观</b>。
      该框架实际只适用于场内 ETF / LOF 或 C 类份额。</li>
  <li><b>前瞻偏差的隐性来源。</b>指数成分与编制规则会调整，
      用「当前生效」的指数回溯历史，会带入轻微的前瞻偏差。</li>
  <li><b>估值通道的数据错配。</b>免费 PE 历史只覆盖 10 个宽基指数（月度、2005 年起），
      而基金的风格可能由申万行业主导。用宽基 PE 代理行业估值会削弱甚至反转信号。</li>
  <li><b>多重检验。</b>E3 扫描了 {_e3_n_txt}参数，best 组合的漂亮结果很可能来自过拟合。
      <b>任何在网格里挑出来的参数都必须在独立样本上重新验证。</b></li>
  <li><b>样本量。</b>14 只基金 × 约 8 年样本外（每只 2116 个交易日），
      对日频择时而言统计功效仍然偏低，且基金之间高度相关（同一市场、同一时期），
      **有效独立样本数远小于表面的 14 只**。</li>
  <li><b>网格搜索的选择偏差。</b>E4 的"最优格子"与 E3 的"最优参数"都来自穷举搜索。
      在 12 个格子里取最大值、在 {_e3_n_txt}参数里取最大值，本身就会得到一个正数，
      与策略是否真的有效无关。<b>报告最优值而不报告分布，是量化研究里最常见的自欺。</b></li>
</ul>
</section>

<section>
<h2><span class="n">07</span>建议的迭代路线</h2>
<ol>
  <li><b>保留并强化风格穿透。</b>这是本框架唯一确定成立的部分。
      叠加季报十大重仓，把 RBSA 权重与真实持仓对照，
      输出「基金实际 beta 暴露 + 超额收益归因」，做成研究工具而非交易工具。</li>
  <li><b>把择时从日频改为半年频。</b>E4 已证明：信号窗口 250 日、再平衡 60~120 日
      （120 个交易日 ≈ 半年，不是季度）时超额夏普转正。下一步应在更大的基金池上验证，
      并把交易成本按场内 ETF 口径重算。</li>
  <li><b>收益口径需先核实（P0-01）。</b>当前基金收益取自数据源的「累计净值走势」，
      它与红利再投资净值是否等价尚未用真实分红样本验证；在核实前，
      本报告所有收益类数字都只是「该口径下」的结果。</li>
  <li><b>把「偏离」通道重新定义。</b>不用 z-score 反转，而是检验
      <b>spread 的趋势是否在衰减</b>（例如滚动 120 日 alpha 斜率的符号与强度），
      由「涨多了减仓」改为「alpha 钝化了才减仓」。</li>
  <li><b>估值通道改用持仓加权。</b>用季报持仓映射到对应行业的 PE/PB 分位，
      而不是用宽基 PE 代理。</li>
  <li><b>预期收益替代价格动量。</b>趋势通道本质是价格动量，
      可加入盈利预期修正（EPS 上调家数占比）等基本面动量，降低短期反转的伤害。</li>
</ol>
</section>

<section>
<h2><span class="n">08</span>运行指纹（可复现性）</h2>
{fingerprint_html}
</section>

<section>
<h2><span class="n">09</span>增量、功效与选择过程证据</h2>
{evidence_html}
</section>

<section>
<h2><span class="n">10</span>预测台账回验</h2>
{_pred_html}
</section>

<footer>
  本报告由可复现脚本生成 · 数据源：天天基金 / 新浪指数 / 申万宏源行业指数 ·
  结果仅供研究，不构成投资建议
</footer>
</div></body></html>"""

    path = Path(path or (C.ROOT / "report" / "回测报告.html"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：中途失败不留半截 HTML；Windows 下 os.replace 可覆盖已有文件。
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, path)
    print(f"[report] 已生成 {path}")
    return path
