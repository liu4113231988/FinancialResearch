# 测试说明

核对日期：2026-11-01。先在项目根目录运行 `uv sync --locked`。下列命令使用可安装的 `market_research` 包及当前测试目录；无需修改 `PYTHONPATH`。

| 目录 | 内容 |
| --- | --- |
| api | HTTP 校验、响应、缓存与任务提交 |
| data | DuckDB 事务、迁移及进程间访问 |
| funds | 基金池与持仓检查 |
| market | 股票/指数行情和纯技术计算 |
| tasks | 同步、内存隔离测试、持久队列及 worker |
| architecture | 模块边界、兼容导入、包资源与统一 CLI |
| frontend | 参数绑定及异步状态回归 |
| browser | Edge CDP 与 jsdom 检查 |
| regression | 通过独立 main 运行的数值回归 |
| integration | 使用实际行情或模型的计算验证 |
| tools | 数据源探针、计时和诊断工具 |

## 常规回归

```powershell
uv run python -m unittest discover -s tests -t . -p 'test_*.py'
uv run python tests/regression/test_p0_fixes.py
uv run python tests/regression/test_regressions.py
node tests/frontend/test_refit.js
node tests/frontend/test_ui_async.cjs
```

unittest 使用 `-t .` 按 `tests.*` 加载，避免分组目录遮蔽第三方包。自动发现不会调用研究脚本的 main，也不等于执行全部真实行情/完整模型验证。Windows ASGI 测试需要允许本机 socketpair；受限沙箱可能阻止该操作。

## 浏览器检查

使用 Node.js 22+、本地 8877 服务和专用 Edge CDP 9229。先启动服务，再用独立测试用户目录启动 Edge，例如：

```powershell
uv run market-research --port 8877
# 另一个终端；按实际安装位置调用 Edge
& "${env:ProgramFiles(x86)}/Microsoft/Edge/Application/msedge.exe" --remote-debugging-port=9229 "--user-data-dir=$PWD/output/edge-test-profile" http://127.0.0.1:8877/
```

保持测试浏览器打开，在项目根目录执行：

```powershell
node tests/browser/market_browser.cjs
node tests/browser/technical_browser.cjs
node tests/browser/datacenter_browser.cjs
node tests/browser/controls_browser.cjs
```

测试会操作 CDP 实例中的页面，截图/状态产物在 `output/ui-evidence/`。不要连接日常使用的浏览器配置。`tests/browser/dom_check.js` 是另一项 jsdom 检查，需额外具备它导入的 Node 依赖；不是上述无额外依赖 CDP 套件的前提。

基金页单独检查：`node tests/browser/fund_browser.cjs http://127.0.0.1:8877 path/to/fund-result.json`。JSON 应为本地可信基金分析结果（`/api/result/{jid}` 响应中的 result 对象），默认使用 `output/ui-evidence/fund-style-fixture.json`。测试拦截分析请求，不提交真实计算，覆盖空状态、区间下限、日期参数、缓存结果图表、切换入口及手机布局。

全局状态检查：`node tests/browser/global_audit.cjs`。另需本地可信 `output/ui-evidence/stock-fixture.json`，与基金结果一起用于离线响应。覆盖市场区间下限、重复入口点击、股票/指数图表、手动模型保留、过期回测、拟合参数传递、相关窗口重置及三类入口的手机布局。请顺序运行浏览器套件，它们使用同一个专用测试标签页。

新增环境与候选模型渲染检查可运行 `node tests/browser/global_audit.cjs http://127.0.0.1:8877 output/ui-evidence/context-fixture.json`，第三个参数为带 `technical.context` 和候选对比结果的可信行情结果文件。本轮采用确定性合成行情生成该文件，只验证界面与计算契约，不据其表现评价投资效果。`tests/market/test_context.py` 覆盖新增指标已知答案、候选预测前缀稳定性、共同成熟日期、缺失降级及指数生产入口。

## 实际基金计算与打包

```powershell
uv run python tests/integration/test_webapi.py 110022 3
uv run python run.py --help
uv run python -m market_research.cli experiments --help
uv build
```

`test_webapi.py` 直接调用基金分析函数，不经过 HTTP；3 是最大重估点数，不是模型数或运行次数。缓存缺失时可能联网、计算较久，不能把它当路由层测试。轮询/HTTP 测试由 api 组另行覆盖。

wheel 检查应在独立虚拟环境安装构建产物，在源码目录之外验证控制台命令、静态资源和 SQL 可读取；源码兼容脚本不是安装包用户的调用方式。

## 已有验证记录

