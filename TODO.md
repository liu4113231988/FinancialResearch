# 项目待办

本文件只记录**未完成项**与防止误判的必要约束。已完成项的实现、验证快照与历史审查证据不在此重复：提交与修复记录见 `git log`（历史审查原文可用 `git show <提交>:TODO.md` 取回），验证记录见 [测试说明](tests/README.md)，方法论与专项结论见 [研究方案](docs/研究方案.md)、[功能完整性检查](docs/功能完整性检查.md) 及 `docs/` 下各专项文档。

优先级沿用历史口径：P1 影响正常计算或结果解释，P2 为参数语义或特定入口问题，P3 为可迭代项；编号（P1-10、P2-xx、P3-x）保持不变，便于历史文档引用。标注 `[产品]` / `[法务]` / `[运维]` 的条目不是纯技术修复，需先拍板再实施。

## 未完成项

### 数据与未来样本验证（需真实数据，不能离线关闭）

- **P1-10 持仓基准股息缺口**：`funds/holdings.py:holding_benchmark` 用行业价格指数加统一年化情景参数（默认 0），缺逐行业**历史时点**真实股息率或全收益行业指数。中证 `stock_zh_index_value_csindex` 不含申万行业、官方文件只保留近一个月（历史靠每日累积，断跑超过一个月无法回补）；`load_dividend_panel` 只能作宽基锚点。`sw_index_first_info` 的逐申万一级行业**当期静态股息率**已由 `holdings --suggest-dividend` 接入，仅作 `RBSA["index_dividend_annual"]` 的情景参数建议值（不写配置、不改默认值、不参与计算）；当期截面不可回填历史，行业腿仍缺时点序列。旧文「每年低估 1.5~2.5%」不是固定误差，不得当作事实引用。
- **P1-11 行业映射时点性**：`load_industry_map` 由**当前**申万成分股并集构造，不能保证历史时点行业归属；需历史时点成分数据才能消除幸存者偏差并决定覆盖不足期次的处理，覆盖率标注不能替代。
- **股票与指数六项**（均需真实数据）：
  - 扩展代表性样本池并验证未来未使用区间；当前回顾性留出及小样本不足以证明交易有效。
  - 成交实证：公司行动、历史停牌/涨跌停、税费明细、实际 ETF、更准确的开盘容量、扩大样本及完整策略不确定性；供应商日历不是交易所公告审计，缺字段假设不能认定可交易。
  - 个股相对行业、行业相对宽基；完整历史行业归属与指数成分版本。
  - 指数市场宽度（MA20/60 以上成分股比例、涨跌家数、新高新低），必须使用历史时点成分名单，避免幸存者偏差。
  - 财报、公告、分红、估值历史分位，记录报告期、公告时间和数据可得时间。
  - 未来未使用时间段的概率校准有效性及完整策略不确定性评估；指数信号在实际 ETF 上验证费用、成交与跟踪偏差。
- **六项未来验证的执行**：预留区间 2026-11-11~2027-05-10、评估日 2027-05-11，台账状态为 `registered_not_executed`。仍需真实未来行情、历史时点行业成分/公司行动、真实 ETF 成交记录；**原始输入内容指纹须在评估前按 `real_trades_schema` 追加到[执行台账](docs/future-validation-execution.json) 的 `items[].evidence`，不得回填冻结证据，也不得用合成数据冒充或离线标记完成**。协议/台账/冻结证据的分工与校验命令见[股票指数验证流程](docs/股票指数验证流程.md)。
- **时点数据与版本**：基金分红/拆分及净值版本、股票公司行动与历史数据版本。
- **交易实证补充**：交易所公告核验、区分停牌与漏抓、真实订单及基金申赎/持有期费用模型。
- **股票基本面与独立样本**：财报/估值/同业比较，以及方向估计与概率校准的未来独立样本有效性。
- **长期模型档案**：[待评估提案](docs/模型档案与算法记录_重构方案.md) 未实现。
- **未覆盖范围**：真实数据研究重放、真实浏览器/移动端、多进程与锁竞争压力、Docker/wheel 构建安装；2026-11-11 前历次全局审查只核对参数/任务入口、队列与 worker、调用链、缓存同步与前端脚本，2026-11-11 一轮新增了 RBSA/持仓/技术分析/资金账户/校准等算法层逻辑与边界核对（发现见新增小节），但仍**未逐行穷尽全部算法**。历次验证记录均标注未重跑，勿据历史测试记录推定已覆盖。

