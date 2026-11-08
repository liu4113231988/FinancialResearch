const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/market_research/api/static/app.js', 'utf8');
const nodes = new Map();
const pending = [];
const context = {URLSearchParams, setTimeout: fn => fn(),
  S: {result: {code:'110022', meta:{window_requested:'2021-01-01', window_requested_end:'2025-01-01'}}},
  $: id => {if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id);},
  api: url => new Promise(resolve => pending.push({url, resolve})),
  renderCorr() {}, showAlert(message) {throw Error(message);},
  applyResult() {throw Error('Stale fund result applied');},
  setProgress() {throw Error('Stale fund progress applied');},
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('let fundGeneration ='), source.indexOf('// ==================================================================== 运行分析')), context);
vm.runInContext(source.slice(source.indexOf('async function poll(jid'), source.indexOf('function applyResult(res)')), context);
(async () => {
  const first = context.updateCorrelation(60);
  const second = context.updateCorrelation(120);
  const query = new URL('http://local' + pending[0].url).searchParams;
  assert.equal(query.get('start'), '2021-01-01');
  assert.equal(query.get('end'), '2025-01-01');
  pending[1].resolve({correlation:'new'}); await second;
  pending[0].resolve({correlation:'old'}); await first;
  assert.equal(context.S.result.correlation, 'new');
  const third = context.updateCorrelation(90);
  context.S.result = {correlation:'other fund'};
  pending[2].resolve({correlation:'obsolete'}); await third;
  assert.equal(context.S.result.correlation, 'other fund');
  const poll = context.poll('old-job');
  await Promise.resolve();
  vm.runInContext('fundGeneration++', context);
  pending[3].resolve({status:'done'}); await poll;
  assert.equal(pending.length, 4);
  // Backtest response fencing must cover both job status and result fetch.
  const btPending = [], btContext = {
    fundGeneration: 0, setTimeout: fn => fn(), $: () => ({}),
    api: url => new Promise(resolve => btPending.push({url, resolve})),
    setProgress() {}, showAlert() {throw Error('Stale backtest alert');},
    renderBacktest() {throw Error('Stale backtest result');},
  };
  vm.createContext(btContext);
  vm.runInContext(source.slice(source.indexOf('async function pollBacktest('), source.indexOf('function renderBacktest(')), btContext);
  const btFirst = btContext.pollBacktest('first'); await Promise.resolve();
  btContext.fundGeneration++;
  btPending[0].resolve({status:'error',error:'obsolete'}); await btFirst;
  assert.equal(btPending.length,1);
  const btSecond = btContext.pollBacktest('second'); await Promise.resolve();
  btPending[1].resolve({status:'done'}); await new Promise(setImmediate);
  assert.equal(btPending[2].url,'/api/result/second');
  btContext.fundGeneration++;
  btPending[2].resolve({result:{}}); await btSecond;
  // 取消按钮：必须真的打到后端，并且递增代次让在途轮询退出。
  const cancelled = [];
  let invalidated = 0;
  const cancelContext = {
    activeJob: 'job-7',
    api: (url, opts) => {cancelled.push([url, opts && opts.method]); return Promise.resolve({});},
    invalidateFund() {invalidated++;}, $: () => ({}), showAlert() {},
  };
  vm.createContext(cancelContext);
  vm.runInContext(source.slice(source.indexOf('async function cancelActiveJob('),
                               source.indexOf('function applyResult(res)')),
                  cancelContext);
  await cancelContext.cancelActiveJob();
  assert.deepEqual(cancelled, [['/api/job/job-7/cancel', 'POST']]);
  assert.equal(invalidated, 1);
  assert.equal(cancelContext.activeJob, null);
  // 无模型可渲染时不得静默 return：详情区必须换成说明，而不是留着上一次的数字。
  const selNodes = new Map();
  const selContext = {
    S: {sel: 'gone', result: {models: {ridge: {name: 'Ridge'}}}},
    $: id => {
      if (!selNodes.has(id)) selNodes.set(id, {style: {}});
      return selNodes.get(id);
    },
  };
  vm.createContext(selContext);
  vm.runInContext(source.slice(source.indexOf('function renderSelected'),
                               source.indexOf('function chart(id, opt)')), selContext);
  selContext.renderSelected();
  assert.equal(selNodes.get('selName').textContent, '无模型详情');
  assert.match(selNodes.get('selDesc').textContent, /不在本次结果里/);
  selContext.S.sel = null;
  selContext.renderSelected();
  assert.match(selNodes.get('selDesc').textContent, /没有可用模型/);
  // 冷缓存（P1-13）：/api/corr 返回 202 + job_id → 独立轮询 corr 任务并写回。
  const corrAlerts = [];
  const corrNodes = new Map();
  const corrContext = {
    URLSearchParams, setTimeout: fn => fn(),
    S: {result: {code:'110022', meta:{window_requested:'2021-01-01', window_requested_end:'2025-01-01'}}, rollWin: 120},
    $: id => {if (!corrNodes.has(id)) corrNodes.set(id, {}); return corrNodes.get(id);},
    api: url => new Promise(resolve => pending.push({url, resolve})),
    renderCorr() {},
    showAlert(message) {corrAlerts.push(message);},
  };
  vm.createContext(corrContext);
  vm.runInContext(source.slice(source.indexOf('let fundGeneration ='), source.indexOf('// ==================================================================== 运行分析')), corrContext);
  vm.runInContext(source.slice(source.indexOf('async function poll(jid'), source.indexOf('function applyResult(res)')), corrContext);
  const corrJob = corrContext.updateCorrelation(150);
  await Promise.resolve();
  pending[4].resolve({ok:true, job_id:'corr9'});
  await new Promise(setImmediate);
  assert.equal(pending[5].url, '/api/job/corr9');
  assert.ok(corrAlerts.some(m => m && m.includes('后台重算')));
  pending[5].resolve({status:'done'});
  await new Promise(setImmediate);
  assert.equal(pending[6].url, '/api/result/corr9');
  pending[6].resolve({result:{correlation:'queued'}});
  await corrJob;
  assert.equal(corrContext.S.result.correlation, 'queued');
  assert.equal(corrContext.S.rollWin, 150);
  const html = fs.readFileSync('src/market_research/api/static/index.html', 'utf8');
  assert.match(html, /id="btnCancelJob"/);
  assert.match(source, /\$\('btnCancelJob'\)\.onclick = cancelActiveJob/);
  assert.match(source, /attempts > 200 \? 2000/);
  // 页面恢复 / 自选 / 带口径导出（P1 修复轮）：控件必须真实接线，导出必须带口径
  assert.match(source, /fil_watchlist/);
  assert.match(source, /fil_last_run/);
  assert.match(source, /data_provenance/);
  assert.match(source, /\$\('btnWatch'\)\.onclick = toggleWatchlist/);
  assert.match(source, /\$\('watchSel'\)\.onchange/);
  assert.match(source, /\$\('btnExport'\)\.onclick = exportResult/);
  assert.match(source, /saveLastRun\(\{code/);
  assert.match(source, /restoreLastRun\(\)/);
  assert.match(html, /id="btnWatch"/);
  assert.match(html, /id="watchSel"/);
  assert.match(html, /id="btnExport"/);
  // 120：错误码提示必须给出可操作的下一步，而不是一律显示后端 message。
  const errCtx = {};
  vm.createContext(errCtx);
  vm.runInContext(source.slice(source.indexOf('function apiErrorText('),
                               source.indexOf('function showAlert(msg)')), errCtx);
  assert.match(errCtx.apiErrorText(429, 'busy', '12'), /队列忙/);
  assert.match(errCtx.apiErrorText(429, 'busy', '12'), /12 秒/);
  assert.match(errCtx.apiErrorText(409, 'cancelled', null), /重新发起/);
  assert.match(errCtx.apiErrorText(404, 'missing', null), /过期/);
  assert.equal(errCtx.apiErrorText(500, 'boom', null), 'boom');
  const css = fs.readFileSync('src/market_research/api/static/style.css', 'utf8');
  const market = fs.readFileSync('src/market_research/api/static/market.js', 'utf8');
  // 116：自选下拉已移入共用工具栏，窄屏换行而非 display:none 隐藏
  assert.match(html, /research-toolbar[\s\S]*id="watchSel"/);
  assert.match(css, /\.search\{[^}]*flex-wrap:wrap/);
  assert.doesNotMatch(css, /#watchSel[^{]*\{[^}]*display:none/);
  // 117 键盘可达；118 未来日期上界与回测预热；120 错误码分级提示
  assert.match(source, /tabindex="0" role="button" aria-pressed=/);
  assert.match(source, /aria-selected="\$\{k === S\.sel\}"/);
  assert.match(source, /\$\('fundEnd'\)\.max = today/);
  assert.match(source, /预热不足 60 个交易日/);
  assert.match(source, /apiErrorText\(r\.status/);
  // 121/123/124：暂停轮询、图表与标题语义、自选与名录容错
  assert.match(source, /typeof pagePaused !== 'undefined'/);
  assert.match(source, /\.chart:not\(\[role\]\)/);
  assert.match(source, /function pruneWatchlist\(\)/);
  assert.match(market, /document\.title = 'Market Research/);
  assert.match(market, /clearTimeout\(rzT\)/);
  assert.match(market, /el\('marketEnd'\)\.max = today/);
  assert.match(html, /id="btnRetryCatalog"/);
  console.log('Cancel button wired, polling capped and backoff applied: OK');
  console.log('Correlation window, response ordering, replaced fund and stale polling: OK');
  console.log('Backtest stale error and late result fencing: OK');
})().catch(error => {console.error(error); process.exitCode=1;});
