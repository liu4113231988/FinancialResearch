# -*- coding: utf-8 -*-
"""数据源使用与再分发授权状态（P2-36 的技术护栏，不是法律意见）。

公开可访问不等于允许对外再分发。默认状态为“待法务审查、禁止原始响应
再分发”；只有显式配置审查编号和批准标记后，工具才允许生成可外发原始
fixture。许可证条款核实、授权范围与地域适用性仍须由法务/数据治理确认。
"""
from __future__ import annotations

import os

NOTICE = ("第三方公开数据源的使用条款、版权与再分发权限尚未完成法务审查；"
          "默认仅限内部研究，禁止提交或对外分发原始响应。")


def _sources() -> list[str]:
    try:
        from market_research.data.provenance import CONTRACTS
        return sorted(str(key) for key in CONTRACTS)
    except Exception:                                             # noqa: BLE001
        return []


def redistribution_allowed() -> bool:
    """只有审查编号和批准标记都存在时才允许生成可外发原始样本。"""
    approved = os.environ.get("FIL_DATA_LICENSE_APPROVED", "").strip() == "1"
    review_id = os.environ.get("FIL_DATA_LICENSE_REVIEW_ID", "").strip()
    return approved and bool(review_id)


def status() -> dict:
    review_id = os.environ.get("FIL_DATA_LICENSE_REVIEW_ID", "").strip()
    approved = redistribution_allowed()
    return {"status": "approved" if approved else "review_required",
            "legal_review_completed": approved,
            "external_use_allowed": approved,
            "raw_fixture_redistribution_allowed": approved,
            "review_id": review_id or None,
            "sources": [{"name": name,
                         "terms_review": "approved" if approved else "required",
                         "raw_redistribution": "allowed" if approved else "denied"}
                        for name in _sources()],
            "notice": NOTICE if not approved else
                      "已配置法务审查编号；对外使用仍须逐源核对授权范围与有效期。"}
