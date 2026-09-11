"""Invitation-scoped research copies, explicit feedback, and deletion controls."""

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Argus · 研究记录与反馈</title><script src="/invite/research.js" defer></script>
<style>body{max-width:1000px;margin:30px auto;padding:20px;background:#0c111b;color:#e6ecf5;
font:16px/1.7 system-ui}a{color:#8dd9ce}section{border:1px solid #405068;border-radius:12px;
padding:20px;margin:20px 0}button,select,textarea,input{font:inherit;background:#172638;color:inherit;
padding:10px;border:1px solid #65758a;border-radius:6px}textarea{display:block;width:90%}
button{cursor:pointer}button:disabled{opacity:.5}pre{white-space:pre-wrap;overflow-wrap:anywhere}
#error{color:#ffb6b6}.warning{color:#edca88}label{display:block;margin:12px 0}</style>
<a href="/invite">返回服务入口</a><h1>研究记录与反馈</h1><p id="identity"></p>
<p>仅凭邀请码进入，无需注册。当前告知版本：operator-analytics-v3-workspace。服务器在同意后持续记录，
关闭浏览器不会停止采集。独立研究副本最多保留30天，容量限制可能更早清除。</p>
<p class="warning">只展示有限的可观察事件；编号为入库顺序，不是因果顺序，过程完整性始终未经证实。
采集边界之前不回填；已授权的工作区工具会记录真实定义、调用参数及公开执行结果，可能包含代码与文件内容。
隐藏推理、系统提示与凭证不属于采集范围；不完整、超限或疑似敏感的训练样本会被排除或隔离。
产物引用不证明文件存在或已经下载；任务完成状态不代表需求已满足。</p>
<section aria-labelledby="permissions-title"><h2 id="permissions-title">内测数据用途与授权记录</h2>
<p>新版团队内测登录告知包含内部模型训练，第三方或商业分享需要另行选择。
下面显示服务器已保存的选择，可在这里撤回。授权只适用于生效之后符合条件且仍保留的记录，不追溯旧记录。</p>
<form id="permissions">
<label><input id="internal-training" type="checkbox" disabled>
允许运营方将今后符合条件的任务与工作区工具过程用于内部模型训练数据集准备。</label>
<label><input id="external-sharing" type="checkbox" disabled>
我另行自愿允许将今后选定、经过去标识处理的记录向第三方提供，包括商业供应或出售。</label>
<p class="warning">去标识不保证无法重新识别。可以随时取消勾选并保存，撤回只影响未来导出；
此前已下载或向第三方交付的副本不能在此自动召回。仅授权不触发自动上传、销售或训练。</p>
<p id="permission-version"></p><button id="save-permissions" disabled>保存独立用途选择</button>
<p id="permission-status" role="status">正在读取当前选择；尚未提交任何用途授权。</p>
</form></section>
<button id="refresh">刷新项目</button><select id="project" aria-label="选择项目"></select>
<button id="open">查看研究回放</button><p id="error" role="alert"></p>
<section><h2>已记录事件</h2><pre id="coverage"></pre><div id="events"></div>
<button id="next" disabled>下一页</button></section>
<section><h2>您的明确评价（不会从任务状态自动推断）</h2>
<form id="feedback"><label>是否满足需求 <select id="verdict">
<option value="not_evaluated">尚未评价</option><option value="met_need">满足需求</option>
<option value="needs_changes">仍需修改</option></select></label>
<label>任务 ID（可选，填写您确认的 ID）<input id="task" maxlength="80"></label>
<label>反馈说明<textarea id="note" maxlength="4000"></textarea></label>
<button id="send">提交明确反馈</button></form><pre id="feedback-history"></pre></section>
<section><h2>删除研究副本</h2><p>此操作删除所选项目的研究事件、输入/回复副本、反馈及人工标注，
并停止该项目后续研究采集。不会停止任务，也不会删除运行时、工作区、源日志或计费账本。
简要删除审计保留最多30天；无内容的删除标记、采集游标和同意凭据单独保留。</p>
<p>本功能不创建研究数据库备份。既有主机快照、独立备份及管理员已下载导出不保证即时删除，
须联系邀请您的运营方处理；数据库逻辑删除也不等同于磁盘安全擦除。</p>
<button id="delete">删除所选项目全部研究副本</button><p id="deleted" role="status"></p></section>
</html>"""

SCRIPT = """
'use strict';
const el = id => document.getElementById(id);
let readonly = true, current = null, cursor = 0;
let permissionVersion = null;
async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options});
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || '操作失败');
  return data;
}
function base() {return '/research/api/projects/' + encodeURIComponent(current);}
function controls() {
  el('send').disabled = readonly || !current;
  el('delete').disabled = readonly || !current;
}
async function listing() {
  const data = await api('/research/api/projects');
  el('project').replaceChildren();
  for (const row of data.projects) {
    const option = document.createElement('option'); option.value = row.id;
    option.textContent = (row.title || row.id) + (row.research_deleted ? ' · 研究副本已删除' : '');
    option.disabled = row.research_deleted; el('project').append(option);
  }
  current = null; controls(); el('next').disabled = true;
  el('events').replaceChildren(); el('coverage').textContent = '';
  el('feedback-history').textContent = '';
}
async function replay(after = 0) {
  const data = await api(base() + '/replay?after_sequence=' + after);
  el('coverage').textContent = JSON.stringify(data.completeness, null, 2);
  el('events').replaceChildren();
  for (const row of data.events) {
    const item = document.createElement('details'), title = document.createElement('summary');
    const labels = {'http.request':'输入已记录', 'http.response':'可见回复 / 响应状态已记录',
      'journal.gap':'明确的采集缺口', 'manager.activity':'已观察到的执行阶段'};
    title.textContent = '#' + row.sequence + ' · ' + (labels[row.kind] || row.kind) +
      (row.payload?.label ? ' · ' + row.payload.label : '') + ' · ' + row.id;
    item.append(title);
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
    item.append(pre); el('events').append(item);
  }
  cursor = data.next_sequence; el('next').disabled = !data.has_more;
  el('feedback-history').textContent = JSON.stringify(await api(base() + '/feedback'), null, 2);
}
async function attempt(fn) {
  try {await fn(); el('error').textContent = '';}
  catch (error) {el('error').textContent = error.message;}
}
async function loadPermissions() {
  try {
    const data = await api('/trial/data-permissions');
    if (typeof data.notice_version !== 'string' || typeof data.internal_training !== 'boolean' ||
        typeof data.external_sharing !== 'boolean') throw Error('用途授权响应格式不正确');
    permissionVersion = data.notice_version;
    el('internal-training').checked = data.internal_training;
    el('external-sharing').checked = data.external_sharing;
    el('internal-training').disabled = readonly; el('external-sharing').disabled = readonly;
    el('save-permissions').disabled = readonly;
    el('permission-version').textContent = '独立用途告知版本：' + permissionVersion;
    el('permission-status').textContent = '已读取当前选择。更改后请保存；未勾选即未授权该用途。';
  } catch (error) {
    permissionVersion = null;
    el('save-permissions').disabled = true;
    el('permission-status').textContent = '用途授权服务暂不可用，未提交任何更改：' + error.message;
  }
}
el('permissions').onsubmit = async event => {
  event.preventDefault();
  if (!permissionVersion || readonly) return;
  el('save-permissions').disabled = true;
  try {
    await api('/trial/data-permissions', {method:'PUT', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({notice_version:permissionVersion,
        internal_training:el('internal-training').checked, external_sharing:el('external-sharing').checked})});
    await loadPermissions();
    el('permission-status').textContent = '用途选择已保存。撤回影响未来导出，既有交付不会自动召回。';
  } catch (error) {
    el('permission-status').textContent = '未能保存：' + error.message;
  } finally {el('save-permissions').disabled = readonly || !permissionVersion;}
};
el('refresh').onclick = () => attempt(listing);
el('open').onclick = () => attempt(async () => {
  const option = el('project').selectedOptions[0];
  if (!option || option.disabled) throw Error('请选择尚未删除研究副本的项目');
  current = option.value; controls(); await replay();
});
el('project').onchange = () => {current = null; controls(); el('next').disabled = true;};
el('next').onclick = () => attempt(() => replay(cursor));
el('feedback').onsubmit = event => {
  event.preventDefault();
  attempt(async () => {
    await api(base() + '/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({verdict:el('verdict').value, note:el('note').value, task_id:el('task').value || null})});
    el('feedback-history').textContent = JSON.stringify(await api(base() + '/feedback'), null, 2);
    el('note').value = '';
  });
};
el('delete').onclick = () => attempt(async () => {
  if (!current || !confirm('永久删除此项目的全部研究副本并停止后续研究采集？工作区与任务不会删除。')) return;
  const result = await api(base(), {method:'DELETE'});
  el('deleted').textContent = result.runtime_files_deleted === false ?
    '研究副本已删除，该项目不再采集。运行时与工作区未删除；审计及删除标记单独保留。' : '请联系运营方确认';
  await listing();
});
attempt(async () => {
  const data = await api('/invite/status'); readonly = data.readonly;
  el('identity').textContent = '测试者 ' + data.tester_id + (readonly ? ' · 只读会话' : '');
  await loadPermissions();
  await listing();
});
"""
