"""预测台账命令：结算到期预测与查看命中率/Brier 汇总。"""
from __future__ import annotations

import argparse
import json
from datetime import date

from market_research.predictions import ledger


def _today(value):
    if not value:
        return None
    return date.fromisoformat(value)


def main(argv=None, *, prog="predictions"):
    parser = argparse.ArgumentParser(prog=prog)
    actions = parser.add_subparsers(dest="action", required=True)

    settle = actions.add_parser("settle", help="结算已到期预测")
    settle.add_argument("--today", default=None, help="按指定日期结算（YYYY-MM-DD）")
    settle.add_argument("--limit", type=int, default=500)
    settle.add_argument("--fetch", action="store_true",
                        help="本地缓存缺失时允许联网补充行情")

    summary = actions.add_parser("summary", help="命中率、Brier 与分桶汇总")
    summary.add_argument("--group-by", choices=ledger.GROUP_FIELDS, default=None)

    migrate = actions.add_parser("migrate", help="把旧口径 5/20 规则行合并为单条状态事件")
    migrate.add_argument("--apply", action="store_true",
                         help="实际写库（默认只预览）；会删除同组重复行，建议先备份 market.duckdb")

    args = parser.parse_args(argv)
    if args.action == "settle":
        from market_research.predictions.judge import settle
        result = settle(limit=args.limit, today=_today(args.today),
                        fetch=args.fetch)
    elif args.action == "migrate":
        result = ledger.migrate_legacy(apply=args.apply)
    else:
        result = ledger.summary(group_by=args.group_by)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
