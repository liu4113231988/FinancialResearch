# 项目待办

本文件**只记录未完成项**。已完成项的实现与验证见 `git log`、[测试说明](tests/README.md) 的验证记录，方法论结论见[研究方案](docs/研究方案.md)与[功能完整性检查](docs/功能完整性检查.md)。

复核日期：2026-11-06（专项复核 2026-11-06 的走势预测 / 买卖点 / 持仓拟合；同日按四项拍板口径完成基金方向概率、当前建议仓位/健康分、P1-19 兜底标注与 P3-C 产品化；随后修复 P2-22 指数缓存口径、P2-23 未映射残差计息、P3-A 买卖点截断、P3-B 面板缺列静默丢弃、P2-21 估值通道不可用仍占权重。P1-10/P1-11 数据缺口仍开放；同日另做生产上线评估，新增 P1-20、P2-24~P2-36、P3-D~P3-I；随后按评估结论关闭 P1-20 与 P2-27/28/30/31/32/37/25/26，P2-24/29/33/34/35/36 已按 2026-11-06 拍板口径（公开抓取、stale 标记继续、内部研究、API 内部使用、单用户可升级）关闭）。2026-11-01 本轮除更新待办外，**已修复当日复核发现的全部程序缺陷**：P1-14（覆盖率 KeyError）、P1-15（固定窗口被截止日后净值污染）、P1-16（持仓基准混合收益口径）、P1-17（"相对基金IR"实为自身收益波动比）、P1-18（持仓缓存忽略请求年份）、P2-19（年数窗口未锚定 end）、P1-11 的代码侧问题（不完整行业映射被永久冻结），以及可选能力 P1-13（/api/corr 冷缓存改为提交队列任务）。每项均带最小复入的回归测试；2026-11-06 全量验证：`.venv/Scripts/python.exe -u -m unittest discover -s tests -p "test_*.py" -t .`（325 项；2026-11-06 回查轮已增至 354 项、修复轮增至 366 项）、`python -u tests/regression/test_regressions.py`、`python -u tests/regression/test_p0_fixes.py` 均通过。剩余的 P1-10 / P1-11（数据部分）是数据缺口，不是本轮可关闭的缺陷。P1 表示影响正常计算或结果解释，P2 表示参数语义或特定入口问题。

**2026-11-06 回查补充**：按「代码实际行为」复核本文件标记关闭的条目，发现 P1-20、P2-21、P2-24/25/26/28/29/30/31/32/33/34/35/36/37、P3-C 共 16 条只完成核心部分（P3-C 缺 payload/报告渲染测试），已回退为「部分落地」并写明差距（详见各条「回查差距」）；P1-19、P2-22/23/27、P3-A/B 已确认落地，保持关闭。另发现 `meta.provenance.data_stamp` 恒为 `null`（见 P2-34 回查差距）。全量单测 354 项通过（2026-11-06 复核执行）。

**2026-11-06 修复轮**：按回查结论补齐 P2-34（`data_stamp` 按操作取数据版本、缓存命中保留产出时版本）、P2-30（`StorageBusy` 映射 503 + 可读原因/重试秒数，路由不再吞掉）、P2-21（研究 CLI 按估值可用性剔除曲线）、P1-20（CLI/报告输出净值口径）、P3-C（持仓 payload 与报告 3.6 内容级断言）。P1-20、P2-21、P3-C 已关闭；P2-34、P2-30 仍为部分落地（剩余差距已在各条更新）。全量单测 366 项通过。

## 2026-11-08 全局复审（第二轮）

按九个功能面复核逻辑、边界与异常路径。以下为**本轮直接修复**（均已补回归测试）：

- `predictions/judge.py` 结算读取库存行情不再要求"同步到今天"（`sync.cached(..., fresh=False)`）；旧实现按 `today+1` 请求结束日、缓存检查日最多只到 `today`，股票/指数记录会永远读不到行情而被误判 `insufficient`。
- `predictions/judge.py` 行情读取失败、信号日收盘尚未入库时**保持 `pending`**（新增 `unavailable` 计数），不再永久定案；宽限期过后仍缺数才判 `insufficient`。
- `data/providers/funds.py` 拆分校准在区间内没有可用锚点时必须标 `degraded`（旧实现只覆盖"锚点≥2 但系数不足"，锚点为 0 时会静默返回未校准序列）；同时支持单锚点校准；联动净值/单位净值无交集时不再对空序列取首行。
- `data/panels.py` 空净值在 `verbose=True` 下先打印 `nav.index[0]` 会抛 `IndexError`，改为正常抛出"无可用数据"口径错误。
- `api/routes/analysis.py` 轻量 `/api/corr` 直连路径补齐 `data_quality` / `data_warnings`，与服务层口径一致。
- `predictions/ledger.py` `pending_records` 对负数/超大 `limit` 做钳制。

以下为**待决策项**（涉及数据口径/跨模块契约，本轮不改代码）：

1. **结算时钟的终判口径**（P1）：`horizon_basis=observed_bars` 且资产退市、清算或供应商停止供数时，目标会话永远不存在，记录会无限 `pending`。计划：① 先确认产品口径——"连续 N 个会话无新报价"应判 `blocked` 还是保持待定；② 在 `judge_record` 增加"行情末日早于目标日且已超宽限"的终判分支；③ 补退市/清算/断供三类测试与 summary 分桶。
2. **宽限期判定日与名义期限漂移**（P2）：目标日缺会话时当前取宽限期内首个可用收盘，实际收益区间长于 5/20 日，且无法区分停牌与漏抓。计划：① 确认"目标日缺会话直接 `blocked`"还是"顺延"；② 若顺延，在记录中冻结实际 `judge_date` 与 `actual_sessions`；③ 让 `summary` 单独统计顺延样本，避免与标准期限混算命中率。
3. **降级 `cum` 基金预测的离线结算**（P2）：累计净值没有 `sync_history_fund_*_cum` 正史，窗口缓存名又绑定分析起点，worker 内置结算（不带 `--fetch`）可能长期 `pending`。计划：① `submit` 按实际 `nav_basis` 注册同步（含 `cum`）；② 结算侧按 `fund_nav_{code}_*_{basis}` 前缀回退查找最宽窗口；③ 补离线结算测试。
4. **CLI 批量分析不入台账**（P3）：`cli/analyze.py` 走 legacy `run_fund`，产物没有 `forecast/current` 契约，无法复用服务层写入钩子。计划：先确认 CLI 是否属于"实盘入口"；若属于，让 `run_fund` 输出与 `services.analyze` 对齐的字段后再挂钩子，避免维护两套映射。

## 基金分析

### P1-10：持仓基准的股息缺口

仍存在：`funds/holdings.py:holding_benchmark` 使用行业价格指数，已有 `dividend_yield` 情景参数，默认跟随 `C.RBSA["index_dividend_annual"]`（0），但尚无逐行业真实股息率或全收益行业指数。需要用真实数据替换统一年化情景假设。旧文中的“每年低估约 1.5~2.5%”不是本轮验证的固定误差，实际影响随时期、行业与股票权重变化，不应作为所有基金的事实。
**按“只取 stock_zh_index_value_csindex”口径的实施结论（2026-11-01）**：估值与股息数据源统一切换为中证官网 csindex（`stock_zh_index_value_csindex`，日频 `市盈率2`/`股息率2`；旧乐咕 `stock_index_pe_lg` 在当前 akshare 版本已缺失，`pe_panel` 此前实际损坏）。关键约束：**csindex 官方文件只保留最近约一个月** —— 历史长度只能靠每日同步把最近一段合并进本地累积缓存（`panels._accumulate_panel`，已挂接 `sync.tick`，`DATA_CACHE_VERSION` 3→4），断跑超过一个月的时段无法回补；且 csindex 不含申万行业（801xxx），**行业腿的股息仍只能靠情景参数或个股分红推导**。`load_dividend_panel`（宽基日频真实股息率历史）已就绪，可作为行业腿数据出现之前的宽基锚点。全收益行业指数通道仍不存在。

### P1-11：行业映射的时点性（幸存者偏差）——仅剩数据缺口

仍存在（数据缺口）：`funds/holdings.py:load_industry_map` 由**当前**申万成分股并集构造映射，不能保证历史时点行业归属；风险不限于“回溯 3 年以上”。需要历史时点成分数据，才能消除该偏差并决定覆盖不足期次的处理。现有覆盖率标注不能消除偏差。代码侧的“不完整映射被永久冻结”问题已于 2026-11-01 修复：缺失行业写入 meta 并按冷却期（`INDUSTRY_RETRY_COOLDOWN_SECONDS = 3600`）自动补取，回归见 `tests/funds/test_payloads.py:IndustryMapCacheTest`。

## 股票与指数

- [ ] 扩展代表性样本池并验证未来未使用区间；当前回顾性留出及小样本结果不足以证明交易有效。
- [ ] 成交实证：公司行动、历史停牌/涨跌停、税费明细、实际 ETF、更准确的开盘容量、扩大样本及完整策略不确定性。供应商日历不是全部交易所公告审计，缺字段假设不能认定可交易。
- [ ] 个股相对行业、行业相对宽基；完整历史行业归属与指数成分版本。
- [ ] 指数市场宽度（MA20/60 以上成分股比例、涨跌家数、新高新低），必须使用历史时点成分名单，避免幸存者偏差。
- [ ] 财报、公告、分红、估值历史分位，记录报告期、公告时间和数据可得时间。
- [ ] 未来未使用时间段的概率校准有效性及完整策略不确定性评估；指数信号在实际 ETF 上验证费用、成交与跟踪偏差。

## 工程与产品

- **预测台账与回验已落地（2026-11-08）**：新增 `prediction_records` 表、`predictions` 命令、服务层写入钩子与 10 项单测；口径见[预测台账与回验](docs/预测台账与回验.md)。剩余：
  - ~~Web 页面与报告展示台账汇总~~ **已关闭（2026-11-08）**：新增只读 `/api/predictions/summary` 双前缀接口、「预测回验」面板与离线 HTML 报告 10 号章节。
  - ~~项目内内置结算调度~~ **已关闭（2026-11-08）**：`worker.supervise` 内置可关闭的定时结算子进程；环境变量见 `docs/运行环境与配置.md`。
  - [ ] 特征分桶归因：`summary` 目前只支持 `source/horizon/asset_kind/regime`，波动率、趋势分等切片待补。
  - ~~旧规则行物理迁移命令~~ **已关闭（2026-11-08）**：`predictions migrate`（默认 dry-run，`--apply` 幂等写库）合并 5/20 为单条 `horizon=0`；旧库需人工执行，命令本身不会自动运行。
- 时点可得的基金分红/拆分及净值版本，股票公司行动与历史数据版本。
- 供应商交易日历和缺口会话处理已有实现（`data/providers/calendar.py`、`market/targets.py`、`market/execution.py`）；仍需交易所公告核验、区分停牌与漏抓、真实订单及基金申赎/持有期费用模型，不能记为“完全没有交易日历”。
- 股票财报/估值/同业比较，以及方向估计与概率校准的未来独立样本有效性。
- ~~`data/storage.py:stamp` 收窄依赖与重复窗口清理~~ **已关闭（2026-11-01）**：stamp 支持按抓取窗口收窄到本基金本窗口的实际依赖（`pe_panel` 与未知数据集仍按设计保守保留）；worker 启动时清理重复窗口的指数池缓存；当前版本结果缓存按 30 天 mtime 淘汰。`CACHE_VERSION` 已同步 +1。
- ~~Web 重估点抽稀的前缀稳定性~~ **已关闭（2026-11-01）**：`rebal_mode` 切换为 `calendar`（锚点只由日期决定），限流改为保留最新锚点、从头部丢弃 —— 已选历史点不位移；`CACHE_VERSION` 连同 +1，回归见 `tests/modeling/test_rebal_calendar.py`。研究路径本就不抽稀，不受影响。
- 页面恢复、自选及带数据口径的研究导出**已实现（2026-11-01）**：localStorage 持久化上次分析状态（只回填表单、不自动触发分析）与自选基金清单（顶栏 ☆），结果页支持导出 JSON（含窗口/模型/超参/重估模式等口径）。仍缺：长期模型档案，为[待评估提案](docs/模型档案与算法记录_重构方案.md)，未实现。

## 全局功能审查（2026-11-08）

范围：按当前工作区源码复核基金分析/调参/回测、股票与指数分析、持仓拟合、预测记录与结算、行情缓存与每日同步、API/持久任务及前端任务状态。以下 8 项均有明确代码证据；P1 影响核心计算或结算，P2 影响特定入口、数据状态或口径，P3 为交互竞争与一致性。已有数据缺口和历史开放项不重复列入。

### P1（优先修复）

- [ ] **G-01：基金预测结算传入不存在的 `end` 参数，所有基金记录都无法结算**
  - 位置：`src/market_research/predictions/judge.py:180-207`；`src/market_research/data/panels.py:222-226`。
  - 触发与影响：`_load_prices()` 的基金分支调用 `load_fund_nav_cached(..., end=end)`，真实函数签名没有 `end`。`predictions settle` 或 worker 自动结算遇到任意基金记录都会抛出 `TypeError`；当前异常分支虽会保持 `pending`，但每轮都会重试失败，基金命中率、收益和 Brier 永远没有有效结算样本。
  - 已复现：直接读取一条基金记录得到 `TypeError: load_fund_nav_cached() got an unexpected keyword argument 'end'`。
  - 修复与验收：移除该调用的 `end`，取得序列后按结算截止日截断；新增真实函数签名的基金端到端结算测试，mock 必须使用 `autospec=True` 或调用真实 loader，覆盖 `total/cum` 两种冻结口径。现有 `tests/predictions/test_ledger.py:299-305` 的普通 mock 会接受任意关键字参数，无法防止回退。

- [ ] **G-04：不可信拟合日仍累计卖出确认天数，恢复可信的第一天可能立即误卖**
  - 位置：`src/market_research/modeling/signals.py:615-640`。
  - 触发与影响：`run_bad` 在检查 `fit_reliable` 之前递增；不可信日只阻止当天交易，不清零或暂停计数。默认 `health_confirm=10` 时，9 个低健康分且不可信的交易日后再来 1 个低健康分可信日，会立即卖出，实际并非“连续 10 个可信交易日”。
  - 已复现：上述 10 日最小输入在第 10 日生成一笔卖出，决策由 `[1,…,1]` 变为末日 `0`。
  - 修复与验收：只在 `ok_rel=True` 时递增卖出确认计数，或在不可信日清零；明确健康分 NaN 日是否也中断连续性，并保持买入门控语义不变。补“不可信 streak → 可信恢复”“可信/不可信交替”“NaN 穿插”回归，不能只验证全程不可信时不卖。

### P2（特定入口 / 数据状态 / 口径）

