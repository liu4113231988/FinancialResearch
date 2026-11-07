"""进程级资源预算：CPU 配额、单任务 worker 数、单任务内存上限（P3-I）。

并发任务上限（FIL_MAX_JOBS）、队列上限（FIL_QUEUE_LIMIT）与单任务超时
（FIL_JOB_TIMEOUT）已由 settings/worker 提供；这里补齐每个任务内部的
并行度与内存约束，避免 N 个并发任务各自按整机 CPU 开池导致超卖。
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from market_research.settings import Settings


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_int(path: str) -> int:
    try:
        return int(Path(path).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def cpu_limit() -> int:
    """可用 CPU 数：优先容器 cgroup 配额，其次进程亲和性，最后物理核数。"""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(
            encoding="ascii").split()[:2]
        if quota not in ("max", "0") and int(period) > 0:
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError, IndexError):
        pass
    quota = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota > 0 and period > 0:
        return max(1, math.ceil(quota / period))
    if hasattr(os, "sched_getaffinity"):
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def model_workers(total_models: int) -> int:
    """单任务并行模型进程数。

    默认 0=auto：min(模型数, CPU-1)，其中 CPU 取 cgroup/亲和性口径；
    `FIL_JOB_WORKERS` 可写死，`FIL_JOB_WORKERS_MAX` 是全局上限（0=不限）。
    """
    settings = Settings.load()
    requested = settings.job_workers
    workers = requested if requested > 0 else max(1, cpu_limit() - 1)
    workers = min(workers, max(1, int(total_models)))
    if settings.job_workers_max > 0:
        workers = min(workers, settings.job_workers_max)
    return max(1, workers)


def job_memory_mb() -> int:
    return max(0, Settings.load().job_memory_mb)


def apply_memory_limit(mb: int | None = None) -> int:
    """在计算子进程内施加内存上限，返回生效字节数（0=未限制）。

    POSIX 用 ``RLIMIT_AS``（超限抛 MemoryError，由 worker 映射为可读错误）；
    Windows 由计算进程的 Job Object 在进程级限制（见 processes.contain_children）。
    """
    mb = job_memory_mb() if mb is None else mb
    if mb <= 0:
        return 0
    limit = mb * 1024 * 1024
    if os.name == "nt":
        return limit
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):
        return 0
    return limit


def limits_snapshot() -> dict:
    """供就绪探针展示当前进程级资源上限。"""
    settings = Settings.load()
    return {
        "cpu": cpu_limit(),
        "max_jobs": settings.max_jobs,
        "queue_limit": settings.queue_limit,
        "job_timeout_s": settings.job_timeout,
        "job_workers": settings.job_workers or "auto",
        "job_workers_max": settings.job_workers_max or None,
        "job_memory_mb": settings.job_memory_mb or None,
    }
