"""Private dataset workbench backed only by retained, purpose-eligible records."""

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 研究数据工作台</title>
<script src="/admin/data/app.js" defer></script><style>
:root{color-scheme:dark;font:14px/1.6 system-ui,sans-serif;color:#e2eaf2;background:#091019}
*{box-sizing:border-box}body{margin:0}a{color:#80d9c2;text-decoration:none}button,input,select{font:inherit}
button,select,input{color:inherit;background:#132331;border:1px solid #344958;border-radius:7px;padding:8px 11px}
button{cursor:pointer}button:disabled{opacity:.4;cursor:default}input[type=checkbox]{accent-color:#77d5bc}
nav{display:flex;justify-content:space-between;gap:16px;padding:18px 36px;border-bottom:1px solid #24333e}
main{max-width:1540px;padding:28px 36px 60px;margin:auto}h1{font-size:32px;letter-spacing:-1px;margin:5px 0 10px}
h2{font-size:18px;margin:0 0 14px}h3{font-size:14px;margin:16px 0 8px}p{margin:8px 0}
.eyebrow{font-size:11px;letter-spacing:2px;color:#8ca4b4}.muted,small{color:#9ab0bf}.warning{color:#efc587}
.hero{display:flex;justify-content:space-between;gap:30px;margin-bottom:24px}.hero>p{max-width:390px}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0 24px}
.metric,.panel{border:1px solid #263946;background:#101d29;border-radius:12px;padding:18px}
.metric strong{display:block;font-size:30px;color:#a5efdc;margin-top:5px}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
label{display:block}.filters label>span{display:block;color:#9ab0bf;font-size:12px;margin-bottom:4px}
.workspace{display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;margin:18px 0}.project{padding:12px 0;border-bottom:1px solid #253946}
.project label{display:flex;gap:9px;align-items:start}.project small{display:block;padding-left:25px;overflow-wrap:anywhere}
.paging{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:16px}
.browser{display:grid;grid-template-columns:minmax(200px,31%) minmax(0,1fr);gap:16px;margin-top:14px}
#samples{max-height:730px;overflow:auto}.sample{display:block;text-align:left;width:100%;margin-bottom:9px;padding:12px;background:#0b1721}
.sample.active{border-color:#78d2bc;background:#15332f}.sample strong,.sample small{display:block;overflow-wrap:anywhere}
.badge{display:inline-block;border:1px solid #38535b;color:#9ee5d1;font-size:11px;border-radius:5px;padding:2px 7px;margin:0 6px 6px 0}
.inspector{min-width:0}.message{padding:12px;margin:10px 0;background:#0a141e;border:1px solid #233744;border-radius:8px}
.message pre{margin:5px 0}.role{color:#8cdcc3;font-size:12px;font-weight:600;text-transform:uppercase}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;max-height:420px;overflow:auto;color:#c4d5e0}
details{border-top:1px solid #273b47;padding-top:10px;margin-top:12px}summary{cursor:pointer;color:#afdcd2}
.review{background:#112b2a;border:1px solid #37675e;margin-top:18px}.review label{margin:10px 0}.review button{background:#9be4cf;color:#092b23;font-weight:700}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.table{overflow:auto}table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:9px;border-bottom:1px solid #2b3c48;font-size:12px;vertical-align:top;overflow-wrap:anywhere}
#error{color:#ffb1ac}#load-status{color:#9ec2d3;min-height:22px}#formats{background:#0a141e;padding:14px;border-radius:8px}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.browser{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(3,1fr)}}
@media(max-width:650px){nav,main{padding:18px}.hero,.split{display:block}.metrics{grid-template-columns:1fr 1fr}.split>.panel{margin-top:18px}}
</style></head><body>
<nav><a href="/admin">ARGUS / 运营后台</a><span>研究数据工作台 · 管理员专用</span><a href="/invite">服务入口 →</a></nav>
<main><div class="hero"><div><div class="eyebrow">RESEARCH DATA / OBSERVED & TRACEABLE</div>
<h1>研究数据工作台</h1><p class="muted">查看真实任务来源，逐条核对工具调用，准备可追溯的训练数据。</p></div>
<p class="muted">候选、有明确质量依据的样本和隔离记录分别展示。采集成功不等于任务成功，工具过程也不代表全局轨迹完整。</p></div>
<div class="filters"><label><span>导出用途</span><select id="purpose"><option value="internal_training">内部训练</option>
<option value="external_sharing">第三方 / 商业分享</option></select></label>
<label><span>邀请码</span><input id="tenant" placeholder="全部邀请码" maxlength="160"></label>
<label><span>项目 ID</span><input id="project-query" placeholder="包含文字" maxlength="160"></label>
<button id="reload">查询 / 刷新</button></div><p id="load-status" role="status">正在读取服务器数据…</p><p id="error" role="alert"></p>
<div id="metrics" class="metrics"></div>
<div class="workspace"><aside class="panel"><h2>项目与任务</h2><p class="muted">勾选本页要导出的项目；切页或刷新会清空审核选择。</p>
<div id="projects"></div><div class="paging"><button id="prev" disabled>上一页</button><span id="page"></span><button id="next" disabled>下一页</button></div></aside>
<section class="panel"><h2>真实样本与工具过程</h2><div class="filters">
<label><span>本页任务 ID</span><input id="task-query" placeholder="按已记录任务 ID 筛选"></label>
<label><span>样本类型</span><select id="sample-kind"><option value="all">全部候选</option><option value="tools">工具调用候选</option><option value="chat">聊天候选</option></select></label></div>
<p id="sample-count" class="muted"></p><div class="browser"><div id="samples"></div><div class="inspector">
<h2 id="sample-title">选择样本查看</h2><p id="sample-identity" class="muted"></p><div id="sample-badges"></div><p id="sample-state" class="muted"></p>
<label><input id="approve" type="checkbox" disabled> 我已核查此候选的任务结果，明确批准其质量。</label>
<div id="messages"></div><details><summary>真实工具定义（tools）</summary><pre id="tools"></pre></details>
<details><summary>任务、模型与事件来源</summary><pre id="source"></pre></details>
<details><summary>训练格式预览</summary><label>格式 <select id="format"><option value="hf">HF / TRL（arguments 为对象）</option>
<option value="portable">通用 JSON（arguments 为 JSON 字符串）</option></select></label><pre id="formats"></pre>
<p class="muted">仅预览此候选。目标模型的 chat template、工具协议和训练配置仍须匹配；诊断记录不进入 SFT 文件。</p></details>
</div></div></section></div>
<section class="panel review"><h2>审核与导出</h2><p id="review-summary">尚未选择项目。</p>
<label><input id="content-approved" type="checkbox"> 我已人工核查所选项目内容、隐私边界及适用范围。</label>
<label><input id="context-approved" type="checkbox"> 我已核查所批准工具候选的完整公开上下文、schema、参数和结果，确认不依赖未提供的系统指令。</label>
<label><input id="rights-approved" type="checkbox"> 我已另行核查第三方提供所需权利与上游许可（对外用途必选）。</label>
<label>独立验证报告 SHA-256（如有） <input id="evidence" maxlength="64" placeholder="64 位小写十六进制摘要"></label>
<button id="download" disabled>服务器校验后下载 ZIP</button><p id="export-status" role="status"></p>
<p class="muted">此处明确勾选的审阅记录为 human_operator；委托自动验收使用 automated_acceptance 并记录验证报告摘要，不冒充人工判断。
服务器重新检查授权、删除状态及样本资格。0 条 SFT 会如实显示在包内 manifest；不会自动训练、上传或出售。</p></section>
<div class="split"><section class="panel"><h2>采集状态与范围</h2><pre id="collector"></pre><details><summary>采集与格式限制</summary><pre id="limits"></pre></details></section>
<section class="panel"><h2>隔离与排除原因</h2><p class="muted">这些记录用于定位数据缺口，不能当作合格训练样本。</p><div id="reasons"></div>
<details><summary>有界诊断索引</summary><pre id="diagnostics"></pre></details></section></div>
<section class="panel" style="margin-top:18px"><h2>最近导出审计</h2><p class="muted">记录操作者权限身份、审阅类型和验证报告摘要；自动验收与人工审核可明确区分。</p>
<div class="table"><table><thead><tr><th>时间 / 操作</th><th>审阅类型 / 身份</th><th>结果 / 样本数</th><th>验证报告 SHA-256</th></tr></thead><tbody id="audit"></tbody></table></div></section>
</main></body></html>"""

SCRIPT = r"""
'use strict';
const el=id=>document.getElementById(id);
let preview=null, active=null, version=0, readonly=true, busy=false;
const selected=new Set(), approved=new Set();
const key=project=>JSON.stringify([project.tenant_id,project.sid]);
const projectName=project=>preview?.projects.find(row=>key(row)===key(project))?.title||project.title||project.sid;
const number=value=>value===null||value===undefined ? '不可用' : Number(value).toLocaleString();
const json=value=>JSON.stringify(value,null,2);
async function api(path,options={}) {
  const response=await fetch(path,{credentials:'same-origin',...options});
  const result=await response.json();
  if(!response.ok) throw Error(result.detail || '数据读取失败');
  return result;
}
function textNode(tag,text,className='') {
  const node=document.createElement(tag); node.textContent=text; node.className=className; return node;
}
function selection() {return preview ? preview.projects.filter(project=>project.eligible===true&&selected.has(key(project))) : [];}
function reviewed() {return preview ? preview.candidates.filter(candidate=>approved.has(candidate.event_id)&&selected.has(key(candidate))) : [];}
function updateControls() {
  const projects=selection(), candidates=reviewed(), toolReview=candidates.some(candidate=>candidate.sample?.tools);
  const retainedReviews=(preview?.candidates||[]).filter(candidate=>candidate.quality_approved&&
    selected.has(key(candidate))&&!approved.has(candidate.event_id)).length;
  const digest=el('evidence').value.trim(), validDigest=!digest || /^[0-9a-f]{64}$/.test(digest);
  el('download').disabled=readonly||busy||!projects.length||!el('content-approved').checked||!validDigest||
    (toolReview&&!el('context-approved').checked)||(el('purpose').value==='external_sharing'&&!el('rights-approved').checked);
  el('approve').disabled=readonly||busy||!active||!selected.has(key(active));
  el('approve').checked=Boolean(active&&approved.has(active.event_id));
  el('review-summary').textContent=number(projects.length)+' 个所选项目 · '+number(candidates.length)+' 条逐条批准候选'+
    ' · '+number(retainedReviews)+' 条已有有效审阅凭据'+
    (readonly?' · 当前为只读会话':'')+'；最终合格条数由服务器在导出时重新核定。';
  el('prev').disabled=busy||!preview||!preview.offset;
  el('next').disabled=busy||!preview?.has_more_projects;
  for(const id of ['purpose','tenant','project-query','reload','content-approved','context-approved','rights-approved','evidence'])
    el(id).disabled=busy||(readonly&&['content-approved','context-approved','rights-approved','evidence'].includes(id));
  for(const input of document.querySelectorAll('.project-choice')) input.disabled=busy||input.dataset.eligible!=='true';
}
function clearReview() {
  for(const id of ['content-approved','context-approved','rights-approved']) el(id).checked=false;
  el('evidence').value='';
}
function renderFormat() {
  if(!active) {el('formats').textContent='';return;}
  const sample=structuredClone(active.sample);
  if(el('format').value==='portable') for(const message of sample.messages||[]) {
    if(message.role==='tool') delete message.name;
    for(const call of message.tool_calls||[]) call.function.arguments=JSON.stringify(call.function.arguments);
  }
  el('formats').textContent=json(sample);
}
function inspect(candidate) {
  active=candidate;
  el('sample-title').textContent=candidate ? projectName(candidate) : '选择样本查看';
  el('sample-identity').textContent=candidate ? candidate.sid+' · '+candidate.tenant_id+
    ' · 任务 '+(candidate.task_id||'未记录可靠任务 ID') : '';
  for(const id of ['messages','sample-badges']) el(id).replaceChildren();
  for(const id of ['tools','source','sample-state']) el(id).textContent='';
  if(candidate) {
    for(const label of [candidate.sample?.tools?'工具调用候选':'单次聊天候选',
      candidate.sample_complete?'公开样本完整性已校验':'公开样本完整性未确认','全局完整性未经证实'])
      el('sample-badges').append(textNode('span',label,'badge'));
    el('sample-state').textContent=candidate.quality_approved ? '已有明确质量依据；导出仍需内容与用途检查。' : '尚未通过本次逐条质量审阅。';
    for(const message of candidate.sample?.messages||[]) {
      const item=document.createElement('article'); item.className='message';
      item.append(textNode('div',message.role+(message.tool_call_id?' · '+message.tool_call_id:''),'role'));
      if(message.content!==null&&message.content!==undefined)
        item.append(textNode('pre',typeof message.content==='string'?message.content:json(message.content)));
      for(const call of message.tool_calls||[]) {
        item.append(textNode('div',(call.function?.name||'工具')+' · '+call.id,'role'));
        item.append(textNode('pre',typeof call.function?.arguments==='string'?call.function.arguments:json(call.function?.arguments)));
      }
      el('messages').append(item);
    }
    el('tools').textContent=candidate.sample?.tools ? json(candidate.sample.tools) : '此聊天候选没有工具定义，不能视为工具训练样本。';
    el('source').textContent=json({task_id:candidate.task_id||null,project:candidate.sid,tenant:candidate.tenant_id,
      event_id:candidate.event_id,event_ids:candidate.event_ids,source:candidate.source,
      runtime:candidate.runtime||candidate.source?.runtime||null,runtime_profile:candidate.runtime_profile,
      scope:candidate.scope,quality_evidence:candidate.quality_evidence,
      model_context_complete:candidate.model_context_complete,global_complete:candidate.global_complete});
  }
  renderFormat(); updateControls();
}
function renderSamples() {
  el('samples').replaceChildren();
  const query=el('task-query').value.trim(), kind=el('sample-kind').value;
  const candidates=(preview?.candidates||[]).filter(candidate=>(!query||String(candidate.task_id||'').includes(query))&&
    (kind==='all'||(kind==='tools')===Boolean(candidate.sample?.tools)));
  el('sample-count').textContent='本页筛选匹配 '+number(candidates.length)+' 条候选；隔离与诊断记录位于下方独立区域。';
  for(const candidate of candidates) {
    const button=document.createElement('button'); button.className='sample'+(active?.event_id===candidate.event_id?' active':'');
    button.append(textNode('strong',projectName(candidate)));
    button.append(textNode('small',candidate.sample?.tools?'工具过程':'聊天回合'));
    button.append(textNode('small',candidate.tenant_id+' / '+candidate.sid));
    button.append(textNode('small','任务 '+(candidate.task_id||'未记录可靠任务 ID')));
    const first=candidate.sample?.messages?.find(message=>message.role==='user');
    button.append(textNode('p',(typeof first?.content==='string'?first.content:'公开消息候选').slice(0,120)));
    button.onclick=()=>{inspect(candidate);renderSamples();}; el('samples').append(button);
  }
  if(active&&!candidates.some(candidate=>candidate.event_id===active.event_id)) inspect(null);
}
function renderAudit(data) {
  el('audit').replaceChildren();
  for(const event of data.events||[]) {
    const row=document.createElement('tr');
    for(const value of [new Date(event.created_at*1000).toLocaleString()+' · '+event.action,
      (event.reviewer_kind||'unspecified')+' / '+event.actor,event.outcome+' / '+number(event.sft),event.evidence_sha256||'未提供'])
      row.append(textNode('td',value));
    el('audit').append(row);
  }
}
async function load(offset=0) {
  if(busy)return;
  const current=++version; selected.clear();approved.clear();clearReview();preview=null;inspect(null);
  el('load-status').textContent='正在读取服务器数据…';el('error').textContent='';
  const params=new URLSearchParams({purpose:el('purpose').value,offset:String(offset),query:el('project-query').value.trim()});
  if(el('tenant').value.trim())params.set('tenant',el('tenant').value.trim());
  try {
    const [data,identity,collector,audit]=await Promise.all([
      api('/admin/api/training/preview?'+params),api('/invite/status'),
      api('/admin/api/research/status').catch(error=>({state:'unavailable',detail:error.message})),
      api('/admin/api/training/audit').catch(error=>({events:[],error:error.message}))]);
    if(current!==version)return;
    if(!Array.isArray(data.projects)||!Array.isArray(data.candidates)||!data.counts)throw Error('数据格式不正确');
    readonly=identity.role!=='admin'||identity.readonly!==false;preview=data;
    el('metrics').replaceChildren();
    for(const [label,value] of [['匹配项目',data.total_projects],['本页工具候选',data.counts.tool_candidates],
      ['本页聊天候选',Number.isInteger(data.counts.candidates)&&Number.isInteger(data.counts.tool_candidates)?data.counts.candidates-data.counts.tool_candidates:null],
      ['本页已有质量依据 SFT',data.counts.sft],['本页隔离记录',data.counts.quarantined]]) {
      const box=textNode('div',label,'metric');box.append(textNode('strong',number(value)));el('metrics').append(box);
    }
    el('projects').replaceChildren();
    for(const project of data.projects) {
      const item=document.createElement('div');item.className='project';
      const label=document.createElement('label'),input=document.createElement('input');
      input.type='checkbox';input.className='project-choice';input.dataset.eligible=String(project.eligible===true);
      input.setAttribute('aria-label','选择 '+project.tenant_id+' / '+project.sid);
      input.onchange=()=>{if(input.checked)selected.add(key(project));else selected.delete(key(project));
        for(const candidate of preview.candidates)if(!selected.has(key(candidate)))approved.delete(candidate.event_id);
        clearReview();updateControls();};
      label.append(input,textNode('span',project.title||project.sid));
      item.append(label,textNode('small',project.sid+' · '+project.tenant_id));
      const taskIds=[...new Set(data.candidates.filter(candidate=>key(candidate)===key(project))
        .map(candidate=>candidate.task_id).filter(Boolean))];
      if(taskIds.length)item.append(textNode('small','任务 '+taskIds.slice(0,3).join(' · ')+
        (taskIds.length>3?' · 另 '+(taskIds.length-3)+' 个':'')));
      item.append(textNode('small',project.eligible?'用途已授权 · 待质量审阅':project.reason||'用途未授权'));
      el('projects').append(item);
    }
    el('page').textContent='第 '+number(Math.floor(data.offset/data.selection_limit)+1)+' 页 / '+number(data.total_projects)+' 项';
    el('collector').textContent=json({research_journal:collector,tool_capture:data.capture_status||null});el('limits').textContent=json(data.limits||[]);
    el('reasons').replaceChildren();
    for(const [reason,count] of Object.entries(data.reason_counts||{}))el('reasons').append(textNode('p',reason+' · '+number(count)));
    if(!Object.keys(data.reason_counts||{}).length)el('reasons').append(textNode('p','本页未报告排除原因。','muted'));
    el('diagnostics').textContent=json({records:data.diagnostics||[],total:data.diagnostics_total,
      truncated:data.diagnostics_truncated});renderAudit(audit);
    if(audit.error)el('error').textContent='审计读取失败：'+audit.error;
    renderSamples();updateControls();el('load-status').textContent='已读取服务器预览；统计和样本范围为当前项目页。';
  }catch(error){if(current!==version)return;el('error').textContent=error.message;el('load-status').textContent='读取失败，未产生数据包。';}
}
el('approve').onchange=()=>{if(!active||readonly||!selected.has(key(active)))return;
  if(el('approve').checked)approved.add(active.event_id);else approved.delete(active.event_id);
  el('context-approved').checked=false;updateControls();};
for(const id of ['content-approved','context-approved','rights-approved','evidence'])el(id).oninput=updateControls;
for(const id of ['task-query','sample-kind'])el(id).oninput=renderSamples;
el('format').onchange=renderFormat;
el('reload').onclick=()=>load();
for(const id of ['purpose','tenant','project-query'])el(id).onchange=()=>load();
el('prev').onclick=()=>load(Math.max(0,preview.offset-preview.selection_limit));
el('next').onclick=()=>load(preview.next_offset);
el('download').onclick=async()=>{
  updateControls();if(el('download').disabled)return;
  const offset=preview.offset;let completed=false;
  const purpose=el('purpose').value,projects=selection().map(project=>({tenant_id:project.tenant_id,sid:project.sid}));
  const review={reviewer_kind:'human_operator',content_approved:el('content-approved').checked,
    tool_context_approved:el('context-approved').checked,rights_reviewed:el('rights-approved').checked,
    approved_event_ids:reviewed().map(candidate=>candidate.event_id)};
  if(el('evidence').value.trim())review.evidence_sha256=el('evidence').value.trim();
  busy=true;updateControls();el('export-status').textContent='服务器正在重新校验并打包…';
  try{
    const response=await fetch('/admin/api/training/export',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose,projects,review})});
    if(!response.ok){const error=await response.json();throw Error(error.detail||'导出失败');}
    if(!response.headers.get('content-type')?.includes('application/zip'))throw Error('服务器未返回 ZIP');
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;
    link.download='argus-'+purpose+'-review.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    el('export-status').textContent='ZIP 已收到。请核对 manifest 的实际 SFT 条数、来源、审阅类型与证据摘要。未启动训练。';
    completed=true;
  }catch(error){el('export-status').textContent='未完成导出：'+error.message;}
  finally{busy=false;updateControls();}
  if(completed)await load(offset);
};
load();
"""