- [ ] **G-02：手动刷新不更新 canonical 历史，下一次普通分析会恢复旧行情**
  - 位置：`src/market_research/market/analysis.py:75-86,122-128`；`src/market_research/data/panels.py:242-274`；`src/market_research/tasks/sync.py:139-186`。
  - 触发与影响：`refresh=True` 跳过 `sync_history_*` 并只重写 `market_v1_*` 或 `fund_nav_*` 窗口缓存；下一次 `refresh=False` 又优先读取 canonical 历史。只要 canonical 的 `through/data_end` 仍通过新鲜度检查，用户刷新看到的新数据就会被旧数据覆盖，股票、指数和基金均受影响。
  - 修复与验收：手动刷新应复用统一同步链路更新 canonical 及元信息，或比较窗口缓存与 canonical 的实际数据版本后选择更新且同口径者。增加“旧 canonical → 手动刷新 → 再次非刷新读取”的股票/指数/基金回归，断言两次都返回新数据且不产生口径混用。

- [ ] **G-03：估值抓取失败或返回空表仍写 `last_success`，同步状态与就绪探针误报正常**
  - 位置：`src/market_research/data/panels.py:72-106`；`src/market_research/tasks/sync.py:301-328`；`src/market_research/api/routes/meta.py:68-89`。
  - 触发与影响：`_accumulate_panel()` 吞掉抓取异常并返回旧缓存或空表；`_accumulate_valuation()` 不检查本次是否成功及数据是否非空，直接写当天 `last_success`。首次无缓存且供应商失败时会形成“从未有数据但同步成功”，当天不再重试，`/api/ready` 也可能据此显示正常。
  - 已复现：两个估值 loader 都返回空表时，隔离状态文件仍写入 `{"last_success": "2026-11-08"}`。
  - 修复与验收：累积函数返回 `fresh_success/fallback_used/error` 等明确状态；仅确认本次抓取成功后更新 `last_success`，降级与失败分别记录 `last_attempt/error`，同日失败允许有限退避重试。补首次失败、已有缓存失败、空表、PE 成功但股息失败及 ready 探针测试。

- [ ] **G-05：行业映射强制刷新失败会用空表或残缺结果覆盖完整旧缓存**
  - 位置：`src/market_research/funds/holdings.py:124-202`。
  - 触发与影响：`refresh=True` 重抓全部行业，但旧缓存仅在 `not refresh` 时参与合并；部分或全部请求失败后仍执行 `_atomic_csv_write()`，完整缓存可能变成残缺表或空表，新的冷却元信息还会让后续普通调用暂时直接返回空映射。
  - 已复现：隔离缓存原有 1 条映射，唯一行业强刷失败后返回值和落盘缓存均变为 0 条。
  - 修复与验收：强刷也对失败行业保留旧值，成功部分与旧映射按明确优先级合并；全部失败时不得覆盖旧文件。补全失败、部分失败、全新行业与股票换行业、缓存损坏及冷却期组合测试。

- [ ] **G-06：CLI 的回测 `--end` 覆盖分析 `--window-end`，两个截止日无法独立控制**
  - 位置：`src/market_research/cli/analyze.py:31-52`；`src/market_research/validation/window.py:162-186`；`src/market_research/funds/pipeline.py:53-70`。
  - 触发与影响：`apply_window()` 已把 `--window-end` 写入 `C.END_DATE`，但 `run_fund()` 又用回测参数 `end_date=args.end` 调用 `D.load_panel(..., end=end_date)`。同时指定两个日期时，拟合面板被裁到回测截止日，而非分析截止日，研究复现口径错误；函数 docstring 对 `end_date` 的两种描述也相互矛盾。
  - 修复与验收：给 `run_fund()` 增加独立 `window_end` 并显式传递，回测 `end_date` 只传给回测层，对齐 `funds/analysis.py` 已有的双窗口接口。补两个截止日相同/不同、仅传其中一个及非法先后关系的 CLI 测试，并断言 loader 与回测分别收到正确日期。

- [ ] **G-07：跨域令牌认证被 CORS 预检阻断**
  - 位置：`src/market_research/api/app.py:108-120`。
  - 触发与影响：启用 `FIL_ACCESS_CONTROL=token` 与 `FIL_CORS_ORIGINS` 后，浏览器跨域请求会携带 `Authorization` 或 `X-API-Key`，但 `allow_headers` 只有 `Content-Type`，预检无法通过；同源页面不受影响。
  - 已复现：携带 `Access-Control-Request-Headers: authorization` 的 OPTIONS 请求返回 `400 Disallowed CORS headers`，允许列表不含认证头。
  - 修复与验收：允许 `Authorization`、`X-API-Key` 及实际使用的必要请求头，保持 origin 白名单而非放开全部来源。补 Bearer/API Key 两种预检、正式请求、非白名单 origin 和同源回归。

### P3（交互竞争）

- [ ] **G-08：持仓拟合按钮未纳入统一 busy/重入保护，可打断当前任务的前端轮询**
  - 位置：`src/market_research/api/static/app.js:902-912,2241-2272`。
  - 触发与影响：`setFundBusy()` 未禁用 `holdRun`，`runHoldings()` 也不检查 `activeJob`，并会递增 `fundGeneration`。分析或回测运行中点击持仓拟合会提交另一类任务并使原任务代次失效；后台原任务仍运行，页面却停止接收其结果，进度、取消按钮和最终展示可能不一致。
  - 修复与验收：把 `holdRun` 纳入 busy 控件，入口复用统一重入保护，并按任务代次清理 `activeJob`。补“分析/回测/持仓两两互斥”“连续点击”“取消后发新任务”“旧轮询晚返回”的前端行为测试。

### 本轮验证与未覆盖范围

- 已通过：`.venv/Scripts/python.exe -u -m unittest discover -s tests -p "test_*.py" -t .`（489 项，139.488 秒）；`tests/regression/test_regressions.py`；`tests/regression/test_p0_fixes.py`；`tests/frontend/test_refit.js`、`test_ui_async.cjs`、`test_market_async.cjs`。现有测试全绿不代表上述分支已覆盖。
- 最小复现已完成：G-01、G-03、G-04、G-05、G-07；其余项由当前调用链和条件分支静态确认。
- 未执行：真实供应商联网取数、真实浏览器/移动端、多进程与锁竞争压力、Docker/wheel 构建安装及完整数值研究重放。本轮仅审查并更新待办，未修改业务代码。

## 本轮代码审查发现（2026-11-01 补记，尚未修复）

由一次覆盖基金/建模、市场/研究、API/任务/数据层、前端的全链路只读审查发现。每条含 位置、触发条件、后果、修复方向。P1 影响默认路径计算结果，P2 特定条件下错误或口径被持久化，P3 健壮性/一致性/打磨。已知数据缺口（P1-10/P1-11 及「股票与指数」六项）不重复列在此处。

### P1（默认主路径，影响每只基金分析结果）

- **P1-A：PE 估值分位面板永久全 NaN，「日积月累」设计不生效**
  位置：`data/providers/valuation.py:80`（`fetch_pe_panel` 在每次抓取的 ~20 行片段上立即算 `rolling(2000, min_periods=250)` → 全 NaN）+ `data/panels.py:55-89`（`_accumulate_panel` 逐日合并的全是 NaN 帧，无论积累多久都不可能出现有效分位）。
  触发：任意走估值通道的基金分析（`pipeline.py:52` → `signals.py:valuation_channel`）。
  后果：`val_score` 全 NaN，`signals.py:238-240` 用 `fillna(0)` 后分母仍含 `w_val_pe=0.15`，融合分被静默压向中性、仓位被系统性压向 0.5；界面无任何降级提示，与文档口径不符。`tests/data/test_valuation.py` 的累积测试直接注入「原始值帧」，从未用真实 `fetch_pe_panel` 串过累积链路，故漏测。
  修复方向：`pe_panel` 改为累积**原始市盈率**（与 `div_panel` 同构），合并出完整历史后再对整块重算分位；`DATA_CACHE_VERSION` +1；补「短片段多轮累积→后期分位有效」的回归。

- **P1-B：自适应仓位的锚点 E 从不按自身 R² 参评**
  位置：`modeling/estimators.py:676-684`（`estimate_equity`）。
  触发：默认配置 `auto_equity=True`、`equity_method="local"`（`config.py:239-243`），beta 不动点落在 `(0.20, 1.00)` 内部——即默认路径下几乎每个窗口（grid 法同构）。
  机理：阶段一在不动点 E 拟合得到 `fr`；local 候选 `[E−0.1, E, E+0.1]` 升序遍历，第一个候选走 else 把 `fr` 重绑成 E−0.1 的拟合；到 `Ec==E` 时「复用阶段一结果」实际复用了邻域拟合，其 R² 与刚写入 best 的值相等、严格 `>` 失败，锚点永远落选，选出的仓位只能是 ±0.10 邻域值。违背「窗口内自适应估仓位」设计。
  修复方向：阶段一结束单独保存 `fr0`，复用分支用 `fr0`（或在复用命中后不再让 else 覆盖该引用）。

### P2（特定条件下错误 / 错误口径被持久化）

- **E1 短周期 ICIR 恒为 NaN**：`research/experiments.py:54-60`，分块长度恰为 `block=max(h,20)`，过滤却写 `len(b) > 20`——h=5/20 时所有块恰 20 长被全丢，研究最关心的短周期 ICIR 永远缺失且与「样本不足」不可区分。改 `>= 20`。
- **walk-forward 配置失败被伪装成样本不足**：`research/walkforward.py:91-92` `except Exception: return None` 丢弃异常信息，失败格静默消失并触发不等样本回退，结论失真。应参照 `research/robustness.py:338` 把 `{type}: {msg}` 写入 diag，并在汇总中显式披露失败配置数。
- **lasso/ridge/krr 内层 CV 标准化泄漏（P1-02 同类漏改）**：`modeling/estimators.py:249/282/472` 未传 `_fold_standardize`，全窗口 mu/sd 进入验证折，α/γ 选择偏乐观且与 enet（`estimators.py:207` 已修）口径不一致。三处以同样方式传折内变换。
- **失效窗口健康度还原只覆盖 1 天**：`modeling/signals.py:285-297`，坏窗口权重实际生效的 `[i_k, i_{k+1})` 整段（约 20+ 交易日）仍填上一窗口 R²，停牌后本应停用的健康门控照常「可信」。应按重估段整体置 NaN。
- **未校准总回报被当 total 口径永久缓存**：`data/providers/funds.py:204-208`，锚点 ≥2 但有效 `scale<2` 时只 print 警告、返回的 F **没有 `attrs["degraded"]`**，绕过 `data/panels.py:211-224` 的防线写入 `_total` 缓存并进入同步正史。年轻基金可触发。修复：该分支给 F 打 `degraded=True`（或返回空让调用方走既有降级）。
- **GBK 控制台 ⚠ 打印把降级变崩溃**：`data/providers/funds.py:207/230/235/246` 与 `data/panels.py:231` 仍有 5 处 `print("⚠")` 漏网（仓库自己在 `panels.py:173-178` 已记录过该坑），直跑 CLI 在 cp936 控制台上降级路径先抛 `UnicodeEncodeError`。改为 `LOG.warning`（ASCII 或让 logging 兜底编码）。worker 子进程注入 `PYTHONUTF8=1` 不受影响。
- **前端：自选存储形状错误打断工作台初始化**：`api/static/app.js:530` 对 localStorage 中「合法 JSON 但非数组」的值直接 `forEach`，异常发生在 init 中途（`app.js:662`），其后刷新/取消/重学/回测/MarketApp 的绑定全部失效。读取时做数组归一化（`Array.isArray(v) ? [...new Set(v.filter(/^\d{6}$/))] : []`）。当前版本自身只写数组，触发需存储被旧版/手动污染。
- **前端：任务重入 / 取消 / 轮询健壮性**
  - 基金分析 busy 期间下拉框（presetSel/watchSel）未禁用，可重复提交挤占队列；旧轮询 `finally` 无条件 `activeJob = null`，秒回的新任务 job_id 会被旧轮询清空（`app.js:837-890`）。
  - POST 返回前点取消，任务随后被创建但不会被真正取消（`app.js:963-974`）。
  - 轮询单次网络抖动即整体判失败，长任务等待作废（`app.js:913-914/1933-1934`、`market.js:86`），应有限退避重试。
  - 市场页 `market.js:82-103` 轮询无次数上限、无 AbortController 超时、无取消入口。
  - 相关性轮询覆写分析任务的 `activeJob`、窗口值不做 `[30,500]` 范围校验（`app.js:684/936-961`）。
  修复方向：入口加 busy 判断/提交令牌；finally 用 `if (activeJob === 本次jid) activeJob = null`；设 `cancelRequested` 标志在 analyze 响应落地后补 cancel；轮询退避；市场接入取消按钮；相关性用独立任务槽位。
- **前端：自选跨入口污染**：`app.js:664-669` 自选下拉在股票/指数页无条件走基金 `run()`；`toggleWatchlist` 只校验 6 位数字不区分 kind，股票代码会混入「基金自选」。非基金入口隐藏/禁用 `#btnWatch` 与 `#watchSel`，或 watchSel 走与 presetSel 相同的 kind 分支。
- **前端：存储型 XSS 面**：`app.js` 约 24 处 `innerHTML` 拼接指数名/模型名/insight 文本（如 tooltip `app.js:1808`、超参属性值 `app.js:1592-1607`），文本来自第三方名录并落本地缓存。市场页与数据中心全用 `textContent` 是干净的。增加统一 `esc()` 工具，所有 interpolate 进 innerHTML 的外部字符串转义；ECharts tooltip 改用 textContent 或对名称转义。

### P3（健壮性 / 一致性 / 打磨，择机处理）

**逻辑与口径**
- ~~beta 自由度 ddof 混用~~ **已关闭（2026-11-08）**：`modeling/engine.py`、`modeling/estimators.py`、`research/experiments.py` 及 TODO 未列的 `research/commands/increment.py` 共四处统一 `np.var(..., ddof=1)`；`CACHE_VERSION 11→12` 让旧口径结果缓存失效。β/γ/权重/R²/TE 数值整体变动，历史报告与导出需重跑后再比较。
- ~~lasso 空支撑集兜底~~ **已关闭（2026-11-08）**：`_fit_lasso` 照 `_fit_enet` 改用 `nan_to_num` + `argmax`，全常数列不再抛 `All-NaN slice` 拖垮整批拟合。
- ~~平局闭合交易压低 win_rate~~ **已关闭（2026-11-08）**：两类账户共用 `FLAT_PNL_EPSILON=1e-9`，胜率分母改为有明确盈亏的交易/周期，单列 `flat_trades` / `flat_cycles`；`closed_trades` 仍为全量。
- ~~Newey-West 零方差假显著~~ **已关闭（2026-11-08）**：LRV≤1e-15 或非有限时返回 NaN；此前 `max(lrv,1e-18)` 把常数非零序列的 t 放大到 ~1e9，被 `abs(t)>1.96` 误判为显著。
- ~~E1 常数块连坐 ICIR~~ **已关闭（2026-11-08）**：剔除并计数 NaN 分块后计算均值/标准差，新增 `ICIR_NaN块数` 列；短样本仍返回 NaN。
- ~~资金账户「每日重试补仓」文案与脉冲信号矛盾~~ **已关闭（2026-11-08，文案层）**：`simulate_capital` 输出 `signal_semantics`，持续目标与脉冲事件分别给出正确的补仓说明。**仍待处理（行为层）**：脉冲遇涨跌停/容量不足后订单永久失效、残余现金不再投入；卖出部分成交后脉冲过期则残留持仓无法再清。彻底修复需给脉冲订单挂单直到成交或反向信号，会改变资金曲线，与既有报告数值不可直接比较。
- ~~分位口径未披露~~ **已关闭（2026-11-08，披露层）**：`context_payload.note` 明示含当日自身、最低 1/N、恒定窗口 100%。**仍待评估（口径层）**：排除末日改为严格历史分位会推翻 version-15 定义、改变训练输入与 8 处断言，须升版本并重放验证（`docs/历史分位缺失值修正_2026-11-01.md:19`）。
- ~~双源失败只留异常类名~~ **已关闭（2026-11-08）**：主/备源均保留 `类名: 200 字符摘要`（单行化），可区分网络超时、空数据、列名变更与代码错误。

