# -*- coding: utf-8 -*-
"""行业股息率 / 全收益指数数据源探针（P1-10 前置可行性）。

问题：持仓基准用申万**价格**指数，缺股息这条腿，现在只能靠
`C.RBSA["index_dividend_annual"]` 做全局统一情景。要关闭 P1-10，
需要逐行业的真实股息率，或直接的全收益行业指数。

本探针只回答"哪些接口能给到字段"，不写缓存、不改业务代码。
接口清单按**当前安装的 akshare 版本实际存在的属性**动态选取，
缺接口标记为 [缺接口] 而不是失败：

    python tests/tools/probe_dividend.py

结果解读：
    [有字段]  接口可用且返回含"股息/红利"相关列 → 可接数
    [无字段]  接口可用但没有股息列 → 排除
    [失败]    接口不可用/离线 → 需换源或维持情景参数
    [缺接口]  当前 akshare 版本没有该函数 → 升级依赖或换源
"""
import sys
import warnings

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                             # noqa: BLE001
    pass

import akshare as ak
import pandas as pd

print("akshare", ak.__version__)
RESULTS = []


def probe(name, attr, *args, **kwargs):
    fn = getattr(ak, attr, None)
    if fn is None:
        RESULTS.append((name, "缺接口", f"akshare {ak.__version__} 无 {attr}"))
        return
    try:
        d = fn(*args, **kwargs)
        if d is None or len(d) == 0:
            RESULTS.append((name, "空", "返回为空"))
            return
        cols = list(map(str, d.columns))
        hit = [c for c in cols if "股息" in c or "红利" in c]
        sample = d.iloc[-1].to_dict()
        note = "; ".join(f"{k}={v}" for k, v in list(sample.items())[:4])
        RESULTS.append((name, "有字段" if hit else "无字段",
                        f"列={cols} | {hit or '无股息列'} | {note}"))
    except Exception as e:                                    # noqa: BLE001
        RESULTS.append((name, "失败", f"{type(e).__name__}: {str(e)[:90]}"))


CODE = "801120"            # 食品饮料（申万一级，项目候选池之一）

# 1. 申万行业信息：若含当期股息率快照，可作静态锚点（逐行业、需定期更新）
probe("sw_index_first_info", "sw_index_first_info")
probe("sw_index_second_info", "sw_index_second_info")

# 2. 中证官方指数估值（csindex）：字段规范（含股息率1/2），
#    但覆盖的是中证指数 —— 只能匹配宽基腿与部分中证行业，不能直接覆盖申万行业
probe("stock_zh_index_value_csindex(000300)",
      "stock_zh_index_value_csindex", "000300")

# 3. 全收益指数行情通道尝试：
#    - 中证红利全收益（H00922 → sh000922 或 H30269 等）
probe("stock_zh_index_daily(sh000922 红利全收益)",
      "stock_zh_index_daily", symbol="sh000922")
#    - 申万全收益指数（801xxx 的全收益代码段 851xxx，通道未必存在）
probe("stock_zh_index_daily(sw 851120 猜测)",
      "stock_zh_index_daily", symbol="sw851120")

# 4. 个股分红明细：可以自建逐行业股息率（成分股股息 / 市值），
#    数据量大但完全可推导 —— 这是"无现成行业口径"时的兜底路线
probe("stock_history_dividend_detail(600519)",
      "stock_history_dividend_detail", symbol="600519", indicator="分红")

frame = pd.DataFrame(RESULTS, columns=["接口", "结论", "说明"])
for _, row in frame.iterrows():
    print(f"{row['接口']:<46}{row['结论']:<8}{row['说明'][:150]}")
print("-" * 110)
ok = [r for r in RESULTS if r[1] == "有字段"]
print(f"可用字段源 {len(ok)}/{len(RESULTS)} 个：" +
      ("；".join(r[0] for r in ok) if ok else
       "暂无接口直接返回股息字段 —— P1-10 需维持情景参数、升级 akshare 或走个股分红推导"))
