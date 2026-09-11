"""Exercise the shipped operator JavaScript with synthetic DOM and API responses."""
import subprocess

from argus_skill.trial.admin_page import SCRIPT

DOM = r"""
import assert from 'node:assert/strict';
class Element {
  constructor(tag='div') {
    this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={};
    this.value=''; this.checked=false; this.disabled=false; this.hidden=false; this.className=''; this._text='';
  }
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this._text=''; this.children=items;}
  setAttribute(key, value) {this.attributes[key]=value;}
  set textContent(value) {this._text=String(value); this.children=[];}
  get textContent() {return this._text+this.children.map(item => typeof item==='string' ? item : item.textContent).join('');}
  click() {}
}
const elements = new Map(), listeners = new Map();
function descendants(item) {
  return [item, ...item.children.filter(value => typeof value !== 'string').flatMap(descendants)];
}
globalThis.document = {
  getElementById(id) {if(!elements.has(id)) elements.set(id,new Element()); return elements.get(id);},
  createElement: tag => new Element(tag), createTextNode: text => String(text),
  querySelectorAll(selector) {
    const [name,pseudo]=selector.slice(1).split(':');
    return [...elements.values()].flatMap(descendants).filter(item =>
      item.className.split(' ').includes(name) && (pseudo !== 'checked' || item.checked));
  }
};
globalThis.window = {addEventListener:(name, fn) => listeners.set(name,fn)};
globalThis.fetch = async () => {throw Error('Unexpected fetch');};
URL.createObjectURL = () => 'blob:synthetic'; URL.revokeObjectURL = () => {};
globalThis.setTimeout = () => 0;
const deferred = () => {let resolve; const promise=new Promise(fn=>resolve=fn); return {promise,resolve};};
"""


