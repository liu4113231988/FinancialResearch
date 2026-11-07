const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('src/market_research/api/static/market.js', 'utf8');
const app = fs.readFileSync('src/market_research/api/static/app.js', 'utf8');
const tick = () => new Promise(setImmediate);
function setup(handler) {
  const dom = new JSDOM(fs.readFileSync('src/market_research/api/static/index.html', 'utf8'), {runScripts:'outside-only', url:'http://localhost'});
  const w = dom.window, d = w.document, timers = new Map();
  let serial = 0;
  w.setTimeout = (fn, ms) => {const id=++serial; if(ms===15000) timers.set(id,fn); else queueMicrotask(fn); return id;};
  w.clearTimeout = id => timers.delete(id);
  w.echarts = {init:()=>({setOption() {},resize() {},dispose() {}})};
  w.eval(source);
  w.$ = id => d.getElementById(id); w.currentKind = () => w.MarketApp.kind;
  w.getWatchlist = () => []; w.S = {cat:{known_funds:[]}};
  w.eval(app.slice(app.indexOf('function renderWatchlist()'), app.indexOf('function toggleWatchlist()')));
  w.MarketApp.init({known_funds:[],assets:{stocks:[],indices:[]}}, {
    request:handler, alert() {}, fund:{invalidate() {},hasResult:()=>false,renderWatchlist:w.renderWatchlist}
  });
  d.getElementById('marketStart').value='2024-01-01'; d.getElementById('marketEnd').value='2025-01-01';
  return {w,d,timers,close:()=>w.close()};
}
(async () => {
  let resolvePost, count=0; const calls=[];
  const h=setup((url, opts) => {
    calls.push(url);
    if(url==='/api/market/analyze') {count++;return new Promise(r=>resolvePost=r);}
    if(url.endsWith('/cancel')) return Promise.resolve({});
    return new Promise(()=>{});
  });
  const first=h.w.MarketApp.submit('600519');
  await h.w.MarketApp.submit('000001'); assert.equal(count,1);
  assert.equal(h.d.getElementById('marketCancel').hidden,false);
  h.d.getElementById('marketCancel').click();
  resolvePost({job_id:'pending'}); await first;
  assert(calls.includes('/api/job/pending/cancel'));
  assert.equal(h.d.getElementById('runBtn').disabled,false);
  h.d.querySelector('[data-asset="fund"]').click();
  assert.equal(h.d.getElementById('fundSearchControls').classList.contains('is-off'), false);
  assert.equal(h.d.getElementById('watchSel').disabled, false);
  h.d.querySelector('[data-asset="stock"]').click();
  assert.equal(h.d.getElementById('fundSearchControls').classList.contains('is-off'), true);
  assert.equal(h.d.getElementById('watchSel').disabled, true);
  h.close();

  let reads=0;
  const retry=setup(url => {
    if(url==='/api/market/analyze') return Promise.resolve({job_id:'retry'});
    reads++; if(reads<3) return Promise.reject(Error('temporary offline'));
    return Promise.resolve({status:'error',error:'terminal'});
  });
  await retry.w.MarketApp.submit('600519');
  assert.equal(reads,3); assert.equal(retry.d.getElementById('runBtn').disabled,false); retry.close();

  let polls=0;
  const limit=setup(url => {
    if(url==='/api/market/analyze') return Promise.resolve({job_id:'long'});
    if(url.endsWith('/cancel')) return Promise.resolve({});
    polls++;return Promise.resolve({status:'running'});
  });
  await limit.w.MarketApp.submit('600519'); assert.equal(polls,1200);
  assert.equal(limit.d.getElementById('marketCancel').hidden,false);
  assert.match(limit.d.getElementById('marketStatus').textContent,/等待超时/);
  limit.d.querySelector('[data-asset="fund"]').click();
  limit.d.querySelector('[data-asset="index"]').click();
  limit.d.getElementById('marketCancel').click(); await tick();
  assert.equal(limit.d.getElementById('runBtn').disabled,false); limit.close();

  const timeout=setup((url,opts) => new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('timeout')))));
  const waiting=timeout.w.MarketApp.submit('600519');
  [...timeout.timers.values()][0](); await waiting;
  assert.equal(timeout.d.getElementById('runBtn').disabled,false); timeout.close();

  const fixture = {name:'Test',code:'sh600519',stats:{},meta:{},warnings:[],comparison:{ok:false},
    series:{dates:[],close:[],candles:[],ma:{},drawdown:[],volatility:[]}};
  let finishRead;
  const switched=setup(url => {
    if(url==='/api/market/analyze') return Promise.resolve({job_id:'switched'});
    if(url.startsWith('/api/result/')) return Promise.resolve({result:fixture});
    return new Promise(r=>finishRead=r);
  });
  const work=switched.w.MarketApp.submit('600519'); await tick();
  switched.d.querySelector('[data-asset="index"]').click();
  switched.d.querySelector('[data-asset="stock"]').click();
  finishRead({status:'done'}); await work;
  assert.equal(switched.d.getElementById('marketName').textContent,'Test · SH600519');
  assert.equal(switched.d.getElementById('runBtn').disabled,false); switched.close();

  let lateRead, submissions=0;
  const cancelled=setup(url => {
    if(url==='/api/market/analyze') return ++submissions===1 ? Promise.resolve({job_id:'old'}) : new Promise(()=>{});
    if(url.endsWith('/cancel')) return Promise.resolve({});
    return new Promise(r=>lateRead=r);
  });
  const obsolete=cancelled.w.MarketApp.submit('600519'); await tick();
  cancelled.d.getElementById('marketCancel').click(); await tick();
  cancelled.w.MarketApp.submit('000001');
  lateRead({status:'error',error:'obsolete'}); await obsolete;
  assert.equal(cancelled.d.getElementById('runBtn').disabled,true);
  assert.doesNotMatch(cancelled.d.getElementById('marketStatus').textContent,/obsolete/);
  cancelled.close();
  console.log('Market reentry, pending cancellation, cross-kind cancellation, watchlist, GET retry, poll limit and request timeout: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
