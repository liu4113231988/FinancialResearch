# 项目待办

本文件**只记录未完成项**。已完成项的实现与验证见 `git log`、[测试说明](tests/README.md) 的验证记录，方法论结论见[研究方案](docs/研究方案.md)与[功能完整性检查](docs/功能完整性检查.md)。

复核日期：2026-11-01。本轮除更新待办外，**已修复当日复核发现的全部程序缺陷**：P1-14（覆盖率 KeyError）、P1-15（固定窗口被截止日后净值污染）、P1-16（持仓基准混合收益口径）、P1-17（"相对基金IR"实为自身收益波动比）、P1-18（持仓缓存忽略请求年份）、P2-19（年数窗口未锚定 end）、P1-11 的代码侧问题（不完整行业映射被永久冻结），以及可选能力 P1-13（/api/corr 冷缓存改为提交队列任务）。每项均带最小复入的回归测试；验证：`.venv/Scripts/python.exe -u -m unittest discover -s tests -p "test_*.py" -t .`（269 项）、`python -u tests/regression/test_regressions.py` 及两套前端回归。实现提交 dc3a4d0（2026-11-01 已推送 origin/main）。剩余的 P1-10 / P1-11（数据部分）是数据缺口，不是本轮可关闭的缺陷。P1 表示影响正常计算或结果解释，P2 表示参数语义或特定入口问题。

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

- 时点可得的基金分红/拆分及净值版本，股票公司行动与历史数据版本。
- 供应商交易日历和缺口会话处理已有实现（`data/providers/calendar.py`、`market/targets.py`、`market/execution.py`）；仍需交易所公告核验、区分停牌与漏抓、真实订单及基金申赎/持有期费用模型，不能记为“完全没有交易日历”。
- 股票财报/估值/同业比较，以及方向估计与概率校准的未来独立样本有效性。
- ~~`data/storage.py:stamp` 收窄依赖与重复窗口清理~~ **已关闭（2026-11-01）**：stamp 支持按抓取窗口收窄到本基金本窗口的实际依赖（`pe_panel` 与未知数据集仍按设计保守保留）；worker 启动时清理重复窗口的指数池缓存；当前版本结果缓存按 30 天 mtime 淘汰。`CACHE_VERSION` 已同步 +1。
- ~~Web 重估点抽稀的前缀稳定性~~ **已关闭（2026-11-01）**：`rebal_mode` 切换为 `calendar`（锚点只由日期决定），限流改为保留最新锚点、从头部丢弃 —— 已选历史点不位移；`CACHE_VERSION` 连同 +1，回归见 `tests/modeling/test_rebal_calendar.py`。研究路径本就不抽稀，不受影响。
- 页面恢复、自选及带数据口径的研究导出**已实现（2026-11-01）**：localStorage 持久化上次分析状态（只回填表单、不自动触发分析）与自选基金清单（顶栏 ☆），结果页支持导出 JSON（含窗口/模型/超参/重估模式等口径）。仍缺：长期模型档案，为[待评估提案](docs/模型档案与算法记录_重构方案.md)，未实现。

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
- beta 自由度 ddof 混用（`np.cov` ddof=1 / `np.var` ddof=0）：`modeling/engine.py:320-322`、`modeling/estimators.py:656-659`、`research/experiments.py:374-378`，β/γ 被系统放大 n/(n−1)。统一 ddof。
- lasso 空支撑集兜底未 `nan_to_num`，常数列可拖垮整模型（`modeling/estimators.py:254-256`，对比 enet `214-216` 已修）。
- 平局闭合交易（|pnl|≤阈值）进 `closed_trades` 分母但不计胜，win_rate 被低估（`market/capital.py:118-123`、`market/execution.py:127-135`）。分母改 wins+losses 或单列持平数。
- Newey-West t 在零方差序列得 ±inf（`research/robustness.py:149-164`），LRV≤阈值时应返回 NaN（参照 `research/market_validation.py:106-107`）。
- E1 块内常数信号使整块 ICIR  NaN，应用 `np.nanmean` 并剔除/计数 NaN 块（`research/experiments.py:57-60`）。
- 资金账户「每日重试补仓」假设对规则脉冲信号不成立（`market/capital.py:139-143` vs `market/execution.py:154-158`）：脉冲遇涨跌停/容量不足后订单永久失效、残余现金不再投入；卖出部分成交后脉冲过期则残留持仓无法再清。至少按目标类型改写文案；更严格做法是脉冲订单挂单直到成交或反向信号。
- 分位含当日自身（`market/context.py:8-9`：`a <= a[-1]`），最低分位为 1/N 而非 0，note 未披露。
- 行情双源失败时只保留两个异常类名（`market/analysis.py:88-93`），网络/空数据/代码错误无法区分；截断保留 `str(primary)`。

