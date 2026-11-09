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

    def validate(self):
        """启动期 schema 校验：尽早拒绝明显非法的运行配置。"""
        if not 1 <= self.port <= 65535:
            raise ValueError("FIL_PORT 必须是 1~65535")
        if not self.host.strip():
            raise ValueError("FIL_HOST 不能为空")
        for name in ("max_jobs", "queue_limit", "job_timeout", "max_attempts",
                     "job_terminal_ttl", "job_queued_ttl"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        for name in ("job_drain_grace", "job_workers", "job_workers_max",
                     "job_memory_mb"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负数")
        return self

    @classmethod
    def load(cls):
        return cls(host=os.environ.get("FIL_HOST", "127.0.0.1"),
                   port=int(os.environ.get("FIL_PORT", "8760")),
                   max_jobs=int(os.environ.get("FIL_MAX_JOBS", "2")),
                   queue_limit=int(os.environ.get("FIL_QUEUE_LIMIT", "100")),
                   job_timeout=int(os.environ.get("FIL_JOB_TIMEOUT", "900")),
                   max_attempts=int(os.environ.get("FIL_JOB_ATTEMPTS", "3")),
                   job_terminal_ttl=int(os.environ.get("FIL_JOB_TERMINAL_TTL", "86400")),
                   job_queued_ttl=int(os.environ.get("FIL_JOB_QUEUED_TTL", "3600")),
                   job_drain_grace=int(os.environ.get("FIL_JOB_DRAIN_GRACE", "30")),
                   auto_services=os.environ.get("FIL_AUTO_SERVICES", "1") != "0",
                   job_workers=int(os.environ.get("FIL_JOB_WORKERS", "0")),
                   job_workers_max=int(os.environ.get("FIL_JOB_WORKERS_MAX", "0")),
                   job_memory_mb=int(os.environ.get("FIL_JOB_MEMORY_MB", "0"))).validate()
