"""Private operator cockpit. All untrusted records are rendered as text."""

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Argus · 试用运营后台</title><script src="/admin/app.js" defer></script>
<style>
:root{color-scheme:dark;font:15px system-ui,sans-serif;background:#0c111b;color:#e6ecf5}
body{max-width:1320px;margin:auto;padding:30px 24px}h1{font-size:28px}h2{font-size:18px}
a{color:#8dd9ce}header{display:flex;justify-content:space-between;align-items:center}
.muted,small{color:#a4b2c6}.panel{background:#141c2a;border:1px solid #293448;
border-radius:14px;padding:22px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.stat{display:block;font-size:28px;margin:8px 0}button,select{font:inherit;color:inherit;
background:#234c49;border:1px solid #407c71;padding:9px 12px;border-radius:8px;cursor:pointer}
button:disabled{opacity:.5}.controls{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.scroll{overflow:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:11px 8px;
border-bottom:1px solid #293448;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;
max-height:550px;overflow:auto;background:#0c111b;padding:14px;border-radius:8px}
#error{color:#ffb6b6;white-space:pre-wrap}.warning{color:#edca88}details{margin:9px 0}
summary{cursor:pointer}button+button{margin-left:6px}textarea{width:95%;min-height:70px}
textarea,input{font:inherit;background:#0c111b;color:inherit;border:1px solid #65758a;padding:8px}
label.annotation{display:block;margin:12px 0}
.dataset{background:linear-gradient(125deg,#142d33,#19243d);border-color:#417c84}
.dataset .grid{margin:18px 0}.dataset-stat{background:#09131a88;padding:16px;border-radius:10px}
.dataset-stat strong{display:block;font-size:26px;margin-top:6px}.dataset label{display:block;margin:12px 0}
#training-status{color:#cbe7f0}.dataset pre{max-height:300px}
</style></head><body>
<header><div><small>ARGUS / OPERATOR ONLY</small><h1>试用运营后台</h1></div>
<nav><a href="/admin/data">研究数据后台 →</a> · <a href="/invite">服务入口</a></nav></header>
<p class="muted">邀请码身份、真实任务、资源消耗和经过过滤的过程记录。内部模拟试用与外部使用分开统计。</p>
<div class="controls"><label>统计窗口 <select id="days"><option value="1">最近 1 天</option>
<option value="7">最近 7 天</option><option value="30">最近 30 天</option></select></label>
<label><input id="internal" type="checkbox"> 包含内部模拟试用</label>
<button id="refresh">刷新</button></div><p id="dashboard-status" role="status"></p><p id="error" role="alert"></p>
<div id="metrics" class="grid"></div>
<section class="panel"><h2>邀请码使用情况</h2>
<p class="muted">“活跃”指最近 5 分钟有访问的邀请码，不等于独立自然人；任务提交不等于任务完成。
token / GPU 数据是累计额度账本，不受上方时间窗口限制。token 分别展示已结算、在途预留、待核实与未归因；预留不是实际消耗。
消息提交与有明确接受证据的任务分别计数，HTTP 成功响应本身不能证明任务已创建。</p>
<div class="scroll"><table><thead><tr><th>邀请码</th><th>类型 / 告知</th><th>最近访问</th>
<th>消息 / 已接受任务 / 计算提交</th><th>token 已结算 / 预留 / 待核实 / 未归因</th><th>GPU 已用 / 预留</th><th>过程数据</th></tr></thead>
<tbody id="accounts"></tbody></table></div></section>
<section class="panel"><h2>任务类别与近期提交</h2><p id="types" class="muted"></p>
<div class="scroll"><table><thead><tr><th>时间</th><th>邀请码</th><th>类别</th><th>接口</th><th>响应状态</th>
</tr></thead><tbody id="requests"></tbody></table></div></section>
<section class="panel"><h2 id="projects-title">选择邀请码查看任务与会话</h2><div id="projects"></div></section>
<section class="panel"><h2 id="trace-title">过程回放</h2>
<p class="warning">只展示用户可见记录与经过过滤的数据，不提供隐藏思维链。
脱敏是尽力而为；导出须人工复核，不能把试用同意当作对外出售或模型训练授权。</p>
<p class="warning">事件编号是入库顺序，不是因果顺序。完整性始终未经证实；缺口会明确显示。
任务状态不代表用户需求已满足，产物引用不证明文件存在或下载成功。</p>
<p id="coverage" class="muted"></p><button id="export" disabled>服务端审计导出当前页</button>
<button id="next" disabled>下一页</button>
<div id="trace"></div></section>
<section class="panel"><h2>人工研究标注（非模型推断）</h2>
<form id="annotation">
<label class="annotation">用户真正目标<textarea id="goal" maxlength="4000"></textarea></label>
<label class="annotation">首次偏离事件（已保留事件 ID；未知则留空）
<input id="deviation" maxlength="160"></label>
<label class="annotation">是否满足需求 <select id="satisfaction">
<option value="unknown">未知 / 尚未人工判断</option><option value="yes">是</option>
<option value="no">否</option></select></label>
<label class="annotation">人工依据 / 备注<textarea id="annotation-note" maxlength="4000"></textarea></label>
<button id="save-annotation" disabled>保存人工标注</button><p id="annotation-status" role="status"></p></form>
<h2>测试者明确反馈（独立于人工判断）</h2><pre id="feedback"></pre></section>
<section class="panel dataset" aria-labelledby="training-title">
<small>DATASET REVIEW / PURPOSE-SCOPED</small><h2 id="training-title">训练数据审阅与打包</h2>
<p>独立用途授权、来源校验、排除原因和人工审核之后，由服务器生成 ZIP；不会自动训练、上传或销售。
研究回放同意不能替代内部训练或商业分享授权。</p>
<div class="controls"><label>导出用途 <select id="training-purpose">
<option value="internal_training">内部训练数据准备</option>
<option value="external_sharing">第三方 / 商业供应准备</option></select></label>
<label>邀请码筛选 <input id="training-tenant" maxlength="160" placeholder="全部邀请码"></label>
<label>项目 ID 筛选 <input id="training-query" maxlength="160" placeholder="包含文字"></label>
<button id="training-preview">应用筛选 / 刷新</button></div>
<div class="controls"><button id="training-prev" disabled>上一页项目</button>
<span id="training-page" role="status"></span><button id="training-next" disabled>下一页项目</button></div>
<p class="muted">先勾选本页要审阅的项目，再逐条审核候选。切页、刷新或修改筛选会清空当前选择与审核确认。</p>
<div id="training-counts" class="grid"></div><p id="training-status" role="status">正在读取服务端候选统计。</p>
<p id="training-ready" class="warning">工具训练就绪状态尚未核验；聊天样本不等于完整 agentic 工具轨迹。</p>
<div class="scroll"><table><thead><tr><th>选择</th><th>项目</th><th>用途资格</th><th>保留事件</th><th>排除原因</th>
</tr></thead><tbody id="training-projects"></tbody></table></div>
<details><summary>排除 / 隔离计数与采集限制</summary><pre id="training-exclusions"></pre></details>
<h2>逐条候选人工质量审阅</h2><div id="training-candidates"></div>
<label><input id="training-content" type="checkbox"> 我已人工核查所选项目的内容、隐私风险与适用范围。</label>
<label><input id="training-tool-context" type="checkbox"> 我已逐条核查所批准工具候选的真实工具 schema、调用参数、结果与完整公开消息，
确认其不依赖未提供的 system 指令，足以独立理解任务（批准工具候选时必选）。</label>
<label><input id="training-rights" type="checkbox"> 我已另行核查对外提供所需的内容权利与上游许可
（第三方 / 商业用途必选；不代表系统提供法律认证）。</label>
<p class="warning">仅使用已有的公开摘要、可观察行动与输出，不提供隐藏推理。
聊天样本是单次可观察对话回合；工具样本仅涵盖经校验的公开工具过程。均不保证全局过程完整、匿名或可销售。
消息格式需按目标训练工具适配，不存在通用的一键兼容标准。</p>
<button id="training-download" disabled>审核后下载 ZIP 审阅包</button>
<p class="muted">实际 SFT 条数以包内 manifest.json / quality_report.json 为准；0 条不会伪装成成功样本。
授权撤回只阻止未来导出，既有交付不能自动召回。查看与打包均保留服务器审计。</p>
</section>
<section class="panel"><h2>邀请访问控制</h2><div id="access"></div></section>
<section class="panel"><h2>采集状态与管理员读 / 导出审计</h2><pre id="collector"></pre>
<button id="audit-refresh">刷新审计</button><pre id="audit"></pre></section>
<section class="panel"><h2>数据保留与口径</h2><pre id="policy"></pre>
<button id="prune">清理全部超期研究副本与运营元数据</button>
<p class="muted">不会删除用户工作区、研究产物或模型/GPU 计费账本。</p></section>
</body></html>"""

SCRIPT = r"""
'use strict';
const el = id => document.getElementById(id);
let selected = null, refreshVersion = 0;
let replaySelection = null, replayVersion = 0, annotationBaseline = null;
const annotationDrafts = new Map();
let trainingPreview = null, trainingBusy = false, trainingVersion = 0;
const trainingSelection = new Set();
const projectKey = (tenant, sid) => JSON.stringify([tenant, sid]);
function number(value, digits = 0) {
  return value === null || value === undefined ? '不可用' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
}
function date(value) { return value ? new Date(value * 1000).toLocaleString() : '尚无访问'; }
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error?.message || JSON.stringify(data));
  return data;
}
function cell(row, text) {
  const td = document.createElement('td'); td.textContent = text; row.append(td); return td;
}
function action(parent, title, fn) {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = title;
  button.onclick = async () => {button.disabled = true;
    try {await fn(); el('error').textContent = '';}
    catch (error) {el('error').textContent = error.message;}
    finally {button.disabled = false;}
  }; parent.append(button);
}
function showRecord(data, title) {
  selected = data; el('trace-title').textContent = title; el('trace').replaceChildren();
  el('export').disabled = !replaySelection;
  el('next').disabled = !replaySelection || !data.has_more;
  el('coverage').textContent = data.completeness ? JSON.stringify(data.completeness) :
    data.truncated ? '记录超过读取上限；当前展示已截断，不能视为完整记录。' :
    '记录仅反映已观察到的过程；排队确认不是研究完成证明。';
  const rows = data.events || data.rows || data.frames;
  if (Array.isArray(rows)) {
    for (const row of rows) {
      const item = document.createElement('details'), heading = document.createElement('summary');
      const value = row.payload || row.data || row;
      const result = value.result || {};
      const preview = [value.summary, value.title, value.text, value.input?.text, result.reply,
        value.reply, result.status, value.status].find(item => typeof item === 'string' && item.trim());
      heading.textContent = [row.sequence && '#' + row.sequence, row.source_kind,
        row.kind || value.type || value.kind || value.role,
        preview && preview.replace(/\s+/g, ' ').slice(0, 240), row.id]
        .filter(Boolean).join(' · ') || '过程记录';
      item.append(heading);
      const stages = row.payload?.steps || row.payload?.result?.steps;
      if (Array.isArray(stages)) {
        const list = document.createElement('ol');
        for (const stage of stages) {
          if (!stage) continue;
          const line = document.createElement('li');
          line.textContent = (stage.label || stage.kind) + ' · ' + (stage.status || 'unconfirmed');
          list.append(line);
        }
        item.append(list);
      }
      const pre = document.createElement('pre'); pre.textContent = JSON.stringify(row, null, 2);
      item.append(pre); el('trace').append(item);
    }
  }
  const raw = document.createElement('details'), summary = document.createElement('summary');
  summary.textContent = '完整脱敏结构与采集范围'; const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(data, null, 2); raw.append(summary, pre); el('trace').append(raw);
}
function researchPath() {
  return '/admin/api/research/' + encodeURIComponent(replaySelection.tenant) + '/' +
    encodeURIComponent(replaySelection.sid);
}
function annotationValue() {
  return {user_goal:el('goal').value, first_deviation_event_id:el('deviation').value || null,
    satisfaction:el('satisfaction').value, note:el('annotation-note').value};
}
function annotationKey() {
  return replaySelection && projectKey(replaySelection.tenant, replaySelection.sid);
}
function keepAnnotationDraft() {
  const key = annotationKey();
  if (!key || !annotationBaseline) return;
  const value = annotationValue();
  if (JSON.stringify(value) === JSON.stringify(annotationBaseline)) annotationDrafts.delete(key);
  else annotationDrafts.set(key, value);
  el('annotation-status').textContent = annotationDrafts.has(key) ?
    '有未保存标注；翻页和切换项目会保留在当前页面，返回该项目可继续编辑。关闭页面前请保存。' : '当前项目标注已保存。';
}
function annotationEnabled(enabled) {
  for (const id of ['goal', 'deviation', 'satisfaction', 'annotation-note', 'save-annotation'])
    el(id).disabled = !enabled;
}
async function loadReplay(tenant, sid, after = 0) {
  keepAnnotationDraft();
  const version = ++replayVersion;
  replaySelection = null;
  el('export').disabled = true; el('next').disabled = true; annotationEnabled(false);
  const path = '/admin/api/research/' + encodeURIComponent(tenant) + '/' + encodeURIComponent(sid);
  const [record, annotation, feedback] = await Promise.all([
    api(path + '/replay?after_sequence=' + after), api(path + '/annotation'), api(path + '/feedback')]);
  if (version !== replayVersion) return;
  replaySelection = {tenant, sid, after};
  showRecord(record, tenant + ' / ' + sid);
  annotationBaseline = {user_goal:annotation.user_goal || '',
    first_deviation_event_id:annotation.first_deviation_event_id || null,
    satisfaction:annotation.satisfaction || 'unknown', note:annotation.note || ''};
  const values = annotationDrafts.get(annotationKey()) || annotationBaseline;
  el('goal').value = values.user_goal; el('deviation').value = values.first_deviation_event_id || '';
  el('satisfaction').value = values.satisfaction; el('annotation-note').value = values.note;
  annotationEnabled(true); keepAnnotationDraft();
  el('feedback').textContent = JSON.stringify(feedback, null, 2);
}
async function projects(tenant) {
  const data = await api('/admin/api/tenants/' + encodeURIComponent(tenant) + '/projects');
  el('projects-title').textContent = tenant + ' · 会话与任务'; el('projects').replaceChildren();
  if (!Array.isArray(data.projects)) throw new Error('项目列表格式不正确');
  for (const project of data.projects) {
    const line = document.createElement('p'), text = document.createElement('span');
    text.textContent = (project.title || project.display_name || project.label || project.id) + ' · ' + project.id +
      (project.research_deleted ? ' · 研究副本已删除' : ' ');
    line.append(text);
    if (!project.research_deleted) action(line, '持久化研究回放', () => loadReplay(tenant, project.id));
    el('projects').append(line);
  }
  action(el('projects'), '查看输入 → 响应链路', async () => {
    const data = await api('/admin/api/tenants/' + encodeURIComponent(tenant) + '/interactions');
    if (!Array.isArray(data.interactions)) throw new Error('请求链路格式不正确');
    for (const item of data.interactions) {
      const line = document.createElement('p'); line.textContent = '#' + item.id + ' · ' + (item.sid || '') + ' ';
      action(line, '查看请求与结果', async () => {
        keepAnnotationDraft(); const version = ++replayVersion;
        replaySelection = null; annotationBaseline = null; annotationEnabled(false);
        el('export').disabled = true; el('next').disabled = true;
        const record = await api('/admin/api/tenants/' + encodeURIComponent(tenant) + '/interactions/' + item.id);
        if (version !== replayVersion) return;
        showRecord(record, tenant + ' · 请求 #' + item.id);
        el('annotation-status').textContent = '当前为请求链路；已有项目草稿仍保留，选择项目回放可继续标注。';
      }); el('projects').append(line);
    }
    if (!data.interactions.length) el('projects').append(document.createTextNode(' 尚无已采集的请求链路。'));
  });
}
async function refresh() {
  const version = ++refreshVersion, days = el('days').value, internal = el('internal').checked;
  el('dashboard-status').textContent = '正在读取最近 ' + days + ' 天数据' +
    (internal ? '（含内部模拟）' : '（仅外部试用）') + '…';
  try {
    const [data, collector, access] = await Promise.all([
      api('/admin/api/dashboard?days=' + days + '&include_internal=' + internal),
      api('/admin/api/research/status'), api('/admin/api/research/testers')]);
    if (version !== refreshVersion) return;
    const s = data.summary; el('metrics').replaceChildren();
    const metrics = [['已使用邀请码', s.consented_accounts], ['最近 5 分钟活跃', s.active_codes_last_5min],
      ['提交消息的账号', s.message_active_accounts], ['任务已接受的账号', s.task_active_accounts],
      ['提交计算的账号', s.compute_active_accounts], ['token 已结算', s.tokens_settled],
      ['token 在途预留', s.tokens_reserved], ['token 待核实', s.tokens_uncertain],
      ['token 未归因', s.tokens_unattributed],
      ['GPU 已用小时', s.gpu_seconds_used === null ? null : s.gpu_seconds_used / 3600],
      ['内部模拟账号', data.internal_testing.summary.configured_accounts]];
    for (const [label, value] of metrics) {
      const panel = document.createElement('div'); panel.className = 'panel'; panel.textContent = label;
      const stat = document.createElement('span'); stat.className = 'stat'; stat.textContent = number(value, 3);
      panel.append(stat); el('metrics').append(panel);
    }
    el('accounts').replaceChildren();
    for (const account of data.accounts) {
      const row = document.createElement('tr'); cell(row, account.tenant_id);
      cell(row, (account.internal_test ? '内部模拟' : '外部试用') + ' / ' + (account.consented ? '已确认' : '未确认'));
      cell(row, date(account.last_request_at));
      cell(row, [account.accepted_message_requests, account.accepted_task_requests,
        account.accepted_compute_job_requests].map(value => number(value)).join(' / '));
      cell(row, [account.tokens_settled, account.tokens_reserved, account.tokens_uncertain,
        account.tokens_unattributed].map(value => number(value)).join(' / '));
      cell(row, number(account.gpu_seconds_used === null ? null : account.gpu_seconds_used / 3600, 3) +
        ' / ' + number(account.gpu_seconds_reserved === null ? null : account.gpu_seconds_reserved / 3600, 3));
      const actions = cell(row, ''); action(actions, '查看', () => projects(account.tenant_id)); el('accounts').append(row);
    }
    el('types').textContent = Object.entries(data.task_types).map(([key, count]) => key + ': ' + count).join(' · ') || '尚无任务提交';
    el('requests').replaceChildren();
    for (const request of data.recent_task_requests) {
      const row = document.createElement('tr');
      for (const value of [date(request.ts), request.tenant_id, request.task_type, request.path, request.status])
        cell(row, value);
      el('requests').append(row);
    }
    el('policy').textContent = JSON.stringify(data.policy, null, 2);
    el('collector').textContent = JSON.stringify(collector, null, 2);
    el('access').replaceChildren();
    for (const tester of access.testers) {
      const line = document.createElement('p');
      line.textContent = tester.tenant_id + ' · ' + (tester.tester_id || '尚未登录') + ' ';
      const label = document.createElement('label'), enabled = document.createElement('input');
      enabled.type = 'checkbox'; enabled.checked = tester.enabled; label.append(enabled, ' 启用 ');
      const expires = document.createElement('input'); expires.type = 'datetime-local';
      expires.setAttribute('aria-label', tester.tenant_id + ' 到期时间（本地时间，留空则无期限）');
      if (tester.expires_at) {
        const date = new Date(tester.expires_at * 1000);
        expires.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0,16);
      }
      line.append(label, expires);
      action(line, '保存邀请访问权限', async () => {
        await api('/admin/api/research/testers/' + encodeURIComponent(tester.tenant_id) + '/access',
          {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
            enabled:enabled.checked, expires_at:expires.value ? new Date(expires.value).getTime()/1000 : null})});
        await refresh();
      });
      el('access').append(line);
    }
    el('dashboard-status').textContent = '当前数据：最近 ' + days + ' 天' +
      (internal ? '，含内部模拟' : '，仅外部试用') + '；更新时间 ' + date(data.generated_at);
    el('error').textContent = '';
  } catch (error) {
    if (version !== refreshVersion) return;
    el('error').textContent = error.message;
    el('dashboard-status').textContent = '本次筛选读取失败；下方保留的是上次成功读取的数据。';
  }
}
el('export').onclick = () => {
  if (!replaySelection) return;
  const link = document.createElement('a');
  link.href = researchPath() + '/export?after_sequence=' + replaySelection.after;
  link.download = 'argus-research-replay.json'; link.click();
};
el('next').onclick = async () => {
  try {await loadReplay(replaySelection.tenant, replaySelection.sid, selected.next_sequence);}
  catch (error) {el('error').textContent = error.message;}
};
el('annotation').onsubmit = async event => {
  event.preventDefault(); if (!replaySelection) return;
  const key = annotationKey(), value = annotationValue();
  try {
    await api(researchPath() + '/annotation', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(value)});
    if (key === annotationKey()) {annotationBaseline = value; keepAnnotationDraft();}
    else if (JSON.stringify(annotationDrafts.get(key)) === JSON.stringify(value)) annotationDrafts.delete(key);
    el('error').textContent = '';
  } catch (error) {el('error').textContent = error.message;}
};
for (const id of ['goal', 'deviation', 'satisfaction', 'annotation-note']) el(id).oninput = keepAnnotationDraft;
window.addEventListener('beforeunload', event => {
  keepAnnotationDraft();
  if (annotationDrafts.size) {event.preventDefault(); event.returnValue = '';}
});
el('audit-refresh').onclick = async () => {
  try {el('audit').textContent = JSON.stringify(await api('/admin/api/research/audit'), null, 2);}
  catch (error) {el('error').textContent = error.message;}
};
el('prune').onclick = async () => {
  try {await api('/admin/api/prune', {method:'POST'}); await refresh();}
  catch (error) {el('error').textContent = error.message;}
};
function trainingDownloadState() {
  const selected = selectedTrainingProjects();
  const approved = approvedTrainingEvents();
  const tools = trainingPreview && trainingPreview.candidates.some(candidate =>
    candidate.sample?.tools && approved.includes(candidate.event_id));
  el('training-download').disabled = trainingBusy || !selected.length || !el('training-content').checked ||
    (el('training-purpose').value === 'external_sharing' && !el('training-rights').checked) ||
    (tools && !el('training-tool-context').checked);
  for (const id of ['training-purpose', 'training-tenant', 'training-query', 'training-preview',
    'training-content', 'training-rights', 'training-tool-context']) el(id).disabled = trainingBusy;
  el('training-prev').disabled = trainingBusy || !trainingPreview || !trainingPreview.offset;
  el('training-next').disabled = trainingBusy || !trainingPreview?.has_more_projects;
  for (const input of document.querySelectorAll('.training-project-choice'))
    input.disabled = trainingBusy || input.dataset.eligible !== 'true';
  for (const input of document.querySelectorAll('.training-event-review'))
    input.disabled = trainingBusy || !trainingSelection.has(input.dataset.project);
}
function selectedTrainingProjects() {
  return trainingPreview ? trainingPreview.projects.filter(project => project.eligible === true &&
    trainingSelection.has(projectKey(project.tenant_id, project.sid))) : [];
}
function approvedTrainingEvents() {
  return [...document.querySelectorAll('.training-event-review:checked')]
    .filter(input => trainingSelection.has(input.dataset.project))
    .map(input => input.dataset.eventId);
}
function resetTrainingReview() {
  for (const id of ['training-content', 'training-rights', 'training-tool-context']) el(id).checked = false;
}
function updateTrainingSelection() {
  resetTrainingReview();
  for (const item of document.querySelectorAll('.training-candidate'))
    item.hidden = !trainingSelection.has(item.dataset.project);
  for (const input of document.querySelectorAll('.training-event-review'))
    if (!trainingSelection.has(input.dataset.project)) input.checked = false;
  trainingDownloadState();
}
async function loadTrainingPreview(offset = 0) {
  if (trainingBusy) return;
  const version = ++trainingVersion;
  const purpose = el('training-purpose').value, tenant = el('training-tenant').value.trim(),
    query = el('training-query').value.trim();
  trainingSelection.clear(); resetTrainingReview();
  trainingPreview = null; trainingDownloadState();
  el('training-counts').replaceChildren(); el('training-projects').replaceChildren();
  el('training-candidates').replaceChildren(); el('training-exclusions').textContent = '';
  el('training-page').textContent = '正在读取项目…';
  el('training-status').textContent = '正在读取服务端真实统计，没有启动导出或训练。';
  el('training-ready').textContent = '正在核验来源；不会把聊天 SFT 或诊断包称为工具训练就绪。';
  try {
    const params = new URLSearchParams({purpose, offset:String(offset), query});
    if (tenant) params.set('tenant', tenant);
    const data = await api('/admin/api/training/preview?' + params);
    if (version !== trainingVersion) return;
    if (!Array.isArray(data.projects) || !Array.isArray(data.candidates) || !data.counts)
      throw Error('训练预览响应格式不正确');
    trainingPreview = data;
    el('training-page').textContent = '匹配 ' + number(data.total_projects) + ' 个项目；当前第 ' +
      number(data.projects.length ? data.offset + 1 : 0) + '–' + number(data.offset + data.projects.length) + ' 个';
    const metrics = [['本页项目', data.counts.projects],
      ['本页用途合格项目', data.projects.filter(project => project.eligible === true).length],
      ['本页候选', data.counts.candidates], ['已有质量依据的聊天 SFT',
        Number.isInteger(data.counts.sft) && Number.isInteger(data.counts.tool_sft) ?
          data.counts.sft - data.counts.tool_sft : undefined],
      ['隔离事件', data.counts.quarantined], ['工具 SFT', data.counts.tool_sft]];
    for (const [label, value] of metrics) {
      const card = document.createElement('div'); card.className = 'dataset-stat'; card.textContent = label;
      const count = document.createElement('strong'); count.textContent = number(value);
      card.append(count); el('training-counts').append(card);
    }
    for (const project of data.projects) {
      const row = document.createElement('tr');
      const choice = document.createElement('input'), key = projectKey(project.tenant_id, project.sid);
      choice.type = 'checkbox'; choice.className = 'training-project-choice';
      choice.dataset.eligible = String(project.eligible === true);
      choice.setAttribute('aria-label', '选择 ' + project.tenant_id + ' / ' + project.sid);
      choice.onchange = () => {
        if (choice.checked && project.eligible === true) trainingSelection.add(key);
        else trainingSelection.delete(key);
        updateTrainingSelection();
      };
      cell(row, '').append(choice);
      cell(row, project.tenant_id + ' / ' + project.sid);
      cell(row, project.eligible ? '仅具备用途资格，仍需质量审阅' : '不合格 / 不导出');
      cell(row, number(project.retained_events)); cell(row, project.reason || '—');
      el('training-projects').append(row);
    }
    for (const candidate of data.candidates) {
      const item = document.createElement('details'), heading = document.createElement('summary');
      const key = projectKey(candidate.tenant_id, candidate.sid), tools = Boolean(candidate.sample?.tools);
      item.className = 'training-candidate'; item.dataset.project = key; item.hidden = true;
      heading.textContent = candidate.tenant_id + ' / ' + candidate.sid + ' · ' +
        (tools ? '工具调用候选 · ' : '聊天候选 · ') +
        (candidate.quality_approved ? '已有明确质量依据' : '尚未通过人工质量审核');
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify({tools:candidate.sample?.tools, messages:candidate.sample?.messages,
        event_id:candidate.event_id, quality_evidence:candidate.quality_evidence,
        scope:candidate.scope, sample_complete:candidate.sample_complete,
        context_review_required:candidate.context_review_required,
        global_complete:candidate.global_complete, duplicate_of:candidate.duplicate_of}, null, 2);
      const label = document.createElement('label'), approve = document.createElement('input');
      approve.type = 'checkbox'; approve.dataset.eventId = candidate.event_id;
      approve.dataset.project = key; approve.dataset.duplicate = String(Boolean(candidate.duplicate_of));
      approve.className = 'training-event-review';
      approve.onchange = () => {el('training-tool-context').checked = false; trainingDownloadState();};
      label.append(approve, candidate.duplicate_of ? ' 已逐条核查该候选质量并批准；它与本页其他候选重复，服务器会按实际所选项目去重' :
        ' 我已逐条核查该回复满足真实需求，明确批准此事件作为候选');
      item.append(heading, pre, label); el('training-candidates').append(item);
    }
    el('training-exclusions').textContent = JSON.stringify({
      reasons:data.reason_counts, limitations:data.limits, has_more_projects:data.has_more_projects,
      selection_limit:data.selection_limit, rights_status:data.rights_status,
      dataset_status:data.dataset_status, agentic_tool_training_ready:data.agentic_tool_training_ready,
      provider_license_review:data.provider_license_review}, null, 2);
    el('training-ready').textContent = data.counts.tool_candidates > 0 ?
      '本页有 ' + number(data.counts.tool_candidates) + ' 条工具候选；选择项目后可查看真实 schema、参数、结果和公开上下文。逐条质量批准及工具上下文确认后，服务器才会计入工具 SFT。' :
      '本页没有可审阅的工具候选；聊天样本与诊断数据不会计入工具 SFT。';
    el('training-status').textContent = !data.projects.some(project => project.eligible === true) ?
      '本页 0 个用途合格项目；可翻页或调整筛选查看其他项目。旧研究同意不会自动变成训练或对外分享授权。' :
      (data.counts.sft === 0 ? '当前 0 条已批准 SFT；可以逐条审核候选或下载真实的诊断审阅包，不会制造成功样本。' :
        '预览已就绪。请选择项目并审核后下载；服务器会在导出时重新检查授权、删除状态及样本资格。');
    trainingDownloadState();
  } catch (error) {
    if (version !== trainingVersion) return;
    trainingPreview = null; trainingDownloadState();
    el('training-page').textContent = '项目读取失败';
    el('training-status').textContent = '数据集服务暂不可用，未产生任何数据包：' + error.message;
  }
}
el('training-preview').onclick = () => loadTrainingPreview();
for (const id of ['training-purpose', 'training-tenant', 'training-query'])
  el(id).onchange = () => loadTrainingPreview();
el('training-prev').onclick = () => loadTrainingPreview(Math.max(0, trainingPreview.offset - trainingPreview.selection_limit));
el('training-next').onclick = () => loadTrainingPreview(trainingPreview.next_offset);
for (const id of ['training-content', 'training-rights', 'training-tool-context']) el(id).onchange = trainingDownloadState;
el('training-download').onclick = async () => {
  if (el('training-download').disabled || !trainingPreview) return;
  const purpose = el('training-purpose').value;
  const projects = selectedTrainingProjects()
    .map(project => ({tenant_id:project.tenant_id, sid:project.sid}));
  const review = {content_approved:el('training-content').checked, rights_reviewed:el('training-rights').checked,
    tool_context_approved:el('training-tool-context').checked, approved_event_ids:approvedTrainingEvents(),
    reviewer_kind:'human_operator'};
  trainingBusy = true; trainingDownloadState();
  el('training-status').textContent = '服务器正在重新验证并打包；尚未报告导出成功。';
  try {
    const response = await fetch('/admin/api/training/export', {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({purpose, projects, review})});
    if (!response.ok) {
      const error = await response.json(); throw Error(error.detail || '导出失败');
    }
    if (!response.headers.get('content-type')?.includes('application/zip')) throw Error('服务器没有返回 ZIP 数据包');
    const blob = await response.blob(), url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = 'argus-' + purpose + '-review.zip';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    el('training-status').textContent = '服务器已生成并审计 ZIP，浏览器已收到下载内容。请核对包内实际 SFT 条数；未启动训练、上传或销售。';
  } catch (error) {
    el('training-status').textContent = '未能完成打包：' + error.message;
  } finally {trainingBusy = false; trainingDownloadState();}
};
el('refresh').onclick = refresh; el('days').onchange = refresh; el('internal').onchange = refresh;
refresh(); loadTrainingPreview();
"""
