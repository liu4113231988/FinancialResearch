"""Runtime configuration shared by API, workers and scheduled sync."""
from dataclasses import dataclass
import os
from pathlib import Path

from market_research import envfile

envfile.load()  # FIL_ENV_FILE 未设置时是空操作


def home_dir():
    default = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file()), Path.cwd())
    return Path(os.environ.get("FIL_HOME", default)).resolve()


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8760
    max_jobs: int = 2
    queue_limit: int = 100
    job_timeout: int = 900
    max_attempts: int = 3
    job_terminal_ttl: int = 86400
    job_queued_ttl: int = 3600
    job_drain_grace: int = 30
    auto_services: bool = True
    job_workers: int = 0
    job_workers_max: int = 0
    job_memory_mb: int = 0
    sync_asset_timeout: float = 300.0

    def validate(self):
        """启动期 schema 校验：尽早拒绝明显非法的运行配置。"""
        if not 1 <= self.port <= 65535:
            raise ValueError("FIL_PORT 必须是 1~65535")
        if not self.host.strip():
            raise ValueError("FIL_HOST 不能为空")
        for name in ("max_jobs", "queue_limit", "job_timeout", "max_attempts",
                     "job_terminal_ttl", "job_queued_ttl", "sync_asset_timeout"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        for name in ("job_drain_grace", "job_workers", "job_workers_max",
                     "job_memory_mb"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负数")
        return self

    @classmethod
    def load(cls):
        # P3-S：int/float 裸解析抛 "invalid literal for int()" —— validate()
        # 精心写的字段级友好文案永远到不了。这里给出字段名，失败仍是
        # fail-fast 非零退出，但错误可定位。
        def _int(name, default):
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"{name} 必须是整数，收到 {raw!r}") from None

        def _float(name, default):
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{name} 必须是数值，收到 {raw!r}") from None

        return cls(host=os.environ.get("FIL_HOST", "127.0.0.1"),
                   port=_int("FIL_PORT", 8760),
                   max_jobs=_int("FIL_MAX_JOBS", 2),
                   queue_limit=_int("FIL_QUEUE_LIMIT", 100),
                   job_timeout=_int("FIL_JOB_TIMEOUT", 900),
                   max_attempts=_int("FIL_JOB_ATTEMPTS", 3),
                   job_terminal_ttl=_int("FIL_JOB_TERMINAL_TTL", 86400),
                   job_queued_ttl=_int("FIL_JOB_QUEUED_TTL", 3600),
                   job_drain_grace=_int("FIL_JOB_DRAIN_GRACE", 30),
                   auto_services=os.environ.get("FIL_AUTO_SERVICES", "1") != "0",
                   job_workers=_int("FIL_JOB_WORKERS", 0),
                   job_workers_max=_int("FIL_JOB_WORKERS_MAX", 0),
                   job_memory_mb=_int("FIL_JOB_MEMORY_MB", 0),
                   sync_asset_timeout=_float("FIL_SYNC_ASSET_TIMEOUT", 300)).validate()
