#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录制真实供应商响应的脱敏契约指纹（P2-35）。

默认：验证并提交库中的合成契约 fixture，不联网、不写文件。
--live：调用真实 AkShare 端点，只把列、dtype、行数、日期范围和内容哈希
写入 manifest；不把第三方响应体写进版本库。
--with-raw：仅当法务审查编号和批准标记都存在时，才把原始样本写到
output/fixtures/raw/（该目录被 .gitignore 排除），供本地调试使用。

真实响应能否对外分发须由法务/数据治理确认；此脚本是技术护栏，不是法律意见。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import market_research.config as C
from market_research import data_license
from market_research.data.provenance import CONTRACTS, resolve_columns
from tests.data.provider_fixtures import MANIFEST, validate_manifest

LIVE_CALLS = {
    "fund_nav": lambda ak: ak.fund_open_fund_info_em(
        symbol="110022", indicator="累计净值走势"),
    "fund_unit_nav": lambda ak: ak.fund_open_fund_info_em(
        symbol="110022", indicator="单位净值走势"),
    "fund_cum_return": lambda ak: ak.fund_open_fund_info_em(
        symbol="110022", indicator="累计收益率走势"),
    "index_sina": lambda ak: ak.stock_zh_index_daily(symbol="sh000300"),
    "index_sw": lambda ak: ak.index_hist_sw(symbol="801010", period="day"),
    "csindex_pe": lambda ak: ak.stock_zh_index_value_csindex(symbol="000300"),
    "csindex_dividend": lambda ak: ak.stock_zh_index_value_csindex(
        symbol="000300"),
}


def _contract_frame(source: str,
                    frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """按契约字段解析真实响应，返回契约列子表与实际用到的别名。

    真实列名可能在 AkShare 版本间漂移；子表如实保留真实列名，别名映射写入
    `field_aliases_applied` 随观测指纹一起记录，便于发现源站变更。
    """
    if frame is None or frame.empty:
        raise ValueError(f"{source} 真实响应为空")
    mapping = resolve_columns(frame, source)
    selected = frame.loc[:, list(mapping.values())].copy()
    if selected.empty:
        raise ValueError(f"{source} 真实响应字段为空")
    aliases = {field: column for field, column in mapping.items()
               if field != column}
    return selected, aliases


def _observation(source: str, selected: pd.DataFrame, aliases: dict) -> dict:
    date_field, numeric_field = selected.columns[:2]
    dates = pd.to_datetime(selected[date_field], errors="coerce").dropna()
    values = pd.to_numeric(selected[numeric_field], errors="coerce").dropna()
    if dates.empty or values.empty:
        raise ValueError(f"{source} 真实响应没有可解析的日期或数值")
    digest = hashlib.sha256(
        selected.to_csv(index=False).encode("utf-8")).hexdigest()
    return {"endpoint": CONTRACTS[source]["endpoint"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "contract_fields": list(CONTRACTS[source]["fields"]),
            "fields": list(selected.columns),
            "field_aliases_applied": aliases,
            "dtypes": {key: str(value) for key, value in selected.dtypes.items()},
            "row_count": int(len(selected)),
            "date_min": dates.min().date().isoformat(),
            "date_max": dates.max().date().isoformat(),
            "numeric_min": float(values.min()),
            "numeric_max": float(values.max()),
            "content_sha256": digest,
            "raw_response_included": False}


def record_live(with_raw: bool = False
                ) -> tuple[dict, dict[str, pd.DataFrame], dict[str, str]]:
    """逐源录制观测指纹；单个来源失败不伪造数据，交由调用方汇总报错。"""
    import akshare as ak
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw_frames, errors = {}, {}
    for source, call in LIVE_CALLS.items():
        try:
            selected, aliases = _contract_frame(source, call(ak))
        except Exception as error:                            # noqa: BLE001
            errors[source] = f"{type(error).__name__}: {error}"
            continue
        manifest["sources"][source]["live_observation"] = _observation(
            source, selected, aliases)
        if with_raw:
            raw_frames[source] = selected
    manifest["live_recorded_at"] = datetime.now(timezone.utc).isoformat()
    manifest["raw_data_included"] = False
    manifest["live_errors"] = errors
    # 观测是「最近一次」快照，不是 CI 断言基准：行数/哈希会随每日行情变化，
    # 离线 CI 只校验结构、契约字段与未登记别名，不比对这些数值。
    manifest["live_observations_mode"] = "sanitized_fingerprint_snapshot"
    return manifest, raw_frames, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="调用真实供应商端点")
    parser.add_argument("--apply", action="store_true", help="写回 sanitized manifest")
    parser.add_argument("--with-raw", action="store_true",
                        help="经授权后额外导出原始响应到 output（不入库）")
    parser.add_argument("--out", default=str(MANIFEST), help="manifest 输出路径")
    args = parser.parse_args(argv)
    if not args.live:
        available = validate_manifest()
        print(f"离线契约 fixture 通过：{len(available)} 个来源")
        return 0
    if args.with_raw and not data_license.redistribution_allowed():
        parser.error("缺少 FIL_DATA_LICENSE_APPROVED=1 与 "
                     "FIL_DATA_LICENSE_REVIEW_ID；禁止导出原始响应")
    manifest, raw_frames, errors = record_live(args.with_raw)
    output = Path(args.out)
    if args.apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    raw_dir = C.ROOT / "output" / "fixtures" / "raw"
    if args.with_raw and args.apply:
        raw_dir.mkdir(parents=True, exist_ok=True)
        for source, frame in raw_frames.items():
            frame.to_csv(raw_dir / f"{source}.csv", index=False)
    print(f"真实响应观测：{len(LIVE_CALLS) - len(errors)}/{len(LIVE_CALLS)} 个来源成功；"
          f"manifest {'已写入' if args.apply else '未写入'} {output}；"
          f"原始响应 {'已写入 ' + str(raw_dir) if args.with_raw and args.apply else '未导出'}")
    if errors:
        print("以下来源录制失败（未伪造数据）：", file=sys.stderr)
        for source, error in errors.items():
            print(f"  {source}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