**边界与防御**
- ~~`_normalize_holdings` 异常季度标签崩溃~~ **已关闭（2026-11-08）**：一次 `extract` + 合法掩码，只对 Q2/Q4 且年份在 Timestamp ns 范围内的行构造日期；`完整` 与 `季末` 共用同一掩码，不再误标。
- ~~`_run_one` 空 rows 抛 KeyError~~ **已关闭（2026-11-08）**：全部窗口被 `len(yw) < min_obs` 跳过时改抛 `ValueError("无有效拟合窗口…")`，由既有 `_failed_report` 统一兜底，不再出现难以定位的 `set_index("date")`。
- ~~meta 的 `n_rebal` 取首个模型~~ **已关闭（2026-11-08）**：抽出 `_n_rebal(reps)`，取任一非空 `weights_hist` 的长度；首个模型是 failed report 时不再显示 0。
- ~~持仓对齐隐含依赖升序输入~~ **已关闭（2026-11-08）**：复查发现 `holding_benchmark` 内部已有 `sort_index()`，真正缺的是 `align_vs_rbsa`；该公开入口现在对 exposure 与 weights 都显式排序（`_rbsa_at` 用 `searchsorted`，乱序会静默对错期）。
- ~~`build_pool` 空排除记录崩溃~~ **已关闭（2026-11-08）**：`frames` 为空时返回带固定三列的空表；顺带给"零入选"路径的 `pool` 补列，避免下一段 `pool["基金代码"]` 抛 KeyError。

**任务 / 数据层**
- ~~`queued` 永不过期 / 终态保留期写死~~ **已关闭（2026-11-08）**：`_expire()` 在每次 `enqueue` / `claim` 执行，终态按 `FIL_JOB_TERMINAL_TTL`（默认 24h，可配）物理删除；`queued` 超 `FIL_JOB_QUEUED_TTL`（默认 1h）转 `error` 并留「排队超时」原因，用户查得到而不是拿 404，worker 久停后不再永久 429。`recover()` 转回 queued 时刷新 `updated`，重试任务仍有完整 TTL 窗口。内存店仍是 30 分钟（`JOB_TTL_SEC`），二者用途不同（单进程回调 vs 跨进程可查），保留差异并已在配置文档写明。
- ~~取消/超时后名额瞬时释放~~ **已关闭（2026-11-08，时间衰减方案）**：`_active_count()` 把"刚异常终态、曾被领取（`lease_until IS NOT NULL`）"的任务在 `FIL_JOB_DRAIN_GRACE`（默认 30s）内仍计入 `max_jobs`，给 supervisor `terminate_tree`/`wait` 留出排水时间；纯时间衰减无需显式释放计数点，不会反过来造成永久 429，正常 `done` 不占位。**未改**：token fencing 与 supervisor `finally` 的重试语义。
- ~~`/api/result` 把用户取消当 500~~ **已关闭（2026-11-08）**：返回体加 `reason`（`cancelled` / `queue_timeout` / `failed`），取消改 409；前端仍按 `/api/job/{jid}` 的 `error` 分支展示，行为不变。
- ~~`end` 无未来上界~~ **已关闭（2026-11-08）**：`parse_window` 对晚于今天的 `end` 抛 `ValueError`（Web 转 400），不再让 2099 触发全量抓取；`today` 参数可注入，便于测试。
- ~~`--once` 绕过 `FIL_SYNC_ENABLED=0`~~ **已关闭（2026-11-08）**：抽出 `sync.enabled()`，`main()` 在 `--once` / `serve()` 分支之前统一判断；计划任务与常驻调度口径一致。
- ~~热点结果缓存被误删~~ **已关闭（2026-11-08）**：命中并成功反序列化后 `path.touch()` 续期 mtime；`touch` 失败只告警、不阻断返回（仍旧重算路径不变）。
- ~~只读链路仍有 DuckDB 写副作用~~ **已关闭（2026-11-08）**：`load_fund_nav_cached` 的 `sync.register` 与 `load_index_universe` 的 `_cache_info_write` 都改为仅 `allow_fetch=True` 执行；`_cached` 的旧 CSV 迁移在只读模式直接视为未命中。`allow_fetch=False` 现为零写入（缺列信息仍挂 `df.attrs`），不与 sync 大事务抢锁。
- ~~僵尸子进程 `wait` 超时炸监督循环~~ **已关闭（2026-11-08）**：抽出 `_stop_child()`，把 `terminate_tree`/`terminate` 与 `wait(timeout=10)` 的 `TimeoutExpired` 及其它异常收敛为日志 + `False`；4 处调用（超时回收、`finally` 回收、sync/settle 子进程）都不再冒泡。`child.poll()` 仍是"已退出"的唯一依据，未回收进程不会被误标重试。
- ~~损坏缓存无自愈~~ **已关闭（2026-11-08）**：`cache.py` 新增 `_safe_metadata()` / `_safe_read()`，损坏记录按未命中处理 —— 可抓取时由 builder 重建并覆盖坏记录，只读时抛 `CacheMiss`（上层 202）而不是 500；旧 CSV 迁移与描述信息写入也都加了异常兜底。同源加固另覆盖 `storage.stamp()`（单条坏 metadata 不再让全库结果缓存指纹崩）、`sync.cached()`（视为未就绪）、`sync.datasets()`（跳过坏行，不拖垮 discover / 同步整批）。
- ~~`_retry` 对编程错误重试~~ **已关闭（2026-11-08，复查确认此前已实现）**：`_non_retryable()` 对 `KeyError/TypeError/AttributeError/ValueError/ProviderContractError` 立即抛出，`JSONDecodeError`（ValueError 子类）特判为可重试；`sleep` 条件是 `i < tries-1`，末次不多睡。补 3 项回归锁定该行为，避免日后回退。
- ~~候选池 ≤200 行原因不可见~~ **已关闭（2026-11-08）**：`fetch_universe` 的 `fetch_errors` 现由 `load_index_universe` 跨 `storage`/`concat` 保留，写入 `info.fetch_errors` 与返回 `attrs`，并由 `load_panel` 汇入 `warnings` → meta / 报告 / 页面；`verbose=False` 时另发一条汇总 WARNING 日志。
- ~~`meta.through` 谎称新鲜~~ **已关闭（2026-11-08，保守方案）**：`through` 保留为"本次同步检查日"（改成数据末日会让周末/长假每次都判缓存失效），`sync.cached()` 增加对 `info.data_end` 的滞后校验：落后 `through` 超过 `FIL_DATA_STALE_DAYS`（默认 10 天）即视为缓存未就绪，交回正常抓取/冷缓存 202；旧缓存无 `data_end` 时保持原行为。**仍待评估**：是否把滞后数据直接标 `stale` 并强制重抓（会改变请求量与 `due()` 重试频率，需实测）。

**UI 打磨**
- ~~≤1150px 隐藏自选下拉~~ **已关闭（2026-11-08，复查确认此前已实现）**：自选早已移入共用 `.research-toolbar`，`.search{flex-wrap:wrap}` 让窄屏换行而非隐藏，1150px 媒体查询不含任何 `display:none`；旧 `style.css:481` 现为宽度规则。补静态断言防回退。
- ~~模型卡片/排名行键盘不可达~~ **已关闭（2026-11-08）**：`.mcard` 加 `tabindex="0" role="button" aria-pressed`，排名 `<tr>` 加 `tabindex="0" aria-selected`（保留 `row` 语义，不伪装成 button）；两处都补 Enter/Space → `click()`。
- ~~日期无 `max=今天` / 回测无预热预检~~ **已关闭（2026-11-08）**：`syncFundDates()` 与 `market.js switchKind()` 动态写 `max=今天`（`controls.js` 的 observer 会同步到日历增强控件）；`runBacktest()` 用 `nav.dates` 计算起点前的交易日数，<60 时前端直接提示，不再白占任务队列名额（结果不带日期轴时不拦截）。
- ~~结果缺字段留下「新头部+旧图表」混合页~~ **已关闭（2026-11-08）**：新增 `normalizeResult()` 把 `rank/meta.models/nav.dates/nav.nav/correlation.top/layers/drift/matrix` 统一补成空集合（浅拷贝，不改原对象）；`clearResultCharts()` 在渲染前 dispose `S.charts` 并清空 `#main .chart`（只动基金结果区，不碰市场图表）；`applyResult()` 事务化 —— 先隐藏 `#main` 再逐项渲染，任一步抛错即回滚 `S.result`、再次清图表、隐藏结果区并给出可读提示，成功后才显示。`renderRank()` 空排名不再对 `undefined` 取列；`renderBacktest()` 同样包 try/catch，`drawBtEquity()`/`drawBtHealth()` 对 `equity/health/meta.thresholds` 做存在性兜底。前端回归新增 `normalizeResult` 行为测试与 4 条静态防回退断言。
- ~~429/409/404 提示无差别~~ **已关闭（2026-11-08）**：`api()` 把 `status`/`retryAfter` 挂到错误对象，新增 `apiErrorText()` 对 429（队列忙 + 重试秒数）/409（已取消或状态冲突，重发）/404（已过期，重发）/503（存储忙）给出可操作提示；`market.js request()` 对 404/409/429 不再无差别重试 3 次。「409 不接续已有任务」复查确认此前已由 `/api/corr` 的 `202 + job_id` + `pollCorrelation()` 解决。
- ~~标签页隐藏后轮询不停~~ **已关闭（2026-11-08）**：新增模块级 `pagePaused`，由 `visibilitychange` 维护；基金/回测/持仓/相关性/市场 5 条轮询在 sleep 后用 `while (typeof pagePaused !== 'undefined' && pagePaused)` 等待，不消耗 `MAX_POLLS`/`polls` 额度。`typeof` 守卫是为了兼容前端回归的 vm 单函数切片（那些 context 没有该符号）。数据中心原本已跳过。
- ~~市场页 resize 无防抖~~ **已关闭（2026-11-08）**：`market.js` 照基金侧补 140ms debounce（`clearTimeout(rzT)`）。
- ~~图表/分段按钮/标题 ARIA 缺失~~ **已关闭（2026-11-08）**：`init()` 里对 `.chart:not([role])` 批量补 `role="img"` + 从面板标题取 `aria-label`（市场侧已有静态属性，不覆盖）；`trendSeg` 动态按钮补 `aria-pressed`（保留 `class="on"` 与文本，兼容 `dom_check.js`）；`market.js switchKind()` 按入口写 `document.title`。
- ~~自选失效代码 / catalog 无 loading 与重试~~ **已关闭（2026-11-08）**：`renderWatchlist()` 在名录就绪时按名称池过滤失效代码（未就绪则不过滤，避免一次性隐藏全部）；`pruneWatchlist()` 放在 vm 切片外做 localStorage 写回，由 `init()` / `toggleWatchlist()` 调用。catalog 加载新增 `#catalogState` loading 文案与 `#btnRetryCatalog` 重试按钮；数据中心 `loadNames()` 失败在 `syncUpdated` 留下可见提示，「刷新状态」会重新拉取。

## 专项检查：走势预测 / 买卖点参考 / 持仓拟合（2026-11-06 补记）

范围：`modeling/trends.py`、`modeling/signals.py`、`modeling/backtest.py`、`market/analysis.py`、`market/technical.py`、`market/targets.py`、`market/execution.py`、`funds/holdings.py`、`funds/payloads.py`、`api/static/market.js`、`api/static/app.js`。检查数据获取→计算→接口/前端→配置与降级的闭环、前视偏差、边界样本与回测/实盘口径。优先级沿用本文件定义：P1 影响正常计算或结果解释；P2 为特定条件或特定入口下错误/口径不自洽；P3 为健壮性、一致性或打磨。已知数据缺口（P1-10/P1-11 及「股票与指数」六项）不重复。

### P1（影响正常计算或结果解释）

- ~~**P1-19：估值通道把申万行业腿静默映射到沪深300 PE**~~ **已关闭（2026-11-06）**
  位置：`modeling/signals.py:47-51`（`PE_SYMBOL` 只映射 6 个宽基，`PE_FALLBACK = "沪深300"`）、`signals.py:122-126`（未映射资产一律回退）、`signals.py:146-151`（按权重重心算 `pe_agg`）。
  触发：风格权重含申万行业（801xxx）的基金——默认面板 47 个指数中有 31 个是行业指数。`PE_SYMBOL` 只覆盖 6 个宽基，其余 399xxx 宽基与全部 801 行业都会回退（宽基→宽基误差尚可，行业→宽基不可接受）。
  后果：`pe_pct`（`signals.py:400` 在 snapshot 中展示为「估值分位」）和 `val_score`（融合分 `w_val_pe=0.15`）实际取的是沪深300 的分位，却被按该基金行业权重解释；行业集中基金（如白酒/半导体）的估值结论完全错位，且没有任何覆盖率或降级提示。该错误还会进入 Web 默认回测图：`funds/pipeline.py:66` 用 `val_score` 构造「仅赛道估值通道」，`funds/payloads.py:585-587` 下发 `equity.series` 全部列，`app.js:2027-2043` 无过滤地绘制所有键，用户看到的默认曲线即含错误口径。
  修复方向：801 行业腿不参与 PE 分位（返回 NaN 并从分母剔除），只对非行业宽基保留兜底；输出 `pe_coverage`（真实 PE 覆盖的权重占比）与 `val_degraded`；补「100% 行业权重 → pe_pct 不可用」的最小回归。
  **实际修法（拍板：保留沪深300 兜底并显式标注）**：`signals.py:valuation_channel` 返回 `pe_real_weight/pe_fallback_weight/pe_coverage/val_available/val_degraded` 及 `attrs["valuation"]`（含 `note`、`fallback_assets` 的代码/名称/权重/行业或宽基类型）；`snapshot` 增加「估值口径」字段。标注透传到 CLI（`funds/pipeline.py` verbose 警示 + 兜底资产）、报告 3.2（橙色提示框）、Web 分析页 `#valNote`，以及 Web 默认回测图的「仅赛道估值通道」曲线说明（`funds/analysis.py` 新增该变体，`payloads.py` 下发 `valuation` 与 `variants[].note/degraded`）。回归见 `tests/funds/test_payloads.py:BacktestCurrentPayloadTest.test_valuation_fallback_reaches_variants_and_payload`。

### P2（特定条件 / 特定入口）

