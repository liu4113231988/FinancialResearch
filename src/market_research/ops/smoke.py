# -*- coding: utf-8 -*-
"""最小真实数据冒烟（P2-35）。

覆盖「抓取 → 面板 → 分析 → 报告」主链路，只跑一只基金、少量模型。
离线 CI 只跑单测、语法检查与脱敏契约回放；真实数据冒烟由独立的
`live-smoke.yml` 按计划或手动触发，并归档结果、按失败类型告警。

失败时按阶段与异常来源分类：
  * data_source —— 公开数据源不可用/结构变化/网络失败
  * logic       —— 本地计算或渲染逻辑异常
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import market_research.config as C


def classify_failure(stage: str, exc: BaseException) -> str:
    """区分数据源问题与逻辑问题，便于值班时决定是否重试/换网络。"""
    from market_research.errors import (NavBasisUnavailable,
                                        ProviderContractError, StorageBusy)
    if isinstance(exc, (ProviderContractError, NavBasisUnavailable, StorageBusy)):
        return "data_source"
    module = type(exc).__module__ or ""
    if stage in ("fetch", "panel") or module.startswith("market_research.data"):
        if isinstance(exc, (KeyError, TypeError, AttributeError)):
            return "logic"
        return "data_source"
    try:
        import requests
        if isinstance(exc, requests.RequestException):
            return "data_source"
    except Exception:                                        # noqa: BLE001
        pass
    return "logic"


def run(code: str = C.MAIN_FUND,
        out_dir: str | Path | None = None) -> dict:
    from market_research.data import marketdata as D
    from market_research.funds.pipeline import run_fund
    from market_research.research.report import build_report
    from market_research.research.runinfo import run_fingerprint

    out_dir = Path(out_dir or (C.RES_DIR / "smoke"))
    out_dir.mkdir(parents=True, exist_ok=True)

    stages = {}
    stage = "fetch"
    started = time.time()
    try:
        panel = D.load_panel(code, start=C.START_DATE, verbose=False)
        if panel is None or panel.empty:
            raise RuntimeError("面板为空")
        stages["fetch"] = round(time.time() - started, 2)

        stage = "analyze"
        started = time.time()
        result = run_fund(code, verbose=False)
        if not isinstance(result, dict) or "backtest" not in result:
            raise RuntimeError("分析结果缺少主链路字段")
        stages["analyze"] = round(time.time() - started, 2)

        stage = "report"
        started = time.time()
        import pandas as pd
        path = build_report(
            result, pd.DataFrame(), {}, {},
            out_dir / f"smoke_{code}.html",
            extra_meta={"n_funds": "1",
                        "period": f"{result['nav'].index[0].date()} ~ "
                                  f"{result['nav'].index[-1].date()}",
                        "data_quality": result.get("data_quality") or {},
                        "completeness": result.get("completeness") or {}},
            fingerprint=run_fingerprint({"smoke": True, "code": code}))
        if not Path(path).exists() or Path(path).stat().st_size == 0:
            raise RuntimeError("报告未生成或为空")
        stages["report"] = round(time.time() - started, 2)
    except Exception as exc:                                  # noqa: BLE001
        report = {"ok": False, "stage": stage,
                  "failure_class": classify_failure(stage, exc),
                  "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                  "trace": traceback.format_exc()[-1200:],
                  "stages": stages}
    else:
        report = {"ok": True, "stage": "report",
                  "failure_class": None, "error": None,
                  "environment": C.ENV, "code": code,
                  "report_path": str(path), "stages": stages}

    (out_dir / "smoke_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真实数据冒烟（P2-35）")
    parser.add_argument("--code", default=C.MAIN_FUND)
    parser.add_argument("--out", default=str(C.RES_DIR / "smoke"))
    args = parser.parse_args(argv)
    result = run(args.code, args.out)
    print(json.dumps({k: result[k] for k in
                      ("ok", "stage", "failure_class", "error", "stages")},
                     ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
