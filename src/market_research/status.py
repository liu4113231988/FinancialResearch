# -*- coding: utf-8 -*-
"""结果状态分层（C3 / P2-31、P2-32）。

一次分析的"能不能用"其实是五件不同的事，混成一个 status 会让页面和导出
说不清话：

- computation：本次计算有没有跑完（ok/partial/failed）；
- data_quality：输入行情/净值的质量（ok/partial/degraded/unavailable）；
- assessment：统计证据是否充分（sufficient/insufficient/failed/unknown）；
- registration：这次结果有没有写入预测台账（recorded/partial/not_recorded/unknown）；
- execution：成交诊断是否实际执行（evaluated/not_evaluated/not_applicable）。

硬约束：任务 done 不等于预测有效；未入账不等于计算失败；任何一层都不输出
"已验证"措辞。这里只做取值与汇总，不解释业务含义。
"""
from __future__ import annotations

from typing import Iterable, Mapping

COMPUTATION = ("ok", "partial", "failed")
DATA_QUALITY = ("ok", "partial", "degraded", "unavailable")
ASSESSMENT = ("sufficient", "insufficient", "failed", "unknown", "not_applicable")
REGISTRATION = ("recorded", "partial", "not_recorded", "unknown")
EXECUTION = ("evaluated", "not_evaluated", "not_applicable")

# 0=正常，1=降级但仍可用，2=阻断
SEVERITY = {"ok": 0, "sufficient": 0, "recorded": 0, "evaluated": 0,
            "not_applicable": 0,
            "partial": 1, "degraded": 1, "insufficient": 1,
            "not_recorded": 1, "not_evaluated": 1, "unknown": 1,
            "unavailable": 2, "failed": 2}

NOTE = "任务完成不等于预测有效；未入账不等于计算失败；各层独立取值。"


def _check(name: str, value: str, allowed: Iterable[str]) -> str:
    if value not in tuple(allowed):
        raise ValueError(f"{name} 取值非法：{value!r}")
    return value


def build(*, computation: str = "ok", data_quality: str = "ok",
          assessment: str = "unknown", registration: str = "unknown",
          execution: str = "not_applicable",
          reasons: Iterable[str] = ()) -> dict:
    """把五层状态收成一个可直接透传给页面/导出的块。"""
    layers = {
        "computation": _check("computation", computation, COMPUTATION),
        "data_quality": _check("data_quality", data_quality, DATA_QUALITY),
        "assessment": _check("assessment", assessment, ASSESSMENT),
        "registration": _check("registration", registration, REGISTRATION),
        "execution": _check("execution", execution, EXECUTION),
    }
    blocking = layers["computation"] == "failed"
    # 入账/成交只做提示：历史区间不写台账是正常状态，不能因此把结果判成"部分失败"。
    core = ("computation", "data_quality", "assessment")
    severity = max(SEVERITY[layers[name]] for name in core)
    if blocking:
        status = "failed"
    elif severity:
        status = "partial"
    else:
        status = "ok"
    return {
        **layers,
        "summary": {
            "status": status,
            "blocking": blocking,
            "degraded": bool(severity) and not blocking,
            "degraded_layers": [name for name, value in layers.items()
                                if SEVERITY.get(value, 1) >= 1],
            "advisory_layers": [name for name in ("registration", "execution")
                                if SEVERITY[layers[name]] >= 1],
            "reasons": [str(reason) for reason in reasons],
        },
        "note": NOTE,
    }


def assessment_layer(statuses: Iterable[str | None]) -> str:
    """把多个模型的评估状态汇总成一层。

    只要有一个模型证据充分就是 sufficient（页面会给每个模型单独标注），
    全部失败才是 failed；没有任何可用状态时才 unknown，绝不默认成 sufficient。
    """
    seen = {str(item or "unknown") for item in statuses}
    if "sufficient" in seen:
        return "sufficient"
    if seen and seen <= {"failed"}:
        return "failed"
    if seen & {"insufficient", "unknown"}:
        return "insufficient" if "insufficient" in seen else "unknown"
    return "unknown"


def evidence_assessment(evidence_states: Iterable[str | None]) -> str:
    """市场技术预测的四态证据 -> assessment 层。

    `not_computable` 与 `insufficient` 都表示"没有可用证据"，不能算 sufficient。
    """
    seen = {str(item or "") for item in evidence_states}
    if seen & {"historical_diagnostic", "independent_verification_pending"}:
        return "sufficient"
    if "insufficient" in seen:
        return "insufficient"
    if "not_computable" in seen:
        return "unknown"
    return "unknown"


def from_result(result: Mapping) -> dict:
    """从既有结果块推断五层；未知项一律保守取 unknown/not_applicable。"""
    completeness = result.get("completeness") or {}
    meta = result.get("meta") or {}
    quality = meta.get("data_quality") or result.get("data_quality") or {}
    models = result.get("models")
    if isinstance(models, Mapping):
        statuses = [(block.get("assessment") or {}).get("status")
                    for block in models.values() if isinstance(block, Mapping)]
        assessment = assessment_layer(statuses)
    else:
        forecasts = ((result.get("technical") or {}).get("forecasts")
                     or result.get("forecasts") or [])
        assessment = evidence_assessment(
            (item.get("evidence_state") for item in forecasts
             if isinstance(item, Mapping))) if forecasts else "unknown"
    registration = (meta.get("registration") or {}).get("status") or "unknown"
    computation = completeness.get("status") or result.get("status") or "ok"
    if computation not in COMPUTATION:
        computation = "partial" if computation == "degraded" else "ok"
    data_status = quality.get("status") or "unavailable"
    if data_status not in DATA_QUALITY:
        data_status = "unavailable"
    return build(computation=computation, data_quality=data_status,
                 assessment=assessment,
                 registration=registration if registration in REGISTRATION else "unknown",
                 execution="not_applicable",
                 reasons=completeness.get("reasons") or quality.get("reasons") or ())


def refresh(result: dict) -> dict:
    """就地刷新 `result['status_layers']` 并返回它（入账后需再刷一次）。"""
    result["status_layers"] = from_result(result)
    return result["status_layers"]