- ~~**P2-21：估值源整体不可用/列缺失仍返回中性 0，且分母保留该权重**~~ **已关闭（2026-11-06）**
  - **回查差距（已修）**：研究 CLI 路径 `funds/pipeline.py` 原无条件构造「仅赛道估值通道」，估值不可用时 `val_score` 为 NaN，会生成 NaN 曲线；现已复用与 Web 回测一致的可用性判断（`valuation.available` 且 `val_score.notna().any()`），不可用时不出该曲线并在 `valuation.excluded/note` 显式标注，verbose 增加提示。回归见 `tests/funds/test_pipeline_valuation.py`（不可用/可用两条路径 + 无 NaN 污染）。
  位置：`modeling/signals.py:117-119`（`pe_pct` 为 None/空 → `val_score` 全 0）、`signals.py:147-151`（无匹配列 → NaN）、`signals.py:233/238-240`（分母恒含 `w_val_pe`）。
  触发：PE 缓存缺失、断跑超过 csindex 约一个月窗口、离线/只读模式；与 P1-A 已修的「累积全 NaN」不同，这是数据源不可用的独立路径。
  后果：融合分被静默压向中性、`position` 系统性向 0.5 收缩，界面/CSV 仍显示「估值中性」，与真实中性不可区分。
  修复方向：通道不可用时返回 NaN，`compute` 按可用通道重归一化分母；payload 输出 `channels.val.available=false`，report 注明「估值通道不可用」。若产品决定「缺失即中性」，需在口径文档显式声明并保留标记。
  **实际修法**：`valuation_channel` 不可用（面板为空/无映射列/关键列整列 NaN）时 `val_score=NaN`、`attrs["valuation"]` 增加 `reason`/`weight=0`/`excluded=true`；`compute` 用逐行 `val_available & val_score.notna()` 计算有效权重 `val_weight_effective`，分母改为 `w_trend+w_dev+有效估值权重`，其余通道自动重归一；`snapshot` 增加原因/通道权重/已剔除。Web `valuationText`、报告估值提示按 `available=false` 改为「已剔除并重归一」，不再写「按宽基口径解读」；回测不再生成 NaN 的「仅赛道估值通道」曲线。回归见 `tests/modeling/test_signals_valuation.py`、`tests/funds/test_pipeline_valuation.py`。
  口径变化（影响历史数值）：估值数据缺失期间，综合分不再被 15% 的中性估值权重稀释，仓位会比旧口径更偏离 0.5、更贴近趋势/偏离通道结论；PE 数据正常时数值不变。

- ~~**P2-22：`analyze_market` 对指数预热历史用了错误的缓存 basis（库层默认参数）**~~ **已关闭（2026-11-06）**
  位置：`market/analysis.py:160`（`sync.cached(kind, code, adjust, warm_start, end)` 用外层 `adjust`，默认 `"qfq"`）；`market/analysis.py:70-76`（`load_bars` 对 index 强制 `adjust=""` 后才 `register/cached`）；`tasks/sync.py:54-55/135-146`（资产键含 basis，`cached` 不做归一化）。
  触发：直接调用 `analyze_market("index", ...)` 且未显式传 `adjust=""`（研究脚本、测试、未来调用方）；Web 路径因 `api/schemas.py:123-129` 已把 index 的 adjust 归一为 `""`，当前不触发。
  后果：`context=None` → 只用请求窗口（默认 1 年），`direction_forecast` 的 `MIN_TRAIN=252` 成熟样本永远不够，指数方向概率/校准/成交对比全部退化为「样本不足」；MA120 也缺少预热（前 120 日空线）。
  修复方向：入口统一 `basis = "" if kind == "index" else adjust` 后再 `sync.cached`/`analyze_technical`；补测试 mock `sync.cached` 断言 index 传入 `""`，并用短窗口验证 `context` 覆盖 warm_start。
  **实际修法**：新增 `market/analysis.py:normalize_adjust`，`load_bars` 与 `analyze_market` 入口共用；`analyze_technical` 也随之一并收到 index 的空口径。回归见 `tests/market/test_context.py:test_direct_index_entry_uses_same_cache_adjust_as_web`。
  口径变化：仅影响**直调**指数路径（研究脚本/测试）。此前直调指数会按前复权口径传 `adjust="qfq"` 给指标层，现在与 Web 一致按原始价；直调指数的 MA/水平位数值可能变化。Web 路径不变。

- ~~**P2-23：持仓基准把未映射股票残差按现金利率计息，与 CLI 口径说明矛盾**~~ **已关闭（2026-11-06）**
  位置：`funds/holdings.py:601-680`（`stock_weight = w.sum()`；`ret = stock_weight*(...) + (1-stock_weight)*cash_rate`，默认 `cash_rate=0.015`）；`research/commands/holdings.py:230-234`（明确「未映射权重 ≠ 现金……同样是股票仓位」）。
  触发：行业映射未覆盖的新股/北交所/停牌股权重较高的历史期次（覆盖率低的基金）。
  后果：残差被当成现金，持仓基准收益被系统性低估；与 RBSA/基金实际收益的差值把这段偏差算进「ML 拟合增量」，方向可能相反，尤其成长/新股暴露高的基金。与 P1-10 的股息缺口是两件事。
  修复方向：残差按可配置情景处理（`cash` / 宽基指数近似），默认改为宽基并在输出标注 `residual_treatment` 与当期 `exposure_coverage`；覆盖率低于阈值时把该基金对比标为 degraded、不进入均值汇总。
  **实际修法**：`holding_benchmark` 新增可选 `coverage`；只有 `100 − 合计`（明确现金/债券）按 `cash_rate` 计息，未映射股票与面板缺失行业一律 **0 收益**，并写入 `attrs["residual_treatment"]` 与 `attrs["diagnostics"]`；`fit_fund_holdings`/`holdings_payload` 下发 `benchmark` 诊断，CLI、报告 3.6、Web 持仓区块同步展示。回归见 `tests/funds/test_payloads.py` 的两个 P2-23 用例。
  **口径变化（影响历史数值）**：此前残差按 1.5% 现金收益计息，相当于把未映射股票当现金赚利息；现在残差 0 收益，**持仓基准年化收益会下降**，因而「基金 − 持仓基准」「RBSA − 持仓基准」的差额、以及据此得出的 ML 增量都会变化，覆盖率越低/未映射越多的基金变化越大。历史持仓拟合结论需按新口径重跑，旧数字不可直接引用。

### P3（健壮性 / 一致性）

- ~~**P3-A：规则买卖点历史只回传最近 12 次，长窗口静默丢点**~~ **已关闭（2026-11-06）**
  位置：`market/technical.py:277`（`"signals": records[-12:][::-1]`）；`api/static/market.js:161/279`（图上 markPoint 与列表直接使用该数组）。
  触发：观察窗口内首次满足次数 > 12（长区间）。
  后果：图上买卖点标记与列表只显示最近 12 次，早期信号无提示消失；标题「最近条件触发记录」不足以说明图上标记也被截断。
  修复方向：返回全量（或上限 50）并附 `signal_total`/`truncated`；前端标记过多时抽样但显示总数，至少在图表下方注明「仅展示最近 N 次」。
  **实际修法**：`signals` 改为按观察窗口全量返回，并新增 `signal_total`/`signals_truncated`（当前恒为全量、截断 0）；前端标题改为「观察窗口内条件触发记录」，note 显示窗口内总次数，若未来设上限会显式标注未列出数量。回归见 `tests/market/test_technical.py:test_signals_are_first_condition_and_use_past_only`。

- ~~**P3-B：`holding_benchmark` 静默丢弃面板缺失的行业列**~~ **已关闭（2026-11-06）**
  位置：`funds/holdings.py:639-641`（`cols = [c for c in exposure.columns if c in panel.columns]`，未记录缺失）。
  触发：`C.INDICES` 池变动或 exposure 出现池外行业代码。
  后果：缺失行业权重从 `stock_weight` 中消失，等价于多算现金腿，收益偏差不可见。
  修复方向：收集 `missing`，写 `attrs["missing_exposure_weight"]` 并在 CLI/报告提示；超过阈值（如 5%）时把结果标 degraded。
  **实际修法**：收集 `missing = [c for c in exposure.columns if c not in panel.columns]`，写入 `attrs["missing_industries"]` 与逐期 `attrs["diagnostics"]`（面板缺失行业/权重、已映射权重、未映射权重、现金债券权重、生效日）；缺失权重按 **0 收益** 处理而非现金。`fit_fund_holdings` 新增 `benchmark` 字段，CLI 打印警示与缺失行业列表，报告 3.6 与 Web 持仓区块展示同一诊断。全部行业都缺时返回空序列并附 `note`，`benchmark.available=false`。回归见 `tests/funds/test_payloads.py:HoldingBenchmarkPanelGapTest`。

- ~~**P3-C：持仓拟合没有 Web/API 入口（仅 CLI 研究命令）**~~ **已关闭（2026-11-06）**
  - **回查差距（已修）**：此前测试只覆盖路由参数转发与 400 校验（mock 掉 `holdings`），无 payload 内容/报告 3.6 渲染断言。现补 `tests/funds/test_payloads.py:HoldingsPayloadTest`（逐期记录、行业表首列「行业」、4 条披露约束原文、比较/基准/诊断明细、失败态显式原因）与 `tests/research/test_report_render.py:HoldingsSectionRenderTests`（3.6 实际数值、首末期相关、缺失行业告警、残差按 0 收益口径、未生成提示），未 mock 核心逻辑；原路由契约测试保留。
  位置：`cli/registry.py:21`（`holdings` 命令）、`research/commands/holdings.py`；`api/routes/` 无对应路由，`funds/payloads.py` 不含持仓拟合结果。
  触发：用户在 Web 工作台查找「持仓拟合/行业暴露/持仓基准」。
  后果：能力只在 `run.py holdings` 可得（CSV 落在 `output/results/`），Web 与基金报告均无入口。若定位为产品能力则链路未闭环；若定位为研究 CLI 则只需明确边界。
  修复方向（二选一，需确认）：①产品化：新增任务类型 + `/api/holdings` + 结果页（复用 H0~H3 的 CSV 口径）；②保留研究：README/页面明确「持仓拟合为 CLI 研究能力」。
  **实际修法（拍板：产品化接入 Web 与报告）**：新增 `funds/holdings.py:fit_fund_holdings`（CLI/API/报告共用一条链路）与 `funds/payloads.py:holdings_payload`；任务队列新增 `holdings` 操作与 `POST /api/holdings`（`schemas.py:HoldingsRequest`）；基金页新增「真实持仓拟合」区块（运行按钮、逐期对齐表、基准对比表）；报告新增 3.6 章节。披露不退化：仍只用半年报/年报、Q2 +62 天 / Q4 +90 天、共同行业归一到 100%、持仓基准 ≥60 日共同区间，失败时显式「未生成」而不是省略。现有回归只有 `tests/api/test_routes.py` 的路由契约（任务参数转发/400 校验），**不含 payload 内容与报告 3.6 渲染断言**，见上方回查差距。

### 已核验合理（留痕，不设待办）

- **趋势标签严格因果**：`classify_at` 只用 `[t−2n+1, t]` 的前后段结构（`modeling/trends.py:8-60`），追加未来数据不改变历史标签；基金「走势带」贴在**样本外合成基准**上且 UI 明示（`funds/payloads.py:271-285`、`funds/analysis.py:384`、`app.js` 文案），不是基金自身 NAV 形态，也不是方向预测。
- **方向概率无前视**：训练集按「标签已成熟」截断，REFIT 只用过去标签；样本不足时返回 `ok=false`+reason（`market/technical.py:83-145`）。`horizon` 缺报价不借用后续报价（`market/targets.py`）。
- **买卖点执行口径一致**：t 日收盘出条件、t+1 开盘成交（`market/execution.py:62-109`），涨跌停/容量/费用有阻断原因；中间概率不生成新订单、持仓保持（`probability_targets`）；规则条件互相排斥，不存在同日买卖冲突。
- **基金择时无前视**：decision 在 t 日、回测 `shift(1)` 生效（`modeling/backtest.py:243-246`）；健康门控在拟合不可信时只禁止卖出；零交易有 `no_trades`/`health_stats.reachable` 诊断（`signals.py:425-470`）。研究结论（日频方向信号无预测力）见 `docs/研究方案.md:912-924`、`README.md:302`；2026-11-08 已按拍板口径把方向概率、规则条件与仓位状态统一定位为**研究诊断**，并在页面提示、结果 `notices` 与全站披露中同步标注该结论，不再声称「只描述、不建议」。
- **持仓拟合时点处理正确**：披露滞后 Q2=62 天 / Q4=90 天、基准按披露日切换、RBSA 对齐只比共同行业并披露 coverage 与重估日（`funds/holdings.py:61-75/485-576`）；持仓基准对比要求共同区间 ≥60 日（`holdings.py:703-710`）；未映射股票与面板缺失行业已按 0 收益处理（P2-23/P3-B 已关闭），只有明确现金/债券部分计息。

### 已拍板口径（2026-11-06 已实施，留痕不设待办）

- **基金走势输出方向概率**：原「只描述形态」保留为形态色带，新增独立的「方向概率（模型参考）」区块；复用 `market/technical.py:series_forecast` → `direction_forecast`，标签按成熟期截断、horizon 缺报价不外借、样本不足返回 `available=false`+reason。输出区分「形态描述」与「概率」，两处都标注不构成买卖建议。
- **基金 Web 展示当前建议仓位/健康分**：回测结果新增 `current`（日期、是否可用、是否降级、仓位、状态、健康分、距离场线、原因清单、免责声明）；缺健康分或门控不可触发/拟合不可信时显式「不可用」或降级标注，不把默认仓位显示成正常值。
- **P1-19 修法**：保留沪深300 兜底，但显式标注「行业估值不可用，已用宽基替代」，并暴露 `fallback_assets` 便于后续补行业 PE 数据源（详见 P1-19 实际修法）。
- **P3-C 持仓拟合接入 Web 与报告**：已产品化，详见 P3-C 实际修法。

## 生产上线评估（2026-11-06）

范围：以「直接把当前系统作为生产系统上线」为前提，对数据链路、计算正确性、运行可靠性、生产必需能力、产品与合规、工程质量做**静态审查**（只读源码、README、Dockerfile 与测试说明，未改代码、未跑长任务）。优先级沿用本文件定义，并按本次要求收紧：**P1 仅用于会导致结果错误、数据丢失或无法运行的问题**；可由运维手段（鉴权反代、卷快照、外部日志采集、容器资源限制）规避的不计 P1。每条标注「缺失功能」或「逻辑/设计问题」；与已关闭/在办条目相关但不重复的，注明区别。

### P1（阻塞上线）