def javascript(scenario, *, script=SCRIPT, startup="refresh(); loadTrainingPreview();"):
    # Only omit startup I/O; all handlers and rendering are the real shipped code.
    source = script.rsplit(startup, 1)[0]
    result = subprocess.run(
        ["node", "--input-type=module"], input=DOM + source + scenario,
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_annotation_drafts_survive_pagination_project_switch_and_pending_save():
    javascript(r"""
const saved={user_goal:'Saved goal',first_deviation_event_id:null,satisfaction:'unknown',note:'Saved evidence'};
api=async path => path.includes('/replay?') ? {events:[],has_more:true,next_sequence:500} :
  path.endsWith('/annotation') ? saved : [];
await loadReplay('tenant-one','project-one');
el('goal').value='Draft goal'; el('annotation-note').value='Draft evidence'; el('goal').oninput();
await el('next').onclick();
assert.equal(el('goal').value,'Draft goal'); assert.equal(el('annotation-note').value,'Draft evidence');
await loadReplay('tenant-two','project-two');
assert.equal(el('goal').value,'Saved goal');
await loadReplay('tenant-one','project-one');
assert.equal(el('goal').value,'Draft goal');
let prevented=false;
listeners.get('beforeunload')({preventDefault(){prevented=true;}});
assert.equal(prevented,true);
const save=deferred(); let posted;
api=async (path,options) => {posted=JSON.parse(options.body); return save.promise;};
const pending=el('annotation').onsubmit({preventDefault(){}});
el('goal').value='New edit while saving'; el('goal').oninput();
save.resolve({}); await pending;
assert.equal(posted.user_goal,'Draft goal');
assert.equal(el('goal').value,'New edit while saving');
assert.match(el('annotation-status').textContent,/未保存/);
api=async ()=>({}); await el('annotation').onsubmit({preventDefault(){}});
prevented=false; listeners.get('beforeunload')({preventDefault(){prevented=true;}});
assert.equal(prevented,false);
""")


def test_dashboard_latest_filter_wins_and_displays_token_and_task_breakdown():
    javascript(r"""
const first=deferred(), latest=deferred(), requested=[];
api=async path => {
  if(path.includes('/dashboard?')) {requested.push(path); return path.includes('days=30&') ? latest.promise : first.promise;}
  return path.endsWith('/testers') ? {testers:[]} : {};
};
function dashboard(count) {return {summary:{message_active_accounts:count,task_active_accounts:2,compute_active_accounts:1,
  tokens_settled:300,tokens_reserved:400,tokens_uncertain:50,tokens_unattributed:null},
  accounts:[],internal_testing:{summary:{}},task_types:{},recent_task_requests:[],policy:{},generated_at:2000000000};}
el('days').value='1'; const oldRequest=refresh();
el('days').value='30'; el('internal').checked=true; const newRequest=refresh();
latest.resolve(dashboard(30)); await newRequest;
first.resolve(dashboard(1)); await oldRequest;
assert.equal(requested.length,2);
assert.ok(requested[1].includes('days=30&include_internal=true'));
assert.match(el('dashboard-status').textContent,/最近 30 天，含内部模拟/);
assert.match(el('metrics').textContent,/提交消息的账号30/);
assert.match(el('metrics').textContent,/任务已接受的账号2/);
assert.match(el('metrics').textContent,/token 已结算300/);
assert.match(el('metrics').textContent,/token 在途预留400/);
assert.match(el('metrics').textContent,/token 待核实50/);
assert.match(el('metrics').textContent,/token 未归因不可用/);
""")


def test_training_requires_selected_projects_and_real_tool_context_review_before_export():
    javascript(r"""
el('training-purpose').value='internal_training';
const toolId='a'.repeat(64), chatId='b'.repeat(64);
const tool={tenant_id:'tenant-one',sid:'tools',event_id:toolId,sample_complete:true,duplicate_of:chatId,
  context_review_required:true,scope:'fresh_pi_public_tool_episode',sample:{
  tools:[{type:'function',function:{name:'sum_numbers',parameters:{type:'object',properties:{a:{type:'integer'}}}}}],
  messages:[{role:'user',content:'Add two and three'},
    {role:'assistant',tool_calls:[{id:'call-real',type:'function',function:{name:'sum_numbers',arguments:{a:2,b:3}}}]},
    {role:'tool',tool_call_id:'call-real',content:'5'},{role:'assistant',content:'The sum is 5.'}]}};
const chat={tenant_id:'tenant-two',sid:'chat',event_id:chatId,sample:{messages:[{role:'user',content:'Other project'}]}};
const preview={offset:0,total_projects:21,next_offset:20,selection_limit:20,has_more_projects:true,
  projects:[{tenant_id:'tenant-one',sid:'tools',eligible:true},{tenant_id:'tenant-two',sid:'chat',eligible:true}],
  candidates:[tool,chat],counts:{projects:2,candidates:2,tool_candidates:1,sft:0,tool_sft:0}};
const urls=[]; api=async path=>{urls.push(path);return preview;};
await loadTrainingPreview();
assert.equal(el('training-download').disabled,true);
assert.equal(el('training-next').disabled,false);
const choices=document.querySelectorAll('.training-project-choice');
choices[0].checked=true; choices[0].onchange();
const candidates=document.querySelectorAll('.training-candidate');
assert.equal(candidates[0].hidden,false); assert.equal(candidates[1].hidden,true);
assert.match(candidates[0].textContent,/sum_numbers/);
assert.match(candidates[0].textContent,/"parameters"/);
assert.match(candidates[0].textContent,/"a": 2/);
assert.match(candidates[0].textContent,/Add two and three/);
const reviews=document.querySelectorAll('.training-event-review');
assert.equal(reviews[0].disabled,false); assert.equal(reviews[1].disabled,true);
reviews[0].checked=true; reviews[0].onchange();
el('training-content').checked=true; el('training-content').onchange();
assert.equal(el('training-download').disabled,true);
el('training-tool-context').checked=true; el('training-tool-context').onchange();
assert.equal(el('training-download').disabled,false);
// Even an accidentally checked hidden candidate cannot enter another project's review.
reviews[1].checked=true;
let exported;
fetch=async (path,options)=>{exported=JSON.parse(options.body);return {
  ok:true,headers:{get:()=> 'application/zip'},blob:async()=>new Blob(['synthetic zip'])};};
await el('training-download').onclick();
assert.deepEqual(exported.projects,[{tenant_id:'tenant-one',sid:'tools'}]);
assert.deepEqual(exported.review.approved_event_ids,[toolId]);
assert.equal(exported.review.tool_context_approved,true);
assert.equal(typeof exported.review.tool_context_approved,'boolean');
assert.equal(exported.review.reviewer_kind,'human_operator');
await el('training-next').onclick();
assert.ok(urls.at(-1).includes('offset=20'));
assert.equal(el('training-content').checked,false);
assert.equal(el('training-tool-context').checked,false);
assert.equal(el('training-download').disabled,true);
el('training-tenant').value='tenant-two'; el('training-query').value='chat';
await el('training-query').onchange();
assert.ok(urls.at(-1).includes('tenant=tenant-two'));
assert.ok(urls.at(-1).includes('query=chat'));
assert.ok(urls.at(-1).includes('offset=0'));
""")


def test_training_stale_same_purpose_preview_does_not_replace_latest_filters():
    javascript(r"""
el('training-purpose').value='internal_training';
const first=deferred(), second=deferred();
api=async path=>path.includes('query=latest') ? second.promise : first.promise;
const older=loadTrainingPreview(); el('training-query').value='latest'; const newer=loadTrainingPreview();
const data={projects:[],candidates:[],counts:{projects:0,candidates:0,sft:0,tool_sft:0},
  total_projects:0,offset:0,has_more_projects:false,selection_limit:20};
second.resolve(data); await newer;
first.resolve({...data,total_projects:99}); await older;
assert.match(el('training-page').textContent,/匹配 0 个项目/);
""")


def test_replay_summary_uses_visible_payload_text_and_keeps_it_as_text():
    javascript(r"""
showRecord({events:[{id:'synthetic-id',sequence:1,kind:'http.response',
  payload:{result:{reply:'Visible <script>example</script> answer'}}}]},'Synthetic project');
const heading=el('trace').children[0].children[0];
assert.match(heading.textContent,/http.response/);
assert.match(heading.textContent,/Visible <script>example<\/script> answer/);
assert.equal(heading.children.length,0);
""")


def test_data_workbench_browses_sources_filters_formats_and_exports_reviewed_tools():
    from argus_skill.trial.data_page import SCRIPT as DATA_SCRIPT

    javascript(r"""
el('purpose').value='internal_training';el('sample-kind').value='all';el('format').value='hf';
const candidate={tenant_id:'tenant-one',sid:'engineering',task_id:'task-optimize',event_id:'a'.repeat(64),
  event_ids:['b'.repeat(64)],sample_complete:true,global_complete:false,runtime:{model:'synthetic-model'},
  source:{session_id:'session-real'},sample:{tools:[{type:'function',function:{name:'calculate',parameters:{type:'object'}}}],
  messages:[{role:'user',content:'Optimize and verify'},
    {role:'assistant',tool_calls:[{id:'call-one',function:{name:'calculate',arguments:{size:100}}}]},
    {role:'tool',tool_call_id:'call-one',name:'calculate',content:'100 verified'},
    {role:'assistant',content:'Result verified.'}]}};
const data={offset:0,selection_limit:20,total_projects:1,has_more_projects:false,
  projects:[{tenant_id:'tenant-one',sid:'engineering',title:'工程优化：加权区间调度',eligible:true}],candidates:[candidate],
  counts:{projects:1,candidates:1,tool_candidates:1,sft:0,quarantined:1},
  reason_counts:{unmatched_tool_call_result:1},diagnostics:[{event_id:'quarantined-only',reason:'unmatched_tool_call_result'}],
  capture_status:{states:[{state:'complete',count:1}]}};
api=async path=>path.includes('/preview?')?data:path==='/invite/status'?{role:'admin',readonly:false}:
  path.endsWith('/audit')?{events:[]}:{state:'running'};
await load();
assert.equal(document.querySelectorAll('.sample').length,1);
assert.doesNotMatch(el('samples').textContent,/quarantined-only/);
assert.match(el('diagnostics').textContent,/quarantined-only/);
assert.match(el('projects').textContent,/工程优化：加权区间调度/);
assert.match(el('projects').textContent,/engineering · tenant-one/);
assert.match(el('projects').textContent,/task-optimize/);
const project=document.querySelectorAll('.project-choice')[0];project.checked=true;project.onchange();
await document.querySelectorAll('.sample')[0].onclick();
assert.equal(el('sample-title').textContent,'工程优化：加权区间调度');
assert.match(el('sample-identity').textContent,/engineering · tenant-one · 任务 task-optimize/);
assert.match(el('messages').textContent,/call-one/);assert.match(el('messages').textContent,/100 verified/);
assert.match(el('tools').textContent,/parameters/);assert.match(el('source').textContent,/synthetic-model/);
assert.match(el('source').textContent,/task-optimize/);
el('format').value='portable';el('format').onchange();
let portable=JSON.parse(el('formats').textContent);
assert.equal(portable.messages[1].tool_calls[0].function.arguments,'{"size":100}');
assert.equal(portable.messages[2].name,undefined);
el('task-query').value='different';el('task-query').oninput();
assert.equal(document.querySelectorAll('.sample').length,0);
el('task-query').value='optimize';el('task-query').oninput();
document.querySelectorAll('.sample')[0].onclick();
el('approve').checked=true;el('approve').onchange();
el('content-approved').checked=true;el('content-approved').oninput();assert.equal(el('download').disabled,true);
el('context-approved').checked=true;el('context-approved').oninput();assert.equal(el('download').disabled,false);
let exported;fetch=async(path,options)=>{exported=JSON.parse(options.body);return {
  ok:true,headers:{get:()=> 'application/zip'},blob:async()=>new Blob(['synthetic'])};};
await el('download').onclick();
assert.equal(exported.review.reviewer_kind,'human_operator');
assert.equal(exported.review.tool_context_approved,true);
assert.deepEqual(exported.review.approved_event_ids,[candidate.event_id]);
assert.deepEqual(exported.projects,[{tenant_id:'tenant-one',sid:'engineering'}]);
candidate.quality_approved=true;
candidate.quality_evidence={persisted:true,reviewer_kind:'automated_acceptance',human_reviewed:false,evidence_sha256:'c'.repeat(64)};
await load();
const refreshedProject=document.querySelectorAll('.project-choice')[0];
refreshedProject.checked=true;refreshedProject.onchange();
assert.match(el('review-summary').textContent,/1 条已有有效审阅凭据/);
document.querySelectorAll('.sample')[0].onclick();
assert.match(el('sample-state').textContent,/已有明确质量依据/);
assert.match(el('source').textContent,/automated_acceptance/);
""", script=DATA_SCRIPT, startup="load();")
