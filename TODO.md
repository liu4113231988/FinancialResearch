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

- **P2-42 剩余**：`FIL_SYNC_TIME` 非法值经 `GET /api/sync`（`routes/sync.py:11`）抛 `ValueError` 仍被全局处理器标成 400 `field=params` —— 服务端配置错误需单独分类；其余子项已修。
- **P3-H 补充** `[产品]`：前端 `static/app.js:939,1000,1019,1068,1079`、`market.js`、`data-center.js`、`ledger.js` 全部硬编码 `/api/*` 旧路径；`api/version.py:21-28` 的 2027-04-01 legacy 自动下线若按期触发，SPA 当天全量 404。auto 下线必须与前端自身迁移同批决策，不能只等外部客户端。
- **P3-M 剩余**：不可回收的僵尸子进程永久占用并发名额、队列停摆（`worker.py:20-43,147-158`），只能重启 supervisor。
- **P3-N**（行为层，会改变资金曲线，未动）：`market/capital.py:71` 无 `units==0` 门控 vs `market/execution.py:99` 仅空仓首买；`capital.py:143-145` `_retry_note` 文案与 orders=targets.shift(1) 实现不符；`compare_probability_execution` 两种账户实际评估不同策略。
- **P3-O**（口径）：离线 `(future > 0)`（`market/technical.py:90`）把平盘计入下跌类，台账 `neutral` 不入分母（`predictions/judge.py:173-176`）；跨口径比较 accuracy/Brier 有偏。
- **P3-P**：`market/technical.py:130-137,202-208` 少样本仍输出数值（仅 `ok=False` 标记），未像 `market_validation.py` 留空。
- **P3-Q 剩余**：`market/capital.py:103-104` 残余仓位卖出额低于最低佣金时被 `insufficient_cash_for_sell_fee` 永久阻塞、闭合周期不计（费用触发的平仓死锁，区别于已登记的容量触发残留）。
- **P3-R 剩余**：`rbsa.py:48-49` 的 `W+30` vs `modeling/engine.py:177-178` 的 `W+60` 两套门槛；`payloads.py:629-654`「仓位假设失配」以名义 0.9 诊断（`auto_equity=True` 实际逐窗自适应）；`pipeline.py:117` 波动目标变体 `fillna(1.0)` vs `backtest.py:177,203` 的 0.5；`config.py:251-252` `l1_ratio/alpha_grid` 死配置；`estimators.py:276,320,380,402` 四模型等权降级不传 status 不可观测。以上均会改变数值或诊断结论，须独立评估。
- **P3-S 剩余**：`commands/holdings.py:135` 的 `✗`、`market_snapshot.py:118/121` 的 `…` 等在 GBK 重定向下仍可能 `UnicodeEncodeError`（`serve.py` 已加 reconfigure，其余入口未推广）。

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
