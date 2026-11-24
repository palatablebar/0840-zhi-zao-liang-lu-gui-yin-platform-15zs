(function () {
  var state = { payload: null, kind: 'bars', run: null, approved: false };
  var UPDATE_LABELS = {UPDATED: '已更新损耗', RETAINED: '沿用已签发'};
  var $ = function (id) { return document.getElementById(id); };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setNotice(text) { $('notice').textContent = text || ''; }
  function renderIssues(data) {
    $('issue-count').textContent = data.issues.length + ' 条';
    $('issue-list').innerHTML = data.issues.length ? data.issues.map(function (item) {
      return '<li><span>' + esc(item.line || '-') + '</span><span>' + esc(item.key || '无主键') + '</span><code>' + esc(item.code) + '</code></li>';
    }).join('') : '<li class="empty">暂无异常</li>';
  }
  function renderBreakdown(items) {
    if (!items || !items.length) return '';
    return '<details><summary>' + items.length + ' 项</summary><ul>' + items.map(function (item) {
      return '<li>' + esc(item) + '</li>';
    }).join('') + '</ul></details>';
  }
  function renderRows(data) {
    var labels = Object.assign({label: '记录'}, state.resultColumns);
    var source = data.row_kind === 'source';
    var hasBreakdown = data.rows.some(function (row) { return row.breakdown && row.breakdown.length; });
    var keys = source ? state.inputColumns : Object.keys(labels).filter(function (key) {
      return data.rows.some(function (row) { return Object.hasOwn(row, key); });
    });
    $('row-count').textContent = source ? data.rows.length + ' 行预览' :
      data.summary.PASS + ' 通过 / ' + data.summary.REVIEW + ' 复核 / ' + data.summary.REJECT + ' 阻断';
    var tail = source ? '' : '<th>结论</th><th>补报状态</th>' + (hasBreakdown ? '<th>计算分项</th>' : '');
    $('row-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key] || key) + '</th>'; }).join('') + tail + '</tr>';
    $('row-body').innerHTML = data.rows.length ? data.rows.map(function (row) {
      var values = keys.map(function (key) {
        var text = row[key];
        if (key === 'rolling' && typeof text === 'number') text = text.toFixed(4);
        return '<td class="' + (typeof row[key] === 'number' ? 'num' : '') + '">' + esc(text) + '</td>';
      }).join('');
      var verdict = source ? '' : '<td><span class="verdict" data-v="' + esc(row.verdict) + '">' + esc(row.verdict || '') + '</span><br><small>' + esc(row.reason || '') + '</small></td>';
      var update = source ? '' : '<td><span class="update" data-v="' + esc(row.update_state || '') + '">' + esc(UPDATE_LABELS[row.update_state] || '-') + '</span><br><small>' + esc(row.supplement_reason || '') + '</small></td>';
      var breakdown = hasBreakdown ? '<td>' + renderBreakdown(row.breakdown) + '</td>' : '';
      return '<tr>' + values + verdict + update + breakdown + '</tr>';
    }).join('') : '<tr><td class="empty" colspan="' + (keys.length + (source ? 0 : 2)) + '">没有可展示条目</td></tr>';
  }
  function renderSupplements(data) {
    var entries = data.supplements || [];
    $('supplement-count').textContent = entries.length + ' 份';
    $('supplement-list').innerHTML = entries.length ? entries.slice().reverse().map(function (entry) {
      var parts = [];
      if (entry.updated && entry.updated.length) parts.push('更新 ' + entry.updated.join('、'));
      if (entry.retained && entry.retained.length) parts.push('沿用 ' + entry.retained.join('、'));
      return '<li><span>' + esc('rev ' + entry.revision) + '</span><span>' + esc(entry.rows) + ' 行</span><code>' + esc(parts.join(' · ') || '无工单变化') + '</code></li>';
    }).join('') : '<li class="empty">暂无补报</li>';
  }
  function renderLedger(data) {
    if (!data.ledger) { $('ledger-list').innerHTML = '<p class="empty">运行对账后显示</p>'; return; }
    var values = data.ledger;
    $('ledger-list').innerHTML = [
      ['来源行', values.counts.input], ['采信行', values.counts.accepted], ['隔离行', values.counts.isolated],
      ['精确重复', values.counts.duplicates], ['忽略行', values.counts.ignored], ['计算条目', values.counts.computed]
    ].map(function (row) { return '<div class="ledger-row"><span>' + row[0] + '</span><strong>' + row[1] + '</strong></div>'; }).join('') +
      (values.updated_orders || values.retained_orders ?
        '<div class="ledger-row"><span>补报更新 / 沿用</span><strong>' + values.updated_orders + ' / ' + values.retained_orders + '</strong></div>' : '') +
      '<div class="ledger-row"><span>' + esc(values.metric_field) + '</span><strong>' + esc(values.metric_total) + '</strong></div>' +
      values.checks.map(function (check) { return '<div class="ledger-row"><span>' + esc(check.label) + '</span><strong class="' + (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>'; }).join('');
  }
  function render(data) {
    state.payload = data;
    $('chart-label').textContent = Object.values(state.resultColumns)[0];
    $('m-input').textContent = data.counts.input;
    $('m-accepted').textContent = data.counts.accepted;
    $('m-isolated').textContent = data.counts.isolated + data.counts.duplicates;
    $('m-rejected').textContent = data.summary.REJECT;
    renderRows(data);
    renderIssues(data);
    renderLedger(data);
    renderSupplements(data);
    window.renderChart($('chart'), data.chart && data.chart.length ? (state.kind || 'bars') : 'bars', data.chart || []);
  }
  function stageButtons(stages) {
    $('stage-nav').innerHTML = stages.map(function (stage, index) {
      return '<button type="button" data-stage="' + stage.id + '" aria-pressed="' + (index === 0 ? 'true' : 'false') + '">' + esc(stage.name) + '</button>';
    }).join('');
    Array.from(document.querySelectorAll('[data-stage]')).forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-stage]').forEach(function (other) { other.setAttribute('aria-pressed', String(other === button)); });
        loadStage(button.getAttribute('data-stage'));
      });
    });
  }
  async function loadStage(stage) {
    try {
      setNotice('');
      document.body.setAttribute('aria-busy', 'true');
      var payload = await request('/api/stages/' + stage, {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      render(payload);
    } catch (error) { setNotice(error.message); }
    finally { document.body.removeAttribute('aria-busy'); }
  }
  function applyRun(run) {
    state.run = run;
    state.approved = run.status === 'APPROVED';
    render(run);
    $('run-status').textContent = run.status + ' · rev ' + run.revision;
    $('revision').textContent = 'rev ' + run.revision;
    $('sign').disabled = run.summary.REJECT > 0 || state.approved;
    $('hold').disabled = state.approved;
    $('download').hidden = !state.approved;
    if (state.approved) $('download').href = '/api/runs/' + run.run_id + '/evidence';
    $('supplement-send').disabled = !state.approved;
    $('supplement-note').textContent = state.approved ? '签发后可用' : '仅已签发批次可接收';
  }
  async function runBatch() {
    try {
      setNotice('');
      $('run').disabled = true;
      var run = await request('/api/runs', {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      applyRun(run);
    } catch (error) { setNotice(error.message); }
    finally { $('run').disabled = false; }
  }
  async function review(decision) {
    if (!state.run) return;
    try {
      var run = await request('/api/runs/' + state.run.run_id + '/review', {
        method: 'POST',
        body: JSON.stringify({decision: decision, comment: $('comment').value, expected_revision: state.run.revision})
      });
      applyRun(run);
    } catch (error) { setNotice(error.message); }
  }
  async function sendSupplement() {
    if (!state.run || !state.approved) return;
    var text = $('supplement').value.trim();
    if (!text) { setNotice('请填写补报内容'); return; }
    try {
      setNotice('');
      $('supplement-send').disabled = true;
      var response = await fetch('/api/runs/' + state.run.run_id + '/supplements?expected_revision=' + state.run.revision, {
        method: 'POST', headers: {'Content-Type': 'text/csv'}, body: text
      });
      var run = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(run.error || ('HTTP ' + response.status));
      $('supplement').value = '';
      applyRun(run);
    } catch (error) { setNotice(error.message); }
    finally { $('supplement-send').disabled = !state.approved; }
  }
  async function boot() {
    try {
      var catalog = await request('/api/catalog');
      state.kind = catalog.chart;
      state.resultColumns = catalog.result_columns;
      state.inputColumns = catalog.columns;
      stageButtons(catalog.stages.slice(0, 5));
      document.querySelector('.rail-note').textContent = catalog.code + ' · ' + catalog.stages[6].name;
      await loadStage('intake');
    } catch (error) { setNotice(error.message); }
    $('run').addEventListener('click', runBatch);
    $('sign').addEventListener('click', function () { review('APPROVED'); });
    $('hold').addEventListener('click', function () { review('HELD'); });
    $('supplement-send').addEventListener('click', sendSupplement);
    $('profile').addEventListener('change', function () {
      state.run = null; state.approved = false;
      $('sign').disabled = true; $('hold').disabled = true; $('download').hidden = true;
      $('supplement-send').disabled = true;
      $('run-status').textContent = '未执行批次'; $('revision').textContent = 'rev 0';
      loadStage('intake');
    });
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
