"""Small authenticated job cockpit; the compute service remains authoritative."""

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Argus · 算力工作台</title>
<style>
:root{color-scheme:dark;font:15px system-ui,sans-serif;background:#0c111b;color:#e6ecf5}
body{max-width:1160px;margin:auto;padding:32px 24px}a{color:#8dd9ce;text-decoration:none}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}
h1{font-size:28px;margin:8px 0}h2{font-size:18px}.muted{color:#a4b2c6}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.panel{background:#141c2a;border:1px solid #293448;border-radius:14px;padding:22px;margin:16px 0}
.stat{font-size:27px;display:block;margin-top:8px}label{display:block;margin:12px 0 6px}
input,textarea,select,button{font:inherit;color:inherit;border:1px solid #394961;
background:#101724;border-radius:8px;padding:10px;box-sizing:border-box}
input,textarea{width:100%}button{cursor:pointer;background:#234c49;border-color:#407c71}
button:disabled{opacity:.5;cursor:wait}.secondary{background:#273144}.danger{background:#632b36}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px 8px;border-bottom:1px solid #293448}
.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto}
#error{color:#ffb6b6;white-space:pre-wrap}small{color:#a4b2c6}.actions{display:flex;gap:8px}
@media(max-width:650px){body{padding:20px 12px}header{align-items:flex-start;gap:16px}.panel{padding:16px}}
</style><script src="/invite/compute.js" defer></script></head>
<body><header><div><a href="/invite">ARGUS / RESEARCH CLOUD</a>
<h1>算力工作台</h1><span class="muted">同一个账号，从想法、实验到结果。GPU 按任务排队，不按账号永久占卡。</span>
</div><a href="/">返回 Argus 工作区 →</a></header>
<div class="grid">
<div class="panel">模型额度<span id="tokens" class="stat">加载中</span><small>输入 + 输出 token，独立累计</small></div>
<div class="panel">GPU 额度<span id="hours" class="stat">加载中</span><small>卡数 × 实际运行小时，排队不计时</small></div>
<div class="panel">共享计算池<span id="pool" class="stat">加载中</span><small>按剩余容量和公平队列调度；非十份固定切割</small></div>
</div><p id="error" role="alert"></p>
<section class="panel"><h2>提交实验</h2>
<p class="muted">先在 Argus 工作区准备代码和输入。任务只访问本账号的文件；退出页面后仍由服务端调度。</p>
<form id="submit"><label for="name">实验名称</label><input id="name" maxlength="80" placeholder="例如：baseline / learning-rate sweep">
<label for="command">命令参数（JSON 数组，不是 shell 字符串）</label>
<textarea id="command" rows="3" required>["python", "experiment.py"]</textarea>
<div class="grid">
<div><label for="cpus">CPU 核数</label><input id="cpus" type="number" value="8" min="1" max="120" required></div>
<div><label for="memory">内存（GiB）</label><input id="memory" type="number" value="32" min="1" max="900" required></div>
<div><label for="gpus">GPU 张数</label><input id="gpus" type="number" value="1" min="0" max="4" required></div>
<div><label for="timeout">最长运行时间（秒）</label><input id="timeout" type="number" value="3600" min="1" max="86400" required></div>
</div><label for="workdir">工作目录（相对本账号 workspace）</label><input id="workdir" value=".">
<p><button id="submit-button" type="submit">提交到任务队列</button></p>
<small>GPU 额度先按请求时限预留，完成后按实际时长结算；超时会停止作业。CPU-only 作业将 GPU 设为 0。</small>
</form></section>
<section class="panel"><h2>我的任务</h2><div class="scroll"><table><thead><tr>
<th>任务</th><th>状态</th><th>资源</th><th>操作</th></tr></thead><tbody id="jobs"></tbody></table></div></section>
<section class="panel"><h2 id="log-title">实验日志</h2><pre id="logs">选择任务查看日志。结果文件保留在本账号工作区。</pre></section>
<footer class="muted">仅展示当前邀请码账号的任务和额度。邀请码等同账号凭证，请勿与其他试用者共享。</footer>
</body></html>"""

SCRIPT = """
'use strict';
const el = id => document.getElementById(id);
let refreshing = false;
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options});
  if (response.status === 401) {
    location.assign('/invite');
    throw new Error('请先输入邀请码');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || data.detail || JSON.stringify(data));
  return data;
}
function cell(row, text) {
  const td = document.createElement('td');
  td.textContent = text;
  row.append(td);
  return td;
}
function button(parent, text, action, cls) {
  const b = document.createElement('button');
  b.type = 'button'; b.textContent = text; b.className = cls || 'secondary';
  b.onclick = async () => {
    b.disabled = true;
    try { await action(); el('error').textContent = ''; }
    catch (error) { el('error').textContent = error.message; }
    finally { b.disabled = false; }
  };
  parent.append(b);
}
async function refresh() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    const [quota, compute, list] = await Promise.all([
      api('/invite/status'), api('/compute/status'), api('/compute/jobs')
    ]);
    const tokens = quota.tokens_remaining ?? quota.quota?.tokens_remaining;
    if (quota.token_unlimited === true) el('tokens').textContent = '不限累计 token';
    else {
      if (typeof tokens !== 'number') throw new Error('模型额度状态缺失');
      el('tokens').textContent = tokens.toLocaleString() + ' token';
    }
    const remaining = compute.gpu_hours_remaining;
    el('hours').textContent = remaining === undefined ? '请查看账户状态' : Number(remaining).toFixed(3) + ' GPU·h';
    el('pool').textContent = compute.capacity.cpus + ' CPU / ' + compute.capacity.gpus + ' GPU';
    el('memory').max = String(compute.capacity.memory_gib);
    const jobs = list.jobs;
    if (!Array.isArray(jobs)) throw new Error('服务返回的任务列表格式不正确');
    el('jobs').replaceChildren();
    for (const job of jobs) {
      const row = document.createElement('tr');
      cell(row, '#' + job.id + ' ' + (job.name || 'Experiment'));
      cell(row, job.status);
      cell(row, job.cpus + ' CPU / ' + job.memory_gib + ' GiB / ' + job.gpus + ' GPU');
      const actions = cell(row, ''); actions.className = 'actions';
      button(actions, '日志', async () => {
        const data = await api('/compute/jobs/' + job.id + '/logs');
        el('log-title').textContent = '实验日志 · #' + job.id;
        el('logs').textContent = data.logs;
      });
      if (['queued', 'starting', 'running', 'cancelling'].includes(job.status)) {
        button(actions, '取消', async () => {
          await api('/compute/jobs/' + job.id + '/cancel', {method:'POST'});
          await refresh();
        }, 'danger');
      }
      el('jobs').append(row);
    }
    if (!jobs.length) {
      const row = document.createElement('tr');
      const empty = cell(row, '尚无任务。在上方提交，或让 Argus 使用 compute_client 发起实验。');
      empty.colSpan = 4; el('jobs').append(row);
    }
    el('error').textContent = compute.scheduler_error || '';
  } catch (error) { el('error').textContent = error.message; }
  finally { refreshing = false; }
}
el('submit').onsubmit = async event => {
  event.preventDefault();
  el('submit-button').disabled = true;
  try {
    const command = JSON.parse(el('command').value);
    if (!Array.isArray(command) || !command.length || command.some(v => typeof v !== 'string'))
      throw new Error('命令必须是非空的字符串 JSON 数组');
    await api('/compute/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: el('name').value, command, cpus: Number(el('cpus').value),
        memory_gib: Number(el('memory').value), gpus: Number(el('gpus').value),
        timeout_seconds: Number(el('timeout').value), workdir: el('workdir').value})});
    await refresh();
  } catch (error) { el('error').textContent = error.message; }
  finally { el('submit-button').disabled = false; }
};
refresh();
setInterval(refresh, 5000);
"""
