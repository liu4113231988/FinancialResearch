"""DuckDB 行情缓存；短连接和文件锁允许 Web 与 CLI 交替访问。"""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import threading
import time

import duckdb
import pandas as pd
import market_research.audit as audit
import market_research.config as C
from market_research.errors import StorageBusy

LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()

# P2-30：锁等待可观测，health/日志据此判断请求是否被同步任务拖住
LOCK_STATS = {"waits": 0, "timeouts": 0, "total_wait_s": 0.0}


def _acquire_file_lock(file, timeout: float) -> float:
    """非阻塞轮询文件锁，返回本次等待秒数；超时抛 OSError。"""
    deadline = time.monotonic() + max(timeout, 0.0)
    started = time.monotonic()
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return time.monotonic() - started
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _release_file_lock(file) -> None:
    if os.name == "nt":
        import msvcrt
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _busy_error(limit: float) -> StorageBusy:
    return StorageBusy(
        f"数据库正被同步任务或其它进程写入，等待 {limit:.0f} 秒仍未获得锁；"
        "请稍后重试；若持续出现，请检查 /api/health 的 storage 状态与同步进程")


@contextmanager
def connection(timeout: float | None = None):
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    limit = C.STORAGE_LOCK_TIMEOUT if timeout is None else float(timeout)
    started = time.monotonic()
    if not _LOCK.acquire(timeout=max(limit, 0.001)):
        LOCK_STATS["timeouts"] += 1
        raise _busy_error(limit)
    try:
        with (C.DATA_DIR / "market.lock").open("a+b") as lock:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                _acquire_file_lock(lock, limit - (time.monotonic() - started))
            except OSError as exc:
                LOCK_STATS["timeouts"] += 1
                raise _busy_error(limit) from exc
            waited = time.monotonic() - started
            if waited > 0.5:
                LOCK_STATS["waits"] += 1
                LOCK_STATS["total_wait_s"] += waited
                LOG.warning("等待数据库锁 %.1fs（同步或其它进程正在写入）", waited)
            try:
                with duckdb.connect(str(C.DATA_DIR / "market.duckdb")) as db:
                    db.execute("CREATE TABLE IF NOT EXISTS datasets (name VARCHAR PRIMARY KEY, metadata VARCHAR, schema_json VARCHAR, revision VARCHAR)")
                    db.execute("CREATE TABLE IF NOT EXISTS prices (dataset VARCHAR, date TIMESTAMP_NS, asset VARCHAR, value DOUBLE, PRIMARY KEY(dataset, date, asset))")
                    yield db
            finally:
                _release_file_lock(lock)
    finally:
        _LOCK.release()


def metadata(name):
    with connection() as db:
        row = db.execute("SELECT metadata FROM datasets WHERE name=?", [name]).fetchone()
    return json.loads(row[0]) if row else None


def read(name):
    with connection() as db:
        row = db.execute("SELECT schema_json FROM datasets WHERE name=?", [name]).fetchone()
        if row is None:
            return None
        frame = db.execute("SELECT date, asset, value FROM prices WHERE dataset=? ORDER BY date", [name]).df()
    schema = json.loads(row[0])
    result = frame.pivot(index="date", columns="asset", values="value").reindex(columns=schema["columns"])
    result.columns.name = None
    result.index.name = schema["index_name"]
    result.index = result.index.as_unit(schema["index_unit"])
    return result.astype(schema["dtypes"])


def write(name, frame, meta):
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates or frame.index.hasnans or frame.index.tz is not None:
        raise ValueError("行情必须使用唯一的日期索引")
    if frame.empty or frame.columns.has_duplicates or not all(isinstance(c, str) for c in frame.columns):
        raise ValueError("行情不能为空，列名必须为唯一字符串")
    schema = json.dumps({"columns": list(frame.columns), "index_name": frame.index.name, "index_unit": frame.index.unit,
                         "dtypes": {c: str(t) for c, t in frame.dtypes.items()}})
    values = frame.rename_axis("date").reset_index().melt(id_vars="date", var_name="asset", value_name="value")
    revision = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes() + schema.encode() + json.dumps({k:v for k,v in meta.items() if k != "info"}, sort_keys=True).encode()).hexdigest()
    with connection() as db:
        db.register("incoming", values)
        db.execute("BEGIN")
        try:
            db.execute("DELETE FROM prices WHERE dataset=?", [name])
            db.execute("INSERT INTO prices SELECT ?, date, asset, value FROM incoming", [name])
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?, ?, ?, ?)", [name, json.dumps(meta), schema, revision])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            audit.record("data.write", ok=False, subject=name,
                         detail={"rows": int(len(frame)),
                                 "columns": int(len(frame.columns))},
                         error="写入事务回滚")
            raise
    # P2-26：数据写入留审计轨迹（只记规模与指纹，不记行情数值）
    audit.record("data.write", subject=name,
                 detail={"rows": int(len(frame)), "columns": int(len(frame.columns)),
                         "revision": revision[:12]})