### 工程与生产化剩余

- **P2-24 多用户能力**：按用户/租户的任务与结果分区、每用户并发/队列配额、注册/登录/OIDC `[产品]`。
- **P2-25 可观测性**：Prometheus 指标、request id、结构化错误上报、worker 独立心跳文件（当前只校验 pid 存活）`[运维]`；接入前需产品/运维确定指标与错误上报后端、保留策略。
- **P2-26 审计**：操作者/IP 字段、审计查询接口、保留期策略与防篡改 `[产品]`。
- **P2-29 数据源韧性**：备用源/本地归档、熔断与 provider 健康度降权。
- **P2-30 存储**：读写分离、独立库文件、多列合并写入（读写仍共用一把锁）。
- **P2-31 结果契约**：页面顶部统一的部分失败横幅；失败模型强制排除属口径变更，需产品确认后单独实施（见下节）。
- **P2-32 报告门禁**：CSV 指纹/窗口元数据持久化、报告版本化归档（现原子覆盖同名文件）；需实验导出侧配合。
- **P2-33 备份**：定时快照调度、异地/不可变存储、保留策略自动化、在线双库切换 `[运维]`；RPO/RTO 由部署方按日备份设定。
- **P2-35 真实数据 CI**：live smoke 需配置 `LIVE_SMOKE_ENABLED` / `LIVE_SMOKE_ALERT` 并在真实网络执行、GitHub Actions 未在本机实跑 `[运维]`；原始响应导出需逐源法务审查编号 `[法务]`。
- **P2-36 合规**：对外披露具体措辞、「仅内部研究 / 采购合规数据源」的取舍，以及逐源数据授权与再分发范围的法务结论 `[法务]`。
- **P2-37 同步 watchdog**：真实慢源按资产调 `FIL_SYNC_ASSET_TIMEOUT` `[运维]`。
- **P3-D 日志**：子进程 stdout 重定向句柄的轮转（长跑进程只在下次启动生效）、磁盘水位告警、Docker log driver。
- **P3-F 检查点**：粒度仍是阶段级（模型阶段内部中断会重跑该阶段）、孤儿进程清理与超时续期。
- **P3-H 旧路径下线**：`2027-04-01` 是代码内计划值，到期前 `/api/*` 仍挂载；实际移除日、对外 `Sunset` 日期与外部客户端迁移完成度待定 `[产品]`。
- **P3-I 资源**：Windows 内存超限自动归因、`Dockerfile` 内置 `--cpus/--memory` 默认值、按用户/租户配额 `[运维]`。
- **资金账户脉冲订单（行为层）**：脉冲遇涨跌停/容量不足后订单永久失效、残余现金不再投入；卖出部分成交后脉冲过期则残留持仓无法再清。修复会改变资金曲线（见下节）。
- **`meta.through` 新鲜度口径**：是否把滞后数据直接标 `stale` 并强制重抓（会改变请求量与 `due()` 重试频率，需实测）。
- **运维 runbook**：备份恢复、监控告警、故障处置；`README.md` 与架构文档尚未覆盖 `[运维]`。
- **旧库迁移**：`predictions migrate`（合并 5/20 为单条 `horizon=0`）需人工执行，命令本身不会自动运行。
- **CLI 批量分析**：若未来成为实盘入口，需先统一服务层结果契约再单列立项。

### 研究口径候选（会改变默认研究数值，须独立评估，勿擅自启用）

- 失效模型从 `rank/best/insights` 强制排除（P2-31，需产品确认后单独实施，会改变排名与报告结论）。
- 排除末日改为严格历史分位：会推翻 version-15 定义、改变训练输入与 8 处断言，须升版本并重放验证（`docs/历史分位缺失值修正_2026-11-01.md:19`）。
- 关键宽基缺失单独升级 `degraded`（P2-28，需产品先定义「关键指数」清单）。
- 脉冲订单挂单直到成交或反向信号（与上节脉冲订单行为层为同一问题，会改变资金曲线，与既有报告数值不可直接比较）。

