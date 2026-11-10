# -*- coding: utf-8 -*-
"""未来未使用样本预注册与真实成交输入校验（不是策略有效性结论）。

预注册只冻结计划与口径；没有未来行情、时点数据和真实成交记录时，不得把
状态写为通过。成交诊断按 FIFO 计算已实现盈亏与显式成本，未平仓不按市值
计价，也不推断费用假设之外的可交易收益。
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED_COLUMNS = ("signal_date", "available_at", "execution_date", "symbol",
                    "side", "quantity", "price", "benchmark_price", "fee",
                    "tax", "slippage")
DATE_COLUMNS = ("signal_date", "available_at", "execution_date")
NUMERIC_COLUMNS = ("quantity", "price", "benchmark_price", "fee", "tax",
                   "slippage")


def _day(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} 需为 YYYY-MM-DD") from error


PROTOCOL_HASH_FIELD = "protocol_sha256"
EVIDENCE_HASH_FIELD = "evidence_sha256"


def _without_hash_field(value: Any, field: str) -> Any:
    """递归剔除指纹字段，使指纹回填到文件后不会改变自身。"""
    if isinstance(value, dict):
        return {key: _without_hash_field(item, field)
                for key, item in value.items() if key != field}
    if isinstance(value, list):
        return [_without_hash_field(item, field) for item in value]
    return value


def _digest(payload: Any, excluded_field: str) -> str:
    encoded = json.dumps(_without_hash_field(payload, excluded_field),
                         ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_hash(plan: dict) -> str:
    """冻结协议的指纹；指纹字段本身不参与计算，避免自引用。"""
    return _digest(plan, PROTOCOL_HASH_FIELD)


def evidence_hash(evidence: dict) -> str:
    """冻结日证据的指纹；排除自身字段以便回填。"""
    return _digest(evidence, EVIDENCE_HASH_FIELD)


def validate_plan(plan: dict, as_of: str | date | None = None) -> dict:
    """校验冻结协议；执行状态与证据不在协议里，见 validate_execution。"""
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("未来验证计划需为 schema_version=1 的对象")
    registered = _day(plan.get("registered_at"), "registered_at")
    start = _day(plan.get("holdout_start"), "holdout_start")
    end = _day(plan.get("holdout_end"), "holdout_end")
    evaluation = _day(plan.get("evaluation_date"), "evaluation_date")
    pre = plan.get("pre_registration") or {}
    freeze = _day(pre.get("freeze_date"), "pre_registration.freeze_date")
    if not registered <= freeze < start < end < evaluation:
        raise ValueError("需满足 registered_at <= freeze_date < holdout_start "
                         "< holdout_end < evaluation_date")
    if pre.get("declared_untouched") is not True or not pre.get("evidence"):
        raise ValueError("预注册必须声明预留区间未触碰且提供证据清单")
    if "status" in plan:
        raise ValueError("协议文件不得含执行状态，状态写入执行台账")
    if not plan.get("execution_ledger"):
        raise ValueError("协议需指定执行台账路径 execution_ledger")
    items = plan.get("items")
    if not isinstance(items, list) or len(items) != 6:
        raise ValueError("必须预注册六项待验证条目")
    ids = [item.get("id") for item in items if isinstance(item, dict)]
    if len(set(ids)) != 6 or any(not value for value in ids):
        raise ValueError("六项条目需有唯一 id")
    for item in items:
        if not item.get("title") or not item.get("completion_criteria"):
            raise ValueError(f"{item['id']} 缺少标题或完成标准")
        if "status" in item or "evidence" in item:
            raise ValueError(f"{item['id']} 含执行状态，协议文件必须保持冻结")
    today = _day(as_of, "as_of") if as_of is not None else date.today()
    if today < start:
        phase = "scheduled"
    elif today <= end:
        phase = "collecting_future_samples"
    elif today < evaluation:
        phase = "waiting_evaluation"
    else:
        phase = "evaluation_due"
    return {"phase": phase, "as_of": today.isoformat(),
            "registered_at": registered.isoformat(),
            "holdout_start": start.isoformat(),
            "holdout_end": end.isoformat(),
            "evaluation_date": evaluation.isoformat(),
            "items_registered": len(items),
            "protocol_sha256": _plan_hash(plan),
            "limitations": pre.get("limitations") or []}


def validate_execution(plan: dict, execution: dict,
                       as_of: str | date | None = None) -> dict:
    """把冻结协议与执行台账合起来，返回当前时间点的真实状态。"""
    protocol = validate_plan(plan, as_of=as_of)
    if not isinstance(execution, dict) or execution.get("schema_version") != 1:
        raise ValueError("执行台账需为 schema_version=1 的对象")
    if execution.get("protocol_sha256") != protocol["protocol_sha256"]:
        raise ValueError("执行台账绑定的协议指纹不匹配，协议可能在冻结后被修改")
    ledger_items = execution.get("items")
    if not isinstance(ledger_items, list):
        raise ValueError("执行台账缺少 items")
    plan_ids = {item["id"] for item in plan["items"]}
    ledger_ids = [item.get("id") for item in ledger_items
                  if isinstance(item, dict)]
    if len(ledger_ids) != len(plan_ids) or set(ledger_ids) != plan_ids:
        raise ValueError("执行台账条目需与协议六项一一对应")
    pending = 0
    for item in ledger_items:
        if not isinstance(item.get("status"), str) or not item["status"]:
            raise ValueError(f"{item['id']} 缺少执行状态")
        if item["status"] == "complete":
            if not item.get("evidence"):
                raise ValueError(f"{item['id']} 不得无证据标记完成")
        else:
            pending += 1
    phase = protocol["phase"]
    status = ("overdue_incomplete" if pending else "ready_for_review") \
        if phase == "evaluation_due" else phase
    return {**protocol, "status": status, "items_pending": pending,
            "execution_status": execution.get("status"),
            "execution_verified": True}


def load_plan(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_execution(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_freeze_evidence(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def validate_freeze_evidence(plan: dict, execution: dict,
                             evidence: dict) -> dict:
    """校验冻结日证据：协议绑定、自指纹、法务门控与台账绑定。"""
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("冻结证据需为 schema_version=1 的对象")
    protocol_hash = validate_plan(plan)["protocol_sha256"]
    if evidence.get("protocol_sha256") != protocol_hash:
        raise ValueError("冻结证据绑定的协议指纹不匹配")
    digest = evidence_hash(evidence)
    if evidence.get(EVIDENCE_HASH_FIELD) != digest:
        raise ValueError("冻结证据自身指纹不匹配，文件可能在冻结后被修改")
    if evidence.get("freeze_date") != (plan.get("pre_registration") or {}).get("freeze_date"):
        raise ValueError("冻结证据日期与协议冻结日不一致")
    auth = evidence.get("data_authorization") or {}
    if auth.get("raw_data_included") is not False:
        raise ValueError("冻结证据不得包含原始数据")
    code = evidence.get("code") or {}
    if not code.get("git_commit") or not code.get("source_sha256"):
        raise ValueError("冻结证据缺少代码指纹")
    runtime = evidence.get("runtime") or {}
    if not runtime.get("python") or not runtime.get("config_hash"):
        raise ValueError("冻结证据缺少运行环境")
    inputs = evidence.get("inputs") or {}
    if inputs.get("declared_no_future_data") is not True:
        raise ValueError("冻结证据须声明冻结日无未来输入，不得伪造原始输入指纹")
    if execution.get("freeze_evidence_sha256") != digest:
        raise ValueError("执行台账绑定的冻结证据指纹不匹配")
    return {"verified": True, "freeze_date": evidence["freeze_date"],
            "protocol_sha256": protocol_hash, "evidence_sha256": digest,
            "code": {"git_commit": code["git_commit"],
                     "git_dirty": code.get("git_dirty"),
                     "source_files": len(code["source_sha256"])},
            "runtime": {"python": runtime["python"],
                        "config_hash": runtime["config_hash"]},
            "inputs": {"status": inputs.get("status"),
                       "declared_no_future_data": True,
                       "fixed_inputs": len(inputs.get("fixed_inputs_sha256") or {})},
            "raw_data_included": False,
            "limitations": evidence.get("limitations") or []}


def evaluate_real_trades(frame: pd.DataFrame) -> dict:
    """校验真实成交时点/成本/仓位，并计算 FIFO 已实现盈亏与滑点。"""
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"真实成交缺少字段 {missing}")
    df = frame.loc[:, list(REQUIRED_COLUMNS)].copy()
    for column in DATE_COLUMNS:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    if df[list(DATE_COLUMNS)].isna().any().any():
        raise ValueError("真实成交日期无法解析")
    for column in ("fee", "tax", "slippage"):
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if df[list(NUMERIC_COLUMNS)].isna().any().any():
        raise ValueError("真实成交存在非数值价格、数量或成本")
    df["side"] = df["side"].astype(str).str.strip().str.lower()
    if not df["side"].isin(("buy", "sell")).all():
        raise ValueError("side 仅允许 buy/sell")
    if (df[["quantity", "price", "benchmark_price"]] <= 0).any().any() or \
       (df[["fee", "tax", "slippage"]] < 0).any().any():
        raise ValueError("数量/价格需为正，费用/税费/滑点不能为负")
    df["symbol"] = df["symbol"].astype(str)
    leakage = ((df["available_at"] > df["signal_date"]) |
               (df["signal_date"] > df["execution_date"]))
    df["slippage_bps"] = ((df["price"] - df["benchmark_price"]) /
                          df["benchmark_price"] * 10000 *
                          df["side"].map({"buy": 1.0, "sell": -1.0}))
    lots = defaultdict(deque)
    realized = 0.0
    violations = []
    for index, row in df.sort_values("execution_date").iterrows():
        quantity = float(row["quantity"])
        price = float(row["price"])
        costs = float(row["fee"] + row["tax"] + row["slippage"])
        if row["side"] == "buy":
            lots[row["symbol"]].append(
                (quantity, (quantity * price + costs) / quantity))
            continue
        proceeds_per_unit = (quantity * price - costs) / quantity
        remaining = quantity
        while remaining > 0 and lots[row["symbol"]]:
            lot_qty, unit_cost = lots[row["symbol"]][0]
            matched = min(remaining, lot_qty)
            realized += matched * (proceeds_per_unit - unit_cost)
            remaining -= matched
            lot_qty -= matched
            if lot_qty <= 1e-12:
                lots[row["symbol"]].popleft()
            else:
                lots[row["symbol"]][0] = (lot_qty, unit_cost)
        if remaining > 1e-9:
            violations.append(f"行 {index}: {row['symbol']} 卖出数量超过持仓")
    open_quantity = float(sum(quantity for symbol_lots in lots.values()
                              for quantity, _ in symbol_lots))
    gross = float((df["quantity"] * df["price"]).sum())
    total_cost = float(df[["fee", "tax", "slippage"]].to_numpy().sum())
    return {"status": "ok" if not int(leakage.sum()) and not violations else "invalid",
            "row_count": int(len(df)),
            "symbols": sorted(df["symbol"].unique().tolist()),
            "first_signal": df["signal_date"].min().date().isoformat(),
            "last_execution": df["execution_date"].max().date().isoformat(),
            "buy_count": int((df["side"] == "buy").sum()),
            "sell_count": int((df["side"] == "sell").sum()),
            "gross_notional": gross, "explicit_cost": total_cost,
            "cost_bps_of_notional": total_cost / gross * 10000 if gross else None,
            "mean_slippage_bps": float(df["slippage_bps"].mean()),
            "max_abs_slippage_bps": float(df["slippage_bps"].abs().max()),
            "slippage_sign_convention": "正值表示相对基准的不利成交，负值表示有利成交",
            "fifo_realized_pnl": realized,
            "open_quantity": open_quantity,
            "future_leak_rows": int(leakage.sum()),
            "violations": violations,
            "limitations": ["未平仓头寸不按市值计价，不含股息、公司行动或融资成本",
                            "FIFO 已实现盈亏不是策略有效性或可交易收益证明",
                            "结果依赖输入中实际成交、费用、税费和滑点的真实完整性"]}
