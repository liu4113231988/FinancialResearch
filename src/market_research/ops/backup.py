# -*- coding: utf-8 -*-
"""数据资产备份 / 恢复 / 一致性校验（P2-33）。

数据资产：
  * DuckDB `market.duckdb` —— 行情、pe_panel、div_panel、指数池、同步历史
  * `audit.jsonl*` —— 操作审计
  * `webcache/*.pkl` —— 分析结果缓存（可重算，但恢复后可立即命中）
  * DATA_DIR 下的 `*.meta.json` 等状态文件

一致性做法：备份时持有 DuckDB 文件锁（storage.connection）并用
`EXPORT DATABASE` 生成 Parquet 快照；恢复时在同一把锁内先清空表再
`IMPORT DATABASE`。备份目录带 `manifest.json`（数据集数量、价格行数、
revision 哈希），恢复后用同一份 manifest 校验运行时数据库。

命令：
    python -m market_research.ops.backup backup --dest <目录>
    python -m market_research.ops.backup verify --src <目录>
    python -m market_research.ops.backup restore --src <目录> --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import duckdb

import market_research.config as C
from market_research.data import storage

MANIFEST = "manifest.json"
PARQUET_TABLES = ("datasets", "prices")
STATE_PATTERNS = ("audit.jsonl*", "*.meta.json")
CACHE_DIRNAME = "webcache"
REQUIRED_DATASETS = ("pe_panel", "div_panel")


class BackupError(RuntimeError):
    pass


def _posix(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _live_counts(db) -> dict:
    n_datasets, n_rows = db.execute(
        "SELECT (SELECT count(*) FROM datasets), (SELECT count(*) FROM prices)"
    ).fetchone()
    names = [r[0] for r in db.execute(
        "SELECT name FROM datasets ORDER BY name").fetchall()]
    revisions = db.execute(
        "SELECT name, revision FROM datasets ORDER BY name").fetchall()
    digest = hashlib.sha256(
        json.dumps(revisions, sort_keys=True).encode()).hexdigest()
    return {"n_datasets": int(n_datasets), "n_price_rows": int(n_rows),
            "dataset_names": names, "revision_hash": digest}


def backup(dest: Path) -> dict:
    """生成一致性备份并返回 manifest。"""
    dest = Path(dest).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise BackupError(f"备份目录 {dest} 非空，请换一个目录或先清理")
    dest.mkdir(parents=True, exist_ok=True)

    with storage.connection() as db:
        db.execute(f"EXPORT DATABASE '{_posix(dest)}' (FORMAT PARQUET)")
        live = _live_counts(db)

    copied = []
    for pattern in STATE_PATTERNS:
        for src in C.DATA_DIR.glob(pattern):
            if src.is_file():
                shutil.copy2(src, dest / src.name)
                copied.append(src.name)
    cache_src = Path(C.DATA_DIR) / CACHE_DIRNAME
    if cache_src.is_dir():
        shutil.copytree(cache_src, dest / CACHE_DIRNAME, dirs_exist_ok=True)
        copied.append(CACHE_DIRNAME + "/")

    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": C.ENV,
        "data_dir": str(C.DATA_DIR),
        "format": "duckdb-export-parquet",
        "state_files": copied,
        **live,
    }
    (dest / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _read_manifest(src: Path) -> dict:
    path = Path(src) / MANIFEST
    if not path.exists():
        raise BackupError(f"{path} 不存在，不是有效的备份目录")
    return json.loads(path.read_text(encoding="utf-8"))


def verify(src: Path) -> dict:
    """校验备份目录内部一致性（不接触运行时数据库）。"""
    src = Path(src).resolve()
    manifest = _read_manifest(src)
    con = duckdb.connect()
    try:
        for table in PARQUET_TABLES:
            frame = con.execute(
                f"SELECT count(*) FROM read_parquet('{_posix(src / (table + '.parquet'))}')"
            ).fetchone()
            if frame is None:
                raise BackupError(f"{table}.parquet 不可读")
        datasets = con.execute(
            f"SELECT name, revision FROM read_parquet('{_posix(src / 'datasets.parquet')}') "
            "ORDER BY name").fetchall()
    finally:
        con.close()
    names = [r[0] for r in datasets]
    digest = hashlib.sha256(
        json.dumps(datasets, sort_keys=True).encode()).hexdigest()
    problems = []
    warnings = []
    if digest != manifest.get("revision_hash"):
        problems.append("数据集 revision 哈希与 manifest 不一致（备份可能被改动）")
    if len(names) != manifest.get("n_datasets"):
        problems.append(f"数据集数量 {len(names)} != manifest {manifest.get('n_datasets')}")
    for name in REQUIRED_DATASETS:
        if name not in names:
            warnings.append(f"缺少关键数据集 {name}（估值/股息面板可能从未同步）")
    audit = list(src.glob("audit.jsonl*"))
    if not audit:
        warnings.append("备份不含审计日志 audit.jsonl*")
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "manifest": manifest, "datasets": names}


def restore(src: Path) -> dict:
    """从备份恢复到当前 DATA_DIR（破坏性操作，CLI 需 --confirm）。"""
    src = Path(src).resolve()
    check = verify(src)
    if not check["ok"]:
        raise BackupError("备份校验未通过，拒绝恢复：" + "；".join(check["problems"]))
    manifest = check["manifest"]

    with storage.connection() as db:
        db.execute("DROP TABLE IF EXISTS prices")
        db.execute("DROP TABLE IF EXISTS datasets")
        db.execute(f"IMPORT DATABASE '{_posix(src)}'")
        live = _live_counts(db)

    for name in manifest.get("state_files") or []:
        source = src / name.rstrip("/")
        if source.is_dir():
            shutil.copytree(source, Path(C.DATA_DIR) / name.rstrip("/"),
                            dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, Path(C.DATA_DIR) / source.name)

    problems = []
    if live["revision_hash"] != manifest.get("revision_hash"):
        problems.append("恢复后 revision 哈希与备份不一致")
    if live["n_price_rows"] != manifest.get("n_price_rows"):
        problems.append(
            f"恢复后价格行数 {live['n_price_rows']} != 备份 {manifest.get('n_price_rows')}")
    if problems:
        raise BackupError("恢复后一致性检查失败：" + "；".join(problems))
    return {"ok": True, "live": live, "manifest": manifest}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="数据资产备份/恢复（P2-33）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_backup = sub.add_parser("backup", help="生成一致性备份")
    p_backup.add_argument("--dest", required=True)
    p_verify = sub.add_parser("verify", help="校验备份目录")
    p_verify.add_argument("--src", required=True)
    p_restore = sub.add_parser("restore", help="从备份恢复（清空当前表）")
    p_restore.add_argument("--src", required=True)
    p_restore.add_argument("--confirm", action="store_true",
                           help="确认恢复会覆盖当前数据")
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            manifest = backup(Path(args.dest))
            print(f"[backup] 完成：{args.dest}")
            print(f"[backup] 数据集 {manifest['n_datasets']} 个，"
                  f"价格行 {manifest['n_price_rows']} 行")
            return 0
        if args.command == "verify":
            result = verify(Path(args.src))
            print(json.dumps({k: result[k] for k in ("ok", "problems", "warnings")},
                             ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "restore":
            if not args.confirm:
                print("[restore] 拒绝执行：恢复会覆盖当前数据，请加 --confirm",
                      file=sys.stderr)
                return 2
            result = restore(Path(args.src))
            print(json.dumps({"ok": result["ok"], "live": result["live"]},
                             ensure_ascii=False, indent=2))
            return 0
    except BackupError as exc:
        print(f"[{args.command}] 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