### 2026-11-11 全局功能完整性审查（修复轮次与剩余项）

本轮覆盖五个域（API/服务层、任务队列与存储、基金研究、股票/指数研究、CLI 与配置）的调用链、逻辑与边界审查；已登记项与「已知实现边界」不重复。编号接续既有序列，已关闭项保留编号供历史引用。

**验证与提交状态（2026-11-11）**：`uv run --locked python -m unittest discover -s tests -t . -p 'test_*.py'` → 575 项 OK（离线，约 178s；上一轮基线 574 项）；修改模块导入冒烟通过。新增 `tests/funds/test_payloads.py::test_open_window_year_is_not_frozen_by_fetched`（P1-21），更新 `tests/tasks/test_taskstore.py` 租约回收测试（P2-39 宽限期）。代码修改随本轮修复提交（提交哈希与验证快照见 `git log`），按关闭标准的实现记录已补齐；推送状态另以 `git status -sb` 为准。本轮未改变研究数值口径（P3-N/O/P、P3-R 的仓位/阈值项一律未动），无旧报告重算要求；未重跑浏览器/真实数据验证。

#### 本轮已关闭（实现摘要，不重复全文）

- **P1-21**：`funds/holdings.py` 增量缓存新增 `pending_years` —— 只有拿到 Q4 或披露窗口关闭（次年 3/31 后）的年份才算终版；未终版年份按披露季起点（Q2 约 7/30、Q4 约次年 1/30）+ 7 天冷却自动重试，空结果不再永久冻结；meta 读写兼容旧文件（缺 `pending_years` 视为无冷却）。新增回归覆盖「当年被 fetched 冻结」不再成立。
- **P2-38**：删除 `semiannual_exposure` / `_map_industries` / `exposure_coverage` 的 `only_full` 死参数，口径改由 `_normalize_holdings` 数据流保证；行动约束中的错误否定结论已更正。
- **P2-39**：`tasks/store.py` 新增 `RECOVER_GRACE_SECONDS=60`（recover 阈值改为 `lease_until < now-60`）与 `claim(exclude=...)`；`tasks/worker.py` 领取时排除本机仍有存活子进程的 jid。锁短暂饿死不再二次领取。
- **P2-40**：`tasks/sync.py::overlay_refresh` 的 read→concat→write 纳入 `leader()`（sync.lock）互斥；拿不到锁时跳过 canonical 合并（窗口缓存已写入、正史由下次同步刷新），不再与 `tick()` 交错丢失更新。
- **P2-41**：supervisor 启动同步子进程加 try/except，失败只记日志并 30s 后重试，不拖垮队列。
- **P2-42**（主要部分）：手工 JSONResponse 补 `ok`/`field` 与 429 `Retry-After`；`/api/corr` catch-all 记 `LOG.exception`；`NavBasisUnavailable`/`ProviderContractError` 不再被 `ValueError→400 params` 误标（改 500 `field=data_source` + 日志）；`/api/config` 改 `.get("RBSA")`；`ordering.py` 非数值排序值按缺失处理不抛 `ValueError`；BodyLimit 413 加 `connection: close`；`/api/result` 取消/超时分类优先用终态 `stage`，文案仅兜底。
- **P3-J**：`market/analysis.py` 默认终点、`predictions/ledger.py` 三处「今天」统一北京时间（`SHANGHAI`），与同步调度一致；可注入的 `now=` 参数不变。
- **P3-L**：`_refresh_window_caches` 逐数据集容错（`AssetSyncAborted` 仍传播），canonical 写成功后单窗口失败记日志继续，不再把整资产标 `error`。
- **P3-M**（DDL 部分）：`tasks/store.py` 新增 `_ensure_schema()`（查 `information_schema.tables` 后才建表），`stats/claim/update/_terminal/public/get` 改用，高频读/轮询不再每次跑 `CREATE TABLE`。
- **P3-Q**（walkforward 部分）：`research/walkforward.py` summary 新增 `IR面板口径`，标注共同可用基金 <2 的不等样本回退及 oracle 抬高提示。
- **P3-R**（一致性部分）：`fit_fund_holdings` 捕获 RBSA 样本不足 `ValueError` 返回 `ok=False`；`estimate_equity` 默认 `method` 与 docstring 统一为 `local`（运行时显式传参，不改结果）；`align_prices` 整行 dropna 的删除行数写入 `attrs.warnings` 并记日志。
- **P3-S**（入口契约部分）：`fil analyze` 失败返回 1；`cli/serve.py` docstring 改为真实入口、`main(argv, prog)` 兼容注册表、就绪失败返回 1、stdout 重配 UTF-8；`cache-warm --start` 复用 `_iso` 严格校验；`FIL_DATA_STALE_DAYS` 非法值回退默认并告警；`Settings.load` 数值解析报出字段名。

