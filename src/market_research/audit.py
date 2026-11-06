"""结构化审计日志（P2-26）。

只记录「发生了什么」的白名单字段：事件、对象、结果、错误摘要、耗时。
参数原文、行情数值、请求头、token/cookie 等一律不落盘；detail 会递归脱敏
并截断，避免把敏感数据或大对象写进审计文件。

写入采用「每次追加、立即关闭」而不是常驻文件句柄：Windows 上常驻句柄会
阻止目录清理/轮转，测试与运维都会受影响。审计失败不能影响主流程，因此
所有异常都在内部吞掉。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

import market_research.config as C

_SENSITIVE = {"password", "passwd", "token", "access_token", "refresh_token",
              "secret", "api_key", "apikey", "authorization", "cookie",
              "set-cookie", "arguments", "params", "headers"}
_LOCK = threading.Lock()
_MAX_BYTES = 5_000_000
_BACKUPS = 5


def _sanitize(value, depth: int = 0):
    if depth > 2:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in list(value.items())[:20]:
            if str(key).strip().lower() in _SENSITIVE:
                cleaned[str(key)] = "<redacted>"
            else:
                cleaned[str(key)] = _sanitize(item, depth + 1)
        return cleaned
    return f"<{type(value).__name__}>"


def _rotate(path) -> None:
    """audit.jsonl → .1 → .2 …，最多保留 _BACKUPS 份。"""
    oldest = path.with_name(f"{path.name}.{_BACKUPS}")
    oldest.unlink(missing_ok=True)
    for index in range(_BACKUPS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _append(line: str) -> None:
    path = C.DATA_DIR / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > _MAX_BYTES:
        _rotate(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def record(event: str, *, ok: bool = True, subject: str = "",
           detail: Optional[dict] = None, error: str = "",
           duration_s: Optional[float] = None) -> dict:
    """追加一条审计记录，返回同样的结构（便于调用方复用/断言）。"""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": str(event)[:60],
        "ok": bool(ok),
        "subject": str(subject or "")[:120],
        "detail": _sanitize(detail or {}),
        "error": str(error or "")[:300],
        "duration_s": (round(float(duration_s), 2)
                       if duration_s is not None else None),
        "pid": os.getpid(),
    }
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _LOCK:
            _append(line)
    except Exception:                                      # noqa: BLE001
        pass
    return entry
