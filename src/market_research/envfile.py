"""可选的 .env 读取：仅在设置 FIL_ENV_FILE 时生效，不改变默认启动行为。

解析规则保持简单：每行一个 KEY=VALUE，忽略空行与整行注释，允许 `export `
前缀与成对引号；不覆盖进程里已经存在的环境变量，命令行优先级最高。
"""
from __future__ import annotations

import os
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load(path: str | os.PathLike[str] | None = None) -> bool:
    """把 .env 写入 os.environ；已存在的变量不覆盖，文件不存在时静默跳过。"""
    target = path or os.environ.get("FIL_ENV_FILE")
    if not target:
        return False
    file = Path(target).expanduser()
    if not file.is_file():
        return False
    for key, value in parse(file.read_text(encoding="utf-8-sig", errors="replace")).items():
        os.environ.setdefault(key, value)
    return True