**边界与防御**
- `_normalize_holdings` 对异常季度标签直接崩溃（`funds/holdings.py:369-377`），向量化构造或先过滤匹配行。
- `_run_one` 全部窗口被 `len(yw) < min_obs` 跳过时 `rows=[]`，`pd.DataFrame([]).set_index("date")` 抛 KeyError（`modeling/engine.py:326-329`）；空结果走 `_failed_report` 一致路径。
- meta 的 `n_rebal` 取插入顺序第一个模型的 `weights_hist`，若首个为 failed report 显示 0（`funds/analysis.py:191-192`）；取任一非空 `weights_hist` 的长度。
- `holding_benchmark` 隐含依赖 exposure 已按季末升序（`funds/holdings.py:649-676`）；公开入口应显式 `sort_index()`。
- `build_pool` 全部排除记录为空时 `pd.concat` 空列表抛异常（`funds/universe.py:270`）。

**任务 / 数据层**
- 终态任务持久店保留 24h（`tasks/store.py:51`）与内存店 30 分钟不一致；`queued` 任务永不过期，worker 久停后队列永久 429。明确口径并给 queued 加 TTL。
- 取消/超时后名额瞬时释放，可致 `max_jobs+1` 个并发写进程（`tasks/store.py:138-145`）。
- `/api/result` 对用户取消（status=error，文案「任务已取消」）返 500，无法与真实失败区分；建议 409/499 或带 reason。
- `end` 无未来日期上界（`validation/window.py:62-86`），2099 会触发 47 指数全量抓取；超过今天直接 400。
- `--once` 绕过 `FIL_SYNC_ENABLED=0`（`tasks/sync.py:347-349`），而计划任务恰好用 `--once`，与 `serve()` 注释承诺相反。
- 结果缓存按 mtime 30 天淘汰（`services/analysis.py:103-108`），热点缓存每天读也被删；应按 atime 或命中时 utime。
- 请求线程内 `/api/corr`（`allow_fetch=False`）仍有 DuckDB 写副作用（`data/panels.py:196-199` 的 `register`、`168-171` 的 `_cache_info_write`），只读模式应跳过，避免与 sync 大事务抢锁。
- supervisor 对杀不掉的僵尸子进程 `wait` 超时会炸整个监督循环（`tasks/worker.py:81-114`）；超时则记录并跳过本轮，不要让单个失控进程拖垮队列。
- 损坏缓存无自愈（`data/storage.py:54-60`、`data/cache.py:75-80`），可能把冷缓存 202 打成 500。
- `_retry` 对 ValueError/KeyError 等编程错误也重试 4 次且末次多睡一轮（`data/http.py:42-47`）。
- 候选池 ≤200 行被静默丢弃（`data/providers/indices.py:60-67`），真实原因不可见。
- `meta.through` 固定写当天，即便 `data_end` 更早（源站滞后），`cached()` 凭 through 判新鲜会静默用旧数据（`tasks/sync.py:189-211`）。

**UI 打磨**
- ≤1150px 顶栏「自选」下拉整体隐藏，手机端无自选入口（`api/static/style.css:481`）；移到基金工具栏/空状态区或允许换行。
- 模型卡片/排名行用 div/tr + onclick，键盘不可达、无 ARIA（`app.js:1125-1127/1182-1184`）；加 `tabindex`、`role="button"`、Enter/Space 处理。
- 日期输入无 `max=今天`；回测 60 交易日预热无前端预检（`index.html:98-99/165-166`）。
- 结果缺字段时渲染中途抛错，留下「新头部+旧图表」混合页（`app.js:976-1006/1744-1747` 等）；渲染函数对可选集合做存在性兜底。
- 429/409/404 提示无差别、409 不接续已有任务（`app.js:475-483`）。
- 标签页隐藏后基金/市场/相关性轮询不停（数据中心已跳过，`data-center.js:104`）。
- 市场页 resize 无防抖（`market.js:284`，基金侧有 140ms debounce）。
- 图表容器缺 `role="img"`/`aria-label`、分段按钮缺 `aria-pressed`；`document.title` 不随入口变化。
- 自选不清理失效代码；catalog 初始加载无 loading 态、失败无重试按钮。

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
