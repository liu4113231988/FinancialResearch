from __future__ import annotations
import json, logging, time
from pathlib import Path
from typing import Optional
import pandas as pd
import market_research.config as C
from market_research.data import storage
from market_research.errors import CacheMiss

LOG = logging.getLogger(__name__)

def data_stamp(*, exclude_stocks=False, fund_code=None,
               fetch_start=None, end=None) -> str:
    """内容指纹；可隔离股票/其他基金，传 fetch_start/end 后进一步收窄到
    本基金本抓取窗口的实际依赖（见 storage.stamp）。"""
    return storage.stamp(exclude_stocks=exclude_stocks, fund_code=fund_code,
                         fetch_start=fetch_start, end=end)


def _cache_meta_path(name: str) -> Path:
    return C.DATA_DIR / f"{name}.meta.json"


def _fingerprint(meta: dict) -> dict:
    """
    只取**参与有效性判等**的字段。

    ★ 必须把 "info" 排除掉（N-07）：info 里放的是抓取时间、数据末日、行数、
    缺失资产这类**描述性**信息，它们每次抓取都会变。若让它们参与相等比较，
    缓存会每次都判为失效 → 每次全量重建，等于没有缓存。
    """
    return {k: v for k, v in (meta or {}).items() if k != "info"}


def _safe_metadata(name: str):
    """损坏的 metadata 不能把一次冷缓存请求打成 500；按"没有元数据"处理。"""
    try:
        return storage.metadata(name)
    except Exception as exc:                                  # noqa: BLE001
        LOG.warning("缓存 %s 元数据损坏，按未命中处理：%s: %s",
                    name, type(exc).__name__, str(exc)[:180])
        return None


def _safe_read(name: str):
    """损坏的行情缓存按未命中处理，由后续 builder 重建覆盖（自愈）。"""
    try:
        return storage.read(name)
    except Exception as exc:                                  # noqa: BLE001
        LOG.warning("缓存 %s 读取失败，按未命中处理：%s: %s",
                    name, type(exc).__name__, str(exc)[:180])
        return None


def _cache_meta_valid(name: str, meta: dict) -> bool:
    """优先校验数据库口径；仅为旧 CSV 迁移读取 sidecar。"""
    saved = _safe_metadata(name)
    if saved is None:
        fp = _cache_meta_path(name)
        if not fp.exists():
            return True
        try:
            saved = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
    return _fingerprint(saved) == _fingerprint(meta)


def _cache_meta_write(name: str, meta: dict):
    if _safe_metadata(name) is None:
        _cache_meta_path(name).write_text(json.dumps(meta), encoding="utf-8")
    else:
        storage.write_metadata(name, meta)


def cache_info(name: str) -> dict:
    return (_safe_metadata(name) or {}).get("info", {}) or {}


def _cache_info_write(name: str, info: dict):
    # 描述性字段写坏了不该阻断主流程（尤其 metadata 已损坏时）。
    try:
        storage.update_info(name, info)
    except Exception:                                         # noqa: BLE001
        LOG.warning("缓存 %s 描述信息写入失败（不影响数据）", name, exc_info=True)


def _cached(name: str, builder, meta: Optional[dict] = None, refresh=False,
            allow_fetch: bool = True):
    """DuckDB 为唯一写入目标；兼容导入旧 CSV，刷新失败保留上一份数据。

    allow_fetch=False 表示"只读"：缓存未命中时抛 `CacheMiss`，绝不调用
    builder（builder 通常就是联网抓取），也不做旧 CSV 迁移写库。
    仅供请求线程内的轻量接口使用。

    metadata / 行情损坏时按未命中处理：可抓取则由 builder 重建并覆盖坏记录
    （自愈），只读则抛 CacheMiss 让上层返回 202，而不是把冷缓存打成 500。
    """
    if not refresh and (meta is None or _cache_meta_valid(name, meta)):
        frame = _safe_read(name)
        if frame is not None:
            return frame
        # 旧 CSV 迁移本身要写库：只读请求线程不做这件事，直接按未命中处理。
        legacy = C.DATA_DIR / f"{name}.csv" if allow_fetch else None
        if legacy is not None and legacy.exists():
            try:
                frame = pd.read_csv(legacy, index_col=0, parse_dates=True)
                if len(frame):
                    saved = _cache_meta_path(name)
                    old_meta = (json.loads(saved.read_text(encoding="utf-8"))
                                if saved.exists() else {})
                    storage.write(name, frame, _with_info(meta or old_meta, frame))
                    return frame
            except Exception as exc:                          # noqa: BLE001
                LOG.warning("旧 CSV 缓存 %s 无法导入，将重新抓取：%s: %s",
                            name, type(exc).__name__, str(exc)[:180])
    if not allow_fetch:
        raise CacheMiss(f"缓存 {name} 未就绪（只读模式不抓取）")
    try:
        frame = builder()
    except Exception as exc:
        # P2-27：数据源故障不回退到「口径已过期」的旧缓存（如旧版 total
        # 缓存里其实是 cum 数据）；只有 meta 仍匹配时才降级使用旧数据，
        # 并把 stale/source_error 挂到返回值上，由统一 data_quality 透传。
        previous = (_safe_read(name)
                    if meta is None or _cache_meta_valid(name, meta) else None)
        if previous is None or not len(previous):
            raise
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        LOG.warning("数据源抓取失败，沿用本地缓存 %s：%s", name, error)
        previous.attrs["stale"] = True
        previous.attrs["source_error"] = error
        previous.attrs["cached_at"] = cache_info(name).get("fetched_at")
        _cache_info_write(name, {
            "last_error": error,
            "last_error_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return previous
    storage.write(name, frame, _with_info(meta or {}, frame))
    return frame


def _with_info(meta: dict, df) -> dict:
    """
    给 meta 补上描述性 info（N-07）：抓取时间、数据末日、行数。

    这些字段**不参与** _cache_meta_valid 的判等（见 _fingerprint），
    否则每次抓取时间不同都会让缓存失效。
    """
    meta = dict(meta or {})
    info = dict(meta.get("info") or {})
    info.setdefault("fetched_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    if isinstance(df, pd.DataFrame) and len(df.index):
        try:
            info.setdefault("data_end", str(pd.Timestamp(df.index[-1]).date()))
            info.setdefault("data_start", str(pd.Timestamp(df.index[0]).date()))
            info.setdefault("n_rows", int(len(df)))
            info.setdefault("n_cols", int(df.shape[1]))
        except (TypeError, ValueError, IndexError):        # noqa: BLE001
            pass
    meta["info"] = info
    return meta
