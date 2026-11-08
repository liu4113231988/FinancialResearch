/* Prediction ledger: read-only backtest summary, independent of the research workspace. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const dialog = $('predictionLedger');
  if (!dialog) return;
  let busy = false, refreshAgain = false;

  function node(tag, text, cls) {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function feedback(message, error = false) {
    const target = $('ledgerFeedback');
    target.textContent = message;
    target.hidden = !message;
    target.classList.toggle('is-error', error);
  }
  async function api(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '请求失败，请稍后重试');
    return data;
  }
  const pct = v => v == null ? '—' : (Number(v) * 100).toFixed(1) + '%';
  const num = v => v == null ? '—' : Number(v).toFixed(4);
  const count = v => v == null ? '—' : String(v);
  const label = key => key == null ? '总体' : String(key);

  function overview(stats) {
    const items = [
      ['当前口径记录', count(stats.current_total)],
      ['方向命中率', pct(stats.hit_rate)],
      ['Brier', num(stats.brier)],
      ['规则事件', count(stats.rule_events)],
      ['待结算', count(stats.pending)],
      ['旧口径行', count(stats.legacy_rows)]
    ];
    $('ledgerOverview').replaceChildren(...items.map(([key, value]) => {
      const box = node('div');
      box.append(node('span', key), node('strong', value));
      return box;
    }));
  }
  function renderRows(stats, groups) {
    const source = groups && groups.length ? groups : [{key: '总体', ...stats}];
    $('ledgerRows').replaceChildren(...source.map(group => {
      const tr = node('tr');
      for (const value of [label(group.key), count(group.current_total),
                           count(group.decided), pct(group.hit_rate), num(group.brier),
                           count(group.rule_events), count(group.legacy_rows),
                           count(group.pending)]) tr.append(node('td', value));
      return tr;
    }));
    $('ledgerEmpty').hidden = source.length > 0 && (stats.current_total || 0) > 0;
  }
  async function refresh() {
    if (busy) {refreshAgain = true; return;}
    busy = true;
    const button = $('refreshLedger');
    button.disabled = true; button.textContent = '读取中…';
    try {
      const group = $('ledgerGroupBy').value;
      const query = group ? '?group_by=' + encodeURIComponent(group) : '';
      const data = await api('/api/predictions/summary' + query);
      const stats = data.overall || {};
      overview(stats);
      renderRows(stats, data.groups);
      const boundary = '命中率只衡量方向，不衡量扣费后收益，也不构成有效性证明。';
      $('ledgerNote').textContent = stats.note ? stats.note + ' ' + boundary : boundary;
      feedback('');
    } catch (error) {
      feedback('无法读取预测台账：' + error.message, true);
    } finally {
      busy = false; button.disabled = false; button.textContent = '刷新汇总';
      if (refreshAgain) {refreshAgain = false; refresh();}
    }
  }
  $('openPredictionLedger').onclick = () => {dialog.showModal(); refresh();};
  $('closePredictionLedger').onclick = () => dialog.close();
  dialog.addEventListener('close', () => $('openPredictionLedger').focus());
  $('refreshLedger').onclick = () => {feedback(''); refresh();};
  $('ledgerGroupBy').onchange = () => {feedback(''); refresh();};
})();
