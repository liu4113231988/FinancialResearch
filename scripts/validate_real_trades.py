#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验六项未来验证预注册与真实成交 CSV，输出可审计诊断 JSON。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_research.research.future_validation import (  # noqa: E402
    evaluate_real_trades, load_execution, load_plan, validate_execution)

DEFAULT_PLAN = ROOT / "docs" / "future-validation-plan.json"
DEFAULT_LEDGER = ROOT / "docs" / "future-validation-execution.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", required=True, help="真实成交 CSV")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--ledger", default=None,
                        help="执行台账 JSON，默认取协议中的 execution_ledger")
    parser.add_argument("--as-of", default=None, help="预注册状态检查日 YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="诊断 JSON 输出路径")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        ledger = Path(args.ledger or plan.get("execution_ledger")
                      or DEFAULT_LEDGER)
        if not ledger.is_absolute():
            ledger = ROOT / ledger
        report = {
            "plan": validate_execution(plan, load_execution(ledger),
                                       as_of=args.as_of),
            "trades": evaluate_real_trades(
                pd.read_csv(args.trades, encoding="utf-8-sig")),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"验证输入无效：{type(error).__name__}: {error}")
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2,
                         sort_keys=True, allow_nan=False)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload)
    valid_trades = report["trades"]["status"] == "ok"
    valid_plan = report["plan"]["status"] != "overdue_incomplete"
    return 0 if valid_trades and valid_plan else 1


if __name__ == "__main__":
    raise SystemExit(main())
