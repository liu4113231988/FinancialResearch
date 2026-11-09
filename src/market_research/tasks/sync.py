"""Only synchronize registered assets; durable state, daily Shanghai schedule.

Run with the web service, or independently: python -m market_research.tasks.sync.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from importlib.resources import files
import threading

import numpy as np
import pandas as pd

import market_research.config as C
import market_research.data.storage as storage
from market_research import logs

LOG = logging.getLogger(__name__)
SHANGHAI = timezone(timedelta(hours=8))
HISTORY_START = "1990-01-01"


def now():
    return datetime.now(SHANGHAI)


def schedule_time():
    value = os.environ.get("FIL_SYNC_TIME", "18:30")
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as error:
        raise ValueError("FIL_SYNC_TIME 必须为 HH:MM，例如 18:30") from error


def migrate():
    with storage.connection() as db:
        for path in sorted((files("market_research.data") / "migrations").iterdir()):
            db.execute(path.read_text(encoding="utf-8"))


def register(kind, code, basis=""):
    if kind not in ("stock", "index", "fund"):
        raise ValueError("未知同步类型")
    if kind == "fund":
        if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
            raise ValueError("基金代码必须为六位数字")
        basis = basis or "total"
        if basis not in ("total", "cum"):
            raise ValueError("未知净值口径")
    else:
        from market_research.market.analysis import normalize_code
        # SW industry indices are already used by fund analysis.
        if not (kind == "index" and code in C.SW_INDUSTRY):
            code = normalize_code(kind, code)
        if kind == "index":
            basis = ""
        elif basis not in ("", "qfq", "hfq"):
            raise ValueError("未知复权口径")
    key = f"{kind}:{code}:{basis}"
    migrate()
    with storage.connection() as db:
        db.execute("INSERT INTO sync_assets(asset_key,kind,code,basis) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                   [key, kind, code, basis])
    return key


def datasets():
    with storage.connection() as db:
        rows = db.execute("SELECT name,metadata,schema_json FROM datasets").fetchall()
    out = []
    for name, meta, schema in rows:
        try:
            out.append((name, json.loads(meta), json.loads(schema)["columns"]))
        except (TypeError, ValueError, KeyError):
            # 一条损坏记录不该让 discover() / 同步刷新整批失败。
            LOG.warning("跳过损坏的数据集描述 %s（不影响其它资产）", name)
    return out


def discover():
    """Import only assets actually stored, never the global catalogue."""
    known = {a["asset_key"] for a in assets()}

    def add(kind, code, basis=""):
        key = f"{kind}:{code}:{basis}"
        if key not in known:
            known.add(register(kind, code, basis))

    for name, meta, columns in datasets():
        try:
            if name.startswith("market_v1_"):
                add(meta["kind"], meta["code"], meta.get("adjust", ""))
            elif name.startswith("fund_nav_"):
                add("fund", meta["code"], meta.get("basis", "cum"))
            elif name.startswith("index_universe_"):
                for code in columns:
                    add("index", code)
        except (KeyError, ValueError):
            LOG.warning("不能识别历史数据集 %s", name)


def assets():
    migrate()
    with storage.connection() as db:
        cursor = db.execute("SELECT * FROM sync_assets ORDER BY asset_key")
        names = [x[0] for x in cursor.description]
        result = [dict(zip(names, row)) for row in cursor.fetchall()]
    for asset in result:
        if not asset.get("state"):
            asset["state"] = ("error" if asset["error"] else "running" if asset["last_attempt"] and
                              (not asset["last_success"] or asset["last_attempt"] > asset["last_success"])
                              else "success" if asset["last_success"] else "pending")
    return result


def request_sync(key):
    """Durable request; active work is never duplicated or reset."""
    migrate()
    with storage.connection() as db:
        row = db.execute("SELECT state,last_attempt,last_success,error FROM sync_assets WHERE asset_key=?", [key]).fetchone()
        if not row:
            raise ValueError("资产尚未添加")
        state, attempt, success, error = row
        if state == "running" and attempt and (now() - datetime.fromisoformat(attempt)).total_seconds() < 1800:
            return False
        db.execute("UPDATE sync_assets SET state='pending', requested_at=?,last_attempt=NULL,error=NULL WHERE asset_key=?", [now().isoformat(), key])
    return True


def history_name(asset):
    return "sync_history_" + asset["asset_key"].replace(":", "_")


def cached(kind, code, basis, start, end, fresh=True):
    """Canonical histories serve analysis windows after successful sync.

    fresh=False 只要求数据存在：预测结算等回溯用途不需要“同步到今天”的
    新鲜度门槛，否则检查日早于同步日时会被误判成没有数据。
    """
    key = f"{kind}:{code}:{basis}"
    name = history_name({"asset_key": key})
    try:
        meta = storage.metadata(name)
    except Exception as exc:                                  # noqa: BLE001
        LOG.warning("同步缓存 %s 元数据损坏，视为未就绪：%s: %s",
                    name, type(exc).__name__, str(exc)[:180])
        return None
    if not meta:
        return None
    if fresh:
        target = end or now().date().isoformat()
        through = str(meta.get("through", ""))
        if through < target:
            return None
        # ★ through 只记录"本次同步检查到哪天"，不代表源站真给到了那天的行情。
        #   源站滞后时 data_end 会明显早于 through；只凭 through 判新鲜会把旧行情
        #   当成新数据。允许跨周末/长假的正常滞后（阈值与数据 stale 判定一致），
        #   超出则视为缓存未就绪，交回正常抓取 / 冷缓存 202 路径。
        data_end = str((meta.get("info") or {}).get("data_end") or "")
        if data_end:
            try:
                lag = (pd.Timestamp(through) - pd.Timestamp(data_end)).days
            except (TypeError, ValueError):
                lag = 0
            if lag > C.DATA_STALE_DAYS:
                LOG.info("同步缓存 %s 的数据末日 %s 落后检查日 %s 共 %d 天，视为未就绪",
                         name, data_end, through, lag)
                return None
    if (start or C.START_DATE) < HISTORY_START:
        return None
    try:
        frame = storage.read(name)
    except Exception as exc:                                  # noqa: BLE001
        LOG.warning("同步缓存 %s 读取失败，视为未就绪：%s: %s",
                    name, type(exc).__name__, str(exc)[:180])
        return None
    if frame is None:
        return None
    frame = frame.loc[start or C.START_DATE:end].copy()
    frame.attrs.update(meta.get("info", {}))
    return frame if len(frame) else None


def due(asset, current):
    attempt = asset.get("last_attempt")
    if attempt and (current - datetime.fromisoformat(attempt)).total_seconds() < 1800:
        return False
    if asset.get("state") == "pending":
        return True
    success = asset.get("last_success")
    if not success:
        return True
    scheduled = datetime.combine(current.date(), schedule_time(), SHANGHAI)
    if current < scheduled:
        scheduled -= timedelta(days=1)
    return datetime.fromisoformat(success) < scheduled


def fetch(asset, start, end):
    import market_research.data.marketdata as D
    kind, code, basis = asset["kind"], asset["code"], asset["basis"]
    if kind == "fund":
        series = D.fetch_fund_total_return(code, start) if basis == "total" else D.fetch_fund_nav(code, start)
        if series.attrs.get("degraded"):
            raise ValueError("总回报构造降级，保留上一份数据")
        return series.to_frame()
    if kind == "index" and code in C.SW_INDUSTRY:
        return D.fetch_index_sw(code, start).to_frame(name="close")
    from market_research.market.analysis import load_bars
    return load_bars(kind, code, start, end, basis, refresh=True, _fetch_only=True)


def _refresh_window_caches(asset, frame):
    """用 canonical 正史刷新既有窗口，不扩大任何同步资产池。"""
    import market_research.data.marketdata as D
    for target, saved, columns in datasets():
        if asset["kind"] == "fund" and target.startswith("fund_nav_") and saved.get("code") == asset["code"] and saved.get("basis", "cum") == asset["basis"]:
            sliced = frame.loc[saved.get("start"):].copy()
            sliced.columns = columns
        elif target.startswith("market_v1_") and saved.get("kind") == asset["kind"] and saved.get("code") == asset["code"] and saved.get("adjust", "") == asset["basis"]:
            sliced = frame.loc[saved.get("start"):saved.get("end")]
        elif asset["kind"] == "index" and target.startswith("index_universe_") and asset["code"] in columns:
            sliced = storage.read(target)
            values = frame.close.loc[saved.get("start"):saved.get("end")]
            sliced = sliced.reindex(sliced.index.union(values.index))
            sliced[asset["code"]] = values.reindex(sliced.index)
        else:
            continue
        if len(sliced):
            storage.write(target, sliced, D._with_info({k:v for k,v in saved.items() if k != "info"}, sliced))


def has_history(kind, code, basis):
    """是否已有 canonical 正史；损坏元信息按未就绪处理。"""
    name = history_name({"asset_key": f"{kind}:{code}:{basis}"})
    try:
        return bool(storage.metadata(name))
    except Exception:
        return False


def overlay_refresh(kind, code, basis, incoming, end=None):
    """把不复权资产的手动刷新窗口合并进已有 canonical。

    没有 canonical 时不创建部分历史；此时普通读取会自然命中刚刷新的窗口缓存。
    基金与复权行情可能重订整个历史，只允许完整覆盖，不能拼接不同基准。
    """
    asset = {"asset_key": f"{kind}:{code}:{basis}", "kind": kind,
             "code": code, "basis": basis}
    name = history_name(asset)
    old = storage.read(name)
    if old is None or old.empty:
        return False
    if incoming is None or incoming.empty:
        raise ValueError("数据源返回空历史，保留旧数据")
    incoming = incoming.loc[:end].sort_index()
    if incoming.empty or not np.isfinite(incoming.to_numpy(dtype=float)[~pd.isna(incoming.to_numpy(dtype=float))]).all():
        raise ValueError("历史数据包含非法数值")
    if (kind == "fund" or basis) and (
            incoming.index[0] > old.index[0] or len(old.index.difference(incoming.index))):
        raise ValueError("基金或复权行情必须完整刷新，拒绝拼接部分历史")
    frame = pd.concat([old.loc[~old.index.isin(incoming.index)], incoming]).sort_index()
    previous = storage.metadata(name) or {}
    through = max(str(previous.get("through", "")), str(end or now().date().isoformat()))
    info = {**(previous.get("info") or {}), **incoming.attrs,
            "fetched_at": now().isoformat(),
            "data_end": frame.index[-1].date().isoformat()}
    storage.write(name, frame, {"kind": kind, "code": code, "basis": basis,
                                "through": through, "info": info})
    _refresh_window_caches(asset, frame)
    return True


def synchronize(asset, current, end=None):
    end = end or current.date().isoformat()
    name = history_name(asset)
    old = storage.read(name)
    # Full adjusted history is intentional: distributions and provider corrections
    # can revise old prices without a reliable corporate-action feed.
    full = old is None or asset["kind"] == "fund" or bool(asset["basis"])
    start = HISTORY_START if full else max(HISTORY_START, (old.index[-1] - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
    incoming = fetch(asset, start, end)
    if incoming is None or incoming.empty:
        raise ValueError("数据源返回空历史，保留旧数据")
    incoming = incoming.loc[:end].sort_index()
    if incoming.empty or not np.isfinite(incoming.to_numpy(dtype=float)[~pd.isna(incoming.to_numpy(dtype=float))]).all():
        raise ValueError("历史数据包含非法数值")
    if old is not None and incoming.index[-1] < old.index[-1]:
        raise ValueError("数据源历史倒退，保留旧数据")
    if full:
        if old is not None and incoming.index[0] > old.index[0]:
            raise ValueError("数据源历史被截断，拒绝部分复权覆盖")
        if old is not None and len(old.index.difference(incoming.index)):
            raise ValueError("数据源缺失已有历史交易日，拒绝部分覆盖")
        frame = incoming
    else:
        # 增量：数据源可能只回补最近若干天。旧写法 `old.index < incoming.index[0]`
        # 会把「旧末段与 incoming 起点之间」的行一起丢掉（数据源少回补几天即可触发）。
        # 这里保留三段：incoming 之前 / incoming / incoming 之后。
        head = old.loc[old.index < incoming.index[0]]
        tail = old.loc[old.index > incoming.index[-1]]
        frame = pd.concat([head, incoming, tail]).sort_index()
        if len(frame) < len(old):
            raise ValueError("增量合并后行数减少，保留旧数据")
    meta = {"kind": asset["kind"], "code": asset["code"], "basis": asset["basis"], "through": end,
            "info": {**incoming.attrs, "fetched_at": current.isoformat(), "data_end": frame.index[-1].date().isoformat()}}
    storage.write(name, frame, meta)
    _refresh_window_caches(asset, frame)
    return "full" if full else "incremental", len(frame)


@contextmanager
def leader():
    """Nonblocking process lock; released automatically after process death."""
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (C.DATA_DIR / "sync.lock").open("a+b") as file:
        file.seek(0, 2)
        if not file.tell():
            file.write(b"0"); file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if os.name == "nt":
                file.seek(0); msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _accumulate_valuation(current) -> None:
    """每日累积估值/股息；只有本次真实抓取成功才记录 last_success。"""
    import market_research.data.marketdata as D
    state_fp = C.DATA_DIR / "valuation_sync.json"
    today = current.date().isoformat()
    try:
        state = json.loads(state_fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("last_success") == today:
        return
    try:
        elapsed = (current - datetime.fromisoformat(
            str(state.get("last_attempt")))).total_seconds()
        if 0 <= elapsed < 1800:
            return
    except (TypeError, ValueError):
        pass

    def write_state():
        try:
            state_fp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except OSError:
            LOG.warning("估值面板累积状态写入失败（不影响数据）", exc_info=True)

    state["last_attempt"] = current.isoformat()
    try:
        pe = D.load_pe_panel(verbose=False)
        dividend = D.load_dividend_panel(verbose=False)
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        LOG.exception("估值/股息面板累积失败，30 分钟后可重试")
        write_state()
        return
    failures = []
    for name, frame in (("估值", pe), ("股息", dividend)):
        status = (getattr(frame, "attrs", {}) or {}).get("sync_status") or {}
        if not status.get("fresh_success"):
            failures.append(f"{name}: {status.get('error') or '本次未取得新数据'}")
    if failures:
        state["error"] = "；".join(failures)
        LOG.warning("估值/股息面板未获得新数据：%s", state["error"])
        write_state()
        return
    state["last_success"] = today
    state["error"] = None
    write_state()


def tick(current=None):
    current = current or now()
    with leader() as acquired:
        if not acquired:
            return
        discover()
        for asset in assets():
            if not due(asset, current):
                continue
            with storage.connection() as db:
                db.execute("UPDATE sync_assets SET last_attempt=?,error=NULL,state='running' WHERE asset_key=?", [current.isoformat(), asset["asset_key"]])
            try:
                mode, rows = synchronize(asset, current)
                with storage.connection() as db:
                    db.execute("UPDATE sync_assets SET last_success=?,mode=?,rows=?,error=NULL,state='success' WHERE asset_key=?",
                               [current.isoformat(), mode, rows, asset["asset_key"]])
            except Exception as error:
                LOG.exception("同步失败 %s", asset["asset_key"])
                with storage.connection() as db:
                    db.execute("UPDATE sync_assets SET error=?,state='error' WHERE asset_key=?", [str(error)[:500], asset["asset_key"]])
        _accumulate_valuation(current)


def enabled() -> bool:
    """同步总开关；常驻调度与 `--once` 单次执行共用同一判断。"""
    return os.environ.get("FIL_SYNC_ENABLED", "1") != "0"


def serve():
    # 同步由 worker 以独立进程启动（`market_research.tasks.sync --once` 也走这里）。
    # FIL_SYNC_ENABLED=0 必须在这个真正干活的入口生效 —— 旧的 `start()`
    # 线程启动器无人调用，等于开关形同虚设。
    if not enabled():
        LOG.info("FIL_SYNC_ENABLED=0，跳过同步调度")
        return
    schedule_time()
    if not LOG.handlers:
        logs.configure("sync")
    while True:
        try:
            tick()
        except Exception:
            LOG.exception("同步调度失败")
        threading.Event().wait(30)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="执行本次到期任务后退出")
    parser.add_argument("--time", help="覆盖北京时间执行时间 HH:MM")
    args = parser.parse_args()
    if args.time:
        os.environ["FIL_SYNC_TIME"] = args.time
    schedule_time()
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logs.configure("sync")
    # ★ --once 才是计划任务/worker 实际调用的入口，不能绕过总开关；
    #   只放在 serve() 里等于 FIL_SYNC_ENABLED=0 对定时同步无效。
    if not enabled():
        LOG.info("FIL_SYNC_ENABLED=0，跳过同步（--once 同样不执行）")
        return
    if args.once:
        tick()
    else:
        serve()


if __name__ == "__main__":
    main()