- ~~**P1-20（逻辑/设计问题）：总回报口径构造失败后静默改用累计净值，输出仍宣称 total 口径**~~ **已关闭（2026-11-06）**
  - **回查差距（已修）**：核心拒绝/标记逻辑、Web/API 的 `meta.data_basis` 已落地；此前 CLI/报告侧缺口径标签。现 `funds/pipeline.run_fund` 输出 `meta.data_basis/nav_basis/nav_degraded/nav_degraded_reason`，verbose 打印「总回报 / 累计净值（降级）」，`research/commands/report.py` 把同样的字段写入报告 meta，报告 hero 固定渲染净值口径（降级用警示样式并带 `data_basis`/原因）。回归见 `tests/funds/test_pipeline_valuation.py:NavBasisMetaTests`、`tests/research/test_report_render.py:NavBasisRenderTests`。
  - 位置：`data/panels.py:218-242`（`_DegradedNav` 在 `data/panels.py:228-230` 正确拒绝写 total 缓存，但 `except` 分支 `data/panels.py:237-239` 直接 `return fetch_fund_nav(...)`，返回序列不带退化标记）；`data/panels.py:308-310` 的打印标签仍按请求的 `nav_basis` 写「总回报」；`funds/analysis.py:229` 的 `meta.data_basis` 固定写「事后月度锚点校准」。
  - 为什么生产不成立：只要单位净值或月频锚点缺失（新基金、分红/拆分修订、第三方接口偶发失败），本次分析就在累计净值上计算，而累计净值相对总回报实测平均低估约 23%（`data/providers/funds.py:156-160`），RBSA 权重、健康分、择时仓位、回测净值、报告与导出全部口径错误；同一参数两次运行还可能一次命中 total 同步历史、一次走 cum 直取（同步路径在 `tasks/sync.py:166-169` 遇降级会 `raise` 保留旧数据，分析路径却绕过该保护）。
  - 影响面：所有基金入口（CLI/Web/API/报告），覆盖率低或分红多的基金最严重；结果不可复现、不可审计。
  - 建议：退化时返回带 `attrs["nav_basis"]="cum"`/`nav_degraded=True` 的序列，并在 `meta`、CLI、报告、Web、导出 JSON 统一显示；或与同步一致，无 total 时直接 `ok=false` 拒绝，仅在显式 `basis=cum` 时允许。补「total 降级 → 不落 total 缓存且输出标记」的回归。
  - 与 P2 第 60 行的区别：那条是「未校准 total 因缺 `degraded` 被永久缓存」；本条是「已有 `degraded` 后，分析路径仍静默改用 cum」，触发点和后果都独立。
  - **实际修法（拍板：默认拒绝降级）**：新增 `config.NAV_DEGRADED_POLICY`（`reject` 默认 / `allow_cum` 显式降级，env `FIL_NAV_DEGRADED_POLICY`）；`load_fund_nav_cached` 在 total 构造退化时默认抛 `errors.NavBasisUnavailable`（带原因、受影响口径字段、数据基准说明与两条放行方式），失败经任务系统变成 job `status=error` 可见，不再静默返回 cum；`allow_cum` 下返回带 `nav_basis=cum`/`nav_degraded=True`/`nav_degraded_reason` 的序列且绝不落 total 缓存。实际口径挂在 Series/panel `attrs` 并透传到 `meta`（新增 `nav_basis`/`nav_degraded`/`nav_degraded_reason`，`data_basis` 由 `funds/analysis.py:nav_basis_meta` 按实际序列生成），CLI 标签改为「总回报/累计净值(降级)」，降级时 `data_warnings` 追加说明。同步路径（`tasks/sync.py`）原有的降级即 `raise` 保持不变。
  - **口径变化（影响历史结论）**：total 正常构造时数值不变（同一输入结果完全一致）；此前「total 退化 → 静默按 cum 计算」的那些分析会由「看似正常的结果」变为**显式失败**，或（仅在显式 `allow_cum` 时）变为带 `nav_degraded` 标记的结果，`data_basis` 不再宣称「事后月度锚点校准」。同时 `DATA_CACHE_VERSION 5→6` 让旧实现写进 `_total` 缓存名下的 cum 数据失效重抓，`CACHE_VERSION 10→11` 让此前可能基于 cum 的结果缓存失效。**需要重跑**：任何在 total 构造失败时生成的基金分析、回测、报告与导出（尤其新基金/分红拆分频繁/单位净值缺失的代码）；`allow_cum` 下产出的历史结论不能与 total 口径结果混用。
  - 回归：`tests/data/test_panels.py:NavBasisPolicyTests`（reject 抛错且不落缓存 / allow_cum 标记且不缓存 / total 成功缓存往返 / `load_panel` 透传降级警告）、`NavBasisMetaTests`（三种口径文案）；`tests/funds`、`tests/market/test_nav_calibration.py`、`tests/market/test_calibration.py` 与两个 regression 脚本全通过。

### P2（上线前应修）

- **P2-24（缺失功能）：认证、授权与配额** **部分落地（2026-11-06 回查）**
  - **回查差距**：可开关令牌认证（`FIL_ACCESS_CONTROL=token`）、身份/租户 `ContextVar` 与审计 actor/tenant 已落地并有 `tests/api/test_access_control.py`；未落地：按用户/租户的任务与结果分区、每用户并发/队列配额、注册/登录/OIDC（本轮口径为单用户默认，仅预留升级入口）。
  - 现状：`api/app.py:64-70` 只装 `BodyLimit` 与可选 CORS，无任何认证/授权中间件；`README.md:46` 明示「没有登录/鉴权」；`tasks/store.py:56-59` 只有全局 `queue_limit`，无按客户端限流；各路由无用户/租户维度。
  - 为什么生产不成立：按 README 的 `0.0.0.0:8877` 暴露后，同网段任何人都能提交高成本任务、读取全部基金结果与持仓、耗尽 100 个队列名额和计算名额；无隔离、无配额。
  - 影响面：滥用、数据泄露、拒绝服务；无法支持多用户。
  - 建议：单人内网场景保持 `127.0.0.1` + 鉴权反代，并把该约束写进启动检查；产品化则需 API Key/OIDC、租户隔离、每用户并发/队列配额与审计（与 P2-26 合并设计）。
  - 优先级说明：反代可降低风险，故不列 P1；但多用户上线前必须修。
  - **实际修法（拍板：单用户为主、保留多用户升级入口）**：新增 `market_research/security.py`：
    - `Identity`（subject / tenant / scopes / authenticated）+ `ContextVar` 请求上下文，服务层统一用 `current_identity()` 读取，不解析 HTTP 头；审计记录在认证模式下自动带 `actor/tenant`。
    - `AccessControl` 中间件：`FIL_ACCESS_CONTROL=off`（默认）完全放行，行为与之前一致；`token` 模式校验 `Authorization: Bearer` / `X-API-Key`，未授权返回 401 + `WWW-Authenticate`；`FIL_API_TOKENS` 支持逗号分隔与 `租户:令牌`，为多租户预留。`/`、`/api/health`、`/api/ready`、`/api/disclosure` 与静态资源保持公开。
    - 启动日志打印「单用户模式（未启用认证）」或「令牌模式（N 个令牌）」；`/api/health` 返回 `access_control` 模式。
    - **未做（明确预留）**：注册/登录、按用户/租户的任务与结果缓存分区、每用户并发/队列配额、OIDC。接入多用户时在中间件后按 `Identity.tenant` 分区数据、在 `tasks/jobs.py` 提交处加租户字段即可，不需要重写路由调用链。
  - 配置文档：`docs/运行环境与配置.md`（默认 `off`，token 模式说明）。
  - 回归：`tests/api/test_access_control.py`（默认放行、缺令牌/错令牌 401、正确令牌 200、探针豁免、身份带 tenant/scopes、本地主体匿名）；`tests/api` 全通过。
  - 语义变化：默认单用户模式下无任何变化；仅开启 `FIL_ACCESS_CONTROL=token` 后业务接口才要求令牌。

- **P2-25（缺失功能）：健康检查、指标与错误上报** **部分落地（2026-11-06 回查）**
  - **回查差距**：`/api/ready` 的存储/worker/队列/同步检查、503 语义与 Dockerfile `HEALTHCHECK` 切换已落地并有 `tests/api/test_routes.py:HealthTests`；未落地：Prometheus 指标、request id、结构化错误上报、worker 独立心跳文件（当前仅校验 pid 存活）。
  - 现状：`api/routes/meta.py:8-10` 的 `/api/health` 只返回静态 `ok/version/framework/models`，不触达 DuckDB、不检查 worker、不检查同步状态和队列深度；`Dockerfile:54-55` 直接用它做 HEALTHCHECK；`api/app.py:54-61` 的 lifespan 只在启动时调用 `JOBS.migrate()` 与 `launch()`。
  - 为什么生产不成立：数据库被锁死/损坏、worker 未启动或反复崩溃、同步连续失败、队列积压时，容器仍显示 healthy，用户看到任务永远 `queued`；没有指标和错误上报，只能翻 `worker.log`/`job-*.log`。
  - 影响面：故障发现延迟、排障成本、SLO 不可度量。
  - 建议：拆出 `/api/ready`（执行 `SELECT 1`、校验 worker pid/心跳、最近同步成功时间、队列/运行数）并让 HEALTHCHECK 使用；接入结构化日志 + request id、Prometheus 指标（任务数/耗时/失败率/缓存命中/同步结果）与错误上报；`/api/health` 保留为纯存活探针。
  - **实际修法**：
    - `/api/health` 保留为纯存活探针（新增 `probe=liveness`、`pid`、`uptime_s`，不触达依赖）；
    - 新增 `/api/ready`：`storage` 实际执行 `SELECT 1`（带 2 秒超时，返回延迟/原因）、`worker` 校验 `worker.pid` 且进程存活（Windows 用 `OpenProcess`，POSIX 用 `os.kill(pid,0)`）、`jobs` 读取 `DurableJobStore.stats()` 的状态分布、`sync` 读取 `valuation_sync.json.last_success` 与阈值（`2×FIL_DATA_STALE_DAYS`，同步关闭时返回 enabled=false）、并附 `storage.LOCK_STATS`；
    - 判定：storage/worker/jobs 任一失败 → `status=unhealthy` 且 HTTP **503**；仅同步过期 → `degraded` + 200；否则 `ok`；
    - `Dockerfile:54-57` 的 HEALTHCHECK 改用 `/api/ready`（503 会让容器标记 unhealthy）。
  - **未做**：Prometheus 指标、request id、结构化错误上报（需要产品/运维确定后端与保留策略，未声称完成）；worker 只校验 pid，没有独立心跳文件（supervisor 崩溃但 pid 残留的极端情形识别不了）。
  - 回归：`tests/api/test_routes.py:HealthTests`（存活探针不依赖存储、依赖故障 503、同步过期 degraded）；`tests/api` 全通过。

- **P2-26（缺失功能）：审计与操作日志** **部分落地（2026-11-06 回查）**
  - **回查差距**：JSONL 审计（5MB×5 轮转、脱敏）及分析/报告发布/数据写入三类记录已落地并有 `tests/data/test_audit.py`；未落地：操作者/IP 字段（认证模式下仅 actor/tenant）、审计查询接口、保留期策略与防篡改。
  - 现状：`tasks/store.py:47-60` 的任务表只记录 `code/models/operation/arguments/created/updated`，无操作者、来源 IP、结果版本；`DurableJobStore.public` 不下发 `arguments`；日志仅 Uvicorn stdout 与 `worker.log`/`job-*.log`（`README.md:194`），无保留期限与不可篡改要求。
  - 为什么生产不成立：无法回答「谁在何时、用什么参数和哪版数据、得到了哪个结果、是否发布过」；任务终态 24 小时后被清理（`tasks/store.py:51` 附近），事故复盘和合规审计没有依据。
  - 影响面：内部治理、合规、结果争议追溯。
  - 建议：新增审计表（actor、ip、operation、参数摘要、`data_stamp`、模型状态、耗时、结果 locator）与结构化日志；明确保留期、脱敏规则与查询接口；审计记录不复用 24h 任务清理。
  - **实际修法**：新增 `market_research/audit.py`：JSONL（`DATA_DIR/audit.jsonl`，5MB×5 轮转），每次追加后立即关闭句柄（避免 Windows 上常驻句柄阻塞目录清理/轮转）；`_sanitize` 对 `params/arguments/headers/token/cookie/authorization` 等键一律替换为 `<redacted>`，字符串截断 200 字符、层级 >2 截断，写入失败静默不影响主流程。记录三类关键操作：
    - **分析运行**：`services/analysis.execute()` 统一包装 `_dispatch`，成功/失败都记 `analysis.run`（operation、code、job_id、status、completeness 状态与缺失项、模型数、耗时；异常记 `type: message`）；CLI/报告链路 `funds/pipeline.run_fund` 末尾追加一条（窗口、状态、缺失项、数据质量）。
    - **报告发布**：`research/commands/report.py` 记 `report.publish`（fund、path、门禁是否通过、是否 `--allow-incomplete` 强制、数据质量/完整性状态）；`PublishRefused` 前记失败与问题清单。
    - **数据写入**：`data/storage.write()` 记 `data.write`（dataset 名、行数、列数、revision 前 12 位；事务回滚记失败），不含任何行情数值。
  - **未做**：无操作者/IP（当前无认证体系，取决于 P2-24 部署形态）、审计查询接口与保留期策略（需产品/合规口径）、审计表入库与防篡改。审计对象仍复用 `DATA_DIR`，未与 24h 任务清理绑定（JSONL 本身不被任务清理触碰）。
  - 回归：`tests/data/test_audit.py`（字段完整、敏感键脱敏、字符串/深度截断、写入失败静默）；`tests/data`(48)、`tests/funds`(37)、`tests/api`(22)、`tests/research/test_report_render`(10) 全通过。
  - 语义变化：无业务返回值变化；副作用是 `DATA_DIR/audit.jsonl` 会新增记录（同步期间每个数据集写入一条，5MB 轮转）。

- ~~**P2-27（逻辑/设计问题）：缓存新鲜度与数据源故障降级不统一**~~ **已关闭（2026-11-06）**
  - 现状：`data/cache.py:62-85` 的 `_cached` 只用 meta 指纹判等，不校验数据年龄；`index_universe` 的 meta 只有 `v/n_candidates/start/end`（`data/panels.py:149-159`），`info.data_end` 仅作描述（`data/panels.py:88-104`、`175-178`）；builder 失败时 `data/cache.py:83-85` 直接抛错，没有「回退旧缓存 + stale 标记」路径（`_cached` 的 docstring 虽称「刷新失败保留上一份数据」，但代码未实现该分支）；只有同步路径有保旧逻辑（`tasks/sync.py:188-208`）。
  - 为什么生产不成立：同步停用/失败或容器长期不运行时，基金分析会无限期使用旧指数池，用户把旧暴露/旧净值当成当前；反过来，数据源短时故障会直接让所有分析任务失败，即使本地已有完整旧数据；同一天多次运行的可用性随网络抖动。
  - 影响面：结果时效性不可判断、可用性受单次网络故障影响。
  - 建议：统一新鲜度模型——缓存读取校验 `data_end` 与预期最近交易日（结合交易日历）差，超过阈值输出 `stale_days/stale=true` 并在 payload/UI/报告标注；读取路径支持 `allow_stale`：取数失败回退最近缓存并附 `source_error`；关键结论（当前仓位/估值）默认拒绝超阈值 stale，历史诊断可带标记继续。
  - 与 P3 第 104 行的区别：那条是同步历史 `meta.through` 写当天导致旧数据被误判新鲜；本条是全局指数池缓存根本没有新鲜度字段，且缺故障回退。建议一并统一，但影响面不同。
  - **实际修法**：新增 `data/quality.py` 作为唯一判定入口：`freshness()` 按数据末日与参考日（`min(今天, 请求 end)`）计算 `stale_days/stale`，`build_quality()` 把过期、候选缺失（占比 ≥20% 升为 degraded）、净值口径降级、取数失败回退合并成 `status=ok/partial/degraded/failed + reasons + missing_assets/n_missing/n_expected`，阈值由 `C.DATA_STALE_DAYS`（env `FIL_DATA_STALE_DAYS`，默认 10 天）控制。`_cached` 在 builder 抛错且**旧缓存 meta 仍匹配**时回退旧数据，挂 `attrs.stale/source_error/cached_at` 并写 `info.last_error`（meta 不匹配时仍抛错，避免 P1-20 的污染缓存被复用）；`load_index_universe` 把缺失清单与回退标记写入 `attrs`；`load_panel` 汇总为 `attrs.data_quality` 并把 reasons 并入 `data_warnings`。透传：分析 `meta.data_quality/data_warnings`、CLI 首行警示、`meta.json` 的 `数据质量`、报告头部警示条、Web 元信息徽标（显示 status 与原因）。
  - 回归：`tests/data/test_panels.py:DataQualityTests`（ok/过期 partial/缺失 partial→degraded/口径与回退 degraded/旧缓存回退可见/meta 不匹配拒绝回退）；`tests/data`、`tests/funds`、`tests/api` 全通过。
  - 语义变化：取数失败不再一律任务失败，而是在本地有同口径旧缓存时**带 stale/degraded 标记继续**（无缓存仍失败）；新增 `meta.data_quality` 字段，`data_warnings` 会多出过期/缺失原因；历史窗口分析（`end` 为过去日期）不会因「距今久」被误标 stale。

