# 项目待办

本文件只记录**未完成项**与防止误判的必要约束。已完成项的实现、验证快照与历史审查证据不在此重复：提交与修复记录见 `git log`（历史审查原文可用 `git show <提交>:TODO.md` 取回），验证记录见 [测试说明](tests/README.md)，方法论与专项结论见 [研究方案](docs/研究方案.md)、[功能完整性检查](docs/功能完整性检查.md) 及 `docs/` 下各专项文档。

优先级沿用历史口径：P1 影响正常计算或结果解释，P2 为参数语义或特定入口问题，P3 为可迭代项；编号（P1-10、P2-xx、P3-x）保持不变，便于历史文档引用。标注 `[产品]` / `[法务]` / `[运维]` 的条目不是纯技术修复，需先拍板再实施。

## 未完成项

**当前执行入口**：[2026-11-16 功能与预测可靠性优化计划](#optimization-plan-20261116)。历史审查小节保留当时状态与证据，不表示其中所有问题当前仍未修复；本轮复核状态、执行依赖与验收门槛以该计划为准。复选框记录各项实施状态。

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
- **未覆盖范围**：真实数据研究重放、完整浏览器/移动端回归、多进程与锁竞争压力、Docker/wheel 构建安装；2026-11-11 前历次全局审查只核对参数/任务入口、队列与 worker、调用链、缓存同步与前端脚本，2026-11-11 一轮新增了 RBSA/持仓/技术分析/资金账户/校准等算法层逻辑与边界核对，但仍**未逐行穷尽全部算法**。2026-11-12 补跑离线测试与隔离浏览器夹具检查，具体范围见下方同日小节；不据历史记录、合成行情或页面可打开推定真实数据与未来预测有效性已覆盖。

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

- **P2-42**：`FIL_SYNC_TIME` 非法值已由 `routes/sync.py` 单独分类为 500 `field=config`，并由 `tests/api/test_routes.py::test_sync_schedule_configuration_error_is_server_side` 覆盖；此前计划基线未同步更新。
- **P3-H 前端迁移**：`app.js`、`market.js`、`data-center.js`、`ledger.js` 已统一通过 `API='/api/v1'` 请求，不再硬编码旧 `/api/*`；后端 legacy 路径仍按 `api/version.py` 计划值保留至 2027-04-01，仅影响外部客户端 `[产品]`。
- **P3-M**：`worker._reap_children` 对连续多轮无法回收的子进程强制释放本地并发名额并转入失败/重试，避免 supervisor 停摆；真实僵尸进程恢复仍待部署环境观察。

**实施记录（2026-11-22，工作区改动）**：`update_record` 返回实际更新行数，并发结算不再重复计数；`_reap_children` 连续三轮无法 `wait()` 时释放本地名额并按超时/崩溃分类；四份 SPA 脚本统一用 `API='/api/v1'`，浏览器与前端测试夹具同步迁移；统一 CLI 同时保护 `stdout/stderr`，两处直接入口改用 ASCII 文案。611 项 unittest 与三套前端脚本通过；未跑浏览器交互或部署环境僵尸进程实机复现。

**新增能力（2026-11-24，工作区改动）**：独立新增 `us_index` 同步资产（IXIC/INX/DJI），不进入 A 股 RBSA 候选池；`cross_market_block` 输出同日探索相关和严格滞后一交易日的 60/120/全样本相关，方向提示仅取滞后口径，样本不足或弱相关时标为不判断。市场结果页新增只读表格与免责说明；数据中心可查看/同步美股指数；结果导出和离线验证报告（版本 16）增加 `cross_market_reference`，不进入模型输入、排名、覆盖率或完成状态。结果缓存版本升至 15。618 项 unittest 与三套前端脚本通过；akshare 接口已做一次联网冒烟，但未把实时判断当作策略有效性验证。随后修正窗口语义：共同样本不足 120/60 时不输出对应窗口相关，方向提示置信度按**实际参与相关性的窗口样本数**判定（60 日窗口不会因共同样本更长而升级），并新增 `corr_window_used/n_window_used`。
- **P3-N**（行为层，会改变资金曲线，未动）：`market/capital.py:71` 无 `units==0` 门控 vs `market/execution.py:99` 仅空仓首买；`capital.py:143-145` `_retry_note` 文案与 orders=targets.shift(1) 实现不符；`compare_probability_execution` 两种账户实际评估不同策略。
- **P3-O**（2026-11-25 已统一）：训练/历史评估/台账 Brier 统一为 `P(R_h>0)`、平盘目标 0；命中率独立只算 up/down，平盘不入分母。
- **P3-P**：`market/technical.py:130-137,202-208` 少样本仍输出数值（仅 `ok=False` 标记），未像 `market_validation.py` 留空。
- **P3-Q 剩余**：`market/capital.py:103-104` 残余仓位卖出额低于最低佣金时被 `insufficient_cash_for_sell_fee` 永久阻塞、闭合周期不计（费用触发的平仓死锁，区别于已登记的容量触发残留）。
- **P3-R 剩余**（2026-11-25：`W+30/W+60` 已统一为 W+30，仓位失配诊断已改用逐窗 `equity_used`，波动目标预热仓位已统一交给 `run_backtest` 的 0.5；以下仍存）：`config.py` 的 `l1_ratio/alpha_grid` 死配置；`estimators.py` 四模型等权降级不传 status 不可观测。
- **P3-S**：统一 CLI 已同时保护 `stdout/stderr`，直接入口的 `✗`/`…` 已替换为 ASCII 文案，GBK 重定向不再因符号输出崩溃；其他模块的同类字符仍由统一入口兜底。

### 2026-11-12 股票、基金、指数拟合预测专项检查

**结论**：三类资产的研究入口及主要计算链路已接通，可以作为内部研究原型运行；尚不能按“完整、可执行且已验证有效的预测/交易系统”验收。本轮只登记问题，未修改算法、默认研究口径、行情库、预测库或未来验证协议/证据。

| 范围 | 当前能力与落地边界 |
| --- | --- |
| 基金 | 11 模型滚动样本外拟合、调参重学、风格/持仓比较、健康度回测、5/20 有效净值日方向概率。拟合是用同期指数收益重建基金收益，不是提前预测未知指数收益；健康度不是涨跌预测。申赎确认/持有期费用、时点净值及行业归属仍缺真实证据。 |
| 股票 | 沪深京 A 股日线、技术条件、5/20 周期方向概率、候选比较、成交诊断及预测回验；离线有冻结训练、独立校准与资金账户。正常页面概率未单独校准，盘后发布约束、降级传播与结算口径仍有下列缺口。 |
| 指数 | `sh000*` / `sz399*` 价格指数研究复用股票方向链路；行业指数还作为基金拟合因子。指数自身不可直接交易，实际 ETF 的跟踪偏差、费用与成交验证仍按既有六项未来验证执行，不用历史小样本代替。 |

**本轮实际验证**（代码基线 `12d2224`，开始时工作区无修改）：

- `uv run --locked python -m unittest discover -s tests -t . -p 'test_*.py'`：**575 项通过，87.915 秒**；`node tests/frontend/test_refit.js`、`node tests/frontend/test_ui_async.cjs`、`node tests/frontend/test_market_async.cjs` 均通过。现有用例通过不代表下列反例已被覆盖。
- 11 模型调用冒烟：`idx=pd.bdate_range('2024-01-01', periods=400)`；`r=np.random.default_rng(42).normal(0,.01,(400,4))`；四指数价格 `exp(cumsum(r))`，基金 `exp(cumsum(r @ [.3,.3,.2,.1]))`；`ModelZoo({**C.RBSA, 'auto_equity':False}).run(panel, MODEL_ORDER, workers=1, max_rebal=1)`。11 个模型均无 `extra.error`、合成收益有限，**但每个仅 7 个 OOS 点**，只能证明调用可运行，不能证明拟合质量或预测有效性；门槛问题补入下方 P3-R。
- 真实浏览器仅做**离线响应夹具**检查：隔离数据目录、关闭 worker，使用现有 `fund-style-fixture.json` / `context-fixture.json`，请求拦截后验证股票/指数结果、30 日区间校验、基金方向概率可用/部分不可用/不可用状态，以及持仓切换和部分失败反例；检查时视口宽 825px，无横向溢出，无控制台 error/warn。旧基金夹具不含方向字段，可用性渲染另注入 `p=.6`、`baseline=.5`、`n=30` 的明确合成夹具，**不作为模型表现证据**。未跑完整浏览器套件、手机尺寸、真实联网分析或部署构建。
- 下列 Python 最小反例使用 `.venv/Scripts/python.exe`、内存合成输入及 `unittest.mock.patch`；供应商/存储调用均替换为内存返回，不写真实缓存或台账。并行审查未返回可用结果，以下只采纳主审直接复核的代码与实测证据。

#### 新增未完成项（按影响优先处理）

- **P1-22 股票原始价预测在结算时被改为前复权**：[`predictions/ledger.py:242-255`](src/market_research/predictions/ledger.py#L242) 正确冻结原始价 `price_basis=""`，但 [`predictions/judge.py:219-248`](src/market_research/predictions/judge.py#L219) 的 `record.get('price_basis') or 'qfq'` 将合法空串当缺失；联网/离线结算都改读 qfq，可改变跨除息期收益及对错标签。
  - **复现**：记录 `{asset_kind:'stock', asset_code:'sh600519', as_of:'2025-01-02', price_basis:''}`，mock `sync.cached` 返回单日 close=100，调用 `_load_prices(record, date(2025,1,10), False)`，实际传给缓存的 basis 为 **`qfq`**。
  - **验收**：区分“字段缺失”和合法空串；覆盖 raw/qfq/hfq 三种记录及 fetch 两分支，构造除息前后原始价与复权价方向不同的夹具，保证结算沿用冻结口径。既有受影响记录先识别/留痕，再决定重判，不能静默覆盖历史判定。

- **P1-23 基金方向模型的日期轴被候选指数缺日改变**：[`data/panels.py:43-68`](src/market_research/data/panels.py#L43) 对齐时会删掉基金本来有净值的日期；[`funds/analysis.py:181-200`](src/market_research/funds/analysis.py#L181) 将这个面板中的基金列交给方向模型，[`funds/payloads.py:170-180`](src/market_research/funds/payloads.py#L170) 按剩余行计周期。台账却冻结 `observed_bars`，[`predictions/judge.py:205-240`](src/market_research/predictions/judge.py#L205) 按独立净值轴结算：训练/历史评估与回验不是同一个“5/20 有效净值日”。
  - **复现**：300 个 `bdate_range('2024-01-01')` 日期，基金及指数 a=`100+arange(300)`，指数 b=`200+arange(300)`；b 的位置 `[100:108]` 设 NaN，默认 `max_ffill=5/min_coverage=.97` 保留该指数但删 3 行。以位置 102（2024-05-22）为起点，`horizon_data(...,5)` 的原始净值目标为 **2024-05-29**，对齐后目标变成 **2024-06-03**；同一基金得到不同标签期限。
  - **验收**：方向模型独立使用原始净值日期轴，指数对齐只影响拟合；预测、历史评估与结算共享同一时钟定义。新增“指数缺日但基金不断档”回归，保证方向概率/目标日不被无关指数缺口改写。

- **P1-24 PCR 内层验证预处理仍使用验证段信息**：[`modeling/estimators.py:338-349`](src/market_research/modeling/estimators.py#L338) 先用完整滚动窗口算 `mu/sd`，再切 80% 训练段选择 PCA 维度；PCA 虽只 fit 前段，输入缩放已依赖后 20%。这是**内层选参泄漏**，不是外层 OOS 使用未来标签，不能用已修复的 enet/lasso/ridge 折内标准化推定 PCR 也已覆盖。
  - **复现**：`X=default_rng(42).normal(size=(300,4))*.01`，`y=X@[.3,.3,.2,.1]`；仅将 `X[240:,0]` 乘 100，y 不变。两次 `fit_window('pcr', ..., equity_ratio=.9)` 捕获首个 `PCA.fit` 输入，原始前 240 行完全相同，但 PCA 训练输入最大绝对差 **3.060200465060608**。
  - **验收**：选择维度时只在内层训练段拟合缩放/PCA，再变换验证段；确定超参后才全窗口重训。回归断言改变验证段不改变训练预处理；修复会改变选参与数值，需版本化并重跑相关研究。

- **P1-25 基金 OOS 候选集合受全区间未来覆盖率筛选影响**：[`data/panels.py:43-56`](src/market_research/data/panels.py#L43)、[`data/panels.py:442-465`](src/market_research/data/panels.py#L442) 在进入滚动拟合之前，按整个抓取/分析截止区间的覆盖率删列；后续才发生的缺数可决定早期窗口允许使用哪些指数。此项区别于 P1-11 的历史行业归属缺失，也区别于已修复的 `end` 之后净值未截断；当前全区间统计仍不是逐窗时点可得选池。
  - **复现**：沿用 P1-23 的 300 行确定性面板，改为 b 的 `[200:]` 缺失；只分析前 200 行时列为 `[a,b,__FUND__]`，追加后 100 行、历史前缀不变时变为 `[a,__FUND__]`。滚动权重之前的特征集合已改变，不能仅凭重估锚点稳定宣称完整 OOS 链路前缀稳定。
  - **验收**：研究路径按训练窗口决定可用因子，或预先冻结并保存候选池；全区间描述性覆盖筛选与因果 OOS 分开标注。测试仅改变未来缺失模式时，共同历史窗口的选池/权重/预测不变（不把有意的尾部重估限流淘汰混入测试）。涉及研究口径变更，须独立评估与重放。

- **P1-26 切换基金后展示上一只基金的持仓结果**：[`api/static/app.js:1194-1230`](src/market_research/api/static/app.js#L1194) 的 `applyResult` 清理回测却未清理 `holdOut/holdMsg`；[`api/static/app.js:2370-2405`](src/market_research/api/static/app.js#L2370) 的持仓 DOM 不绑定基金代码或分析指纹。新的基金头部可与旧持仓数字并存，调参/切换窗口也有相同问题。
  - **浏览器复现**：基金 A=110022，`renderHoldings` 输入 `ok=true,n_periods=3`，notes.coverage=`这是基金 A 的持仓`；再 `applyResult` 基金 B=000001。实测头部已为 B，但 `holdOut.hidden=false` 且仍显示 A 的文字。
  - **验收**：新分析/基金/窗口/拟合配置生效时清空持仓结果与错误提示，或严格按同一指纹恢复；补 A→B、同基金换窗口、重学、失败结果及在途持仓响应的浏览器回归。

- **P1-27 股票/指数缓存降级标记在结果中丢失**：[`market/analysis.py:98-107`](src/market_research/market/analysis.py#L98) 会返回带 `stale/source_error` 的旧行情；但 [`market/analysis.py:235-270`](src/market_research/market/analysis.py#L235)、[`market/analysis.py:298-302`](src/market_research/market/analysis.py#L298) 只输出区间/缺日等信息，没有传播回退原因或完整性状态，结果接口又默认 `status=ok`。日期无差距不等于本次数据源刷新成功。
  - **复现**：80 个 `bdate_range('2025-01-01')`，close=`100+arange(80)`、open=close−.1/high=close+.5/low=close−.5/volume=1000；mock `load_bars` 返回 attrs=`{stale:True,source_error:'TimeoutError: offline audit',fetched_at:'2025-04-22T16:00:00'}`，`sync.cached=None`。调用 `analyze_market('stock','600519',start='2025-01-01',end='2025-04-22')`，gap=0，但结果无 stale、错误摘要、`status` 或 `meta.data_quality`。
  - **验收**：资产及基准各自的回退/错误/抓取时间进入统一质量契约、页面及导出；股票和指数都补降级回归，并与 P2-31 的展示门禁衔接。不要仅凭自然日差距推断停牌或新鲜度。

- **P1-28 持仓比较失败仍以完整成功显示，净值口径也未贯通**：[`funds/holdings.py:942-975`](src/market_research/funds/holdings.py#L942) 不按 `compare.ok` / 基准可用性调整顶层成功状态，也未把 panel 的 `nav_basis/nav_degraded/data_quality` 带出；[`funds/payloads.py:226-275`](src/market_research/funds/payloads.py#L226) 保留子块失败但顶层仍 `ok=True`。[`api/routes/tasks.py:33-37`](src/market_research/api/routes/tasks.py#L33) 默认报正常，页面不展示 `compare.reason` / `benchmark.note`。这不是 P1-21 的持仓年份缓存问题。
  - **复现**：`holdings_payload({code:'110022',ok:True,compare:{ok:False,reason:'共同交易日不足60'},benchmark:{available:False}})`，结果顶层仍真；mock done job 交给结果路由后 `status='ok', completeness={}`。浏览器 `renderHoldings` 后持仓区可见、错误提示为空，比较失败原因和基准不可用说明均未显示；向序列化输入加入 cum/degraded/quality 字段也不会输出。
  - **验收**：保留可用的持仓结构部分，但按子块生成 partial/failed 状态并显示原因；净值 total/cum、降级和源质量贯通服务/API/页面/报告。分别测试对齐成功但比较不足 60 日、全部行业行情缺失及 `allow_cum` 情形，不用空表冒充成功。

- **P1-29 “实盘最新收盘”入账门禁只按 14 个自然日放行**：[`predictions/ledger.py:94-117`](src/market_research/predictions/ledger.py#L94)、[`predictions/ledger.py:219-223`](src/market_research/predictions/ledger.py#L219)、[`predictions/ledger.py:363-383`](src/market_research/predictions/ledger.py#L363) 虽拒绝显式历史 end，却不核验无 end 请求使用的缓存是否为最后完成会话、目标是否已到期或当日是否收盘。旧缓存可在事后被登记为前瞻记录；不可变首条机制还会阻止同日后续正确版本替换。
  - **复现**：内存结果 stock/sh600519、`as_of='2025-01-02'`、5 日 probability=.6/ok=true、`params.end=None`，`now=2025-01-15 16:00+08:00`；mock `_insert` 只统计，仍准备插入 **1 条**。将 now 改为信号日 **10:00** 同样放行；后者证明入口无收盘守卫，不代表本轮已验证真实供应商盘中响应。
  - **验收**：用资产发布时点与最后完成交易会话确认可入账性，排除已成熟或过期缓存，并记录分析生成/数据可得时间；基金需考虑净值披露滞后，周末长假不能简单改成固定天数阈值。覆盖显式历史、旧缓存无 end、盘中暂态、正常盘后和长假；旧记录审计后分组标注，不能直接充当未来验证证据。

- **P2-43 持仓 API 接受但忽略分析窗口和仓位参数**：[`api/schemas.py:117-119`](src/market_research/api/schemas.py#L117) 继承 `FundWindow`，公开接受 start/end/auto_equity；[`services/analysis.py:34-55`](src/market_research/services/analysis.py#L34) 用 `**_` 丢弃，底层 [`funds/holdings.py:911-933`](src/market_research/funds/holdings.py#L911) 按默认窗口/配置重拟合。网页持仓按钮也未传当前窗口，不能把结果默认理解为与上方拟合相同设置。
  - **复现**：`HoldingsRequest(code='110022',start='2020-01-01',end='2024-01-01',auto_equity=False)` 校验通过；mock 行业表及底层 fit 后调用服务，fit 实际只收到 code、industry_map 和 `refresh=False`。
  - **验收**：若支持窗口/仓位则完整透传、写入结果并绑定 UI；若持仓固定采用独立窗口，则从请求契约拒绝无效参数，并显式展示实际窗口/模型/仓位/重估方式。不可继续“接受成功但不生效”。

- **P2-44 单候选指数时 PCR 维度边界失败**：[`modeling/estimators.py:341-348`](src/market_research/modeling/estimators.py#L341) 的 `kmax` 与候选集合强制下限 2，与 `n_features=1` 冲突；候选池部分降级到单列后 PCR 不可运行。
  - **复现**：沿用 P1-24 的 X/y，调用 `fit_window('pcr',X[:,:1],y,['a'],equity_ratio=.9)`，实际抛 `ValueError: Found array with 0 feature(s) (shape=(240, 0)) ... LinearRegression`；相同输入保留两列则可运行。
  - **验收**：在模型适用性层明确拒绝并返回可读 unavailable，或实现有定义的一维退化；测试 0/1/2 列与短训练段，失败不进入最佳模型推荐（排名口径沿用 P2-31 的决策约束）。

#### 与已有待办合并的补充（不另起重复编号）

- **P3-R 样本门槛补充**：除既有 `W+30/W+60` 不一致，还需检查重估限流**之后**的实际 OOS 样本数。[`modeling/engine.py:135-136`](src/market_research/modeling/engine.py#L135) 保留尾部锚点，[:242](src/market_research/modeling/engine.py#L242) 才建立合成序列，[:318-321](src/market_research/modeling/engine.py#L318) 直接算指标；[`funds/analysis.py:71-83`](src/market_research/funds/analysis.py#L71) 只以 error/空 R² 判模型失败。本轮上述 400 行、`max_rebal=1` 冒烟的 11 模型都只有 **7 个 OOS 点**，仍输出指标。验收需公开有效 OOS 起止/n 与重估覆盖，单独定义足够样本门槛；不足时不作为正常排名/推荐，不能仅以输入总长度关闭。
- **P2-31 结果契约补充**：已有基金页 meta badge 并不等于统一失败门禁；P1-27/P1-28 的市场/持仓链路甚至缺状态传播，应与顶部横幅、导出/报告同批贯通。失败模型是否强制退出 rank/best/insights 仍按原 `[产品]` 决策约束处理，本轮未改变排名。
- P1-10/P1-11、P3-N/O/P/Q、六项未来验证、时点数据版本、基金申赎费用及模型档案仍保持未完成；本轮没有用离线测试、模拟成交或合成夹具替这些条目验收。建议先处理入账/结算与错误结果显示，再修复研究时点性与参数契约；P1-24/P1-25/P3-R 等涉及研究数值变更的项需单独升版本、重放后比较。

### 2026-11-12 修复验证记录

- **已实现**：P1-22 冻结原始价、前复权和后复权的结算口径。P1-23 用独立净值日期轴估计方向概率。P1-24/P2-44 在内层训练段拟合 PCR 预处理，并处理零列、单列、两列与短窗口。P1-26 使持仓展示跟随当前分析并拒绝旧请求响应。P1-27/P1-28 显示市场行情回退及持仓部分失败，保留净值口径与来源质量。P2-43 透传持仓窗口和仓位参数。P2-42 将无效同步时间归为服务端配置错误。P3-S 的注册表入口兼容 GBK 重定向。
- **部分实现**：P1-29 的股票和指数入账要求供应商日历覆盖、信号日为最后完成会话、收盘后发起的请求返回对应日期的日线，并记录取数与分析时间。旧缓存须成功刷新才有时间证据。基金净值的可得时点及旧记录分组仍待处理。P2-31 的页面结果状态与导出已接通，失败模型排名规则仍待决策。
- **回归**：设置 `PYTHONUTF8=1` 后运行 `.venv/Scripts/python.exe -m unittest discover -s tests -t . -p 'test_*.py'`，585 项通过。`node tests/frontend/test_refit.js`、`node tests/frontend/test_ui_async.cjs`、`node tests/frontend/test_market_async.cjs` 通过。合成夹具包括种子 42 的 300×4 PCR 输入及验证段扰动、300 个工作日净值与 8 日候选指数缺口、原始价和复权价跨事件方向相反、80 根日线的零日期差回退，以及长假和盘中入账。
- **PCR 快照重放**：结果缓存版本 13→14。用本机归档 `fund_nav_110022_2016-01-01_total` 和 `index_universe_2016-01-01` 调 `align_prices`，截取 2019-01-01 起的 1,909 行和 43 个因子，`panel_sha256=8385ff8b263d2239ec6628c9f6a5c93d3f0919a2d0fde4feda48567954dac200`。同一面板上比较 Git HEAD 与修复版的 PCR，`auto_equity=False, workers=1, max_rebal=0`。共同样本外区间为 2020-01-14 至 2026-11-11（1,656 点），R² 从 0.721612 变为 0.723686，TE 从 0.117283 变为 0.116846。旧归档仅用于本次固定输入对照。其他历史报告须按新版本重跑后引用。
- **台账只读审计**：本机 `market.duckdb` 的 `prediction_records` 中股票 `price_basis=''` 为 0 条，缺少收盘时间证据的旧股票待结算记录为 3 条。台账未重判或改写。未来验证执行台账仍按既有协议等待真实输入。

<a id="optimization-plan-20261116"></a>

## 2026-11-16 功能与预测可靠性优化计划

### 目标、范围与当前基线

**目标**：先保证结果不使用判定时点之后的数据、失败与证据不足不被伪装为成功，再提高基金拟合的时点一致性及预测评估的可比性，最后完成可复现的独立验证。验收对象分为“工程正确”“评估充分”“前瞻有效”“实际可交易”四层，不以某一层通过替代其余层。

**该计划初次编写时仅为计划**：未修改业务代码、研究配置、行情/预测数据库或冻结协议。下方勾选状态与实施记录是后续按计划执行的结果；涉及 `[产品]` / `[研究口径]` / `[法务]` / `[运维]` 的决策，由项目负责人或相应责任方确认，不把本次文档更新视为批量迁移、重判、部署或启用新默认值的授权。

- 复审基线：`9f75233`，开始时工作区无修改。2026-11-16 实跑 `PYTHONUTF8=1 uv run --locked python -m unittest discover -s tests -t . -p 'test_*.py'`，**585 项通过，94.259 秒**；三套前端脚本 `test_refit.js`、`test_ui_async.cjs`、`test_market_async.cjs` 通过。
- 当前基线（2026-11-25）：**642 项通过，约 234 秒**；三套前端脚本通过。`FIL_SYNC_TIME` 配置错误已实测为 500 `field=config`，此前“剩余”描述是文档未同步。
- 已修并保留回归：P1-22 原始价结算、P1-23 独立基金净值预测轴、P1-24/P2-44 PCR 折内预处理与维度边界、P1-26 持仓旧结果、P1-27/P1-28 数据降级及持仓部分失败、P2-43 持仓参数、P1-29 基金入账及历史记录审计、P1-30/P1-31 结算时点与收盘证据、P2-42 配置错误分类、P3-H 前端 `/api/v1` 迁移、P3-M 不可回收子进程释放本地名额。**不重新实现或重复立项**。
- 仍需完成：P1-25 全区间选池、P3-R 实际 OOS 门槛、P3-O 评分口径、P3-P 少样本展示、P2-31 失效模型推荐门禁。
- 正面检查：种子 99、800 个工作日、`close=100*exp(cumsum(N(.0002,.012)))`，OHLC 分别为 close×(.999,1.01,.99,1)；只将位置 651 之后的 OHLC 乘 1.5，5/20 日模型此前的概率均不变。该检查仅证明固定日期/候选定义下的前缀稳定性，不证明预测优势。
- 本轮未跑浏览器交互、真实行情研究重放或部署。当前本地结果目录未找到可核验的历史研究报告；已有文档中的 17/48 候选改善、40/60 校准拟合成功等仅是历史记录，不作为当前版本验收。
- 冻结协议及证据指纹只读校验通过；六项未来验证仍未完成，预留期 **2026-11-11～2027-05-10**、评估日 **2027-05-11** 不变。

### 分阶段路线与依赖

工作包编号仅用于实施排序，缺陷编号沿用原 TODO；仅新增 P1-30、P1-31。工作量为单人有效开发日的粗估，包含该包回归，不含审批、数据采购及未来样本等待，不是交付日期承诺。

| 阶段 | 工作包 / 对应条目 | 交付结果 | 前置依赖 | 粗估 |
| --- | --- | --- | --- | --- |
| Now：M1 时点安全 | A1 / P1-30 | 结算不越过判定日 | 无 | 0.5～1 日 |
| Now：M1 时点安全 | A2 / P1-31 | 盘中与缺收盘证据不终判 | A1 的时间接口；日历/来源元数据 | 1～2 日 |
| 已完成：M1 时点安全 | A3 / P1-29 | 基金前瞻入账、可追溯拒绝原因及只读历史审计 | 数据源公告时点仍未知 | 合成回归及本地只读审计 |
| Next：M2 因果拟合 | B1 / P1-25 | 逐窗选池、冻结区间权重、保留缺日 | 原始价格轴及版本方案 | 3～5 日 |
| Next：M2 证据门槛 | B2 / P3-R、P2-31 | 有效 OOS 覆盖与模型资格 | B1；推荐门槛确认 | 1～2 日 |
| Next：M3 统一评估 | C1 / P3-O | 预测评分与成交诊断分离 | A1～A3；标签口径确认 | 2～3 日 |
| Next：M3 统计可靠性 | C2 / P3-P、六项验证 | 共同样本、基线、不确定性报告 | C1；B2 的样本元数据 | 2～3 日 |
| Next：M3 展示闭环 | C3 / P2-31、P2-32 | API/页面/导出统一证据状态 | B2、C1、C2；可先按契约做夹具 | 1～2 日 |
| Later：M4 校准候选 | D1 / 概率校准验证 | 版本匹配的离线校准与发布门禁 | M1～M3；训练/发布协议确认 | 2～4 日，不含独立验证等待 |
| Later：M4 交易合理性 | D2 / P3-N、P3-Q、交易实证 | 固定策略下的账户对照与费用边界 | C1；订单语义确认 | 2～4 日，不含外部数据 |
| Later：M5 实证验收 | E1 / 既有六项未来验证 | 冻结协议下真实、独立、可审计的证据 | 相应工程包、合规数据与评估日期 | 外部依赖，不承诺关闭日期 |

**执行顺序**：先 A1→A2→A3，完成 M1 后再交付后续功能；B1 与 C1 可并行，B2 依赖 B1，C2/C3 随契约稳定后完成；D1/D2 是独立后续包，不阻塞先发布时点安全修复。E1 可提前收集授权数据与输入指纹，但不得提前查看冻结留出表现。各包实施前检查工作区；按包评审和验证，不把整个优化做成一次不可归因的大改。

### M1：阻止错误入账和提前结算

#### A1 / P1-30【新增，P1】结算可能使用判定日之后的宽限日报价

**证据与落点**：[`predictions/judge.py:219`](src/market_research/predictions/judge.py#L219) 将行情上界设为 `today+1`；[`judge.py:130`](src/market_research/predictions/judge.py#L130) 的候选报价没有 `<=today` 限制。[`tasks/sync.py:240`](src/market_research/tasks/sync.py#L240) 按包含末日的区间切片。通过结算主流程可复现：`today=2025-01-09` 时准备写入 `judge_date=2025-01-10`。

- [x] 在结算入口统一可注入的判定时点；默认用北京时间。将“按历史日期诊断”与“本次真实可得时点”区分，不能用 `--today` 声称恢复了历史数据版本。
- [x] `_load_prices` 的股票、指数、基金、联网及缓存分支统一裁剪上界；`judge_record` 再按判定上界筛候选，不能只依赖 loader 正确。下游不得读取未来报价，找不到已可得目标价时返回 pending。
- [x] 若底层供应商必须接受排他性结束参数，也必须在返回边界二次裁剪；不得利用供应商接口差异重新放入下一日报价。
- [x] 仅在目标已到期、适用的真实会话宽限已结束且来源检查成功时，才按既有规则终判缺失；日历覆盖不足不能用工作日猜测后永久定案。
- [x] **验收**：信号日 2025-01-02、h=5、10 个 `bdate_range` 会话；open=100/high=102/low=99/volume=1000，信号收盘100、后续101，删除目标日 1月9日。1月9日结算不得写入1月10日价格；候选只在1月10日实际可得后参与诊断。覆盖 raw/qfq/hfq、基金、fetch 两分支、历史参数、宽限期及无供应商日历；所有写入均断言 `judge_date<=判定日期`。

**实施记录（2026-11-16，工作区改动）**：修复前，合成夹具在 1 月 9 日提前采用 1 月 10 日价格。修复后，1 月 9 日保持待结算，1 月 10 日写回顺延判定和 1% 收益。`--today` 仅诊断当前可读数据，跳过写回。缓存缺少覆盖证据且本次未成功刷新时继续等待。隔离 `FIL_DATA_DIR` 后运行 `.venv/Scripts/python.exe -m unittest discover -s tests -t . -p 'test_*.py'`，591 项通过（84.292 秒），三套前端脚本通过。盘中快照和收盘证据由 A2 验证，未使用真实行情重放。

#### A2 / P1-31【新增，P1】结算端未核验目标日收盘及报价可得时间

**证据与落点**：[`judge.py:57`](src/market_research/predictions/judge.py#L57) 只接收日期，没有检查盘中状态；[`tasks/worker.py:74`](src/market_research/tasks/worker.py#L74) 的定期结算也没有收盘门控。已有 [`ledger.py:280`](src/market_research/predictions/ledger.py#L280) 只保护新股票/指数预测入账，不能替代结算检查。

- [x] 保留 A1 的统一时间参数，在判定函数内部验证候选会话已结束；调度层可以减少盘中空跑，但不能把调度时间作为唯一保护。
- [x] 复用 `request_started_at/observed_at/observed_data_end` 等来源元数据，区分“当前时间已收盘”和“输入确实为完成会话报价”。盘中快照即使15点后拿来用，也不得伪装为盘后快照。
- [x] 历史完整会话可使用覆盖该会话且有相应时间证据的后续快照；旧缓存缺证据时标记等待核验，交给现有同步/显式刷新补证据，不在请求线程偷偷联网。没有公告可得时间的基金净值按 A3 另行处理。
- [x] 缺失/过期/错误元数据不补当前时间、不读文件修改时间冒充源时间。补充终判用输入版本、观察时点与结算规则版本的可追溯记录。
- [x] **验收**：补回目标日报价并标记 10:00 观察、09:59 请求，盘中和 16:00 复用该快照均保持待结算，15:00 发起且成功返回的合成快照才通过判定。覆盖 UTC 时点、周末和春节闭市、历史已完成会话、时区缺失、观察时间异常、日历越界和缓存合并保留旧行。CLI 和 worker 走同一个结算入口。

**实施记录（2026-11-17，工作区改动）**：缓存报价与来源元数据在同一锁内读取，合并保留的旧行不会继承新观察时间。股票和指数的新行情判定写入 `settlement_evidence`，包含输入 SHA-256、覆盖区间、请求时点、观察时点及规则版本。旧台账的证据留空。隔离 `FIL_DATA_DIR` 后运行 `.venv/Scripts/python.exe -m unittest discover -s tests -t . -p 'test_*.py'`，594 项通过（95.031 秒），三套前端脚本通过。使用合成夹具验证，未实测供应商盘中刷新或盘后发布时间。

**补充修复（2026-11-22，工作区改动）**：`--fetch` 成功只认证供应商实际返回的 `observed_data_start/observed_data_end`，请求上界不再冒充来源覆盖，避免静默短历史把缺报价误终判。缺报价终判的 `input_sha256` 扩展至判定日实际扫描末行，同时保留 `decision_through` 表示规则窗口上界。新增短历史与哈希覆盖回归。

#### A3 / P1-29：基金入账可得性及历史污染隔离（已完成）

**落点**：[`predictions/ledger.py:309`](src/market_research/predictions/ledger.py#L309)、[`services/analysis.py:271`](src/market_research/services/analysis.py#L271)、[`data/panels.py:442`](src/market_research/data/panels.py#L442)；保留已修的独立净值轴，不复用股票15点规则来假装基金净值已经发布。

- [x] 从基金 provider/缓存到 forecast/meta 贯通净值日期、来源实际观察时间、分析生成时间、总回报/累计净值口径和数据版本。供应商公告时间拿不到时记未知，观察时间只是“至迟已取得”，不是公告时间。
- [x] 入账前核对预测起点是已验证的最新可用净值，并检查记录生成未落在该预测的已成熟期之后；不能仅靠14自然日阈值放行。延期披露、长假、停更等无法确认最新性时保留页面研究结果，但不纳入前瞻台账。
- [x] 明确区分新入账、重复记录、历史窗口、过期净值、可得性未知和写入失败；服务/API/页面给出本次是否记账及原因，不再用返回0条掩盖所有拒绝路径。规则事件与方向概率分别检查，不把规则h=0硬套成5日预测。
- [x] 区分可缓存的计算结果与本次请求的入账状态，处理现有“先写结果缓存、后入账”的顺序。缓存命中不得沿用旧的本次入账成功标记，也不得用当前时间为旧输入重新签发可得性证据；数据库写入失败只降级入账状态，不使已经完成的分析变成失败。
- [x] 先做只读历史审计，按资产/模型/口径/入账时间证据区分可验证与待核验样本；保留首条不变语义，不通过重建ID或覆盖预测值“修好”历史。备份、迁移与旧记录重判须另行确认，不能把缺失证据补写成已验证。
- [x] **验收**：基金110022、净值日2025-01-02、h=5/p=.6、end=None、now=2025-01-15 16:00+08:00，应拒绝前瞻入账；另测正常晚间发布、次日发布、长假、total/cum、刷新失败、重复与缓存命中。写入路径与同步/取消/重试不新增真实库污染。

**实施记录（2026-11-22，工作区改动）**：净值序列携带 `nav_observed_at`（本地至迟取得时间）与 `nav_observed_data_end`，结果 meta 记录公告时间未知；入账前用供应商日历要求数据末日等于观察时点前最后完成会话，并按方向/规则各自的实际评估会话检查成熟期。`record_result_ex` 将新入账、重复、历史窗口、过期净值、刷新失败、可得性未知、成熟期与写失败区分返回；服务层把状态放入 `result.meta.registration`，缓存命中重置为 `cache_hit`，页面与导出可见。`predictions audit-fund` 只读统计历史证据，未改写旧记录。验收使用隔离数据目录的合成夹具，未实测供应商公告时点，也未执行真实库迁移或旧记录重判。

**剩余工作完成（2026-11-27）**：

- 基金来源返回后记录带时区的观察时间、净值末日与快照指纹；窗口缓存与同步缓存均在同一次数据库读取中取回数值和证据。预测和分析结果保留实际 `total/cum` 口径、预测净值日期、预测与分析生成时间，以及实际使用净值序列的指纹。旧缓存的通用抓取时间、导入时间和文件修改时间不用于补证据。
- 入账同时检查观察时及登记时的已结束会话。规则事件保留 `h=0`，单独使用规则评估会话数；方向预测使用各自周期。重试保留首条记录，取消或已失去任务所有权的尝试不得写入。基金分析、回测页面及结果 API 显示本次状态；缓存命中只返回 `cache_hit`，保留计算时的元数据。
- 合成回归覆盖 110022 的过期五日预测、晚间和次日上午观察、长假、两种净值口径、旧缓存缺证据、刷新失败、重复、取消与重试、写入失败和缓存命中。
- 隔离数据目录运行 647 项 unittest，646 项通过。剩余跨市场验证测试在未修改的 Git 基线上也失败，夹具未提供基准行情。三套前端脚本通过。
- 对本地现有 `market.duckdb` 使用 DuckDB 只读连接执行 `predictions audit-fund`。台账有 2 条指数记录及 0 条基金记录，因而基金可核验与待核验计数均为 0。文件 SHA-256 审计前后一致。未迁移、回填或重判真实台账。
- 净值公告时间仍为未知。观察时间仅表示本地至迟取得，交易日历仅用于筛查已结束会话。实际披露延迟、长时间停更及真实供应商发布时间仍缺实测证据；无法确认的结果继续供研究使用。

### M2：基金拟合保持因果性，指标具有明确样本基础

#### B1 / P1-25：把全区间描述性对齐与逐窗研究选池分开

**落点**：[`data/panels.py:32`](src/market_research/data/panels.py#L32)、[`modeling/engine.py:140`](src/market_research/modeling/engine.py#L140)、[`engine.py:235`](src/market_research/modeling/engine.py#L235)、[`funds/rbsa.py:31`](src/market_research/funds/rbsa.py#L31)。RBSA直调 `_run_one`，不能只改 ModelZoo.run 而遗漏持仓/回测入口。

- [ ] 数据加载保留未经全区间删列的候选价格及原始净值日期轴；描述性相关性可保留全区间覆盖摘要，但不能把它的删列/删行结果送入因果研究路径。基金方向预测与净值展示/统计使用原始基金轴，候选指数缺日不得改写基金日期或收益。
- [ ] 每个重估点仅用此前训练窗口，按预先固定的覆盖率与有效训练样本规则选择可用因子；保存该窗口候选、剔除原因、训练起止与指纹。当前配置池不等于历史时点池，P1-11仍保留，不能因解决数据缺失选池就声称消除全部幸存者偏差。
- [ ] 在原始有序日期轴先构造相邻期收益，再对训练行做有效性筛选，禁止先删日期再diff形成跨缺口的假“日收益”。主 OOS 口径不把未知指数收益填0；如需沿用有限前填，必须单列有版本的敏感性对照，不能混入主结果。
- [ ] 权重/因子集合从本次重估冻结到下一重估；期间某个必需因子缺报价时，该点合成收益标不可用并记录原因，不利用后续缺数重新选池或事后重分配仓位。未被选中的指数缺数不得删除本次 OOS 点。
- [ ] 下游图表、健康度、回测不得将不可用点补0或跨缺口重新合成连续收益；需要连续输入的窗口显式失效。单列有效OOS覆盖率与实际日历跨度，年化指标不能把不连续多日期收益当逐日样本。
- [ ] 所有模型、持仓RBSA、重学、回测、研究walkforward和检查点使用同一选池契约；保留11模型实际预测器与线性权重的区分。修改研究/结果版本并隔离旧检查点，不静默复用旧清洁面板。
- [ ] **验收**：300个工作日，基金/a=`100+arange(300)`、b=`200+arange(300)`，仅把b[200:]设空，共同历史窗口的候选/权重/预测应一致；另测未来新增因子、前缀缺日、训练内缺日、预测段缺日、0/1/2因子、全模型边界。固定种子、串行、相同重估锚点比较；有意丢弃的尾部限流外历史不混入前缀断言。

**实施记录（2026-11-24，工作区改动）**：`data.panels.load_panel` 在 `attrs.causal_pool` 保留未经全区间删列/删行的原始候选价格与基金轴；`ModelZoo.run` 与 `RBSA.fit_rolling` 优先使用它，在**每个训练窗口内**按 `min_coverage=0.97` 计算因子可得率，低覆盖因子逐窗剔除。收益先对原始有序轴做 `diff`，不用前填价构造假收益；权重在重估区间内冻结，未被选中因子缺数不影响该 OOS 点，必需因子缺报价则该日合成收益置空并计入 `oos_gap_n/oos_coverage`，评分只在共同有限样本上计算。`CACHE_VERSION` 升至 16；`analysis` 传 `require_causal_panel=True`，旧检查点/无因果池结果不进入推荐。新增回归覆盖“未来缺数不改写历史窗口”和“预测段缺因子留空”。现已落盘 `train_start/train_end/selected/dropped/fingerprint`（`fit_diag.csv` 导出，页面只显示最近一窗摘要），OOS 与逐窗 TE 均按“实际样本数 / 日历交易日”折算（`te_basis`、`te_ppy`）；`ModelZoo.run` 最小门槛统一为 W+30，短样本返回“评估不足”而非直接报错；`_attach_common_oos` 取跨模型交集并重算 `common_oos_r2/te`；新增回归覆盖未来新增因子、前缀缺日、0/1 因子、起始窗口全缺与预测段缺数。**未完成**：全区间描述性相关性仍用删列面板（仅展示）；历史行业归属缺失（P1-11）与候选池幸存者偏差未消除；持仓/回测的资金曲线年化仍假定逐日连续。

#### B2 / P3-R、P2-31：按实际OOS而非输入总行数决定评估资格

**落点**：[`engine.py:318`](src/market_research/modeling/engine.py#L318)、[`engine.py:384`](src/market_research/modeling/engine.py#L384)、[`funds/payloads.py:427`](src/market_research/funds/payloads.py#L427)、[`funds/payloads.py:498`](src/market_research/funds/payloads.py#L498)、[`funds/analysis.py:289`](src/market_research/funds/analysis.py#L289)。

- [ ] 分开“足够训练”“已有预测”“足够评估”“允许比较/推荐”；公开拟新增的 `oos_n/oos_start/oos_end/rebalance_count/coverage/assessment_status/reasons`，计数只含实际有限配对样本。
- [ ] 统一 RBSA 的 W+30、ModelZoo 的 W+60 与 `min_obs` 的语义；保留必要训练门槛，重估限流之后再检查实际OOS。用户选择max_rebal=1可以做快速拟合，但不得暗示已完成充分评估。
- [ ] `[产品/研究口径，待确认建议]`：拟合评估的初始工程下限采用 OOS≥60 且有效重估窗口≥3；这些数字只作最低资格，不代表统计功效或策略可靠。跨模型比较使用共同OOS日期，交集不足时不产生“最佳”，同时保留各自覆盖诊断。
- [ ] 确认后让 error、评估不足或降级的模型不参与 `rank/best/insights` 的默认推荐；原始诊断仍可查。四模型等权降级与黑箱fallback统一暴露，不能只凭有限R²判成功。全部不合格时返回“暂无可推荐模型”，不自动挑第一个。
- [ ] 自适应仓位诊断使用逐窗实际 `equity_used`，不拿名义0.9与自适应结果直接比较；波动目标预热仓位的0.5/1.0差异单独确认后修正，不与选池补丁一起悄悄改资金曲线。仅清理确认无调用的死配置，不借机扩大重构。
- [ ] **验收**：种子42、400行、四指数`exp(cumsum(N(0,.01)))`、基金权重[.3,.3,.2,.1]、auto_equity=False/max_rebal=1，Ridge/PCR的7个OOS点必须为评估不足；再测59/60/61点、2/3窗口、负R²、常数目标、模型失败、缺段及全部不合格。单测门槛与可展示原始诊断分开断言。

**实施记录（2026-11-24，工作区改动，元数据、展示与推荐门禁）**：`engine._coverage_summary` 公开 `oos_n/oos_start/oos_end/oos_gap_n/oos_coverage/rebalance_count/assessment_status/assessment_reasons`，门槛常量 `MIN_OOS_SESSIONS=60`、`MIN_REFITS=3`。`ModelZoo.rank` 新增“样本外天数/有效重估/评估状态/可推荐/阻断原因”，**排序仍按样本外 R²**；`blocking_reasons` 将失败、R² 未定义、覆盖不足、降级拟合及“要求因果面板却未使用”列为不可推荐。`analysis` 只在 `可推荐=是` 的模型中选 best/漂移/估值基准；全部不合格时 `meta.best_model=None`、页面隐藏“区间最优”、insights 明确输出“暂无可推荐模型”，原始排名仍可查。`model_payload` 增加 `assessment` 块。新增回归覆盖 59/60 边界、单重估窗口、失败模型、非因果面板与全不合格措辞。W+30 与 W+60 已统一为 W+30（W+60 不再作为硬门槛）；自适应仓位诊断改用逐窗 `equity_used` 中位数；跨模型按共同 OOS 交集重算 R²，交集 <60 标记不可推荐，`analysis` 优先按“共同 OOS R²”排序并在 meta 记录 `ranking_basis`。**未完成**：共同 OOS 上的置信区间/块自助未实现。

### M3：统一预测评分、证据强度和用户解释

#### C1 / P3-O：冻结目标定义，分开概率质量与成交限制

**落点**：[`market/targets.py:6`](src/market_research/market/targets.py#L6)、[`market/technical.py:83`](src/market_research/market/technical.py#L83)、[`predictions/judge.py:130`](src/market_research/predictions/judge.py#L130)、[`predictions/ledger.py:574`](src/market_research/predictions/ledger.py#L574)。

- [ ] `[产品/研究口径，待确认建议]`：统一模型目标为 `P(R_h>0)`；概率Brier及对应二分类正确率的平盘标签为0，与当前训练一致。页面“上涨/下跌/平盘命中”另列，平盘可继续作为neutral，不冒充同一个分母。零收益容差、价格口径、周期时钟与规则版本显式冻结。
- [x] calendar_sessions 的目标会话没有报价时，不用第h+1/h+2会话收益为固定h周期评分；宽限仅用于等待目标日数据补齐。顺延收益保留为不同期限的诊断，不能混入固定周期Brier和命中率。
- [ ] 如果目标收盘可得，涨跌停/一字板/成交量0不自动剔除概率评分样本；能否成交单独记录。严格区别“方向预测正确”和“交易可以实现”。规则h=0仍只做事件收益诊断。
- [ ] 复用已有字段并仅补必要的目标/评分版本及资格元数据，保存总体数、成熟数、有效评分数和各类排除原因。相同资产、周期、版本、数据口径的预测才允许合并；旧版/证据未知样本分组显示，不静默升级或覆盖既有判定。
- [ ] **验收**：同一组概率/价格同时送入历史评估和台账统计，固定h目标、平盘、一字板、缺目标价、顺延价、observed_bars/calendar_sessions均得到一致的概率评分资格、标签和分母；混版本不得合并。Brier可独立手算核对。

**实施记录（2026-11-27，顺延样本隔离）**：`ledger._stats` 把 `reason=grace_session` 的方向记录从命中率与 Brier 分母中剔除（`excluded_grace_rows`），其收益只以 `grace_scored_rows/grace_return_mean` 单列诊断；`brier_basis` 追加 `grace_excluded`，`scoring_version` 升至 `flat_as_down_v3`，与旧汇总不混算。判定侧的原始 label/收益与 `actual_sessions` 保持不动，不覆盖旧判定。报告预测台账表新增“顺延结算（诊断，不进分母）”一行。回归覆盖顺延样本混入与不混入两种分母、以及 `decided/hit_rate/probability_rows` 边界。**未完成**：`insufficient/no_feed` 仍无法评分；页面卡片尚未单列顺延样本（归 C3）。

**实施记录（2026-11-24，工作区改动，审计与统一评分）**：`technical.direction_forecast` 与 `compare_forecasts` 新增 `label_policy=flat_as_down`、`target_version=P(R_h>0)_flat_as_down_v1`、`flat_n`、`n_excluding_flat`、`accuracy/brier_excluding_flat` 及 `neutral_epsilon=1e-9`。台账 `_stats` 的 Brier 改为与训练一致的 `P(R_h>0)` 口径（平盘目标 0，neutral 仍进入 Brier 分母），命中率继续只算 up/down 对错、剔除 neutral；新增 `brier_basis/scoring_version/neutral_scored_rows`，历史行可按已存的 `realized_return` 直接重算。新增“Brier 含平盘、命中率剔平盘”手算回归。Brier 进一步纳入目标收盘可得的 blocked（涨跌停/一字板/零成交），`scoring_version=flat_as_down_v2`，命中率仍只算 up/down；新增 blocked 评分回归。**未完成**：calendar_sessions 目标日缺价的顺延收益仍只作诊断、未与固定 h 完全隔离；`insufficient/no_feed` 仍无法评分。

#### C2 / P3-P：评估基线、不确定性与独立证据等级

**落点**：[`market/technical.py:119`](src/market_research/market/technical.py#L119)、[`research/market_validation.py:83`](src/market_research/research/market_validation.py#L83)、[`market/uncertainty.py:8`](src/market_research/market/uncertainty.py#L8)、[`funds/payloads.py:161`](src/market_research/funds/payloads.py#L161)。

- [ ] 概率是否可计算与评估是否充分分别返回；继续按固定非重叠起点取样，公开训练行数、成熟评估数、类别分布和日期范围。n≥20仅保留为已有评估展示下限，不等同“可靠”；样本不足时主指标/优势结论留空，详细研究诊断可显式保留。
- [ ] 每个资产/周期都与仅用当时训练标签得到的频率基线比较；拟合高R²、候选优于原模型、校准成功不能替代“优于频率基线”的验证。统一共同评估日期，不跨不同样本直接排名。
- [ ] 复用现有配对移动块自助/HAC诊断，补齐模型相对频率基线的Brier差及预定比较家族；明确点态区间、家族校正、重叠标签相关性和固定预测路径的条件性限制。置信区间跨0时不宣称优势；不得按结果挑块长、阈值或显著资产。
- [ ] 扩展到基金方向评估时使用基金独立净值轴及分类样本清单；按基金类型/市场环境披露结果，缺行情、清算/退市、单类别和训练失败均列入覆盖清单，不从总体中消失。
- [ ] 证据状态使用可核实描述：不可计算、评估不足、历史诊断可用、独立验证待完成；只有预注册验收完成后才能描述已验证的范围。不得把启发式分数汇成没有统计依据的“可靠性百分比”。
- [ ] **验收**：种子99、800行固定OHLC、展示最后252行，20日模型可计算但只有12个非重叠样本，主评估必须不足；覆盖n=0/19/20、常数/极端概率、两类不足、日期交集变化、相关与独立合成序列、固定种子复现。额外用无信号随机输入做负对照，但不把单次随机输赢当统计保证。

#### C3 / P2-31、P2-32：API、页面与导出展示同一套结论

**落点**：[`funds/analysis.py:294`](src/market_research/funds/analysis.py#L294)、[`market/analysis.py:246`](src/market_research/market/analysis.py#L246)、[`funds/payloads.py:427`](src/market_research/funds/payloads.py#L427)、[`services/analysis.py:271`](src/market_research/services/analysis.py#L271)，以及既有 `src/market_research/api/static/app.js`、`market.js`、`ledger.js` 与报告渲染路径。

- [ ] 将 computation（计算）、data_quality（数据）、assessment（证据）、registration（入账）、execution（成交）分开表达，再汇总部分失败状态；任务done不等于预测有效，未入账不等于计算失败。
- [ ] 预测卡片展示截止时点、5/20周期的实际时钟、样本数、频率基线、是否校准、证据状态及未入账原因。基金高R²注明“同期收益解释”，黑箱重要性不称真实持仓，指数不直接给出可执行报价。
- [ ] 少样本/失败/降级时不显示“最佳”“更可靠”等推荐措辞；空值不补0.5或0。保留当前已修的持仓切换、窗口指纹、取消和过期响应拦截。
- [ ] API、CSV、HTML报告、CLI读取同一资格/评分契约；导出包含样本窗口、模型/数据/评估版本和有效样本数。新研究报告使用新文件名和输入指纹，禁止覆盖原证据。
- [ ] **验收**：启动隔离服务并实际浏览器验证股票/指数/基金的正常、少样本、全部失败、部分降级、未入账、旧版本和无成交状态；覆盖390px与桌面宽度、基金切换、窗口变更、刷新/取消/重入；检查网络请求、控制台、前端回归及报告快照。浏览器只用明确夹具，不自动写真实预测库。

### M4：在可靠评估基础上优化算法与交易诊断

#### D1：离线校准与模型版本发布，不先增加模型复杂度

**落点**：[`research/calibration.py:9`](src/market_research/research/calibration.py#L9)、[`research/market_validation.py:113`](src/market_research/research/market_validation.py#L113)、[`market/technical.py:83`](src/market_research/market/technical.py#L83)。

- [ ] 首轮保留固定逻辑回归、既有特征及60%/40%交易阈值，保留原始概率基线；先检验数据和协议，不以增加神经网络/指标或反复调阈值提高同一历史区间成绩。
- [ ] 用训练→独立校准→留出三段协议；训练标签成熟日不能进入校准段，校准标签不能越过留出段。对固定模型/缩放器版本拟合固定logit校准，保存适用资产、周期、训练截止、校准窗口、参数与指纹。
- [ ] `[产品/研究口径，待确认建议]`：如未来上线校准，先采用“模型与校准器成对冻结、成对发布”的版本；不能将为旧冻结模型拟合的校准器套到每21日重训的新模型。若坚持滚动重训，须另立滚动校准协议并重新做独立验证。
- [ ] 无校准器、非正斜率、单类别、过期或版本不匹配时明确输出原始未校准状态，不能声称已校准；校准失败仍计入预定家族。是否允许发布校准概率取决于独立概率质量验证，不按历史39/40成功比例自动启用。
- [ ] **验收**：未来段扰动不影响训练/校准参数，序列与版本不匹配被拒绝，拟合失败有原因；概率质量与固定阈值扣费表现分别比较。校准改善不作为盈利保证，正常页面是否切换单独确认。

#### D2 / P3-N、P3-Q：固定同一策略，再比较账户与可交易性

**落点**：[`market/execution.py:88`](src/market_research/market/execution.py#L88)、[`market/execution.py:252`](src/market_research/market/execution.py#L252)、[`modeling/backtest.py:162`](src/market_research/modeling/backtest.py#L162)，以及既有 `market/capital.py`、`funds/pipeline.py`。

- [ ] `[产品/研究口径]` 先确定首次买入与持续加仓、受限订单当日失效或持续挂单、反向信号撤单的单一策略语义；资金账户与可分割账户输入同一目标/订单时钟。不得把两种策略的收益差当作账户模型优劣。
- [ ] 修复最低佣金导致残余持仓无法退出的处理：真实无法支付费用时明确保留残仓/不可完成退出，不伪造免费平仓或删除仓位；部分成交、拒单、残仓、净值估值和闭合周期分别计数。
- [ ] 对齐费用/滑点/T+1/整手/容量的可用输入；缺停牌或涨跌停证据时保持 `constraint_data_available=false`。公司行动、基金申赎确认及持有期费用、实际ETF跟踪偏差没有授权真实数据时保留阻塞，不填猜测值。
- [ ] 健康度回测继续作为拟合可信度风控，不包装为方向预测；拆开择时、仓位与被动持有贡献。股票原始价、复权价、指数点位的用途保持明确。
- [ ] **验收**：固定已知答案的费用、T+1、容量、零成交、单次买入、连续高概率、部分卖出、极小残仓和反向信号场景；变更后报告逐笔成交与净值差异，旧曲线只作旧版本对照，禁止只比较最终收益。

### M5：独立样本与发布验收

#### E1：复用既有六项未来验证，不用本轮开发取代实证

- [ ] 开发与回归只使用合成数据、批准的历史快照或预留期之前的数据；不得为了调试Brier、校准或阈值提前重放2026-11-11起的冻结留出区间。准备数据收集流程不等于查看模型表现。
- [ ] 授权数据取得后先记录提供方、覆盖范围、公告/可得时间、缺失处理与输入指纹；真实原始内容遵循法务与再分发范围。开发代理不承诺替代供应商补齐历史时点数据。
- [ ] 现有冻结协议、冻结代码证据及日期不修改；修复版与冻结版本不一致时单独标明，不能悄悄替换冻结受测对象。新的算法/校准版本若需独立验证，另经确认预注册尚未使用的区间；旧协议保持原样。
- [ ] 评估前按既有协议向执行台账追加真实输入证据；达到2027-05-11且必要数据齐备后，再依固定清单与比较家族执行。数据缺口或结果无优势均如实报告，不能以只保留成功资产来达标。
- [ ] **关闭条件**：六项逐项提供真实证据、版本/数据指纹、覆盖失败项、共同样本指标与不确定性；“按协议完成评估”不等于“算法有效”。未通过的有效性结论保留，不通过调阈值或删失败项补成成功。

### 需要确认的口径与不受阻的工作

| 决策 | 建议 | 确认前允许做的工作 |
| --- | --- | --- |
| B2：默认排名/推荐资格 | OOS≥60、有效重估≥3、同样本比较，失败/降级不推荐；下限不代表有效性 | 补覆盖元数据、展示不足和回归，不改变既有排名 |
| C1：概率评分的平盘、顺延及受限样本 | P(R_h>0)固定周期评分；方向中性及成交限制另列，旧版分组 | 实现对照与差异审计，不覆盖旧判定或混用分母 |
| D1：正常页面是否使用校准概率 | 先离线验证，模型与校准器成对冻结发布 | 完善离线报告、版本匹配及失效检查，页面标未校准 |
| D2：订单生命周期/仓位预热 | 先确定同一策略，再改账户及预热行为 | 补场景测试、订单轨迹与差异报告，不更换默认策略 |
| A3/E1：旧库处置及外部数据 | 先审计、备份、授权，再批准迁移或补数据 | 只读审计、合成回归与证据模板，不写真实台账 |

### 统一测试矩阵、交付与回退要求

| 范围 | 优先扩展的现有测试 | 必须验证的结果 |
| --- | --- | --- |
| A1～A3 时点与入账 | `tests/predictions/test_ledger.py`、`test_commands.py`、`tests/market/test_market.py`、`tests/data/test_panels.py` | 不读未来、不用盘中快照终判、基金拒绝无证据前瞻入账、拒绝原因可见 |
| B1 因果选池 | `tests/data/test_panels.py`、`tests/regression/test_p0_fixes.py`、`test_p3_fixes.py`、`tests/funds/test_holdings.py` | 未来缺失不改变历史、无跨缺日收益、ModelZoo与RBSA一致 |
| B2 资格与诊断 | `tests/funds/test_payloads.py`、`tests/regression/test_regressions.py` | 7点OOS不正常推荐、共同样本、全部失败、降级可观测 |
| C1/C2 目标与统计 | `tests/market/test_session_targets.py`、`test_technical.py`、`test_uncertainty.py`、`test_validation_coverage.py`、`tests/predictions/test_ledger.py` | 分母一致、目标日期一致、少样本留空、确定性区间与家族完整 |
| C3 页面及报告 | 三套现有前端回归、`tests/api/test_routes.py`、`tests/research/test_report_render.py`、隔离浏览器 | 页面/API/导出一致；正常、边界及原有功能无回归 |
| D1/D2 后续研究 | `tests/market/test_calibration.py`、`test_model_execution.py`、`test_capital.py`、`test_model_capital.py`、`tests/funds/test_pipeline_valuation.py` | 校准隔离、版本配对、订单/成本可核对，不把合成收益当实证 |
| E1 冻结验证 | `tests/research/test_future_validation.py` + 协议/执行证据只读校验 | 协议未改、代码版本明确、真实证据完整；离线测试不关闭六项 |

- [ ] 每包先保存最小失败反例，再实施、跑定向测试、全量585项基线及新增测试；三套前端回归保持通过。涉及UI的包必须启动服务做真实浏览器操作；未测的环境明确列出，不用单测代替UI验收。
- [ ] B1/B2/C1/D1/D2 的数值或资格变化分别记录版本和前后差异；结果缓存、阶段检查点、模型/校准器、报告与台账各自的版本边界同步检查，不能仅递增结果缓存版本就视为全部隔离。旧输入缺字段应拒绝复用或明确标旧版，不凭空补证据。
- [ ] 真实历史重放须使用获准的固定输入及新输出名，并避开冻结留出调参；记录原始轴/共同评估轴指纹、参数、随机种子、起止/n、缺口、耗时、内存与结果差异。未取得快照时保留“未重放”，不能引用历史文档代替。
- [ ] 交付以包为单位附变更范围、测试命令、最小输入、数值差异和剩余限制；只有用户要求时才提交或推送。数据库迁移先备份和dry-run，检查影响数量及幂等性，单独确认后执行。
- [ ] 回退优先回退代码版本并保留新旧报告；不得回滚/覆盖已发布预测事实或删除不利结果。新版本不合格时维持“不可用/证据不足”，而不是静默回退成伪成功。

**最终验收门槛**：M1保证时点安全，M2/M3保证拟合与评估可解释且可比较，M4仅提供经独立审查的候选优化，M5才负责真实有效性与可交易性证据；任何阶段不能承诺固定预测准确率或投资收益。与上述目标无关的多租户、认证、监控后端、云部署和数据采购继续留在原待办，不混入本轮优化。

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