完成状态一轮（2026-11-01）：完整160项unittest及两套前端回归通过。新增8项覆盖单个留出项目误报、全部预定项目与样本门槛、开发期缺失、重复候选、空/部分校准、缺数据资产分母、规则阶段/费用覆盖、零成交和单会话边界。先复现旧版两项误报，再修复并用五份历史报告只读重评；没有重新训练、重放账户或执行浏览器/打包检查，见[状态记录](../docs/验证完成状态检查_2026-11-01.md)。

概率校准一轮（2026-11-01）：完整152项unittest、两套前端回归和修改Python文件编译通过。新增12项覆盖时间隔离、未来标签/概率不改变拟合、目标成熟边界、样本与类别门槛、常数/负斜率拒绝、0/1端点、缺预测、输入不变、可选清单校验、冻结截止、五版本报告及家族计数、旧协议、资金约束与失败报告非零返回。仅扩展离线能力，未重跑浏览器或打包，详见[校准记录](../docs/股票指数概率校准_2026-11-01.md)。

不确定性诊断一轮（2026-11-01）：完整140项unittest与两套前端回归通过。新增10项覆盖确定性配对区间、样本门槛、常数退化、非有限值/溢出、相同路径抵消、少交易、初始费用和资金归一化、不修改输入、日期对齐、双账户模式透传及成熟样本一致性。本轮只扩展离线报告，没有重跑浏览器或打包检查；统计边界与实际报告见[诊断记录](../docs/股票指数不确定性诊断_2026-11-01.md)。

模型资金账户一轮（2026-11-01）：完整130项unittest、两套前端回归及语法/格式检查通过，新增6项验证原模型/候选/持有资金条件、共同预测屏蔽、日历和冻结训练透传。两个长历史原始价样本均完成留出回放。本轮仅扩展离线账户，没有新增页面或声称重跑浏览器，详见[资金账户记录](../docs/资金账户情景验证_2026-11-01.md)。

资金账户一轮（2026-11-01）：完整124项unittest、两套前端回归及语法/格式检查通过；新增11项资金/整手/最低佣金/上一会话容量/部分成交与口径拒绝检查，两个原始价股票情景均评估完成。本轮为离线扩展，没有新增页面资金输入；此前浏览器记录按原范围保留。见[资金账户情景验证](../docs/资金账户情景验证_2026-11-01.md)。

完整固定清单一轮：113项unittest、两套前端回归、语法/格式检查和真实快照浏览器检查通过。新增11项覆盖日历、不可覆盖快照与仓位匹配对照；固定6项均完成评估。数据准备、指纹和实际结果见[完整样本验证记录](../docs/完整样本验证记录_2026-10-31.md)。

模型阈值成交一轮：102项unittest、两套前端回归、语法/格式及全局浏览器检查通过；新增6项模型成交回归，并扩展开发期分类/账户边界检查。固定6证券正常方向入口输出包含冻结留出期模型配对成交摘要，3项可评估、3项未覆盖，不将缺失当成通过。实际结果与阈值协议见[成交诊断](../docs/股票指数成交诊断.md)。

成交诊断一轮：95项unittest通过，最后新增非交易会话信号隔离后，成交模块12项复查通过；两套前端回归、全局浏览器及展开成交详情的手机宽度检查通过。真实缓存规则回放有3项可评估、3项未覆盖，保留全部状态，见[成交诊断](../docs/股票指数成交诊断.md)。

股票/指数第二批：84项unittest通过，新增6项验证冻结训练截止日、标签不跨开发边界、环境分组穷尽、缺字段降级、清单校验和小样本统计处理；可选成交字段修正后相关6项复查通过。两套前端回归及带环境分组的全局浏览器检查通过。真实缓存评估按固定6证券清单运行，3只缺缓存保留为未覆盖，详见[验证流程](../docs/股票指数验证流程.md)。

2026-10-31 全局检查实际执行：73 项 unittest、两套数值回归、两套前端回归通过；基金、控件、数据中心和新增全局状态共四套 Edge 检查通过。完整范围与未覆盖项见[全局页面检查](../docs/全局页面检查_2026-10-31.md)。

最近代码调整记录为 68 项 unittest 通过、两套独立数值回归通过、两套前端回归通过、4 套 Edge 页面检查通过；统一 CLI 另验证 14 个子命令帮助和 wheel 独立安装。证据位于本地 output 日志和界面产物，通常不随 Git 分发。本次文档更新未重新执行完整研究和浏览器回归，记录不承诺其他机器、数据版本或网络下相同结果。
