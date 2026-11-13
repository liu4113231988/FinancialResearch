"""应用日志：按大小轮转、保留份数，Windows 下不长期占用句柄。

借鉴 audit.py 的做法：写入时「追加、立即关闭」，不保留常驻文件句柄，
因此轮转（重命名/删除）不会被本进程自己的打开句柄阻塞。日志目录与
API/worker/sync 的既有 output/data 约定一致，可用 FIL_* 环境变量覆盖。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import market_research.config as C

_LOCK = threading.Lock()
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def log_dir() -> Path:
    return Path(os.environ.get("FIL_LOG_DIR", C.DATA_DIR)).resolve()


def max_bytes() -> int:
    return _int_env("FIL_LOG_MAX_BYTES", 5_000_000)


def backups() -> int:
    return _int_env("FIL_LOG_BACKUPS", 3)


def retention_days() -> int:
    return _int_env("FIL_LOG_RETENTION_DAYS", 7)


def level() -> int:
    name = os.environ.get("FIL_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


def rotate(path: Path, *, limit: int | None = None, keep: int | None = None) -> bool:
    """超过上限时把 path → .1 → … → .N；返回是否发生了轮转。"""
    path = Path(path)
    limit = max_bytes() if limit is None else limit
    keep = backups() if keep is None else keep
    try:
        if not path.exists() or path.stat().st_size < limit:
            return False
        path.with_name(f"{path.name}.{keep}").unlink(missing_ok=True)
        for index in range(keep - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        # 文件正被其它进程以不可重命名的句柄打开（Windows）：保持追加写入，
        # 下次启动再轮转，不让日志失败影响主流程。
        return False


def prune_job_logs(directory: Path | None = None, *, days: int | None = None) -> list[str]:
    """清理超过保留期的任务日志，返回被删除的文件名。"""
    directory = Path(directory or log_dir())
    days = retention_days() if days is None else days
    cutoff = time.time() - days * 86400
    removed = []
    try:
        for path in directory.glob("job-*.log*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed.append(path.name)
            except OSError:
                continue
    except OSError:
        pass
    return removed


class RotatingAppendHandler(logging.Handler):
    """每次写入打开、写完关闭；超过大小上限先轮转。"""

    def __init__(self, path: Path, *, limit: int | None = None,
                 keep: int | None = None):
        super().__init__()
        self.path = Path(path)
        self.limit = limit
        self.keep = keep

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with _LOCK:
                rotate(self.path, limit=self.limit, keep=self.keep)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:                                  # noqa: BLE001
            self.handleError(record)

    def close(self) -> None:
        # 无常驻句柄，无需额外释放；保留 logging 的锁语义。
        super().close()


def configure(name: str, *, path: Path | None = None,
              limit: int | None = None, keep: int | None = None,
              level_override: int | None = None) -> logging.Logger:
    """为进程安装唯一的轮转文件 handler；重复调用是幂等的。"""
    path = Path(path or (log_dir() / f"{name}.log"))
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_market_research_log", False):
            root.removeHandler(handler)
            handler.close()
    handler = RotatingAppendHandler(path, limit=limit, keep=keep)
    handler._market_research_log = True                      # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.setLevel(level_override if level_override is not None else level())
    root.addHandler(handler)
    return logging.getLogger(name)
