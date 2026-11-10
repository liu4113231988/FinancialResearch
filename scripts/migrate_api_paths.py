#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把调用方引用的旧 /api/* 路径迁移到 /api/v1/*。

默认 dry-run：只列出待改文件与次数；确认后加 --apply 才写入。
迁移只改引号/反引号内的具体调用路径，不改 /api/v1/*、静态资源或普通说明文字。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = (
    "src/market_research/api/static",
    "tests/frontend",
    "scripts",
    "docs",
    "README.md",
)
SUFFIXES = {".js", ".cjs", ".mjs", ".py", ".ts", ".tsx",
            ".json", ".yml", ".yaml", ".md"}
SKIP_DIRS = {".git", ".venv", "node_modules", "output", "__pycache__"}
PATH_RE = re.compile(
    r'(?P<quote>["\'`])/api/(?!v\d+(?:/|$))(?P<tail>[^"\'`\s]*)')


def replace_paths(text: str) -> tuple[str, int]:
    matches = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal matches
        matches += 1
        return f"{match.group('quote')}/api/v1/{match.group('tail')}"

    return PATH_RE.sub(_replace, text), matches


def iter_files(roots: list[str]):
    script = Path(__file__).resolve()
    for item in roots:
        root = Path(item)
        if not root.is_absolute():
            root = ROOT / root
        explicit_file = root.is_file()
        candidates = [root] if explicit_file else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.resolve() == script:
                continue
            if path.suffix.lower() not in SUFFIXES:
                continue
            if not explicit_file and any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=None,
                        help="文件或目录；可重复，默认扫描前端、测试脚本和文档")
    parser.add_argument("--apply", action="store_true",
                        help="实际写入；缺省只做 dry-run")
    args = parser.parse_args(argv)
    roots = args.root or list(DEFAULT_ROOTS)
    changed, hits = 0, 0
    for path in iter_files(roots):
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated, count = replace_paths(original)
        if not count:
            continue
        changed += 1
        hits += count
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"{'apply' if args.apply else 'dry-run'} {shown}: {count}")
        if args.apply:
            path.write_text(updated, encoding="utf-8")
    print(f"文件 {changed} 个，路径 {hits} 处；"
          f"{'已写入' if args.apply else '未写入（使用 --apply 执行）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
