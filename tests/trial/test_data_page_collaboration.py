"""Exercise retained four-role observations and simple export in the shipped UI."""

from argus_skill.trial.data_page import PAGE, SCRIPT
from tests.trial.test_admin_page import javascript

FIXTURE = r"""
el('purpose').value='internal_training';el('sample-kind').value='all';el('format').value='hf';
el('observation-scope').value='project';
const project={tenant_id:'tenant-one',sid:'project-one',title:'Project request',eligible:true};
const task={id:'task-record',...project,task_id:'task-one',title:'Original task request',
  mission_title:'验证格点计数猜想',objective:'寻找反例并验证修正公式。',request:null,
  roles:[{role:'engineer',label:'执行',observations:2,episodes:1,tool_pairs:1},
    {role:'reviewer',label:'审查',observations:1,episodes:1,tool_pairs:0}],
  task_outcome:{state:'unknown',label:'任务结果未确认'},quality:{approved_samples:1,candidates:1},
  collection:{collected_episodes:2,states:{complete:1,failed:1}},last_observed_at:100};
const context=(id,text)=>({id:id+'-context',sequence:0,kind:'context',observed_at:100,
  payload:{messages:[{role:'system',content:'Recorded system input'},{role:'user',content:text}],tools:[]}});
const ending=(id,text)=>({id:id+'-end',sequence:3,kind:'agent_end',observed_at:110,
  payload:{messages:[{role:'assistant',content:[{type:'text',text}]}]}});
const engineer={episode_id:1,task_id:'task-one',role:'engineer',label:'执行',state:'complete',
  collection:{event_count:4,complete:true,issues:[]},quality:{state:'approved'},events:[
    context('engineer','## Task authority\nFramework input, not the original task.'),
    {id:'engineer-call',sequence:1,kind:'tool_call',payload:{toolCallId:'real-call',toolName:'bash',input:{command:'python verify.py'}}},
    {id:'engineer-result',sequence:2,kind:'tool_result',payload:{toolCallId:'real-call',toolName:'bash',content:[{type:'text',text:'40000 checks passed'}],isError:false}},
    ending('engineer','Verification completed.')],tool_pairs:[{call_id:'real-call'}]};
const reviewer={episode_id:2,task_id:'task-one',role:'reviewer',label:'审查',state:'failed',
  collection:{event_count:2,complete:false,issues:['runtime_failed']},quality:{state:'needs_work'},
  events:[context('reviewer','Review the result'),ending('reviewer','The run ended before review completed.')],tool_pairs:[]};
const manager={episode_id:3,task_id:null,role:'manager',label:'统筹',state:'complete',
  collection:{event_count:2,complete:true,issues:[]},quality:{state:'not_evaluated'},
  events:[context('manager','Coordinate the project'),ending('manager','Recorded manager coordination')],tool_pairs:[]};
const planner={episode_id:4,task_id:null,role:'planner',label:'规划',state:'capturing',
  collection:{event_count:1,complete:false,issues:[]},quality:{state:'not_evaluated'},
  events:[context('planner','Recorded planner decomposition')],tool_pairs:[]};
const other={...engineer,episode_id:5,task_id:'task-other',quality:{state:'not_evaluated'},
  events:[context('other','Other task context'),ending('other','Other task output')]};
const overviewResponse={tasks:[task],projects:[project],offset:0,total_projects:1,has_more_projects:false,
  counts:{tasks:1,roles:4,collected_episodes:5,tool_pairs:1,approved_samples:1}};
const detail={...task,episodes:[engineer,reviewer],segments:[],handoffs:[],global_complete:false};
let rawResponse={tenant_id:'tenant-one',sid:'project-one',purpose:'internal_training',
  episodes:[engineer,reviewer,manager,planner,other],pagination:{has_more:false,next_cursor:null}};
let readonlyIdentity=false;
api=async path=>{
  if(path.includes('/observations/')){
    const taskId=new URLSearchParams(path.split('?')[1]).get('task_id');
    return {...rawResponse,episodes:taskId?rawResponse.episodes.filter(episode=>episode.task_id===taskId):rawResponse.episodes};
  }
  if(path.includes('/collaboration?'))return overviewResponse;
  if(path.includes('/collaboration/'))return detail;
  if(path==='/invite/status')return {role:'admin',readonly:readonlyIdentity};
  if(path.endsWith('/audit'))return {events:[]};
  return {state:'running'};
};
"""