- **P2-28（逻辑/设计问题）：候选指数缺失没有进入用户可见口径** **部分落地（2026-11-06 回查）**
  - **回查差距**：`missing_assets/n_missing/n_expected` 进入 `meta.data_quality`、`data_warnings` 与回测/相关接口已落地并有 `DataQualityTests`；未落地：关键宽基缺失单独升级 `degraded`（需产品先定义「关键指数」清单）。
  - 现状：`data/panels.py:160-185` 把缺失指数写入 `_cache_info_write` 并 `LOG.warning`；`load_panel` 的 `warnings` 只来自 `align_prices`（`data/panels.py:311-321`）；`funds/analysis.py:230-232` 只透传 `panel.attrs["warnings"]`，cache info 里的 `missing_assets/n_missing/n_expected` 没有进入 payload。
  - 为什么生产不成立：47 个候选缺 3 个时，RBSA 候选池缩小、权重重新分配，用户看到 `meta.n_assets`（实际列数）却不知道少了谁、为什么少；若缺的是基金主风格指数，结论可能反转而无提示。
  - 影响面：RBSA 画像、风格漂移、估值覆盖与回测变体的解释力。
  - 建议：把 `missing_assets/n_missing/n_expected` 从 cache info 提升到 `panel.attrs` 与 payload `meta.data_quality`；缺失比例超阈值或关键宽基缺失时标 `degraded`；CLI/报告/Web 显示缺失清单与重试建议。
  - **实际修法**：`load_index_universe` 把 `missing_assets/n_expected` 写入返回 DataFrame 的 `attrs`；`load_panel` 交给 `build_quality` 合并（缺失占比 ≥20% 判 `degraded`，少量缺失判 `partial`，缺失清单与「重跑可补齐」提示进入 `data_quality.reasons` 和 `data_warnings`）。分析 `meta` 新增 `missing_assets`、`n_assets_expected`、`data_quality`；回测 payload 的 `meta` 同样带 `data_quality`；滚动相关轻量接口返回 `data_quality/data_warnings`；Web 元信息显示「候选指数 实际/应有」并附缺失清单 tooltip，回测页新增数据质量提示；CLI/报告延续 P2-27 的警示。未实现的建议项：**关键宽基缺失单独升级 degraded**（需要产品定义哪些指数是关键），已在挂起口径中说明，不声称完成。
  - 回归：`tests/data/test_panels.py:DataQualityTests.test_load_panel_reports_missing_candidates`；`tests/data`、`tests/api`、`tests/funds` 全通过。

- **P2-29（缺失功能）：数据源单点、限流与供应商契约** **部分落地（2026-11-06 回查）**
  - **回查差距**：全进程限流、指数退避、编程错误不重试、`data/provenance.py` 溯源与 `ProviderContractError` 契约校验已落地并有 `tests/data/test_http.py`；未落地：备用源/本地归档、熔断与 provider 健康度降权（本轮口径为继续公开抓取）。
  - 现状：指数走 `data/providers/indices.py:41-60` 的 6 线程批量抓取；基金总回报需 3 个接口（`data/providers/funds.py:224-260`）；估值走 csindex（`data/providers/valuation.py`）；重试为固定 4 次、线性 1.2s（`data/http.py:36-47`）；同步进程与 2 个分析进程各自独立访问，无全局令牌桶/熔断/provider 健康状态。
  - 为什么生产不成立：全部依赖同一类公开接口（akshare/新浪/东财/中证），限流、反爬或字段变更会让同步与分析同时失败；多进程并发放大请求；30 分钟冷却重试在长时间故障时持续打满上游，进一步触发封禁。
  - 影响面：可用性、数据完整性、被抓取方封禁风险。
  - 建议：全局按 host 限速 + jitter/backoff + 熔断；缓存优先（stale 可服务时先返回并标记）；至少一条独立备用源或本地归档；为 provider 关键列建立契约 fixture 与监控（与 P2-35 共用）。
  - **实际修法（拍板：继续公开抓取）**：
    - 限流：`data/http.py:rate_limit()` 全进程共享最小请求间隔，`FIL_HTTP_MAX_RPS` 默认 8（`<=0` 关闭），在 `_retry` 每次尝试前执行。
    - 重试：改为指数退避 `wait × 2^i`（上限 30s + 抖动）；`ValueError/KeyError/TypeError/AttributeError/ProviderContractError` 判定为编程/契约错误，**不重试**；网络/超时类才重试，避免空转四次。
    - 降级路径保持统一：重试耗尽后交给既有 `_cached` 回退（stale + `source_error`，P2-27），不在各 provider 各自实现。
    - 溯源：新增 `data/provenance.py`，每次抓取记录 `source/endpoint/last_fetch_at/ok/degraded/error/calls`，由 `build_quality(provenance=...)` 写入 `meta.data_quality.provenance`（Web/报告/接口可见）。
    - 供应商契约：`data/provenance.py:CONTRACTS` + [数据源供应商契约](docs/数据源供应商契约.md) 固化端点与字段；`require_columns` / `_require_nav_frame` 在缺列、空表、列改名时抛 `ProviderContractError`（含来源、实际列、端点）；`fetch_pe_panel/fetch_dividend_panel` 不再吞掉契约错误；候选池**全部**失败时 `fetch_universe` 显式抛错，部分失败仍进入 `missing_assets`。
    - **未做**：备用源/本地归档、熔断与 provider 健康度降权（拍板不采购合规源，且需要新增数据源工程；未声称完成）。
  - 回归：`tests/data/test_http.py:RetryAndProvenanceTests/ContractValidationTests`（瞬时错误重试成功、编程错误只调一次、耗尽标记 degraded、限流参数解析、缺列报错含端点）；`tests/data`、`tests/market`、`tests/research/test_report_render` 全通过。
  - 语义变化：抓取默认增加限流（总体变慢但不改变数值）；编程错误不再重试 4 次；契约变化由「静默用错列/空表」变为显式失败；`data_quality` 新增 `provenance` 字段。

- **P2-30（逻辑/设计问题）：DuckDB 全局写锁使请求路径与同步互相阻塞** **部分落地（2026-11-06 回查）**
  - **回查差距**：有限等待、`StorageBusy`、`LOCK_STATS` 已落地；此前 `StorageBusy` 未映射为明确 HTTP 状态。现注册 FastAPI 异常处理映射为 **503**（`ok=false`、`field=storage`、可读原因、`retry_after_s`），并让 `/api/corr` 的通用 `except Exception` 不再吞掉它；错误码已登记到 README。回归见 `tests/api/test_routes.py:test_storage_lock_timeout_maps_to_retryable_503`。未落地：读写分离/独立库文件/多列合并写入，读写仍共用一把锁（只保证不无限阻塞）。
  - 现状：`data/storage.py:18-40` 每次连接都取阻塞式跨进程文件锁 + 进程内 `RLock`；所有数据集读取/写入都走短连接；写入是整表 `DELETE` + `INSERT`（`data/storage.py:72-82`）；同步逐资产「读整表 + 写整表」（`tasks/sync.py:296-309`），47 个指数各一次。
  - 为什么生产不成立：每日同步或任一 CLI 研究写入期间，API 的入队、状态查询、结果读取、`/api/name`、`/api/sync` 都会排队；没有 busy timeout 与只读降级，长批次会让请求线程长时间阻塞，p99 尖刺甚至触发网关超时。
  - 影响面：请求延迟、线程池耗尽、同步与在线服务争抢。
  - 建议：读路径使用只读连接或 `busy_timeout` + 有限重试；把 47 个指数的更新合并为一次多列写入/单事务，减少锁次数；同步放到独立库文件或低峰执行；加锁等待指标与告警。
  - **实际修法**：`storage.connection()` 改为**非阻塞轮询 + 有限等待**：进程内 `RLock` 与跨进程文件锁都按 `FIL_STORAGE_LOCK_TIMEOUT`（默认 10 秒）等待，超时抛 `errors.StorageBusy`，错误信息明确说明「同步/其它进程正在写入」与排查入口；新增 `storage.LOCK_STATS`（waits/timeouts/total_wait_s），等待超过 0.5 秒写 warning 日志。持有锁的范围不变（仍是每次连接内的短事务），同步的联网抓取仍在锁外。**没有做**：读写分离/独立库文件/多列合并写入（需要改存储模型与同步链路，风险大），因此「彻底解耦」未完成；当前保证「不会无提示卡死」。
  - 语义变化：锁竞争从「无限阻塞」变为「10 秒后明确失败」；请求侧收到 **503**（`field=storage`、可读原因与 `retry_after_s`）后可稍后重试，同步侧若被长时间占用也会失败并在 `sync_assets.error` 留下原因。
  - 回归：`tests/data/test_storage.py:test_connection_times_out_with_explicit_busy_error`（线程持锁 → 超时抛 StorageBusy 并计数）、原有跨进程写入测试仍通过；`tests/data` 全通过。

- **P2-31（缺失功能）：分析结果的完整性/部分失败没有契约** **部分落地（2026-11-06 回查）**
  - **回查差距**：`result_completeness()`（`status/ok/sections/missing/reasons`）与 `/api/result` 透传 `status/completeness`、Web 徽标已落地并有 `CompletenessTests`；未落地：失败模型从 `rank/best/insights` 强制排除（当前只标记、排名仍保留失败行），以及页面顶部统一的部分失败横幅。
  - 现状：`services.analysis.execute('analyze')` 原样返回 `analyze()` 的 dict（`funds/analysis.py:186-238`），没有 `ok/status/partial_failed`；单模型失败由 `_failed_report` 填 NaN 与 `extra.error`（`modeling/engine.py:10-24`），`rank` 仍保留失败行（`modeling/engine.py:377-392`）；`/api/result` 对 `done` 一律返回 `{"ok":true}`（`api/routes/tasks.py:14-23`）。
  - 为什么生产不成立：11 个模型失败 8 个时任务仍显示「完成」，排名/insights 可能选中空权重或出现 NaN；调用方无法判断结果能否引用、能否发布。
  - 影响面：Web/API/报告的结论可信度；自动化下游无法做质量门禁。
  - 建议：结果增加 `status`（ok/degraded/failed）、`failed_models`、`degraded_reasons`；失败模型从 rank/best/insights 中排除；全失败返回失败终态或明确 `ok=false`；UI 顶部统一横幅；补部分失败契约测试。
  - **实际修法**：`funds/analysis.py:result_completeness()` 定义统一契约 `{status: ok|partial|failed, ok, sections, missing[{section,item,reason}], reasons}`：
    - `models` 按 `extra.error` 或 `oos_r2 is None` 判定单个模型失败（全失败 → failed，部分失败 → partial），缺失项逐个带原因；
    - `correlation/forecast/valuation/style_drift` 作为可选章节，缺失记 `unavailable` 并计入 partial；`data` 章节直接采用 P2-27 的 `data_quality.status`；
    - 顶层结果新增 `status/ok/completeness`；`/api/result` 透传 `status/completeness`（信封 `ok` 保持 true，避免破坏现有调用）；Web 元信息新增「结果部分完成/失败 + 缺失项」徽标，tooltip 显示原因；报告路径 `funds/pipeline.py:_completeness()` 用同构结构（nav/exposure/health/backtest/valuation/style_drift/data），写入 `meta.json` 的「完整性」，报告头部与数据质量警示合并展示。
  - **未做**：失败模型从 `rank/best/insights` 中强制排除（属 P2 行为变更，会改变现有排名与报告结论；当前只把失败标记出来，`rank` 仍保留失败行）。如需切换，需产品确认后单独实施。
  - 回归：`tests/data/test_panels.py:CompletenessTests`（部分缺失 → partial 且 `ok=true`、全模型失败 → failed 且 `ok=false`）；`tests/data`、`tests/funds`、`tests/api` 全通过。

- **P2-32（缺失功能）：报告发布缺少完整性/窗口一致性门禁** **部分落地（2026-11-06 回查）**
  - **回查差距**：`validate_publish()` 在 `build_report` 之前执行、默认 `PublishRefused` 不覆盖旧报告、`--allow-incomplete` 强制模式与 `publish_gate` 记录已落地并有 `PublishGateTests`；未落地：CSV 指纹/窗口元数据持久化（当前只按「样本天数」做量级校验）、报告版本化归档（仍原子覆盖同名文件）。
  - 现状：`research/commands/report.py:8-10` 仅以文档提醒「跨基金表必须与主案例同窗口」；`research/commands/report.py:57-61` 缺 CSV 时用空 `DataFrame` 继续；`research/report.py:664` 附近空表照常渲染；`research/report.py:1018-1023` 原子写会直接覆盖已有报告。
  - 为什么生产不成立：主案例与跨基金表可能来自不同窗口或旧文件，关键证据缺失也能生成「完整」报告并覆盖上一版；没有完整性清单、没有非零退出、没有版本留痕。
  - 影响面：对外/对内结论被误引；历史版本不可回溯。
  - 建议：生成前校验各 CSV 的窗口/指纹与主案例一致，缺失则标 `incomplete`、非零退出且不覆盖旧版；报告页脚显示窗口、`data_stamp`、fingerprint；保留版本化输出。
  - **实际修法**：`research/commands/report.py:validate_publish()` 在调用 `build_report` **之前**执行，返回问题清单：
    - 完整性：主案例 `completeness.status == failed` → 拒绝；关键表格（跨基金汇总、E1/E2/E3/E4）任一缺失/为空 → 拒绝；
    - 窗口一致性：凡带「样本天数」的跨基金表（汇总/E2/E3），最大样本天数与主案例样本外天数相差超过 `max(30 天, 15%)` → 拒绝；
    - 有问题是默认 `raise PublishRefused`（不写文件、不覆盖旧报告），并逐条打印原因；新增 `--allow-incomplete` 显式强制发布，此时报告顶部出现红色「发布门禁未通过」横幅，`meta_extra.publish_gate` 记录 `{ok, problems, forced}`。
  - **未做**：CSV 指纹/窗口元数据的持久化（现有 CSV 无 fingerprint 列，只能按「样本天数」做量级校验）；报告版本化归档（仍原子覆盖同名文件）。这两项需要实验导出侧配合，未声称完成。
  - 回归：`tests/research/test_report_render.py:PublishGateTests`（一致通过/缺表拒绝/窗口不一致拒绝/主案例失败拒绝），现有报告渲染测试全部通过。

