# -*- coding: utf-8 -*-
"""免责声明与数据披露（P2-36）。

产品口径：内部研究，声明保持简短但必须在 Web 页显著位置与报告首页出现：
数据来源为公开抓取、存在延迟与不完整、结论仅供研究不构成投资建议。

同时保留「对外交付物」升级位：
    FIL_DISCLOSURE_SCOPE=internal|external   默认 internal
    FIL_DISCLOSURE_TEXT=<自定义文案>          覆盖默认文案
    FIL_DISCLOSURE_POSITION=top|footer        默认 top（footer 仅用于降级场景）
"""
from __future__ import annotations

import os

TEXT = {
    "internal": ("数据来源为公开抓取，存在延迟与不完整；"
                 "历史研究未建立稳定的日频方向预测优势，方向概率、规则条件与仓位状态"
                 "均为研究诊断而非可执行交易信号；结论仅用于研究参考，不构成投资建议。"),
    "external": ("本报告数据来源于公开渠道，可能存在延迟、缺失或口径差异；"
                 "历史研究未建立稳定的日频方向预测优势，方向概率、规则条件与仓位状态"
                 "均为研究诊断而非可执行交易信号；相关内容仅供研究参考，不构成任何投资建议。"),
}


def scope() -> str:
    value = os.environ.get("FIL_DISCLOSURE_SCOPE", "internal").strip().lower()
    return value if value in TEXT else "internal"


def position() -> str:
    value = os.environ.get("FIL_DISCLOSURE_POSITION", "top").strip().lower()
    return value if value in ("top", "footer") else "top"


def text() -> str:
    override = os.environ.get("FIL_DISCLOSURE_TEXT", "").strip()
    return override or TEXT[scope()]


def payload() -> dict:
    return {"scope": scope(), "position": position(), "text": text()}


def html() -> str:
    """报告首页横幅；文案经过最小转义，避免自定义文案破坏结构。"""
    body = text().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<div class="disclosure" role="note">'
            f'<b>数据与使用声明</b>　{body}</div>')