def run(scenario):
    javascript(FIXTURE + scenario, script=SCRIPT, startup="load();")


def test_default_project_scope_shows_all_roles_without_quality_or_tool_gate():
    run(r"""
await load();
assert.equal(document.querySelectorAll('.role-card').length,4);
assert.equal(document.querySelectorAll('.sample').length,5);
assert.match(el('collaboration-caption').textContent,/项目范围.*4 \/ 4/);
assert.match(el('metrics').textContent,/已采集过程/);
document.querySelectorAll('.role-card')[0].onclick();
assert.equal(activeRole,'manager');
assert.match(el('role-messages').textContent,/Recorded manager coordination/);
document.querySelectorAll('.role-card')[1].onclick();
assert.match(el('role-messages').textContent,/Recorded planner decomposition/);
assert.match(el('role-messages').textContent,/项目级过程 · 未关联具体任务/);
assert.equal(preview.candidates.find(candidate=>candidate.episode.role==='planner').task_id,null);
document.querySelectorAll('.role-card')[3].onclick();
assert.match(el('role-messages').textContent,/The run ended before review completed/);
assert.doesNotMatch(el('role-messages').textContent,/40000 checks passed/);
assert.match(el('task-outcome').textContent,/未确认/);
assert.equal(el('handoffs').textContent,'');
assert.match(el('samples').textContent,/项目级过程 · 未关联具体任务/);
""")


def test_task_scope_does_not_assign_project_level_runs_to_a_task():
    run(r"""
await load();
el('observation-scope').value='task';await el('observation-scope').onchange();
assert.equal(document.querySelectorAll('.sample').length,2);
assert.deepEqual(preview.candidates.map(candidate=>candidate.episode.episode_id),[1,2]);
assert.match(document.querySelectorAll('.role-card')[0].textContent,/未采到记录/);
assert.match(document.querySelectorAll('.role-card')[1].textContent,/未采到记录/);
assert.doesNotMatch(el('samples').textContent,/项目级过程/);
el('sample-kind').value='chat';el('sample-kind').oninput();
assert.equal(document.querySelectorAll('.sample').length,1);
assert.match(el('samples').textContent,/审查/);
""")


def test_raw_tool_steps_and_message_inputs_remain_visible_without_review():
    run(r"""
await load();
document.querySelectorAll('.role-card')[2].onclick();
assert.match(el('role-messages').textContent,/python verify.py/);
assert.match(el('role-messages').textContent,/40000 checks passed/);
assert.match(el('role-messages').textContent,/Recorded system input/);
assert.equal(el('task-title').textContent,'验证格点计数猜想');
assert.equal(el('task-description').textContent,'寻找反例并验证修正公式。');
assert.doesNotMatch(el('task-description').textContent,/Task authority/);
assert.doesNotMatch(el('task-goal').textContent,/Framework input/);
""")


def test_export_sends_only_purpose_and_projects_without_review_prompts():
    assert 'id="evidence"' not in PAGE
    assert 'id="content-approved"' not in PAGE
    assert 'id="context-approved"' not in PAGE
    assert 'id="rights-approved"' not in PAGE
    assert 'id="approve"' not in PAGE
    assert "SHA-256" not in PAGE
    run(r"""
await load();
openDrawer('export');
assert.equal(el('download').disabled,false);
let exported,pathUsed;fetch=async(path,options)=>{pathUsed=path;exported=JSON.parse(options.body);return {
  ok:true,headers:{get:()=> 'application/zip'},blob:async()=>new Blob(['synthetic retained observations'])};};
await el('download').onclick();
assert.equal(pathUsed,'/admin/api/training/export-observations');
assert.deepEqual(exported,{purpose:'internal_training',projects:[{tenant_id:'tenant-one',sid:'project-one'}]});
assert.equal(exported.review,undefined);
assert.match(el('export-status').textContent,/包含未验收、未结束与失败/);
el('purpose').value='external_sharing';await el('purpose').onchange();
assert.equal(selected.size,0);assert.equal(el('download').disabled,true);
""")


