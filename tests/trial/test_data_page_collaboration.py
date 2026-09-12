"""Exercise task/role separation in the shipped data workbench JavaScript."""

from argus_skill.trial.data_page import SCRIPT
from tests.trial.test_admin_page import javascript

FIXTURE = r"""
el('purpose').value='internal_training';el('sample-kind').value='all';el('format').value='hf';
const candidate={tenant_id:'tenant-one',sid:'same-project',task_id:'task-one',event_id:'a'.repeat(64),
  quality_approved:true,sample_complete:true,
  sample:{tools:[{type:'function',function:{name:'bash',parameters:{type:'object'}}}],messages:[
    {role:'user',content:'## Task authority\nFramework context, not the original user request.'},
    {role:'assistant',tool_calls:[{id:'real-call',function:{name:'bash',arguments:{command:'python verify.py'}}}]},
    {role:'tool',tool_call_id:'real-call',content:'40000 checks passed'}]}};
const taskOne={id:'one',tenant_id:'tenant-one',sid:'same-project',task_id:'task-one',
  title:'Original task description',mission_title:'验证格点计数猜想',objective:'检查猜想、寻找反例并验证修正公式。',
  roles:[{role:'engineer',label:'执行',observations:1,episodes:1,tool_pairs:1},
    {role:'reviewer',label:'审查',observations:1,episodes:1,tool_pairs:0}],
  quality:{approved_samples:1,candidates:1},task_outcome:{state:'unknown',label:'任务结果未确认'},
  collection:{accepted_episodes:1,quarantined_episodes:1},
  request:{text:'Real user request',association:'authoritative'}};
const taskTwo={...taskOne,id:'two',task_id:'task-two',title:'Second real task',mission_title:null,roles:[],
  quality:{approved_samples:0,candidates:0},request:{text:'A separate task'}};
const projectActivity={...taskTwo,id:'unassigned',task_id:null,title:'未关联到具体任务的项目活动',request:null};
const overviewResponse={tasks:[taskOne,taskTwo,projectActivity],
  counts:{tasks:2,roles:2,tool_pairs:1,approved_samples:1}};
const detail={...taskOne,episodes:[
  {episode_id:1,sample_event_id:candidate.event_id,role:'engineer',state:'complete',quality_approved:true,
    tool_pairs:[{call_id:'real-call'}]},
  {episode_id:2,role:'reviewer',state:'quarantined'}],
  segments:[{role:'engineer',label:'执行',started_at:100,summary:'工具过程采集',source_kind:'tool_episode'},
    {role:'reviewer',label:'审查',started_at:120,summary:'工具过程采集',source_kind:'tool_episode'}],
  handoffs:[],global_complete:false};
const previewResponse={offset:0,selection_limit:20,total_projects:1,has_more_projects:false,
  projects:[{tenant_id:'tenant-one',sid:'same-project',title:'Long project request',eligible:true}],
  candidates:[candidate],counts:{candidates:1,tool_candidates:1,sft:1},reason_counts:{},diagnostics:[]};
overviewResponse.projects=previewResponse.projects;
let readonlyIdentity=false;
api=async path=>path.includes('/preview?')?previewResponse:
  path.includes('/collaboration?')?overviewResponse:
  path.includes('/collaboration/')?(path.includes('task-one')?detail:
    {...(path.includes('task-two')?taskTwo:projectActivity),segments:[],episodes:[],handoffs:[]}):
  path==='/invite/status'?{role:'admin',readonly:readonlyIdentity}:path.endsWith('/audit')?{events:[]}:{state:'running'};
"""


def run(scenario):
    javascript(FIXTURE + scenario, script=SCRIPT, startup="load();")


def test_roles_follow_real_episodes_and_keep_quarantined_review_separate():
    run(r"""
await load();
assert.equal(document.querySelectorAll('.role-card').length,2);
assert.match(el('role-run-title').textContent,/执行/);
assert.match(el('role-messages').textContent,/40000 checks passed/);
assert.match(el('task-outcome').textContent,/未确认/);
assert.match(el('capture-note').textContent,/采集缺口/);
assert.equal(el('handoffs').textContent,'');
const reviewer=document.querySelectorAll('.role-card')[1];
assert.match(reviewer.textContent,/工具详情未保留/);
assert.doesNotMatch(reviewer.textContent,/0 次可查看调用/);
reviewer.onclick();
assert.match(el('role-messages').textContent,/采集缺口/);
assert.doesNotMatch(el('role-messages').textContent,/40000 checks passed/);
await document.querySelectorAll('.project-open')[1].onclick();
assert.equal(activeTask.task_id,'task-two');
assert.equal(document.querySelectorAll('.sample').length,0);
assert.equal(document.querySelectorAll('.role-card').length,0);
assert.doesNotMatch(el('messages').textContent,/40000 checks passed/);
""")