#### 复核更正（勿再列为待修）

- **P3-K `through=max(...)` 不成立为缺陷**：`overlay_refresh` 的 `frame` 只做合并（保留旧行 + 新行），`data_end` 取 `frame.index[-1]`，不会因更早 `end` 回退；`cached()`（`sync.py:220-229`）另有 `data_end` 滞后 `DATA_STALE_DAYS` 的未就绪守卫。剩余只是 `through`（检查日）语义单调、与 `synchronize` 倒退拒绝的表述不对称，归入已登记的 `meta.through` 新鲜度口径候选，未改代码。
- **P3-Q 的块自助「循环复用」是误报**：`research/robustness.py:216` 的 `starts` 已限制在 `[0, n-block]`，拼接索引恒 `< n`，`idx[:n]` 恰为 n 个，`np.resize`（`:221`）为不可达的防御性死代码；non-circular 口径成立。

#### 剩余未完成项

- **P2-42 剩余**：`FIL_SYNC_TIME` 非法值经 `GET /api/sync`（`routes/sync.py:11`）抛 `ValueError` 仍被全局处理器标成 400 `field=params` —— 服务端配置错误需单独分类；其余子项已修。
- **P3-H 补充** `[产品]`：前端 `static/app.js:939,1000,1019,1068,1079`、`market.js`、`data-center.js`、`ledger.js` 全部硬编码 `/api/*` 旧路径；`api/version.py:21-28` 的 2027-04-01 legacy 自动下线若按期触发，SPA 当天全量 404。auto 下线必须与前端自身迁移同批决策，不能只等外部客户端。
- **P3-M 剩余**：不可回收的僵尸子进程永久占用并发名额、队列停摆（`worker.py:20-43,147-158`），只能重启 supervisor。
- **P3-N**（行为层，会改变资金曲线，未动）：`market/capital.py:71` 无 `units==0` 门控 vs `market/execution.py:99` 仅空仓首买；`capital.py:143-145` `_retry_note` 文案与 orders=targets.shift(1) 实现不符；`compare_probability_execution` 两种账户实际评估不同策略。
- **P3-O**（口径）：离线 `(future > 0)`（`market/technical.py:90`）把平盘计入下跌类，台账 `neutral` 不入分母（`predictions/judge.py:173-176`）；跨口径比较 accuracy/Brier 有偏。
- **P3-P**：`market/technical.py:130-137,202-208` 少样本仍输出数值（仅 `ok=False` 标记），未像 `market_validation.py` 留空。
- **P3-Q 剩余**：`market/capital.py:103-104` 残余仓位卖出额低于最低佣金时被 `insufficient_cash_for_sell_fee` 永久阻塞、闭合周期不计（费用触发的平仓死锁，区别于已登记的容量触发残留）。
- **P3-R 剩余**：`rbsa.py:48-49` 的 `W+30` vs `modeling/engine.py:177-178` 的 `W+60` 两套门槛；`payloads.py:629-654`「仓位假设失配」以名义 0.9 诊断（`auto_equity=True` 实际逐窗自适应）；`pipeline.py:117` 波动目标变体 `fillna(1.0)` vs `backtest.py:177,203` 的 0.5；`config.py:251-252` `l1_ratio/alpha_grid` 死配置；`estimators.py:276,320,380,402` 四模型等权降级不传 status 不可观测。以上均会改变数值或诊断结论，须独立评估。
- **P3-S 剩余**：`commands/holdings.py:135` 的 `✗`、`market_snapshot.py:118/121` 的 `…` 等在 GBK 重定向下仍可能 `UnicodeEncodeError`（`serve.py` 已加 reconfigure，其余入口未推广）。