- **P2-33（缺失功能）：备份与恢复** **部分落地（2026-11-06 回查）**
  - **回查差距**：`ops/backup.py` 的 `backup|verify|restore`（锁内 `EXPORT DATABASE`、revision/行数校验、`--confirm`）与 [备份与恢复](docs/备份与恢复.md)、`tests/data/test_backup.py` 已落地；未落地：定时快照调度、异地/不可变存储、保留策略自动化、在线双库切换（需部署方 cron/对象存储配合）。
  - 现状：`README.md:48` 只要求「备份/迁移 DuckDB 前先停止容器内所有写入进程」，无备份命令、定时快照、校验、恢复演练或 RPO/RTO；DuckDB 单文件同时存放行情、任务与累积面板；`tasks/sync.py:260-266` 明示 csindex 估值面板断跑超过约一个月无法回补。
  - 为什么生产不成立：卷损坏、误删或失败升级会丢失不可重建的估值历史与任务记录；没有恢复验证，备份本身可能不可用。
  - 影响面：数据资产（尤其 `pe_panel`/`div_panel`）与历史结果可复现性。
  - 建议：提供 `backup/restore` 命令（`CHECKPOINT` 或 `EXPORT DATABASE`，含校验和），每日快照与保留策略，恢复演练文档；至少把累积估值/股息面板导出到独立不可变存储。卷快照可部分规避，故列 P2 而非 P1。
  - **实际修法**：新增 `market_research/ops/backup.py`（CLI：`python -m market_research.ops.backup backup|verify|restore`）：
    - 数据资产明确为 DuckDB（行情 / `pe_panel` / `div_panel` / 指数池 / 同步历史）、`audit.jsonl*`、`webcache/*.pkl`、`*.meta.json`。
    - 一致性：在 `storage.connection()` 持有的文件锁内执行 `EXPORT DATABASE ... (FORMAT PARQUET)`（停写快照），再复制审计与缓存；`manifest.json` 记录数据集数量、价格行数、revision 哈希、环境与时间。
    - `verify` 用独立 DuckDB 读 Parquet 校验数量与 revision 哈希（不一致拒绝恢复），关键数据集/审计缺失只作 warning。
    - `restore` 必须显式 `--confirm`：同一把锁内清空 `datasets/prices` → `IMPORT DATABASE` → 复制审计/缓存 → 用 manifest 比对 revision 哈希与价格行数，不一致即报错。
    - 文档：[备份与恢复](docs/备份与恢复.md)（资产清单、命令、RPO/RTO 建议、恢复后四步一致性检查）。
  - **未做**：定时快照调度、异地/不可变存储、保留策略自动化（需要部署方 cron/对象存储配合）；未做在线双库切换。
  - 回归：`tests/data/test_backup.py`（备份→verify→restore 往返、篡改 manifest 被拒、无效备份拒绝恢复）。
  - 语义变化：仅新增运维命令与文档，不影响 CLI/Web 业务路径。

- **P2-34（缺失功能）：多环境配置与结果溯源** **部分落地（2026-11-06 回查）**
  - **回查差距**：`FIL_ENV` 与可分离目录、`meta.provenance`、`/api/health.environment` 已落地；此前 `_stamp()` 未传数据版本导致 `data_stamp` 恒 `null`，现已修复：基金类操作（analyze/backtest/refit/corr）使用本基金本窗口的数据指纹（与结果缓存键同口径），market/holdings 使用全局指纹；缓存命中读旧 pickle 时保留产出时的版本；analyze 在计算前后各取一次，期间数据变化则跳过写缓存。回归见 `tests/api/test_provenance.py:test_data_stamp_changes_when_data_version_changes`。未落地：`/api/config` 脱敏视图、启动期 schema 校验、参数级指纹（`C.RBSA`/窗口参数未进入每个结果）。
  - 现状：`settings.py:14-25` 全部依赖 `FIL_*` 环境变量，启动时只做 `int` 转换，无 schema 校验/环境分层；`config.py` 中大量研究参数是代码常量；`api/routes/meta.py:8-10` 的版本硬编码；结果缓存与分析 payload 现在共用同一 `data_stamp` 口径（`services/analysis.py`），但配置指纹仍只有版本级。
  - 为什么生产不成立：dev/staging/prod 无隔离；升级后无法判断某个结果是哪版代码、哪版数据、哪套窗口/参数产生；「同输入同输出」无法自证。
  - 影响面：发布管理、问题回溯、合规审计、跨环境对比。
  - 建议：启动时用 `pydantic-settings` 校验并打印生效配置；把 `data_stamp`、git commit、关键 `C.RBSA`/窗口参数、`cache_version`、同步状态写入 `meta.provenance`（API/报告/导出统一）；提供脱敏的 `/api/config`。
  - **实际修法**：`config.py` 新增 `ENV`（`FIL_ENV=dev|local|prod`，未知回退 `local`）与可分离目录：`FIL_DATA_DIR` / `FIL_FIG_DIR` / `FIL_RES_DIR`；`local` 默认路径保持 `output/data|figures|results`，`dev` 默认落 `output/dev/*`，行为不变。`research/runinfo.py:result_provenance()` 输出 `environment/code_version/config_version/git_commit/git_dirty/data_stamp/generated_at`（git 查询进程内缓存）；`services.analysis._stamp()` 在 `_dispatch` 对所有操作结果写 `meta.provenance`，缓存文件保留产出时的版本；报告 `runinfo.data_summary()` 增加 `environment/data_dir`；`/api/health` 返回 `environment`。配置说明见 [运行环境与配置](docs/运行环境与配置.md)。
  - **未做**：`/api/config` 脱敏配置视图、启动期 pydantic schema 校验、把完整 `C.RBSA` 参数写入每个结果（当前指纹是版本级，非参数级）。
  - 回归：`tests/api/test_provenance.py`（指纹字段、`_stamp` 挂载与缺 meta 兜底、数据变化后 `data_stamp` 变化/基金窗口只跟踪自身依赖、`FIL_ENV=dev` 切换数据目录、未知环境回退）；`tests/api/test_routes.py` 仅调整一处断言以容纳新增 `meta.provenance`，缓存语义不变。
  - 语义变化：结果 payload 新增 `meta.provenance`；默认 `FIL_ENV=local` 时路径与数值完全不变。

- **P2-35（缺失功能，工程质量）：真实数据测试、CI 与接口契约** **部分落地（2026-11-06 回查）**
  - **回查差距**：手动真实冒烟（`ops/smoke.py`、`scripts/smoke_real.py`）、`.github/workflows/ci.yml` 的 `compileall`+`node --check`+全量 unittest、provider 契约单测已落地；未落地：真实数据 CI、录制 fixture 库、每日 live smoke 告警。CI 未在本机执行过 GitHub Actions。
  - 现状：`tests/README.md` 说明默认只跑离线/合成数据，真实数据用例需手动联网；仓库无 `.github/`（已用 Glob 确认），无 PR 门禁；`tests/api/test_routes.py` 只断言路由存在，不校验响应字段/错误码；provider 适配层无字段契约测试。
  - 为什么生产不成立：akshare 列名/接口变更历史上已发生，CI 不会捕获；重构可能破坏前端依赖字段而不自知；发布前无自动化全量回归。
  - 影响面：回归风险、供应商契约漂移、交接质量。
  - 建议：CI 跑全量 unittest + `node --check` 前端；为关键响应加 JSON schema/字段契约；用录制的真实响应 fixture 测 provider；每日/每周 live smoke（告警但不阻塞合并）。
  - **实际修法（拍板：内部研究，不强制全量真实数据 CI）**：
    - 手动真实冒烟：`market_research/ops/smoke.py` + `scripts/smoke_real.py`，覆盖抓取→面板→分析→报告，产物 `smoke_result.json`；失败按阶段与异常来源分类 `data_source | logic`（契约/净值口径/存储/网络类归 data_source，其余本地计算/渲染归 logic）。
    - CI：新增 `.github/workflows/ci.yml`，跑 `python -m compileall src scripts`、`node --check` 全部前端 JS、`python -m unittest discover`（全量离线单测）；不跑真实数据。
    - 字段契约：provider 缺列/改名由 P2-29 的 `ProviderContractError` 在单测中覆盖（`tests/data/test_http.py`）。
  - **未做**：真实数据 CI、录制 fixture 库、每日 live smoke 告警。真实冒烟失败时由 `failure_class` 区分数据源/逻辑，不自动重试。
  - 回归：`tests/data/test_smoke.py`（分类与失败落盘）；CI 配置为新增文件，未在本机执行 GitHub Actions。

- **P2-36（缺失功能，产品与合规）：免责声明、数据延迟披露与数据源授权** **部分落地（2026-11-06 回查）**
  - **回查差距**：`disclosure.py`、`/api/disclosure`、Web 顶部横幅与报告首页横幅、`FIL_DISCLOSURE_SCOPE/TEXT/POSITION` 开关已落地并有 `tests/api/test_disclosure.py`、`DisclosureTests`；未落地：每个 API 响应的统一 `meta.disclaimer/data_as_of/data_latency/model_limits` 信封；数据源再分发授权评估仍属法务事项。
  - 现状：Web 页脚（`api/static/index.html:382`）、报告页脚（`research/report.py:1014`）、预测/当前仓位/回测 payload（`funds/payloads.py:173`、`209`、`739`）已有免责声明；但顶层分析、持仓、`/api/result` 没有统一信封，`meta` 无 `data_as_of`/延迟说明；akshare/新浪/东财/中证数据的生产使用与再分发授权未确认。
  - 为什么生产不成立：通过 API/导出消费结果的人拿不到免责声明与数据截止/延迟；数据源条款风险未评估；用户无法区分「模型参考」与「投资建议」。
  - 影响面：合规、用户误解、对外分发风险。
  - 建议：所有响应加统一 `meta.disclaimer/data_as_of/data_latency/model_limits`；报告/导出与 API 同源；明确「仅内部研究」或采购合规数据源；具体措辞与授权范围需产品/法务拍板。
  - **实际修法（拍板：内部研究，保留对外交付升级位）**：新增 `market_research/disclosure.py`，默认文案明确「数据来源为公开抓取、存在延迟与不完整、结论仅供研究参考、不构成投资建议」；Web 页顶部显著横幅（`index.html` + `app.js` 从 `/api/disclosure` 读取，失败保留内置文案）与报告首页横幅（`build_report` 在 hero 之前插入，带独立高对比样式）。文案与位置可配：`FIL_DISCLOSURE_SCOPE=internal|external`、`FIL_DISCLOSURE_TEXT`、`FIL_DISCLOSURE_POSITION=top|footer`；`/api/disclosure` 与页面/报告同源，便于未来对外交付统一替换。
  - **未做**：每个 API 响应的统一 `meta.disclaimer/data_as_of/data_latency/model_limits` 信封（当前是页面与报告级披露 + `data_quality` 已有数据截止/降级信息）；数据源再分发授权评估仍属法务事项（拍板继续公开抓取、API 内部使用）。
  - 回归：`tests/research/test_report_render.py:DisclosureTests`（横幅位于 hero 之前且含三项声明、文案覆盖生效）、`tests/api/test_disclosure.py`（默认内部文案、对外作用域/位置/自定义文案、token 模式下接口仍公开）；相关 19 项测试通过。
  - 语义变化：Web 页与报告新增显著声明条；默认文案为内部研究口径，数值结果不变。

- **P2-37（逻辑/设计问题）：抓取超时使用进程级全局 socket 默认值，与 6 线程并发冲突** **部分落地（2026-11-06 回查）**
  - **回查差距**：`http_timeout` 改为 `threading.local()`（支持嵌套恢复）+ 模块导入时给 `requests.Session.request` 注入线程级 timeout 已落地并有 `tests/data/test_http.py`；未落地：同步单资产的 watchdog/总超时（仍属 P2-29 挂起范围，`tasks/sync.py` tick 循环无看门狗）。
  - 现状：`data/http.py:25-33` 的 `http_timeout` 通过 `socket.setdefaulttimeout` 设置/恢复**进程级**默认超时，注释称「局部设置」，但该 API 不是线程局部的；`data/providers/indices.py:54-55` 同时用 6 个线程抓取，每个线程都会进入/退出该上下文。
  - 为什么生产不成立：并发交错时，一个线程可能在另一个线程仍在请求时把全局默认值恢复为 `None`，使新建立的连接没有超时；全部结束后还可能在进程里留下 30s 的全局默认值，影响后续连接。AkShare 请求没有显式 timeout，一旦落到无超时状态，同步线程可无限挂起，`tasks/sync.py:328-333` 的 tick 循环没有 watchdog，数据刷新会长期停摆。
  - 影响面：同步停摆、候选池抓取卡死、进程级网络行为被意外改变；问题只在并发/慢请求下出现，难以复现。
  - 建议：改用显式 per-request timeout（或执行器内线程局部配置），不要在共享进程里改全局默认；至少给同步单资产抓取加 watchdog/总超时，并在 `finally` 用引用计数保证最后一个退出者才恢复原值。
  - 与 P2-29 的关系：P2-29 讲限流与单点，本条是并发实现本身的全局状态缺陷，修法不同。
  - **实际修法**：`http_timeout` 改为 `threading.local()` 保存超时值（支持嵌套并在退出时恢复上一值），完全不调用 `socket.setdefaulttimeout`；模块导入时一次性给 `requests.Session.request` 注入线程级 `timeout`（已有显式 timeout 时不覆盖），把超时下沉到**每个请求**。这样 6 线程各自超时互不干扰，Flask 监听/连接 socket 不再被波及。
  - **未做**：同步单资产的 watchdog/总超时（属 P2-29 数据源路线/调度策略范畴，保持挂起）。
  - 回归：`tests/data/test_http.py`（全局 socket 默认值不变、线程隔离、嵌套恢复、requests 注入仅在缺省时）。
  - 语义变化：所有走 `requests` 的抓取（AkShare 主要入口）在 `http_timeout` 内都会收到显式 timeout；若某 provider 走原生 `urllib`/socket 且未传 timeout，则不再受进程级默认值保护（这是本次刻意移除的全局副作用）。

### P3（可迭代）