def test_task_heading_uses_recorded_mission_without_model_context_fallback():
    run(r"""
taskOne.request=null;
await load();
assert.equal(el('task-title').textContent,'验证格点计数猜想');
assert.equal(el('task-description').textContent,'检查猜想、寻找反例并验证修正公式。');
assert.doesNotMatch(el('task-description').textContent,/Task authority/);
assert.doesNotMatch(el('task-goal').textContent,/Framework context/);
assert.match(el('messages').textContent,/Framework context/);
assert.match(el('messages').textContent,/模型输入（原始上下文）/);
taskOne.objective=null;
await load();
assert.match(el('task-description').textContent,/该轮原始请求未记录/);
assert.doesNotMatch(el('task-description').textContent,/Task authority/);
""")


def test_explicit_tasks_and_unassigned_project_activity_have_distinct_counts():
    run(r"""
await load();
assert.equal(document.querySelectorAll('.project-open').length,2);
assert.match(el('project-count').textContent,/2 个任务 · 1 组项目活动/);
assert.doesNotMatch(el('projects').textContent,/未关联到具体任务的项目活动/);
el('task-view').value='activity';el('task-view').onchange();
assert.equal(document.querySelectorAll('.project-open').length,1);
assert.match(el('project-count').textContent,/1 条项目活动/);
assert.match(el('projects').textContent,/未关联到具体任务的项目活动/);
await document.querySelectorAll('.project-open')[0].onclick();
assert.equal(activeTask.task_id,null);
assert.equal(document.querySelectorAll('.sample').length,0);
""")


def test_readonly_session_cannot_record_approval_or_export():
    run(r"""
readonlyIdentity=true;
await load();
assert.equal(el('approve').disabled,true);
assert.equal(el('select-sample-project').disabled,true);
assert.equal(el('content-approved').disabled,true);
assert.equal(el('download').disabled,true);
selected.add(key(candidate));
el('approve').checked=true;el('approve').onchange();
assert.equal(approved.size,0);
el('content-approved').checked=true;el('context-approved').checked=true;updateControls();
assert.equal(el('download').disabled,true);
assert.equal(el('session-label').textContent,'只读会话');
""")


def test_quarantined_role_with_activity_summaries_explains_missing_tool_contents():
    run(r"""
detail.segments.push({role:'reviewer',label:'审查',started_at:121,summary:'读取文件',
  source_kind:'journal_event',tool_name:'read',status:'observed'});
await load();
document.querySelectorAll('.role-card')[1].onclick();
assert.match(el('role-messages').textContent,/已确认该角色参与。以下是保留的活动摘要；工具参数与返回内容未保留。/);
assert.match(el('role-messages').textContent,/读取文件/);
assert.doesNotMatch(el('role-messages').textContent,/40000 checks passed/);
assert.match(el('task-outcome').textContent,/未确认/);
assert.equal(preview.candidates[0].quality_approved,true);
""")


def test_project_preview_limit_keeps_collaboration_and_other_projects_available():
    run(r"""
taskTwo.sid='second-project';
const secondProject={tenant_id:'tenant-one',sid:'second-project',title:'Another task',eligible:true};
overviewResponse.projects=[previewResponse.projects[0],secondProject];
const secondCandidate={...candidate,sid:'second-project',task_id:'task-two',event_id:'b'.repeat(64)};
const baseApi=api,previewRequests=[];
api=async path=>{
  if(!path.includes('/preview?'))return baseApi(path);
  previewRequests.push(path);
  const params=new URLSearchParams(path.split('?')[1]);
  assert.equal(params.get('tenant'),'tenant-one');
  assert.ok(params.get('query'));
  if(params.get('query')==='same-project')throw Error('training_source_size_limit');
  assert.equal(params.get('query'),'second-project');
  // A contains-query may return another project; only the exact requested project is retained.
  return {...previewResponse,projects:[previewResponse.projects[0],secondProject],
    candidates:[candidate,secondCandidate]};
};
await load();
assert.equal(previewRequests.length,1);
assert.equal(document.querySelectorAll('.project-open').length,2);
assert.equal(document.querySelectorAll('.role-card').length,2);
assert.equal(el('error').textContent,'');
assert.match(el('sample-preview-status').textContent,/超过单次读取上限/);
assert.match(el('metrics').textContent,/真实任务/);
assert.equal(selection().length,0);
assert.ok(document.querySelectorAll('.project-choice').every(input=>input.disabled));
await document.querySelectorAll('.project-open')[1].onclick();
assert.equal(previewRequests.length,2);
assert.equal(activeTask.sid,'second-project');
assert.equal(document.querySelectorAll('.sample').length,1);
assert.equal(preview.candidates.length,1);
assert.equal(preview.candidates[0].sid,'second-project');
assert.equal(el('error').textContent,'');
assert.doesNotMatch(el('sample-preview-status').textContent,/读取上限/);
assert.equal(document.querySelectorAll('.project-open').length,2);
assert.equal(document.querySelectorAll('.project-choice').filter(input=>!input.disabled).length,1);
""")


