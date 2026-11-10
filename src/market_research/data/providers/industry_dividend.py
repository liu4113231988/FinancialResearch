# -*- coding: utf-8 -*-
"""申万一级行业**当期静态股息率快照**：P1-10 的情景参数锚点。

★ 三条不可越界的约束：

1. `akshare.sw_index_first_info()` 返回的是**当期截面**（列名
   `静态股息率`，单位 %），不是历史时点序列。拿当期值回填历史持仓
   基准会引入未来信息，与 `holding_benchmark()` 的披露滞后约束、
   P1-11 的行业归属时点性要求冲突。
2. 本模块只产出 `C.RBSA["index_dividend_annual"]` 的**建议值**：
   不写配置、不写缓存、不进入 `holding_benchmark()` / `RBSA` /
   `ModelZoo` 的任何计算路径。默认值仍是 0.0，现有结果一律不变。
3. 因此 P1-10 **仍未关闭**：仍缺逐行业历史时点股息率或全收益行业指数。
"""
from __future__ import annotations

from datetime import date
import re

import pandas as pd

from market_research import config as C

DIVIDEND_COLUMN = "静态股息率"
SOURCE = "akshare.sw_index_first_info"
_CODE_RE = re.compile(r"^\s*(\d{6})")


def _industry_code(value: object) -> str | None:
    """`801010.SI` / `801010` / 801010 → 6 位申万一级代码。"""
    m = _CODE_RE.match(str(value))
    return m.group(1) if m else None


def normalize_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    """原始快照 → 候选行业明细（股息率转为年化小数，0.02 = 2%）。"""
    columns = ["行业代码", "行业名称", "股息率"]
    if snapshot is None or getattr(snapshot, "empty", True):
        return pd.DataFrame(columns=columns)
    if DIVIDEND_COLUMN not in snapshot.columns:
        raise ValueError(f"快照缺少 {DIVIDEND_COLUMN} 列：{list(snapshot.columns)}")
    rows = []
    for _, row in snapshot.iterrows():
        code = _industry_code(row.get("行业代码", ""))
        if code not in C.SW_INDUSTRY:
            continue
        value = pd.to_numeric(row.get(DIVIDEND_COLUMN), errors="coerce")
        if pd.isna(value) or float(value) < 0:
            continue
        rows.append({"行业代码": code, "行业名称": C.SW_INDUSTRY[code],
                     "股息率": round(float(value) / 100.0, 6)})
    return pd.DataFrame(rows, columns=columns)


def suggest_index_dividend(snapshot: pd.DataFrame,
                           as_of: str | date | None = None) -> dict:
    """推导建议值；返回值纯 JSON 可序列化，**不修改任何配置**。"""
    detail = normalize_snapshot(snapshot)
    base = {
        "as_of": str(as_of or date.today()),
        "source": SOURCE,
        "unit": "年化小数（0.02 = 2%）",
        "static_snapshot": True,
        "not_for_backfill": True,
        "default_unchanged": True,
        "note": "仅作 C.RBSA['index_dividend_annual'] 情景参数建议值；当期静态截面"
                "不可回填历史，默认值仍为 0.0，现有结果不变。",
        "detail": detail.to_dict("records"),
        "covered": len(detail),
        "expected": len(C.SW_INDUSTRY),
    }
    if detail.empty:
        return {**base, "available": False,
                "reason": "快照无可用申万一级行业股息率", "suggested": None}
    values = detail["股息率"]
    return {
        **base,
        "available": True,
        "suggested": round(float(values.median()), 6),
        "aggregation": "候选行业中位数（等权、稳健，避免高股息行业拉高）",
        "mean": round(float(values.mean()), 6),
        "min": round(float(values.min()), 6),
        "max": round(float(values.max()), 6),
        "coverage": round(len(detail) / len(C.SW_INDUSTRY), 4),
    }


def fetch_snapshot(verbose: bool = True) -> pd.DataFrame:
    """联网取一次当期快照；取数失败由调用方决定如何降级。"""
    import akshare as ak
    from market_research.data.http import _retry

    fn = getattr(ak, "sw_index_first_info", None)
    if fn is None:
        raise RuntimeError("当前 akshare 版本无 sw_index_first_info")
    snapshot = _retry(fn, tries=2, wait=1.0)
    if snapshot is None or snapshot.empty:
        raise RuntimeError("sw_index_first_info 返回空")
    if verbose:
        print(f"[sw] 当期静态快照 {len(snapshot)} 行"
              f"（{DIVIDEND_COLUMN}，单位 %）")
    return snapshot


if __name__ == "__main__":
    # 独立只读入口：uv run python -m market_research.data.providers.industry_dividend
    try:
        result = suggest_index_dividend(fetch_snapshot())
    except Exception as error:      # noqa: BLE001
        print(f"取数失败：{type(error).__name__}: {error}")
        raise SystemExit(1)
    if not result["available"]:
        print(f"无可用建议值：{result['reason']}")
        raise SystemExit(1)
    print(f"{result['source']} 当期静态快照 {result['as_of']}")
    print(f"  覆盖 {result['covered']}/{result['expected']} 个申万一级行业")
    print(f"  建议值（中位数）{result['suggested']:.2%} · 均值 {result['mean']:.2%}"
          f" · 区间 {result['min']:.2%}~{result['max']:.2%}")
    print("  仅作 C.RBSA['index_dividend_annual'] 情景参数建议值；"
          f"当前默认值 {C.RBSA['index_dividend_annual']:.2%} 未改动，结果不变。")
    print("  当期静态截面不可回填历史（会引入未来信息）；"
          "P1-10 仍需逐行业历史时点股息率或全收益行业指数。")