- **P3-D（缺失功能）：日志不轮转、无保留策略** — **已修复（2026-11-07）**
  - 现状（修复前）：`tasks/processes.py:87-94` 以 `ab` 追加打开 `worker.log`/`job-{id}.log`/`sync-process.log`，永不轮转；只有 `tasks/sync.py` 的 `sync.log` 用 `RotatingFileHandler`。
  - 已落地：新增 `logs.py`（append-and-close 的 `RotatingAppendHandler`，按 `FIL_LOG_MAX_BYTES`/`FIL_LOG_BACKUPS` 轮转，`FIL_LOG_DIR`/`FIL_LOG_LEVEL` 可配）；API→`api.log`、worker→`worker.log`、sync→`sync.log` 统一走该 handler；`launch()` 在打开重定向文件前先轮转，任务日志按 `FIL_LOG_RETENTION_DAYS`（默认 7 天）清理。Windows 下不保留常驻句柄（测试直接重命名/删除已验证）。
  - 剩余：子进程 stdout 重定向文件的句柄在进程生命周期内不可重命名，长跑进程的轮转只在下次启动生效（与审计日志同样的取舍）；未做磁盘水位告警、未接 Docker log driver。
  - 回归：`tests/data/test_logs.py`（6 项：大小轮转/备份份数、环境变量、无句柄占用、幂等配置、任务日志保留期）。

- **P3-E（逻辑/设计问题）：入队去重非原子** — **已修复（2026-11-07）**
  - 现状（修复前）：`tasks/store.py:47-60` 先 `SELECT` 再 `INSERT`，`task_key` 没有唯一约束；多 API 进程或线程并发提交同一参数时可能各插一条。
  - 已落地：改为单条 `INSERT ... SELECT ... WHERE NOT EXISTS (同 key 且 status IN ('queued','running'))`，插入后按 key 回读并返回 `(jid, merged)`；队列上限检查仍在同一事务内，已存在同 key 时直接返回而不报 429。跨进程由 `storage.connection()` 的文件锁串行化，单进程内由线程锁保证。
  - 回归：`tests/tasks/test_taskstore.py:test_concurrent_processes_enqueue_same_key_once`（6 个 spawn 进程并发提交同 key，只返回一个 jid、仅一次 `merged=False`、库中只有一条排队/running 记录）。
  - 剩余：DuckDB 无部分唯一索引，原子性由写入语句+文件锁保证；绕过 `DurableJobStore` 直接写表仍可能产生重复。

- **P3-F（逻辑/设计问题）：重试无断点，崩溃后从头开始** — **已修复（2026-11-07，阶段级）**
  - 现状（修复前）：`tasks/worker.py:64-95` 的崩溃重试重新执行整个任务；模型级无检查点。
  - 已落地：新增 `tasks/checkpoints.py`（`StageCheckpoint`，按 `job_id` 持久化到 `FIL_DATA_DIR/checkpoints/<jid>/`，默认保留 2 天）。`analyze` 对数据、净值、方向概率、相关性、模型结果五个阶段读写检查点；`refit`/`backtest` 对数据与单模型；`holdings` 对行业映射与拟合结果。检查点写参数、窗口与数据版本指纹，重启后指纹不一致或 pickle 损坏则整体丢弃并从零执行；失败保留供重试，成功或最终完成时删除。
  - 行为差异：仅进程中断/超时后的重试会跳过已完成阶段；正常首次运行行为不变（检查点写入是附加开销，失败不影响计算）。`FIL_CHECKPOINT_DIR`/`FIL_CHECKPOINT_TTL_DAYS` 可配。
  - 回归：`tests/tasks/test_checkpoints.py`（6 项：阶段读写、指纹变更丢弃、损坏阶段丢弃、过期清理、`_dispatch` 失败后重试不重算、真实 `analyze` 阶段接线）。
  - 剩余：粒度是阶段（模型阶段整体一个产物），单模型内部中断仍会从该阶段起点重跑；孤儿进程清理与超时续期未在本条处理（超时已由 `FIL_JOB_TIMEOUT` 提供固定上限）。

- **P3-G（逻辑/设计问题）：排名并列时结果顺序不稳定** — **已修复（2026-11-07）**
  - 现状（修复前）：`modeling/engine.py:194-216` 按进程完成顺序写入 `out`；`modeling/engine.py:377-392` 的 `sort_values` 无稳定 tie-break，多处权重/相关性排序同样只按值排序。
  - 已落地：新增 `ordering.py`（`series_order`/`ranked_series`/`ranked_frame`/`rows_order`：并列按名称或代码升序、NaN 统一排最后、稳定排序），统一替换 11 处排序点：模型排名、估计器权重、RBSA 最新暴露、相关系数 top N 与分层、总权重、每日信号回退资产、NW t 值、图表暴露、实验特征/命中/权重、持仓行业偏差、研究报告方案排名；命中并列按时间序、相关性并列按代码。
  - 行为差异：仅并列时的先后顺序固定，不改变入选集合与数值；`best_model` 在并列时选择 key 升序的第一个。
  - 回归：`tests/modeling/test_ordering.py`（6 项：frame 并列、NaN 置底与输入顺序无关、Series 并列、rows 并列、ModelZoo.rank 两种插入顺序一致、RBSA 暴露并列按名称）。
  - 剩余：`funds/universe.py` 按基金代码排序（本身唯一，无需 tie-break）；`payloads.py:605` 的超限计数列表只做数值降序，不承载顺序语义。

- **P3-H（缺失功能）：API 版本与响应契约** — **已修复（2026-11-07）**
  - 现状（修复前）：路由为未版本化的 `/api/*`；`api/schemas.py` 只定义请求模型，响应多以裸 dict 返回；版本在 `api/app.py` 与 `api/routes/meta.py` 两处硬编码。
  - 已落地：新增 `api/version.py` 作为版本单一来源（`SERVICE_VERSION=0.3.0`、`API_VERSION=1`）。同一批路由同时挂在 `/api`（兼容）与 `/api/v1`（正式契约），两者都在 `/openapi.json` 中可见；所有 HTTP 响应带 `X-API-Version: 1` 头；`/api/health` 与 `/api/ready` 增加 `api_version` 字段。关键响应定义 Pydantic 契约：任务状态 `JobResponse`、结果信封 `ResultResponse`、存活/就绪 `HealthResponse`/`ReadyResponse`，由 FastAPI 在返回时校验并过滤未声明字段。认证模式下 v1 的 health/ready/disclosure 与旧路径一样公开。
  - 前端：`api/static/*` 继续调用兼容路径（两个路径由同一实现提供），本轮未改动，避免打断；旧路径未标记删除，文档注明 v1 为推荐路径。
  - 回归：`tests/api/test_contract.py`（6 项：新旧路径同体、版本头、OpenAPI 同时列出、v1 校验/提交、任务状态字段集合、结果信封字段集合、就绪版本）。`tests/api/test_routes.py` 18 项保持通过。
  - 剩余：旧 `/api/*` 未设置 `Deprecation`/`Sunset` 头，也没有自动迁移脚本；兼容路径何时移除待后续拍板。

- **P3-I（缺失功能）：资源上限与容器配额** — **已修复（2026-11-07）**
  - 现状（修复前）：`modeling/engine.py` 每个任务默认开 `cpu_count()-1` 个子进程；`FIL_MAX_JOBS=2` 时理论上同时占用约 `2×(CPU-1)` 个进程；不读取 cgroup，也没有单任务内存上限。
  - 已有并保留：并发任务上限 `FIL_MAX_JOBS`、队列总量 `FIL_QUEUE_LIMIT`（超限 429）、单任务超时 `FIL_JOB_TIMEOUT`（900s，超时终止并明确报错）、结果图表点数/相关性 top N/持仓季度行等既有截断。
  - 已落地：新增 `resources.py`。`cpu_limit()` 依次读 cgroup v2 `cpu.max`、v1 `cfs_quota/cfs_period`、进程亲和性、物理核；`model_workers()` 默认 `min(模型数, max(1, CPU-1))`，可用 `FIL_JOB_WORKERS`（0=auto）与 `FIL_JOB_WORKERS_MAX`（0=不限）收紧；`FIL_JOB_MEMORY_MB`（0=不限制）在 POSIX 子进程设 `RLIMIT_AS`，在 Windows 通过已有 Job Object 设 `JOB_OBJECT_LIMIT_PROCESS_MEMORY`；`MemoryError` 映射为含配置项与建议的失败原因。`/api/ready` 新增 `checks.limits` 展示当前上限。
  - 行为差异：默认值保持原行为（内存不限制、worker 仍按 CPU-1，但容器内按 cgroup 而非宿主机核数）；仅在显式设置时收紧。
  - 回归：`tests/tasks/test_resources.py`（6 项：auto/cap/显式 worker、超时与内存默认值、平台差异下的内存限制、MemoryError 可读错误）。
  - 剩余：Windows 内存超限通常表现为进程被终止而非 Python `MemoryError`，最终错误是「进程退出」重试耗尽，未在错误里自动归因到内存；Dockerfile 未内置 `--cpus/--memory` 默认值（仍由部署参数控制）；按用户/租户配额未做（本产品为单用户形态）。

### 已具备（看似缺失但实际已有，不重复列项）

- **持久任务与恢复**：DuckDB 任务表、45s 租约、尝试令牌、崩溃/超时恢复、终态 24h 清理已实现（`tasks/store.py:15`、`47-124`、`tasks/worker.py:64-95`）；同 key 运行中任务复用（`tasks/store.py:51-55`）。
- **进程隔离与超时终止**：单实例 supervisor + 每任务独立子进程 + 进程树终止（`tasks/worker.py:64-95`、`tasks/processes.py:11-56`）；Windows 用 Job Object 连带回收孙子进程。
- **请求与错误契约**：8MB 请求体上限、400/404/429/500 统一错误结构（`api/app.py:24-47`、`72-92`）；任务取消入口已实现。
- **部署基础**：Docker 多阶段、非 root UID/GID 10001、`/data` 卷、HEALTHCHECK、锁定 `uv.lock`（`Dockerfile:1-58`）；单机文件系统与多 API 进程约束已写入 `README.md:33`。
- **写入安全**：DuckDB 事务写入、空/倒退/截断历史拒写并保留旧数据（`data/storage.py:72-82`、`tasks/sync.py:188-208`）；缓存版本与数据指纹、旧结果缓存清理已实现（`data/storage.py:101-125`、`services/analysis.py:94-125`）。
- **报告与溯源（部分）**：报告原子写与运行指纹已实现（`research/report.py:1018-1023`、`research/runinfo.py`）；结果 JSON 导出含窗口/模型口径（`README.md:34`）。
- **已有免责声明与降级标记**：Web 页脚、报告页脚、预测/当前仓位/回测 payload 已有声明（`api/static/index.html:382`、`research/report.py:1014`、`funds/payloads.py:173`/`209`/`739`）；估值/持仓/健康分已有 `available/degraded/reason` 字段（P2-21 部分落地、P3-B 已关闭）。
- **因果与执行口径**：方向概率按标签成熟截断、基金择时 `shift(1)`、规则买卖点 t 收盘/t+1 开盘已在「已核验合理」留痕；模型随机种子固定（`modeling/estimators.py:343/424/453/526`）。
- **文档交接**：`README.md` 与 `docs/架构设计.md`、`docs/模块与入口.md`、`docs/功能完整性检查.md` 已覆盖数据流、进程、部署约束与命令映射；缺的是运维 runbook（备份恢复、监控告警、故障处置），已在 P2-25/P2-33 列出。

### 需要产品/口径决策（不是纯技术修复）

- ~~**部署形态**~~：已拍板**当前单用户为主**，保留多用户升级入口；P2-24 实现为默认可无认证、可开启令牌 + 身份/租户上下文，配额与数据分区未做。
- ~~**数据源路线**~~：已拍板**继续公开抓取**，不采购合规数据源；P2-29 实现限流/退避/溯源/契约校验，备用源与熔断未做。
- ~~**数据延迟/新鲜度策略**~~：已拍板**带 stale 标记继续**，不拒绝分析（P2-27 已实现；关键结论由调用方自行决定是否引用）。
- ~~**NAV 降级策略**~~：已拍板并实现为**默认拒绝**（P1-20 核心已落地、CLI/报告口径标注待补）；保留 `FIL_NAV_DEGRADED_POLICY=allow_cum` 作为显式降级并强制标注。
- ~~**报告定位**~~：已拍板**内部研究**，保留对外交付物升级位；P2-32 门禁按内部研究严格度执行，版本化归档未做。
- ~~**免责声明与 API 开放范围**~~：已拍板**API 内部使用、暂不提供给第三方**；结果对外提供时的措辞/授权仍需法务确认，P2-36 已提供 internal/external 两套文案开关。
- ~~**备份目标**~~：已拍板把 DuckDB（含 `pe_panel`/`div_panel`）、审计与缓存视为数据资产；P2-33 提供脚本与恢复校验，RPO/RTO 由部署方按日备份设定。
- ~~**发布流程**~~：已拍板**不强制全量真实数据 CI**；CI 跑全量单测与语法检查，真实数据冒烟手动触发（P2-35）。

## 本轮复核范围与证据

- 原“股票与指数”六项仍保留为研究/数据缺口：已有方向模型、概率校准、规则/模型成交和资金账户诊断，以及配对不确定性估计；它们不等于独立未来样本有效性、完整策略不确定性、真实 ETF 成交、历史时点成分或财报可得时间。这些缺口不是本轮通过合成数据即可关闭的缺陷。
- 已核对 API 参数/任务入口、持久队列与 worker、基金分析/重估/回测调用链、持仓研究、行情对齐与缓存/同步、股票指数分析入口和自有前端脚本；未逐行穷尽全部算法。
- 2026-11-01 修复轮实际通过：`.venv/Scripts/python.exe -u -m unittest discover -s tests -p "test_*.py" -t .`（265 项，含 P1-14/15/16/17/18、P2-19 新增回归）；`python -u tests/regression/test_regressions.py`（R1~R13）；`python -u tests/funds/test_holdings.py`。修复均使用确定性最小输入与 mock，无随机种子、无真实行情依赖。
- 仍未重跑：真实数据研究、浏览器、构建和多进程压力测试。已有历史测试记录不代表本轮执行。

## 约束（复核已否定的判断，不要重复引入）

- 不要回退到"CSV 写行情 / Flask / 进程内线程跑任务"；旧导入只留在 `compat/`，不要再新增根目录 `run_*`，研究入口只登记到 CLI 注册表。
- 请求线程内的端点（`/api/corr`、`/api/name`）**不得联网取数**：取数交回一次正式的基金分析（走队列、可取消、有名额限制）。
- `src/__init__.py`、`webapp/__init__.py` 是 `compat/` 的兼容桥接（`src.*` → `compat/src`），`tests/data/test_storage.py` 与 `pyproject.toml` 的 `include` 依赖它们，不是遗留脚手架。
- `semiannual_exposure(only_full=False)` 在函数体内真实生效（`holdings[holdings["完整"]]` vs 全量），上游 `dropna` 不使该分支失效，不是死参数。
- 持仓暴露表里"行合计 < 100%"的差额**不是现金**：包含抓不到行业的新股/北交所/停牌股。引用时必须带上 `exposure_coverage()` 的覆盖率，否则会把股票仓位说成现金。

## 关闭标准

每个 TODO 关闭时记录实现提交、验证命令、数据快照和结果；凡以具体数值作为证据的，必须同时记录可复现的最小输入（样本长度、随机种子、参数）。不得仅凭"脚本退出码为 0""所有模型都有返回值"或"页面能打开"关闭研究有效性相关任务。当前回归基线与未覆盖范围见[测试说明](tests/README.md)。