def test_old_project_preview_response_cannot_replace_new_task_selection():
    run(r"""
taskTwo.sid='second-project';
const secondProject={tenant_id:'tenant-one',sid:'second-project',title:'Another task',eligible:true};
overviewResponse.projects=[previewResponse.projects[0],secondProject];
const secondCandidate={...candidate,sid:'second-project',task_id:'task-two',event_id:'b'.repeat(64)};
const firstPreview=deferred(),baseApi=api;
api=async path=>{
  if(!path.includes('/preview?'))return baseApi(path);
  const params=new URLSearchParams(path.split('?')[1]);
  return params.get('query')==='same-project'?firstPreview.promise:
    {...previewResponse,projects:[secondProject],candidates:[secondCandidate]};
};
const loading=load();
for(let turn=0;turn<30&&document.querySelectorAll('.role-card').length<2;turn++)await Promise.resolve();
assert.equal(el('tab-sample-count').textContent,'…');
assert.match(el('role-messages').textContent,/正在读取此项目的工具详情/);
await document.querySelectorAll('.project-open')[1].onclick();
firstPreview.resolve(previewResponse);await loading;
assert.equal(activeTask.sid,'second-project');
assert.equal(preview.candidates.length,1);
assert.equal(preview.candidates[0].sid,'second-project');
assert.equal(active.event_id,secondCandidate.event_id);
""")


def test_lazy_project_selection_preserves_approved_export_scope_and_resets_purpose():
    run(r"""
taskTwo.sid='second-project';
const secondProject={tenant_id:'tenant-one',sid:'second-project',title:'Another task',eligible:true};
overviewResponse.projects=[previewResponse.projects[0],secondProject];
const secondCandidate={...candidate,sid:'second-project',task_id:'task-two',event_id:'b'.repeat(64)};
const baseApi=api;
api=async path=>{
  if(!path.includes('/preview?'))return baseApi(path);
  const second=new URLSearchParams(path.split('?')[1]).get('query')==='second-project';
  return {...previewResponse,projects:[second?secondProject:previewResponse.projects[0]],
    candidates:[second?secondCandidate:candidate]};
};
await load();
let choice=document.querySelectorAll('.project-choice')[0];choice.checked=true;choice.onchange();
el('approve').checked=true;el('approve').onchange();
await document.querySelectorAll('.project-open')[1].onclick();
assert.equal(selection().length,1);
assert.ok(approved.has(candidate.event_id));
choice=document.querySelectorAll('.project-choice')[1];choice.checked=true;choice.onchange();
el('approve').checked=true;el('approve').onchange();
assert.equal(selection().length,2);
assert.equal(reviewed().length,2);
el('content-approved').checked=true;el('context-approved').checked=true;updateControls();
assert.equal(el('download').disabled,false);
let exported;fetch=async(path,options)=>{exported=JSON.parse(options.body);return {
  ok:true,headers:{get:()=> 'application/zip'},blob:async()=>new Blob(['synthetic'])};};
await el('download').onclick();
assert.deepEqual(exported.projects,[{tenant_id:'tenant-one',sid:'same-project'},
  {tenant_id:'tenant-one',sid:'second-project'}]);
assert.deepEqual(exported.review.approved_event_ids,[candidate.event_id,secondCandidate.event_id]);
assert.equal(exported.review.reviewer_kind,'human_operator');
assert.equal(exported.purpose,'internal_training');
choice=document.querySelectorAll('.project-choice').find(input=>!input.disabled);choice.checked=true;choice.onchange();
el('content-approved').checked=true;el('evidence').value='c'.repeat(64);
el('purpose').value='external_sharing';await el('purpose').onchange();
assert.equal(selected.size,0);assert.equal(approved.size,0);
assert.equal(el('content-approved').checked,false);assert.equal(el('rights-approved').checked,false);
assert.equal(el('evidence').value,'');assert.equal(el('download').disabled,true);
""")


def test_legacy_preview_fallback_keeps_task_metadata_when_unselected_bodies_are_released():
    run(r"""
taskTwo.sid='second-project';
const secondProject={tenant_id:'tenant-one',sid:'second-project',title:'Another task',eligible:true};
const secondCandidate={...candidate,sid:'second-project',task_id:'task-two',event_id:'b'.repeat(64)};
previewResponse.projects.push(secondProject);previewResponse.candidates.push(secondCandidate);
const baseApi=api;
api=async path=>path.includes('/collaboration?')?{state:'legacy'}:baseApi(path);
await load();
assert.equal(document.querySelectorAll('.project-open').length,2);
await document.querySelectorAll('.project-open')[1].onclick();
assert.equal(activeTask.sid,'second-project');
assert.equal(document.querySelectorAll('.project-open').length,2);
assert.equal(tasksOnPage().filter(task=>task.task_id).length,2);
assert.equal(preview.candidates.length,1);
assert.equal(preview.candidates[0].sid,'second-project');
""")