def write_metadata(name, meta):
    with connection() as db:
        db.execute("UPDATE datasets SET metadata=? WHERE name=?", [json.dumps(meta), name])


def update_info(name, info):
    """Merge descriptive fields under the same lock as market-data writes."""
    with connection() as db:
        row = db.execute("SELECT metadata FROM datasets WHERE name=?", [name]).fetchone()
        if row is None:
            return
        meta = json.loads(row[0])
        meta.setdefault("info", {}).update(info)
        db.execute("UPDATE datasets SET metadata=? WHERE name=?", [json.dumps(meta), name])


def stamp(*, exclude_stocks=False, fund_code=None, fetch_start=None, end=None):
    """数据指纹：结果缓存键与运行记录共用的"数据侧版本号"。

    ★ 依赖收窄（P1 修复轮）：传入 `fetch_start`（= fetch_start_for(分析起点)）
      后，基金命名空间只统计**本基金、本抓取窗口**的缓存，候选指数池只统计
      **本窗口**的缓存 —— `load_panel` 实际只会读这几个数据集，其它窗口的
      重复缓存被同步刷新也不该让结果缓存失效。不传时保持旧的保守口径
      （全部纳入），未知数据集永远保守纳入。
    """
    with connection() as db:
        rows = db.execute("SELECT name, revision, metadata FROM datasets ORDER BY name").fetchall()
    if exclude_stocks:
        rows = [row for row in rows
                if not row[0].startswith(("market_v1_stock_", "sync_history_stock_"))]
    if fund_code is not None:
        if fetch_start:
            # 只保留本基金本窗口的净值/同步历史（sync_history 无窗口段，
            # 按基金前缀匹配即可）
            rows = [row for row in rows
                    if not row[0].startswith(("fund_nav_", "sync_history_fund_"))
                    or row[0].startswith((f"fund_nav_{fund_code}_{fetch_start}",
                                          f"sync_history_fund_{fund_code}_"))]
        else:
            rows = [row for row in rows
                    if not row[0].startswith(("fund_nav_", "sync_history_fund_"))
                    or row[0].startswith((f"fund_nav_{fund_code}_", f"sync_history_fund_{fund_code}_"))]
    if fetch_start is not None:
        # 共享指数池收窄到本抓取窗口对应的具体缓存名（load_panel 的读法一致）
        keep = {f"index_universe_{fetch_start}"}
        if end:
            keep.add(f"index_universe_{fetch_start}_to{end}")
        rows = [row for row in rows
                if not row[0].startswith("index_universe_")
                or row[0] in keep]
    # Metadata-only basis/version edits must also invalidate dependent results.
    # 单条损坏记录不能让全库指纹计算崩掉（结果缓存键会被所有分析入口调用）。
    revisions = []
    for name, revision, meta in rows:
        try:
            parsed = json.loads(meta)
            if not isinstance(parsed, dict):
                raise ValueError("metadata 不是对象")
        except (TypeError, ValueError):
            LOG.warning("数据指纹跳过损坏的元数据 %s", name)
            parsed = {"__corrupt__": str(meta)[:120]}
        revisions.append((name, revision,
                          {k: v for k, v in parsed.items() if k != "info"}))
    return hashlib.sha256(json.dumps(revisions, sort_keys=True).encode()).hexdigest()


def prune_universe_windows(keep) -> list:
    """删除与当前抓取窗口不符的候选指数池缓存（重复窗口，可随时重抓）。

    load_index_universe 的缓存名带窗口（`index_universe_{start}[_to{end}]`），
    显式自定义窗口会在 DuckDB 里留下一次性窗口的整份指数缓存。返回被删除的
    数据集名；只动 `index_universe_` 命名空间，其它数据集一律不碰。
    """
    keep = set(keep)
    with connection() as db:
        rows = db.execute("SELECT name FROM datasets WHERE starts_with(name, 'index_universe_')").fetchall()
        stale = [r[0] for r in rows if r[0] not in keep]
        for name in stale:
            db.execute("DELETE FROM prices WHERE dataset=?", [name])
            db.execute("DELETE FROM datasets WHERE name=?", [name])
    return stale


def summary():
    """运行记录使用的数据库统计，与旧 CSV 备份数量无关。"""
    with connection() as db:
        total, funds, indices = db.execute(
            "SELECT count(*), count(*) FILTER (WHERE starts_with(name, 'fund_nav_')), "
            "count(*) FILTER (WHERE starts_with(name, 'index_universe_')) FROM datasets"
        ).fetchone()
        rows = db.execute("SELECT count(*) FROM prices").fetchone()[0]
    return {"storage": "duckdb", "n_datasets": total, "n_fund_nav_cached": funds,
            "n_index_cache": indices, "n_price_rows": rows}