def test_readonly_can_view_raw_roles_but_cannot_export():
    run(r"""
readonlyIdentity=true;await load();
assert.equal(document.querySelectorAll('.sample').length,5);
openDrawer('export');
assert.equal(el('download').disabled,true);
assert.ok(document.querySelectorAll('.project-choice').every(input=>input.disabled));
assert.equal(el('select-sample-project').disabled,true);
""")


def test_raw_pagination_merges_episode_events_and_keeps_quality_separate():
    run(r"""
rawResponse={...rawResponse,episodes:[{...engineer,events:engineer.events.slice(0,2)}],
  pagination:{has_more:true,next_cursor:'page-two'}};
const baseApi=api;api=async path=>path.includes('cursor=page-two')?
  {...rawResponse,episodes:[{...engineer,events:engineer.events.slice(1)},manager],
    pagination:{has_more:false,next_cursor:null}}:baseApi(path);
await load();
assert.equal(el('load-more-observations').hidden,false);
document.querySelectorAll('.role-card')[2].onclick();
assert.match(el('role-messages').textContent,/尚未保留对应返回/);
await el('load-more-observations').onclick();
assert.equal(document.querySelectorAll('.sample').length,2);
assert.equal(preview.candidates[0].episode.events.length,4);
assert.equal(el('load-more-observations').hidden,true);
assert.match(el('role-messages').textContent,/40000 checks passed/);
assert.equal(preview.candidates[1].episode.quality.state,'not_evaluated');
""")


def test_raw_read_failure_does_not_erase_collaboration_or_block_authorized_export():
    run(r"""
const baseApi=api;api=async path=>{if(path.includes('/observations/'))throw Error('temporary_read_failure');return baseApi(path);};
await load();
assert.equal(document.querySelectorAll('.project-open').length,1);
assert.equal(document.querySelectorAll('.role-card').length,4);
assert.equal(el('error').textContent,'');
assert.match(el('sample-preview-status').textContent,/temporary_read_failure/);
openDrawer('export');assert.equal(el('download').disabled,false);
""")


def test_scope_change_ignores_old_raw_response():
    run(r"""
const firstPage=deferred(),baseApi=api;
api=async path=>path.includes('/observations/')&&!path.includes('task_id=')?firstPage.promise:baseApi(path);
const loading=load();
for(let turn=0;turn<30&&!activeTask;turn++)await Promise.resolve();
el('observation-scope').value='task';await el('observation-scope').onchange();
firstPage.resolve(rawResponse);await loading;
assert.equal(observationScope,'task');
assert.deepEqual(preview.candidates.map(candidate=>candidate.episode.episode_id),[1,2]);
""")


def test_recovered_session_message_keeps_its_source_and_missing_observer_context_clear():
    run(r"""
const recovered={...manager,runtime:{recovery:{source:'pi_session_jsonl',
  provider_requests_available:false,tool_schemas_available:false}},events:[
  {id:'historical-message',sequence:0,kind:'session_message',payload:{messages:[
    {role:'assistant',content:'Actual retained historical manager message.'}]}}]};
rawResponse={...rawResponse,episodes:[recovered]};
await load();
document.querySelectorAll('.role-card')[0].onclick();
assert.match(el('role-messages').textContent,/历史会话消息/);
assert.match(el('role-messages').textContent,/Actual retained historical manager message/);
assert.match(el('role-messages').textContent,/从原始会话日志恢复；原始模型请求和工具定义未保留/);
assert.match(el('sample-badges').textContent,/历史会话已恢复/);
assert.match(el('sample-badges').textContent,/观察器工具轨迹未保留/);
assert.doesNotMatch(el('sample-badges').textContent,/已采集 · 已结束/);
assert.equal(toolCount(active),0);
""")
