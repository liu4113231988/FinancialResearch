# -*- coding: utf-8 -*-
"""访问控制与请求身份上下文（P2-24）。

产品口径：当前默认**单用户、无强制认证**；对外提供 API 时才需要打开开关。
为了让后续接入多用户/多租户不必重写调用链，身份统一放在 `Identity` 里，
通过 contextvar 随请求传播，服务层只读 `current_identity()`，不直接解析 HTTP 头。

环境变量：
    FIL_ACCESS_CONTROL=off|token   默认 off（单用户）
    FIL_API_TOKENS=<token[,token]> token 模式下的允许令牌；
                                    支持 `租户:令牌` 形式，为多租户预留
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import hmac
import logging
import os

LOG = logging.getLogger(__name__)

# 探针与静态资源不需要业务授权；健康检查被监控系统调用时也不该要令牌。
PUBLIC_PATHS = frozenset({"/", "/api/health", "/api/ready", "/api/disclosure"})
PUBLIC_PREFIXES = ("/static/", "/vendor/")


@dataclass(frozen=True)
class Identity:
    """一次请求的身份与作用域；单用户模式下是匿名的本地主体。"""
    subject: str = "local"
    tenant: str = "default"
    scopes: frozenset = frozenset({"read", "write"})
    authenticated: bool = False


LOCAL_IDENTITY = Identity()
_IDENTITY: ContextVar[Identity] = ContextVar("fil_identity", default=LOCAL_IDENTITY)


def current_identity() -> Identity:
    return _IDENTITY.get()


def set_identity(identity: Identity):
    return _IDENTITY.set(identity)


def reset_identity(token) -> None:
    _IDENTITY.reset(token)


def access_mode() -> str:
    mode = os.environ.get("FIL_ACCESS_CONTROL", "off").strip().lower()
    return mode if mode in ("off", "token") else "off"


def _configured_tokens() -> dict:
    """解析 `租户:令牌,令牌`；令牌本身含冒号时按最后一个冒号切分。"""
    raw = os.environ.get("FIL_API_TOKENS", "")
    out = {}
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        tenant, sep, token = item.rpartition(":")
        if not sep:
            tenant, token = "default", item
        out[token] = tenant or "default"
    return out


def identity_for_token(token: str) -> Identity | None:
    tokens = _configured_tokens()
    for candidate, tenant in tokens.items():
        # 逐个定长比较，避免按长度泄露令牌数量
        if hmac.compare_digest(candidate, token):
            return Identity(subject=candidate[:8], tenant=tenant,
                            scopes=frozenset({"read", "write"}),
                            authenticated=True)
    return None


def mode_description() -> str:
    mode = access_mode()
    if mode == "token":
        n = len(_configured_tokens())
        if n == 0:
            return "令牌模式（未配置 FIL_API_TOKENS，所有业务请求将被拒绝）"
        return f"令牌模式（{n} 个令牌）"
    return "单用户模式（未启用认证）"


class AccessControl:
    """可开关的 HTTP 访问控制中间件；默认 off 时行为完全不变。"""

    def __init__(self, app, mode: str | None = None):
        self.app = app
        self.mode = (mode or access_mode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        headers = dict(scope.get("headers") or [])
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await self.app(scope, receive, send)

        if self.mode != "token":
            token = set_identity(LOCAL_IDENTITY)
            try:
                return await self.app(scope, receive, send)
            finally:
                reset_identity(token)

        raw = (headers.get(b"authorization", b"").decode("latin-1").strip()
               or headers.get(b"x-api-key", b"").decode("latin-1").strip())
        supplied = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        identity = identity_for_token(supplied) if supplied else None
        if identity is None:
            from fastapi.responses import JSONResponse
            response = JSONResponse(
                {"ok": False, "error": "未授权：需要有效的 API 令牌",
                 "field": "authorization"}, status_code=401)
            response.headers["WWW-Authenticate"] = "Bearer"
            return await response(scope, receive, send)

        token = set_identity(identity)
        try:
            return await self.app(scope, receive, send)
        finally:
            reset_identity(token)