## 行动约束

复核已否定的判断，不要重复引入：

- 不要回退到「CSV 写行情 / Flask / 进程内线程跑任务」；旧导入只留在 `compat/`，不要再新增根目录 `run_*`，研究入口只登记到 CLI 注册表。
- 请求线程内的端点（`/api/corr`、`/api/name`）**不得联网取数**：取数交回一次正式的基金分析（走队列、可取消、有名额限制）。
- `src/__init__.py`、`webapp/__init__.py` 是 `compat/` 的兼容桥接（`src.*` → `compat/src`），`tests/data/test_storage.py` 与 `pyproject.toml` 的 `include` 依赖它们，不是遗留脚手架。
- ~~`semiannual_exposure(only_full=False)` 不是死参数~~ **该结论有误，已更正（2026-11-11）**：`_normalize_holdings` 的 `dropna(subset=["季末"])` 删除的正是 Q1/Q3 行，存活行 `完整` 恒为 True，`only_full=True/False` 输出相同（最小样本实证与参数删除见 P2-38）。原否定只验证了分支语法会执行，未核对 dropna 删除的行集合；参数已于 2026-11-11 删除。
- 持仓暴露表里「行合计 < 100%」的差额**不是现金**：包含抓不到行业的新股/北交所/停牌股。引用时必须带上 `exposure_coverage()` 的覆盖率，否则会把股票仓位说成现金。
- 不要记为「完全没有交易日历」；也不要把 `live_observation` 一次性快照的行数/哈希当作 CI 断言基准。

### 历史口径变更后的重跑与引用约束

以下修复改变了既有数值口径，**旧报告/导出/结论不得直接引用，须按新口径重跑后再比较**：

- beta 自由度统一 `ddof=1`（`CACHE_VERSION 11→12`）：β/γ/权重/R²/TE 数值整体变动，历史报告与导出需重跑。
- P2-23 持仓基准残差改为 0 收益：历史持仓拟合结论，以及「基金 − 基准」「RBSA − 基准」差额须按新口径重跑。
- P1-20 总回报降级默认拒绝：total 构造失败期间生成的基金分析/回测/报告/导出须重跑；`allow_cum` 产出的历史结论不能与 total 口径结果混用。

### 已知实现边界（有意为之，勿重复列为缺陷）

- 同步 watchdog 守护线程不被强杀，只在检查点协作退出；`config_hash` 只含研究常量级运行配置（滚动 `START_DATE/END_DATE`、运行时 `Settings` 环境变量与单次请求参数不进 hash，另见 `run_fingerprint.extra`）。
- 原生 `urllib`/socket 且未传 timeout 的 provider 不再受进程级默认超时保护（刻意移除的全局副作用）。
- live smoke 失败 Issue 去重仅按精确标题取前 100 个 open issue。
- DuckDB 无部分唯一索引，入队原子性由写入语句 + 文件锁保证；绕过 `DurableJobStore` 直接写表仍可能产生重复。
- token fencing 与 supervisor `finally` 的重试语义未改动。
- `funds/universe.py` 按基金代码排序、`payloads.py` 超限计数列表只做数值降序：按设计无需 tie-break，不列为缺陷。

## 关闭标准

每个 TODO 关闭时记录实现提交、验证命令、数据快照和结果；凡以具体数值作为证据的，必须同时记录可复现的最小输入（样本长度、随机种子、参数）。不得仅凭「脚本退出码为 0」「所有模型都有返回值」或「页面能打开」关闭研究有效性相关任务。当前回归基线与未覆盖范围见 [测试说明](tests/README.md)。
