#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冻结日预注册证据：默认校验，``--apply`` 仅在文件不存在时生成（拒绝覆盖）。

不联网、不读行情库，只记录脱敏指纹与冻结日声明。真实未来输入的内容指纹
须在评估前按 ``real_trades_schema`` 追加到执行台账的 ``items[].evidence``，
不得回填到本文件。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_research.research.future_validation import (  # noqa: E402
    _plan_hash, evidence_hash, load_execution, load_freeze_evidence, load_plan,
    validate_freeze_evidence)
from market_research.research.runinfo import (  # noqa: E402
    _git_dirty, _git_head, _lib_versions, config_hash)

PLAN = ROOT / "docs" / "future-validation-plan.json"
LEDGER = ROOT / "docs" / "future-validation-execution.json"
EVIDENCE = ROOT / "docs" / "future-validation-freeze-evidence.json"
EVIDENCE_REL = "docs/future-validation-freeze-evidence.json"

SOURCE_FILES = (
    "src/market_research/config.py",
    "src/market_research/research/future_validation.py",
    "src/market_research/research/market_validation.py",
    "src/market_research/research/market_snapshot.py",
    "src/market_research/research/runinfo.py",
    "src/market_research/research/calibration.py",
    "src/market_research/market/technical.py",
    "src/market_research/market/context.py",
    "src/market_research/market/execution.py",
    "src/market_research/market/capital.py",
    "src/market_research/market/uncertainty.py",
    "src/market_research/market/targets.py",
    "scripts/validate_real_trades.py",
    "scripts/freeze_future_validation_evidence.py",
)
FIXED_INPUTS = (
    "docs/future-validation-plan.json",
    "docs/market-validation-plan.json",
    "docs/market-calibration-plan.json",
    "docs/capital-validation-plan.json",
    "docs/capital-model-validation-plan.json",
    "tests/fixtures/providers/contracts.json",
)
LOCK_FILES = ("requirements.txt", "requirements.lock.txt", "uv.lock")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_digest(relative_paths: tuple[str, ...]) -> dict:
    return {rel: _sha256(ROOT / rel) for rel in relative_paths
            if (ROOT / rel).is_file()}


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dirty_files() -> list:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True, timeout=10,
                         encoding="utf-8", errors="replace")
    return out.stdout.splitlines() if out.returncode == 0 else []


def build_evidence(plan: dict) -> dict:
    """生成冻结日快照；只读取入版本库的文件与运行环境，不触碰行情数据。"""
    evidence = {
        "schema_version": 1,
        "name": "future-validation-freeze-evidence",
        "freeze_date": plan["pre_registration"]["freeze_date"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": "docs/future-validation-plan.json",
        "protocol_sha256": _plan_hash(plan),
        "code": {
            "git_commit": _git_head(ROOT),
            "git_dirty": _git_dirty(ROOT),
            "dirty_files": _dirty_files(),
            "dirty_note": "冻结日证据固化时工作区存在未提交改动；source_sha256 是当时工作区文件的内容快照，提交后只要内容不变，指纹仍然有效。",
            "source_sha256": _files_digest(SOURCE_FILES),
            "source_scope": "研究结果相关计算模块、配置、预注册校验、成交诊断入口与冻结证据生成脚本；非全仓逐文件审计。",
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "libs": _lib_versions(),
            "config_hash": config_hash(),
            "seeds": {"bootstrap": 20261011, "sklearn_random_state": 0},
            "lock_files_sha256": _files_digest(LOCK_FILES),
        },
        "inputs": {
            "status": "no_future_inputs_at_freeze",
            "declared_no_future_data": True,
            "note": "留出期 2026-11-11 才开始，冻结日不存在未来行情、历史时点成分、公司行动或真实成交记录。评估前须按本文件 real_trades_schema 采集真实输入，把逐文件内容指纹与可复现命令追加到执行台账 items[].evidence，不得回填本文件，也不得用合成数据冒充原始输入。",
            "fixed_inputs_sha256": _files_digest(FIXED_INPUTS),
            "real_trades_schema": plan["real_trades_schema"],
            "real_trades_schema_sha256": _canonical_sha256(plan["real_trades_schema"]),
            "collection_rule": "逐文件记录 data_sha256/csv_sha256、采集时间、可得时间与来源；未取得的字段保留缺口，不推断为可交易。",
        },
        "data_authorization": {
            "status": plan["data_authorization"]["status"],
            "raw_data_included": False,
            "notice": "仅记录脱敏指纹与文件哈希；不采集、不提交、不再分发原始行情或成交记录。逐源数据授权与再分发范围仍待法务结论。",
        },
        "limitations": [
            "本文件是冻结日快照，不是六项验证的结果或有效性证据。",
            "程序化登记不能独立证明作者未查看过历史数据。",
            "output/ 被 .gitignore 整体忽略，其快照 manifest/receipt 不作为本证据的唯一依据。",
        ],
        "evidence_sha256": None,
    }
    evidence["evidence_sha256"] = evidence_hash(evidence)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="首次生成冻结证据；已存在则拒绝覆盖")
    args = parser.parse_args(argv)
    plan = load_plan(PLAN)
    if args.apply:
        if EVIDENCE.exists():
            print("冻结证据已存在，拒绝覆盖（不得倒改冻结日证据）")
            return 2
        evidence = build_evidence(plan)
        EVIDENCE.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"已生成 {EVIDENCE_REL}")
        print(f"freeze_evidence_sha256 = {evidence['evidence_sha256']}")
        print("请把该值写入执行台账的 freeze_evidence_sha256；协议文件无需改动")
        return 0
    try:
        execution = load_execution(LEDGER)
        path = ROOT / (execution.get("freeze_evidence") or EVIDENCE_REL)
        summary = validate_freeze_evidence(
            plan, execution, load_freeze_evidence(path))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"冻结证据校验失败：{type(error).__name__}: {error}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
