"""任务阶段检查点：长任务崩溃重试时跳过已完成阶段（P3-F）。

粒度是阶段（数据、净值、相关性、模型结果），不做行级；检查点按任务 id
持久化在 `FIL_DATA_DIR/checkpoints/<jid>/`。创建时写入本次运行的指纹
（操作、参数、数据版本），重启后指纹不一致或文件损坏一律丢弃并从头执行，
避免用可疑状态续跑。任务成功后删除；失败的保留给下一次尝试，并按保留期
清理（默认 2 天）。
"""
from __future__ import annotations

import json
import os
import pickle
import shutil
import time
from pathlib import Path

import market_research.config as C
from market_research.data.files import _atomic_pickle_dump


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def root() -> Path:
    return Path(os.environ.get("FIL_CHECKPOINT_DIR", C.DATA_DIR / "checkpoints"))


def ttl_days() -> int:
    return _int_env("FIL_CHECKPOINT_TTL_DAYS", 2)


class NullCheckpoint:
    """不需要检查点的操作（行情/相关性等）用它占位，调用方无需分支。"""

    def load(self, stage: str):
        return None

    def save(self, stage: str, value) -> None:
        return None

    def clear(self) -> None:
        return None


class StageCheckpoint:
    def __init__(self, jid, fingerprint, *, base: Path | None = None):
        self.base = Path(base or root())
        safe = "".join(char if char.isalnum() or char in "._-" else "_"
                       for char in str(jid))
        self.dir = self.base / safe
        self.fingerprint = json.dumps(fingerprint, sort_keys=True,
                                      default=str, ensure_ascii=False)
        self._prepare()

    def _prepare(self) -> None:
        self._prune()
        if self.dir.exists():
            try:
                existing = (self.dir / "fingerprint.json").read_text(encoding="utf-8")
            except OSError:
                existing = None
            if existing != self.fingerprint:
                # 参数、窗口或数据版本变了：旧的阶段产物不可信，整体丢弃。
                shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "fingerprint.json").write_text(self.fingerprint,
                                                   encoding="utf-8")

    def _prune(self) -> None:
        days = ttl_days()
        if not days:
            return
        cutoff = time.time() - days * 86400
        try:
            for path in self.base.iterdir():
                if path == self.dir or not path.is_dir():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    continue
        except OSError:
            pass

    def _path(self, stage: str) -> Path:
        return self.dir / f"{stage}.pkl"

    def load(self, stage: str):
        path = self._path(stage)
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                return pickle.load(handle)
        except Exception:                                     # noqa: BLE001
            path.unlink(missing_ok=True)
            return None

    def save(self, stage: str, value) -> None:
        try:
            _atomic_pickle_dump(value, self._path(stage))
        except Exception:                                     # noqa: BLE001
            # 检查点只是加速手段，写失败不应影响主计算。
            pass

    def clear(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
