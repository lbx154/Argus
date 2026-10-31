import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { request as httpRequest } from 'node:http';
import { previewFetch } from './helpers/preview-fetch.js';
import { appendFile, chmod, mkdir, mkdtemp, readdir, readFile, rm, stat, symlink, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import corePackage from '../package.json' with { type: 'json' };
import { inspectApiMeta, REQUIRED_API_CAPABILITIES, requireSnapshotContract, type ConversationPage, type EventMsg, type Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent, HandOff, Role } from '../src/types.js';
import { SKILLS } from '../src/prompts.js';
import { Store } from '../src/store.js';
import { agentEnv } from '../src/agent/sandbox.js';
import { serve } from '../src/serve/index.js';
import { follow, readEvents, webEvents, type Recorded } from '../src/serve/views.js';

/** One scripted turn: the role expected, its letter (no `to`: none), and what it does first. */
interface Step {
  role: Role;
  to?: string;
  letter?: string;
  /** Files the turn writes into the workdir. */
  files?: Record<string, string>;
  /** The turn lasts until it is interrupted. */
  hang?: boolean;
  /** The turn ends once this settles, or it is interrupted. */
  until?: Promise<unknown>;
  /** What the role says as its turn goes on. */
  says?: string;
  progress?: import('../src/types.js').Step[];
  open?: string[];
}

function agent(steps: Step[]): { callAgent: CallAgent; requests: AgentRequest[]; closed: string[] } {
  const requests: AgentRequest[] = [];
  /** The sessions ended, by the prefix of their keys. */
  const closed: string[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    const step = steps.shift();
    assert.ok(step, `no turn scripted for the ${request.role}`);
    assert.equal(request.role, step.role);
    for (const [name, body] of Object.entries(step.files ?? {})) await writeFile(join(request.cwd, name), body);
    if (step.says) request.onStep?.({ kind: 'message', text: step.says });
    for (const progress of step.progress ?? []) request.onStep?.(progress);
    const interrupted = new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }));
    await Promise.race([step.until, interrupted]);
    if (step.hang) {
      await interrupted;
      return { callId: 'c', threadId: null, completed: false, error: 'interrupted', handoff: null, model: 'fake', usage: null, ms: 1 };
    }
    const handoff: HandOff | null = step.to === undefined ? null
      : { form: request.form, to: step.to, letters: [step.letter ?? `${step.role} to ${step.to}`], ...step.open ? { open: step.open } : {} };
    const tokens = { input_tokens: 10, cached_input_tokens: 0, output_tokens: 5, reasoning_output_tokens: 0 };
    const callId = `call-${requests.length}`;
    await appendFile(request.usageFile, `${JSON.stringify({ ts: Date.now() / 1000, callId, role: request.role, backend: request.backend, model: request.model ?? 'fake-model', tokens, cost_usd: 0.01 })}\n`);
    return {
      callId, threadId: `${request.role}-${requests.length}`, completed: true,
      error: handoff ? null : 'The turn ended without the hand-off form.', handoff, model: request.model ?? 'fake-model', usage: { ...tokens, cost_usd: 0.01 }, ms: 1,
    };
  };
  return { callAgent: Object.assign(callAgent, { close: async (prefix: string) => { closed.push(prefix); } }), requests, closed };
}

async function setup(t: TestContext, steps: Step[], token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-serve-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(join(web, 'assets'), { recursive: true });
  await writeFile(join(web, 'index.html'), '<!doctype html><title>Argus</title><script type="module" crossorigin src="/assets/app-1.js"></script>');
  await writeFile(join(web, 'assets', 'app-1.js'), 'export {};');
  const store = new Store(home);
  const { callAgent, requests, closed } = agent(steps);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web, ...(token ? { token } : {}) });
  t.after(() => served.close());
  const headers = token ? { authorization: `Bearer ${token}` } : {};
  const call = (path: string, init: RequestInit = {}) => fetch(served.url + path, { ...init, headers: { ...headers, ...init.headers } });
  const get = async <T = Record<string, unknown>>(path: string): Promise<T> => {
    const response = await call(path);
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return await response.json() as T;
  };
  const post = (path: string, body: unknown) =>
    call(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { home, store, requests, closed, call, get, post, url: served.url };
}

/** Every `data:` frame of a server-sent event stream, a record's naming its line as its id;
 * `until` stops reading an endless one. */
async function frames(response: Response, until?: (frame: EventMsg) => boolean): Promise<EventMsg[]> {
  const read: EventMsg[] = [];
  const decoder = new TextDecoder();
  let buffer = '';
  for await (const chunk of response.body!) {
    buffer += decoder.decode(chunk, { stream: true });
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const [, id, data] = /^(?:id: (.*)\n)?data: (.*)$/s.exec(buffer.slice(0, end))!;
      const frame = JSON.parse(data!) as EventMsg;
      assert.equal(id, frame.event_id === undefined ? undefined : String(frame.event_id));
      buffer = buffer.slice(end + 2);
      read.push(frame);
      if (until?.(frame)) return read;
    }
  }
  return read;
}

async function eventually<T>(read: () => Promise<T>, done: (value: T) => boolean): Promise<T> {
  for (let tries = 0; ; tries++) {
    const value = await read();
    if (done(value)) return value;
    assert.ok(tries < 200, 'timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

const brief = 'Summarize the CSV.\nreport.md gives the column totals.';

test('credentials preserve UTF-8 across real HTTP chunks and large fetch bodies', async t => {
  const { home, url, call } = await setup(t, [], 'chunk-token');
  const previous = process.env.ARGUS_HOME;
  process.env.ARGUS_HOME = home;
  t.after(() => {
    if (previous === undefined) delete process.env.ARGUS_HOME;
    else process.env.ARGUS_HOME = previous;
  });
  const literal = '  before 中 😀\n中文\t after  ';
  const body = Buffer.from(JSON.stringify({ name: 'HF_TOKEN', value: literal }));
  // Replay the reported split inside E4 B8 AD, at both possible byte boundaries.
  for (const offset of [1, 2]) {
    const split = body.indexOf(Buffer.from('中')) + offset;
    const response = await new Promise<{ status: number; body: string }>((resolve, reject) => {
      const request = httpRequest(`${url}/api/settings/credentials`, {
        method: 'PUT', headers: { authorization: 'Bearer chunk-token', 'content-type': 'application/json', 'transfer-encoding': 'chunked' },
      }, response => {
        let text = '';
        response.setEncoding('utf8');
        response.on('data', chunk => { text += chunk; });
        response.on('error', reject);
        response.on('end', () => resolve({ status: response.statusCode!, body: text }));
      });
      request.on('error', reject);
      request.write(body.subarray(0, split));
      // The gap makes these separate HTTP reads instead of coalesced writes.
      setTimeout(() => request.end(body.subarray(split)), 30);
    });
    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(response.body), { names: ['HF_TOKEN'] });
    assert.equal(JSON.parse(await readFile(join(home, 'credentials.json'), 'utf8')).HF_TOKEN, literal);
    for (const backend of ['codex', 'claude', 'copilot', 'pi'] as const) assert.equal(agentEnv(backend).HF_TOKEN, literal);
  }
  const large = '中'.repeat(65_000);
  const saved = await call('/api/settings/credentials', {
    method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'WANDB_API_KEY', value: large }),
  });
  assert.equal(saved.status, 200);
  assert.deepEqual(await saved.json(), { names: ['HF_TOKEN', 'WANDB_API_KEY'] });
  assert.equal(JSON.parse(await readFile(join(home, 'credentials.json'), 'utf8')).WANDB_API_KEY, large);
  for (const backend of ['codex', 'claude', 'copilot', 'pi'] as const) assert.equal(agentEnv(backend).WANDB_API_KEY, large);
});

test('authenticated settings keep literal credentials in one private file and only return names', async t => {
  const { home, call, get, store } = await setup(t, [], 'settings-token');
  const path = '/api/settings/credentials';
  const previous = { ...process.env };
  Object.assign(process.env, {
    ARGUS_HOME: home, HF_TOKEN: 'ambient-hf', WANDB_API_KEY: 'ambient-wandb', UNREGISTERED_SECRET: 'filtered',
    ANTHROPIC_API_KEY: 'claude-login', OPENAI_API_KEY: 'codex-login', COPILOT_GITHUB_TOKEN: 'copilot-login',
  });
  t.after(() => { process.env = previous; });
  for (const method of ['GET', 'PUT', 'DELETE']) {
    const refused = await call(method === 'DELETE' ? `${path}/HF_TOKEN` : path, { method, headers: { authorization: '' } });
    assert.equal(refused.status, 401);
  }
  assert.deepEqual(await get(path), { names: [] });
  const literal = '  hf fixture\n"quoted" \\ 中文\t ';
  const save = async (name: string, value: unknown) => {
    const response = await call(path, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, value }) });
    assert.equal(response.status, 200);
    return response.json();
  };
  assert.deepEqual(await save('HF_TOKEN', literal), { names: ['HF_TOKEN'] });
  assert.deepEqual(await save('WANDB_API_KEY', '  wandb fixture  '), { names: ['HF_TOKEN', 'WANDB_API_KEY'] });
  assert.equal((await call(path)).headers.get('cache-control'), 'no-store');
  const file = join(home, 'credentials.json');
  assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), { HF_TOKEN: literal, WANDB_API_KEY: '  wandb fixture  ' });
  if (process.platform !== 'win32') assert.equal((await stat(file)).mode & 0o777, 0o600);
  for (const backend of ['codex', 'claude', 'copilot', 'pi'] as const) {
    const env = agentEnv(backend);
    assert.equal(env.HF_TOKEN, literal);
    assert.equal(env.WANDB_API_KEY, '  wandb fixture  ');
    assert.equal(env.UNREGISTERED_SECRET, undefined);
  }
  assert.equal(agentEnv('codex').OPENAI_API_KEY, 'codex-login');
  assert.equal(agentEnv('claude').ANTHROPIC_API_KEY, 'claude-login');
  assert.equal(agentEnv('copilot').COPILOT_GITHUB_TOKEN, 'copilot-login');
  assert.equal(agentEnv('pi').ANTHROPIC_API_KEY, 'claude-login');
  assert.equal(agentEnv('codex').ANTHROPIC_API_KEY, undefined);
  assert.equal(agentEnv('claude').OPENAI_API_KEY, undefined);
  if (process.platform !== 'win32') await chmod(file, 0o644);
  await save('HF_TOKEN', '');
  if (process.platform !== 'win32') assert.equal((await stat(file)).mode & 0o777, 0o600);
  assert.equal(agentEnv('codex').HF_TOKEN, '');
  await save('__proto__', 'literal-property');
  assert.equal(agentEnv('codex').__proto__, 'literal-property');
  await call(`${path}/__proto__`, { method: 'DELETE' });
  for (const body of [{ name: 'BAD-NAME', value: literal }, { name: ' KEY', value: literal }, { name: 'KEY', value: 42 }, { name: 'KEY', value: '\0' }]) {
    const response = await call(path, { method: 'PUT', body: JSON.stringify(body) });
    assert.equal(response.status, 400);
    assert.equal((await response.text()).includes('hf fixture'), false);
  }
  assert.deepEqual(await (await call(`${path}/HF_TOKEN`, { method: 'DELETE' })).json(), { names: ['WANDB_API_KEY'] });
  assert.equal(agentEnv('codex').HF_TOKEN, undefined);
  await call(`${path}/WANDB_API_KEY`, { method: 'DELETE' });
  assert.equal(agentEnv('codex').WANDB_API_KEY, undefined);
  assert.deepEqual(await get(path), { names: [] });
  assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), {});
  assert.deepEqual((await readdir(home)).filter(name => name.includes('credentials')), ['credentials.json']);
  const work = store.create({ request: '', workdir: '', backend: 'codex', model: null });
  assert.equal((await readFile(join(store.dir(work.id), 'project.json'), 'utf8')).includes('wandb fixture'), false);
  await writeFile(file, '{"HF_TOKEN":"private-value', 'utf8');
  const unreadable = await call(path);
  assert.equal(unreadable.status, 500);
  assert.equal((await unreadable.text()).includes('private-value'), false);
});

test('a recorded request is accepted again without another message or Manager call, running and after restart', async t => {
  const original=JSON.parse(await readFile(new URL('./fixtures/message-identity.jsonl',import.meta.url),'utf8')) as {text:string;request_id:string};
  const requestId=original.request_id;
  let finish!: () => void;const until=new Promise<void>(resolve => {finish=resolve;});
  const {store,post,get,requests}=await setup(t,[{role:'manager',to:'operator',letter:'Done once.',until}]);
  t.after(()=>finish());const work=store.create({request:'',workdir:'',backend:'codex',model:null});
  const path=`/api/projects/${work.id}/message/stream`;
  const first=frames(await post(path,{text:original.text,request_id:requestId}));
  await eventually(async()=>requests.length,number=>number===1);
  const duplicate=await frames(await post(path,{text:original.text,request_id:requestId}));
  assert.ok(duplicate.some(frame=>frame.type==='phase'&&frame.accepted===true));
  assert.deepEqual(duplicate.at(-1)!.result,{kind:'received',accepted:true,request_id:requestId,status:'running',reply:'已收下'});
  const queuedDuplicate=await (await post(`/api/projects/${work.id}/message/followup`,{text:original.text,request_id:requestId})).json() as {accepted:boolean;status:string};
  assert.equal(queuedDuplicate.accepted,true);assert.equal(queuedDuplicate.status,'running');
  assert.equal(requests.length,1);
  finish();await first;
  const repeated=await frames(await post(path,{text:'A different body cannot change an existing identity',request_id:requestId}));
  assert.equal((repeated.at(-1)!.result as {status:string}).status,'replied');
  const messages=readEvents(store,work.id).filter(event=>event.type==='message'&&event.request_id===requestId);
  assert.equal(messages.length,1);assert.equal(requests.length,1);
  const restarted=await serve({store,callAgent:async()=>{throw new Error('Duplicate started a Manager');},backend:'codex',model:null,port:0,web:store.home});
  t.after(()=>restarted.close());
  const replay=await frames(await fetch(`${restarted.url}${path}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:original.text,request_id:requestId})}));
  assert.equal((replay.at(-1)!.result as {kind:string}).kind,'received');
  assert.equal(readEvents(store,work.id).filter(event=>event.type==='message'&&event.request_id===requestId).length,1);
  assert.equal((await get<ConversationPage>(`/api/projects/${work.id}/conversation`)).rows.filter(row=>row.type==='ui.operator').length,1);
});

test('simultaneous POSTs with one identity record one message and call the Manager once', async t => {
  let finish!: () => void;const until=new Promise<void>(resolve=>{finish=resolve;});
  const {store,post,get,requests}=await setup(t,[{role:'manager',to:'operator',letter:'One reply',until}]);
  t.after(()=>finish());const work=store.create({request:'',workdir:'',backend:'codex',model:null});
  const path=`/api/projects/${work.id}/message/stream`,body={text:'Shared draft',request_id:'shared-request'};
  const responses=await Promise.all([post(path,body),post(path,body)]);
  for(const response of responses){const accepted=await frames(response,frame=>frame.type==='phase');assert.equal(accepted[0]!.accepted,true);}
  await eventually(async()=>requests.length,number=>number===1);finish();
  await eventually(()=>get<Snapshot>(`/api/projects/${work.id}/snapshot`),snap=>!snap.daemon.alive);
  assert.equal(readEvents(store,work.id).filter(event=>event.type==='message'&&event.request_id===body.request_id).length,1);
  assert.equal(requests.length,1);
});

test('a legacy project keeps its original name after resetting the Manager and first delegating a follow-up', async t => {
  const record = JSON.parse(await readFile(new URL('./fixtures/project-name/record.json', import.meta.url), 'utf8')) as {
    request: string; followup: string; task_title: string;
  };
  const { store, requests, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: record.task_title },
    { role: 'engineer', to: 'operator', letter: '请确认图表格式。' },
  ]);
  // The old Manager delivered the initial request itself: no stored name or tasks.
  const project = store.create({ workdir: '', request: record.request, backend: 'codex', model: null });
  project.sessions.manager = 'legacy-manager';
  store.save(project);
  const P = `/api/projects/${project.id}`;
  const snapshot = () => get<Snapshot>(`${P}/snapshot`);
  assert.equal((await snapshot()).session.display_name, record.request);
  assert.equal((await post(`${P}/reset`, {})).status, 200);
  assert.deepEqual(store.load(project.id).sessions, {});
  const sent = await post(`${P}/message/stream`, { text: record.followup, request_id: 'followup-after-reset' });
  assert.equal(sent.status, 200);
  await sent.text();
  const settled = await eventually(snapshot, value => !value.daemon.alive);
  assert.equal(settled.backlog[0]?.title, record.task_title);
  assert.equal(settled.session.display_name, record.request);
  const rows = await get<{ projects: { id: string; display_name: string }[] }>('/api/projects');
  assert.equal(rows.projects.find(row => row.id === project.id)?.display_name, record.request);
  assert.equal(requests[0]?.resume, undefined);
});

test('the cockpit runs a project: handshake, message, progress, question, answer, results', async t => {
  const { home, get, post, call, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', to: 'reviewer', letter: 'report.md written', says: 'Totalling the columns.',
      files: { 'report.md': '# Totals\n\n| a | b |\n', 'page.html': '<script>alert(1)</script>', 'data.bin': '0123456789', '.hidden': 'secret' } },
    { role: 'reviewer', to: 'operator', letter: 'Ship it as is?' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    { role: 'manager', to: 'operator', letter: 'report.md gives the totals.' },
  ]);

  const metaResponse = await call('/api/meta');
  assert.equal(metaResponse.headers.get('x-argus-release'), null);
  const compatibility = inspectApiMeta(await metaResponse.json());
  assert.equal(compatibility.compatible, true, compatibility.reason);
  assert.equal(compatibility.meta?.service, 'argus-webapi');
  assert.equal(compatibility.meta?.runtime.package_version, corePackage.version);
  assert.deepEqual(Object.keys(compatibility.meta!.runtime).sort(), [
    'package_version', 'pid', 'started_at', 'source_root', 'executable', 'state_root',
  ].sort());
  assert.deepEqual(compatibility.meta?.authentication, { required: false, authenticated: true });
  // Only what this server has, so the page hides the rest: settings, the machine's resources, slash commands.
  assert.deepEqual(compatibility.meta?.capabilities, [
    ...REQUIRED_API_CAPABILITIES, 'project.attachments.v1', 'system.resources.v1', 'project.workdir.v1', 'project.config.v1',
    'project.editions.v1',
  ]);
  assert.equal((await get('/api/application-brand')).id, 'argus');

  const created = await (await post('/api/daemons', { objective: '', name: '', workdir: '' })).json() as { sid: string; rc: number; workdir: string };
  assert.equal(created.rc, 0);
  const sid = created.sid;
  const P = `/api/projects/${sid}`;
  assert.deepEqual((await get<{ projects: { id: string }[] }>('/api/projects')).projects.map(row => row.id), [sid]);

  const live = new AbortController();
  t.after(() => live.abort());
  const stream = frames(await call(`${P}/stream?replay=40&view=ui`, { signal: live.signal }), frame => frame.type === 'life.mission.completed');

  const reply = await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r1' }));
  assert.deepEqual(reply.map(frame => frame.type), ['phase', 'delta', 'done']);
  // The operator reads the Manager's letter to the team: the brief is its reply.
  // The reply is its row in the record, as the conversation shows it after a reload: whom it is to, and the message it answers.
  assert.deepEqual(reply[1], { type: 'delta', event: { type: 'ui.argus', ts: (reply[1]!.event as EventMsg).ts, event_id: 3,
    agent_layer: 'manager', to: 'engineer', text: brief, message_id: 'm0-argus', request_id: 'r1', row_id: 'm0-argus', order: 0.1 } });
  assert.deepEqual(reply[2], { type: 'done', result: { kind: 'task', reply: brief } });

  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot`),
    snapshot => Boolean(snapshot.pending_questions?.length) && !snapshot.daemon.alive);
  requireSnapshotContract(asking);
  assert.equal(asking.schema_version, 8);
  for (const field of ['recent_events', 'daemon_commands', 'observability', 'mission_view', 'partial', 'diagnostics',
    'global_spend_usd', 'global_spend_status', 'global_usage_summary', 'daemon_admission', 'continuous', 'storage']) {
    assert.equal(Object.hasOwn(asking, field), false, field);
  }
  for (const field of ['read_status', 'read_error', 'protocol_compatible', 'protocol_error',
    'liveness_source', 'heartbeat_age_seconds', 'health', 'mission_width', 'protocol', 'capabilities', 'runtime']) {
    assert.equal(Object.hasOwn(asking.daemon, field), false, field);
  }
  assert.equal(asking.session.objective, 'Total the columns of data.csv');
  assert.deepEqual(asking.backlog.map(item => [item.id, item.status]), [['t1', 'blocked']]);
  assert.equal(asking.usage_summary?.call_count, 3);
  assert.equal(asking.spend_usd, 0.03);
  // The first call's model is saved and passed to each later role.
  assert.deepEqual([asking.roles.find(role => role.role === 'engineer')?.model, asking.last_call?.role, asking.last_call?.model], ['fake-model', 'reviewer', 'fake-model']);
  assert.equal('role_events' in asking, false);
  assert.equal('role_round' in asking, false);
  assert.equal(asking.work_status?.label['zh-CN'], '等你回答');
  const card = (asking.pending_questions![0] as { operator_decision: { id: string; question: string; options: unknown[] } }).operator_decision;
  assert.deepEqual([card.question, card.options], ['Ship it as is?', []]);

  assert.equal((await call(`${P}/events?limit=120&view=ui`)).status, 404);
  const replay = new AbortController();
  const events = await frames(await call(`${P}/stream?replay=40&view=ui`, { signal: replay.signal }), event => event.type === 'engineer.progress' && event.agent_layer === 'reviewer');
  replay.abort();
  assert.deepEqual(events.map(event => event.type), [
    'ui.operator', 'provider.request.started', 'provider.request.completed', 'ui.argus', 'life.mission.started',
    'provider.request.started', 'engineer.progress', 'provider.request.completed', 'engineer.progress',
    'provider.request.started', 'provider.request.completed', 'engineer.progress',
  ]);
  // What the Engineer said as its turn went on is in the feed while it works.
  assert.deepEqual(events.find(event => event.type === 'engineer.progress'), { ...events.find(event => event.type === 'engineer.progress'),
    kind: 'agent_message', agent_layer: 'engineer', text: 'Totalling the columns.', item_id: 't1' });
  const { rows } = await get<{ rows: EventMsg[] }>(`${P}/conversation?limit=120`);
  assert.deepEqual(rows.map(row => row.type), ['ui.operator', 'ui.argus', 'ui.task', 'ui.question']);
  assert.deepEqual(rows[1], reply[1]!.event);

  assert.equal((await post(`${P}/decisions/q-stale/resolve`, { option_id: 'custom', note: 'yes' })).status, 409);
  assert.equal((await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: ' ' })).status, 400);
  assert.deepEqual(await (await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: 'Ship it today' })).json(), { resolved: true });
  const done = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done' && !snapshot.daemon.alive);
  assert.deepEqual(done.pending_questions, []);
  // Each read is stamped later than the one before, so a page keeps the newer of two.
  assert.ok((await get<Snapshot>(`${P}/snapshot`)).read_at! > done.read_at!);
  assert.match(requests.at(-2)!.prompt, /They answered: Ship it today/);
  // The Manager's report, written after the team's work, is in the conversation too.
  assert.deepEqual((await get<{ rows: { text: string }[] }>(`${P}/conversation?limit=120`)).rows.at(-1)?.text, 'report.md gives the totals.');

  const streamed = await stream;
  live.abort();
  assert.deepEqual(streamed.map(event => event.type).slice(0, 2), ['ui.operator', 'provider.request.started']);
  assert.equal(streamed.at(-1)?.item_id, 't1');

  const { artifacts } = await get<{ artifacts: { path: string; kind: string }[] }>(`${P}/artifacts`);
  assert.deepEqual(artifacts.map(artifact => artifact.path).sort(), ['data.bin', 'page.html', 'report.md']);
  const report = await get(`${P}/artifact?path=report.md`);
  assert.equal(report.kind, 'markdown');
  assert.equal(report.preview, '# Totals\n\n| a | b |\n');
  const part = await call(`${P}/artifact/raw?path=data.bin`, { headers: { range: 'bytes=2-5' } });
  assert.equal(part.status, 206);
  assert.equal(part.headers.get('content-range'), 'bytes 2-5/10');
  assert.equal(await part.text(), '2345');
  // A page is sent as what it is, sandboxed: a script in it runs in no origin.
  const download = await call(`${P}/artifact/raw?path=page.html&download=true`);
  assert.deepEqual([download.headers.get('content-type'), download.headers.get('content-security-policy')], ['text/html; charset=utf-8', 'sandbox']);
  assert.equal(download.headers.get('content-disposition'), "attachment; filename*=UTF-8''page.html");
  // Outside the workdir by either separator or an absolute path; hidden, also by its Windows short name.
  const record = join(home, 'projects', sid, 'project.json');
  for (const outside of [`../../projects/${sid}/project.json`, `..\\..\\projects\\${sid}\\project.json`, record, '.hidden', 'HIDDEN~1']) {
    assert.equal((await call(`${P}/artifact/raw?path=${encodeURIComponent(outside)}`)).status, 404, outside);
  }
});

test('each open question is a card of its own, and its answer reaches only its asker', async t => {
  const { get, post, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Total the columns of data.csv' },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?' },
    { role: 'manager', to: 'engineer', letter: 'Total the columns of data2.csv' },
    { role: 'engineer', to: 'operator', letter: 'Which encoding?' },
    { role: 'engineer', to: 'reviewer', letter: 'data2.csv totalled' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    { role: 'manager', to: 'operator', letter: 'data2.csv is totalled; data.csv waits for your answer.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  type Card = { operator_decision: { id: string; question: string } };
  const asked = (count: number) => eventually(() => get<Snapshot>(`${P}/snapshot`),
    snapshot => snapshot.pending_questions?.length === count && !snapshot.daemon.alive);
  await frames(await post(`${P}/message/stream`, { text: 'Total data.csv', request_id: 'r1' }));
  await asked(1);
  await frames(await post(`${P}/message/stream`, { text: 'Total data2.csv too', request_id: 'r2' }));
  const cards = ((await asked(2)).pending_questions as Card[]).map(card => card.operator_decision);
  assert.deepEqual(cards.map(card => card.question), ['Which delimiter?', 'Which encoding?']);
  await post(`${P}/decisions/${cards[1]!.id}/resolve`, { option_id: 'custom', note: 'UTF-8' });
  const rest = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[1]?.status === 'done' && !snapshot.daemon.alive);
  assert.deepEqual([rest.backlog[0]?.status, (rest.pending_questions as Card[]).map(card => card.operator_decision.id)], ['blocked', [cards[0]!.id]]);
  assert.equal(requests[4]?.prompt, '## From the operator\nYou asked the operator: Which encoding?\nThey answered: UTF-8');
  assert.equal((await post(`${P}/decisions/${cards[1]!.id}/resolve`, { option_id: 'custom', note: 'again' })).status, 409);
});

test('a refused form goes back to the Manager within the same message', async t => {
  const { post } = await setup(t, [{ role: 'manager' }, { role: 'manager', to: 'operator', letter: 'Hello.' }]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const reply = await frames(await post(`/api/projects/${sid}/message/stream`, { text: 'Hi', request_id: 'r1' }));
  assert.deepEqual(reply.map(frame => frame.type), ['phase', 'delta', 'done']);
  assert.deepEqual(reply.at(-1), { type: 'done', result: { kind: 'chat', reply: 'Hello.' } });
});

interface MapTask { id: string; title: string; status: string; role: string; ts: number; finished_ts: number | null;
  latest_action?: import('@argus/contracts').BacklogItem['latest_action']; result_paths: string[] }
interface MapLetter { id: string; item_id: string; type: string; role: string; to: string; text: string }
interface ProgressMap { id: string; tasks: MapTask[]; events: MapLetter[]; cursor: string }
interface Trace { task_id: string; groups: { call_id: string; role: string; entries: { kind: string; text: string }[] }[] }

test('map updates carry each task’s holder, latest words and only the Manager’s first selected file', async t => {
  let releaseEngineer = (): void => {};
  let releaseReviewer = (): void => {};
  const engineer = new Promise<void>(resolve => { releaseEngineer = resolve; });
  const reviewer = new Promise<void>(resolve => { releaseReviewer = resolve; });
  t.after(() => { releaseEngineer(); releaseReviewer(); });
  const { store, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', to: 'reviewer', letter: 'Ready to check.', until: engineer, says: 'Totalling the columns.',
      progress: [{ kind: 'command', text: 'node totals.mjs' }], open: ['draft.md'], files: { 'draft.md': '# Draft' } },
    { role: 'reviewer', to: 'manager', letter: 'Accepted. Ignore guessed.md in this letter.', until: reviewer,
      says: 'Checking the totals.', open: ['report.md', 'data.csv'], files: { 'report.md': '# Totals', 'data.csv': 'a,b\n1,2' } },
    { role: 'manager', to: 'operator', letter: 'All done.', open: ['selected.md', 'data.csv'], files: { 'selected.md': '# Selected' } },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  // The operator's message is a card of its own, before the task its answer opens.
  const t1 = (map: ProgressMap) => map.tasks.find(task => task.id === 't1');
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns', request_id: 'r1' }));
  const first = await eventually(() => get<ProgressMap>(`${P}/map`), map => t1(map)?.role === 'engineer' && !!t1(map)?.latest_action);
  // The command run after the sentence does not replace it.
  assert.deepEqual(t1(first)!.latest_action, { role: 'engineer', text: 'Totalling the columns.', ts: t1(first)!.latest_action!.ts });
  assert.deepEqual(t1(first)!.result_paths, []);
  // Later activity from another task or the Manager must not replace this task's action.
  store.append(sid, { type: 'step', role: 'engineer', task: 't2', kind: 'command', text: 'unrelated command' });
  store.append(sid, { type: 'step', role: 'manager', kind: 'message', text: 'unrelated message' });
  assert.deepEqual(t1(await get<ProgressMap>(`${P}/map?after=${first.cursor}`))!.latest_action, t1(first)!.latest_action);
  releaseEngineer();
  const checking = await eventually(() => get<ProgressMap>(`${P}/map?after=${first.cursor}`), map => t1(map)?.latest_action?.text === 'Checking the totals.');
  assert.equal(t1(checking)!.role, 'reviewer');
  assert.equal(t1(checking)!.latest_action!.role, 'reviewer');
  assert.deepEqual(t1(checking)!.result_paths, []);
  releaseReviewer();
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  const done = await get<ProgressMap>(`${P}/map?after=${checking.cursor}`);
  assert.equal(t1(done)!.status, 'done');
  assert.deepEqual(t1(done)!.result_paths, ['selected.md']);
  assert.equal(t1(done)!.latest_action!.text, 'Accepted. Ignore guessed.md in this letter.');
  const unchanged = await get<ProgressMap>(`${P}/map?after=${done.cursor}`);
  assert.deepEqual(unchanged.events, []);
  assert.deepEqual(unchanged.tasks, done.tasks);
});

test('a closing handoff without files clears earlier open files and does not infer a path from its letter', async t => {
  const { store, get } = await setup(t, []);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  store.save({ ...project, tasks: [{ id: 't1', title: 'T', objective: '', from: 'manager', status: 'closed', holder: 'reviewer', note: null }] });
  store.append(project.id, { type: 'handoff', task: 't1', role: 'engineer', to: 'reviewer', letter: 'Draft.', open: ['draft.md'] });
  store.append(project.id, { type: 'handoff', task: 't1', role: 'reviewer', to: 'manager', letter: 'See report.md.' });
  store.append(project.id, { type: 'task.closed', task: 't1' });
  assert.deepEqual((await get<ProgressMap>(`/api/projects/${project.id}/map`)).tasks[0]!.result_paths, []);
});

test('the map shows each task and the letters written about it, and a task its calls and their letters', async t => {
  const { get, post, call } = await setup(t, [
    { role: 'manager', to: 'planner', letter: 'Total the columns, in steps.' },
    { role: 'planner', to: 'engineer', letter: brief },
    { role: 'engineer' },
    { role: 'engineer', to: 'reviewer', letter: 'report.md written' },
    { role: 'reviewer', to: 'operator', letter: 'Ship it as is?' },
    { role: 'reviewer', to: 'engineer', letter: 'Add the units.' },
    { role: 'engineer', to: 'reviewer', letter: 'Units added.' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted.' },
    { role: 'planner', to: 'manager', letter: 'The totals are in report.md.' },
    { role: 'manager', to: 'operator', letter: 'report.md gives the totals.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  assert.equal((await call('/api/map-datasets')).status, 404);
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r1' }));
  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => Boolean(snapshot.pending_questions?.length) && !snapshot.daemon.alive);

  const first = await get<ProgressMap>(`${P}/map`);
  assert.equal(first.id, `live:${sid}`);
  assert.deepEqual(first.tasks.map(task => [task.id, task.title, task.status, task.role, "pending_question" in task]), [
    // A request delegated to the team completes once the work it set going is done.
    ['q0', 'Total the columns of data.csv', 'in_progress', 'manager', false],
    ['t1', 'Summarize the CSV.', 'blocked', 'reviewer', false],
  ]);
  assert.deepEqual(first.events.map(letter => [letter.item_id, letter.type, letter.role, letter.to, letter.text]), [
    ['t1', 'handoff', 'engineer', 'reviewer', 'report.md written'],
    ['t1', 'handoff', 'reviewer', 'operator', 'Ship it as is?'],
  ]);
  const card = (asking.pending_questions![0] as { operator_decision: { id: string } }).operator_decision;
  await post(`${P}/decisions/${card.id}/resolve`, { note: 'Add the units first.' });
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done' && !snapshot.daemon.alive);

  // A page that holds a cursor is sent the letters after it, and every task as it stands.
  const later = await get<ProgressMap>(`${P}/map?after=${first.cursor}`);
  assert.deepEqual(later.events.map(letter => [letter.role, letter.to, letter.text]),
    [['operator', 'reviewer', 'Add the units first.'], ['reviewer', 'engineer', 'Add the units.'], ['engineer', 'reviewer', 'Units added.'], ['reviewer', 'planner', 'Accepted.'],
      // The Manager's report on the work answers the request that set it going.
      ['manager', 'operator', 'report.md gives the totals.']]);
  const task = later.tasks.find(card => card.id === 't1');
  assert.deepEqual([task!.status, 'pending_question' in task!], ['done', false]);
  assert.equal(later.tasks.find(card => card.id === 'q0')!.status, 'done');
  assert.ok(task!.ts < task!.finished_ts!);
  assert.deepEqual((await get<ProgressMap>(`${P}/map?after=${later.cursor}`)).events, []);

  // The Planner's call wrote the brief; the Engineer's refused form is a call with no letter.
  const trace = await get<Trace>(`${P}/map-trace?task=t1`);
  assert.equal(trace.task_id, 't1');
  assert.deepEqual(trace.groups.map(group => [group.role, group.entries.map(entry => entry.text)]), [
    ['engineer', []], ['engineer', ['report.md written']], ['reviewer', ['Ship it as is?']],
    ['reviewer', ['Add the units.']], ['engineer', ['Units added.']], ['reviewer', ['Accepted.']],
  ]);
  assert.ok(trace.groups.every(group => !Object.hasOwn(group, 'output')));
  assert.deepEqual((await get<Trace>(`${P}/map-trace?task=t9`)).groups, []);
});

test('the built pages are served with the shell uncached and the assets immutable', async t => {
  const { call } = await setup(t, []);
  const shell = await call('/');
  assert.equal(shell.headers.get('cache-control'), 'no-store');
  assert.match(await shell.text(), /<title>Argus/);
  const asset = await call('/assets/app-1.js');
  assert.equal(asset.headers.get('cache-control'), 'public, max-age=31536000, immutable');
  assert.equal(asset.headers.get('content-type'), 'text/javascript; charset=utf-8');
  assert.equal((await call('/..%2F..%2Fetc%2Fpasswd')).status, 404);
  assert.equal((await call('/missing.js')).status, 404);
  assert.equal((await call('/api/projects/p-00000000/snapshot')).status, 404);
  assert.equal((await call('/api/projects/..%2F..%2Fx/snapshot')).status, 404);
});

test('static gzip negotiation preserves the original type, content and cache policy', async t => {
  const { call, home } = await setup(t, []);
  for (const [path, mime, cache] of [
    ['index.html', 'text/html; charset=utf-8', 'no-store'],
    ['assets/app-1.js', 'text/javascript; charset=utf-8', 'public, max-age=31536000, immutable'],
  ]) {
    const file = join(home, 'web', path!);
    const original = await readFile(file);
    const compressed = gzipSync(original);
    await writeFile(`${file}.gz`, compressed);
    const url = path === 'index.html' ? '/' : `/${path}`;
    for (const [encoding, accepted] of [
      ['gzip', true], ['gzip, deflate, br, zstd', true], ['br', false], ['identity', false], ['', false],
    ] as const) {
      const response = await call(url, { headers: { 'accept-encoding': encoding } });
      assert.equal(response.status, 200);
      assert.equal(response.headers.get('content-encoding'), accepted ? 'gzip' : null, encoding);
      assert.equal(response.headers.get('content-type'), mime);
      assert.equal(response.headers.get('cache-control'), cache);
      assert.equal(response.headers.get('vary'), 'Accept-Encoding');
      assert.equal(response.headers.get('content-length'), String(accepted ? compressed.length : original.length));
      // fetch decodes the wire representation, so both versions must read identically.
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), original);
    }
    const head = await call(url, { method: 'HEAD', headers: { 'accept-encoding': 'gzip' } });
    assert.equal(head.headers.get('content-encoding'), 'gzip');
    assert.equal(head.headers.get('content-length'), String(compressed.length));
    assert.equal(await head.text(), '');
  }
  await writeFile(join(home, 'web', 'assets', 'no-sidecar.css'), 'body { color: red }');
  const uncompressed = await call('/assets/no-sidecar.css', { headers: { 'accept-encoding': 'gzip' } });
  assert.equal(uncompressed.headers.get('content-encoding'), null);
  assert.equal(uncompressed.headers.get('content-type'), 'text/css; charset=utf-8');
  assert.equal(await uncompressed.text(), 'body { color: red }');
});

test('with a token, project requests need it and the handshake tells the page to pair', async t => {
  const { call } = await setup(t, [], 'secret');
  const unpaired = await call('/api/meta', { headers: { authorization: '' } });
  const meta = await unpaired.json() as { authentication: unknown; runtime: { source_root: string; state_root: unknown } };
  assert.deepEqual(meta.authentication, { required: true, authenticated: false });
  assert.equal(meta.runtime.source_root, '<redacted>');
  assert.equal(meta.runtime.state_root, null);
  assert.equal((await call('/api/projects', { headers: { authorization: 'Bearer wrong' } })).status, 401);
  assert.equal((await call('/api/application-brand', { headers: { authorization: '' } })).status, 200);
  assert.equal((await call('/api/projects')).status, 200);
});

test('stop and start interrupt and resume the work', async t => {
  const { get, post, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', hang: true },
    { role: 'manager', to: 'operator', letter: 'The team is stopped; resume it when you are ready.' },
    { role: 'engineer', to: 'reviewer' },
    { role: 'reviewer', to: 'manager' },
    { role: 'manager', to: 'operator' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;

  const reply = await frames(await post(`${P}/message/stream`, { text: 'second', request_id: 'r2' }));
  assert.equal(reply.at(-1)?.type, 'done');
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles.find(role => role.role === 'engineer')!.active);
  const stopped = await (await post(`${P}/daemon/stop`, { command_id: 'c1' })).json() as { rc: number };
  assert.equal(stopped.rc, 0);
  const paused = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.deepEqual([paused.backlog.map(item => item.status), paused.daemon.resumable], [['in_progress'], true]);
  // Talking to the Manager does not start the stopped team again.
  await frames(await post(`${P}/message/stream`, { text: 'how is it going?', request_id: 'r3' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.deepEqual(requests.map(request => request.role), ['manager', 'engineer', 'manager']);

  assert.equal((await (await post(`${P}/daemon/start`, { command_id: 'c2' })).json() as { rc: number }).rc, 0);
  // Done, there is nothing for Resume to take up.
  const done = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done' && !snapshot.daemon.alive);
  assert.equal(done.daemon.resumable, false);
  assert.deepEqual(requests.map(request => request.role), ['manager', 'engineer', 'manager', 'engineer', 'reviewer', 'manager']);
  // The interrupted Engineer starts afresh: its backend reported no session to go on in.
  assert.equal(requests[3]!.resume, undefined);
});

test('a stopped team keeps only the request currently being answered on the live map, including a replacement after another stop', async t => {
  const { get, post, store, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', hang: true },
    { role: 'manager', hang: true },
    { role: 'manager', hang: true },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Build the CSV tool.', request_id: 'original' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), value => value.roles.some(role => role.role === 'engineer' && role.active));
  await post(`${P}/daemon/stop`, {});
  await eventually(() => get<Snapshot>(`${P}/snapshot`), value => !value.daemon.alive);
  type Map = { cursor: string; tasks: Array<{ id: string; title: string; status: string }> };
  let replaced: string | undefined;
  for (const request of ['stopped-old', 'stopped-new']) {
    const before = Date.now() / 1000;
    const pending = post(`${P}/message/followup`, { text: request, request_id: request });
    try {
      const current = await eventually(() => get<Snapshot>(`${P}/snapshot`), value => value.daemon.calls?.some(call => call.answers === request) === true);
      assert.equal(store.load(sid).status, 'stopped');
      assert.equal(current.work_status?.state, 'running');
      assert.deepEqual(current.daemon.calls?.map(call => [call.role, call.answers]), [['manager', request]]);
      const call = current.daemon.calls![0]!;
      assert.ok(call.started_ts >= before && call.started_ts <= current.read_at! / 1000);
      assert.ok(call.request_card_id);
      const map = await get<Map>(`${P}/map`);
      assert.ok(map.tasks.some(card => card.id === call.request_card_id && card.title === request && card.status === 'in_progress'));
      assert.ok((await get<Map>(`${P}/map?after=${map.cursor}`)).tasks.some(card => card.id === call.request_card_id));
      assert.ok(!replaced || !map.tasks.some(card => card.id === replaced));
      replaced = call.request_card_id;
    } finally {
      await post(`${P}/daemon/stop`, {});
      await pending;
      await eventually(() => get<Snapshot>(`${P}/snapshot`), value => !value.daemon.alive);
    }
    assert.ok(!(await get<Map>(`${P}/map`)).tasks.some(card => card.id === replaced));
  }
  assert.deepEqual(requests.map(request => request.role), ['manager', 'engineer', 'manager', 'manager']);
});

test("a project whose work comes to rest ends its sessions, unless a question waits in one, though it was stopped and started on the way", async t => {
  const { get, post, closed } = await setup(t, [
    { role: 'manager', to: 'operator', letter: 'Hello.' },
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', hang: true },
    { role: 'engineer', to: 'operator', letter: 'Which column?' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'hi', request_id: 'r1' }));
  await eventually(async () => closed, sessions => sessions.length === 1);
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r2' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles.find(role => role.role === 'engineer')!.active);
  await post(`${P}/daemon/stop`, { command_id: 'c1' });
  await eventually(async () => closed, sessions => sessions.length === 2);
  await post(`${P}/daemon/start`, { command_id: 'c2' });
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.pending_questions?.length === 1 && !snapshot.daemon.alive);
  assert.deepEqual(closed, [`${sid}/`, `${sid}/`]);
});

test('a failed review ends its review sessions while the Engineer’s background work runs on', async t => {
  const { store, post, closed } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', to: 'reviewer', letter: 'Training runs in the background; the first checkpoint is in two hours.' },
    { role: 'reviewer' },
    { role: 'reviewer' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  await frames(await post(`/api/projects/${sid}/message/stream`, { text: 'Train it', request_id: 'r1' }));
  await eventually(async () => store.load(sid).status, status => status === 'failed');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(closed, [`${sid}/t1.reviewer`, `${sid}/t1.reviewer`]);
});

test('work a shutdown cut off goes on when Argus starts again; what the operator stopped stays stopped', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-serve-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const task = { id: 't1', title: 'Summarize the CSV.', from: 'manager' as const, objective: brief, status: 'open' as const, holder: 'engineer' as const, note: null };
  const [cut, stopped] = [0, 1].map(() => store.create({ workdir: '', request: 'Summarize data.csv', backend: 'codex', model: null }));
  store.save({ ...cut!, status: 'working', tasks: [task] });
  store.save({ ...stopped!, status: 'stopped', tasks: [task] });
  const { callAgent, requests } = agent([{ role: 'engineer', to: 'manager', letter: 'report.md written' }, { role: 'manager', to: 'operator', letter: 'Done.' }]);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => served.close());
  await eventually(async () => store.load(cut!.id).status, status => status === 'idle');
  assert.deepEqual([requests.map(request => request.role), store.load(stopped!.id).status], [['engineer', 'manager'], 'stopped']);
});

test("a reply a shutdown cut off on a resting project is given when Argus starts again", async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-serve-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: 'What is in data.csv?', backend: 'codex', model: null });
  store.append(project.id, { type: 'message', from: 'operator', text: 'What is in data.csv?', request_id: 'r1' });
  store.save({ ...project, status: 'idle', forManager: [{ text: 'What is in data.csv?', request_id: 'r1' }] });
  const { callAgent, requests } = agent([{ role: 'manager', to: 'operator', letter: 'Three columns.' }]);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => served.close());
  await eventually(async () => store.load(project.id).forManager ?? [], letters => letters.length === 0);
  assert.deepEqual([requests.map(request => request.role), store.load(project.id).status], [['manager'], 'idle']);
});

test('a robot model opens in 3D, with the meshes it names read from beside it', async t => {
  const { get, post, call } = await setup(t, []);
  const { sid, workdir } = await (await post('/api/daemons', {})).json() as { sid: string; workdir: string };
  const P = `/api/projects/${sid}`;
  await mkdir(join(workdir, 'leg', 'urdf'), { recursive: true });
  await mkdir(join(workdir, 'leg', 'meshes'), { recursive: true });
  await writeFile(join(workdir, 'leg', 'urdf', 'leg.urdf'), '<robot name="leg"/>');
  await writeFile(join(workdir, 'leg', 'meshes', 'thigh.stl'), 'solid thigh');

  assert.equal((await get(`${P}/artifact?path=leg/urdf/leg.urdf`)).kind, 'model');
  // XML may carry a script: it is never a document of this origin.
  assert.equal((await call(`${P}/artifact/raw?path=leg/urdf/leg.urdf`)).headers.get('content-security-policy'), 'sandbox');
  const asset = (ref: string) => call(`${P}/artifact/model-asset?${new URLSearchParams({ path: 'leg/urdf/leg.urdf', ref })}`);
  for (const ref of ['../meshes/thigh.stl', 'package://leg/meshes/thigh.stl', 'package://other/meshes/thigh.stl']) {
    const found = await asset(ref);
    assert.equal(found.status, 200, ref);
    assert.equal(await found.text(), 'solid thigh');
  }
  for (const ref of ['../../../projects/x/project.json', '/etc/hostname', 'file:///etc/hostname', 'missing.stl']) {
    assert.equal((await asset(ref)).status, 404, ref);
  }
});

test('a message whose reply a stop cut off waits for Resume, which the page offers', async t => {
  let finish!: () => void;
  const until = new Promise<void>(resolve => { finish = resolve; });
  const { get, post, requests } = await setup(t, [{ role: 'manager', hang: true },
    { role: 'manager', says: 'Checking the original question', until, to: 'operator', letter: '42' }]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const reply = frames(await post(`${P}/message/stream`, { text: 'What is 6 times 7?', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles[0]!.active);
  await post(`${P}/daemon/stop`, { command_id: 'c1' });
  // The reply ends as stopped, not as a failure.
  assert.deepEqual((await reply).at(-1), { type: 'done', result: { kind: 'stopped', reply: null } });
  const stopped = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.equal(stopped.daemon.resumable, true);
  const refreshed = await get<ConversationPage>(`${P}/conversation`);
  assert.deepEqual(refreshed.rows.map(row => [row.type, row.request_id, row.status]),
    [['ui.operator', 'r1', undefined], ['ui.argus', 'r1', 'stopped']]);
  await post(`${P}/daemon/start`, { command_id: 'c2' });
  const replying = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles[0]!.active);
  assert.equal(replying.work_status?.label['zh-CN'], '正在回复你的消息');
  assert.equal(replying.manager_requests?.[0]?.request_id, 'r1');
  const inFlight = await get<ConversationPage>(`${P}/conversation`);
  assert.equal(inFlight.rows[1]?.text, '');
  assert.equal(inFlight.rows[1]?.live, true);
  assert.match(requests[1]!.prompt, /What is 6 times 7\?/);
  finish();
  const answered = await eventually(() => get<{ rows: EventMsg[] }>(`${P}/conversation`), ({ rows }) => rows.some(row => row.request_id === 'r1' && row.text === '42'));
  assert.deepEqual(answered.rows.map(turn => [turn.type, turn.text]), [['ui.operator', 'What is 6 times 7?'],
    ['ui.argus', '42']]);
  // Answered, what it said on the way is the answer's folded process.
  assert.deepEqual(answered.rows.find(row => row.type === 'ui.argus')?.process, ['Checking the original question']);
  assert.equal((await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive)).daemon.resumable, false);
  assert.equal(answered.rows.find(row => row.type === 'ui.argus' && row.request_id === 'r1')?.message_id, refreshed.rows[1]?.message_id);
  assert.equal(requests.length, 2);
});

test("the skill library: Argus's skills and the roles' own, what they changed as the learning feed", async t => {
  const { home, store, get, call, post } = await setup(t, []);
  const own = join(home, 'skills');
  // A file of the roles' skills, changed `minutes` after an hour from now: later than the folders that hold it.
  const hour = Math.ceil(Date.now() / 1000) + 3600;
  const at = (minutes: number): number => hour + minutes * 60;
  const write = async (path: string, text: string, minutes: number): Promise<void> => {
    await mkdir(join(own, path, '..'), { recursive: true });
    await writeFile(join(own, path), text);
    await utimes(join(own, path), at(minutes), at(minutes));
  };
  const { id } = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  const skill = (description: string) => `---\nname: x\ndescription: ${description}\n---\n`;
  await write('quant/SKILL.md', `${skill('Backtest a trading idea without lookahead')}\nRun check.mjs.\n`, 1);
  await write('quant/check.mjs', 'console.log("no lookahead");\n', 2);
  await write('research/SKILL.md', skill('Research as this operator wants it'), 3);
  const catalog = await get<{ items: Record<string, unknown>[] }>('/api/skill-library?sid=p-1');
  assert.deepEqual(Object.keys(catalog), ['items']);
  const item = (name: string) => catalog.items.find(item => item.name === name);
  assert.deepEqual(item('quant'), {
    library: 'shared', source: 'shared', path: 'quant/SKILL.md', name: 'quant',
    description: 'Backtest a trading idea without lookahead', version: '', updated_at: at(1),
  });
  // The team's research skill is listed beside Argus's, which it leaves as it is.
  assert.deepEqual(catalog.items.filter(item => item.name === 'research').map(item => item.source), ['bundled', 'shared']);
  assert.equal(catalog.items.filter(item => item.name === 'research')[1]?.description, 'Research as this operator wants it');
  assert.deepEqual([item('software')?.library, item('software')?.path, item('software')?.source, item('software')?.updated_at],
    ['bundled', 'software/SKILL.md', 'bundled', null]);

  const document = (library: string, path: string) => call(`/api/skill-library/document?${new URLSearchParams({ library, path })}`);
  assert.equal((await (await document('shared', 'quant/check.mjs')).json() as { content: string }).content, 'console.log("no lookahead");\n');
  assert.equal((await (await document('bundled', 'software/SKILL.md')).json() as { content: string }).content,
    await readFile(join(SKILLS, 'software', 'SKILL.md'), 'utf8'));
  // Only the files of the skills can be read.
  for (const [library, path] of [['shared', `../projects/${id}/project.json`], ['shared', join(home, 'projects', id, 'project.json')],
    ['bundled', '../package.json'], ['shared', 'quant'], ['wiki', 'quant/SKILL.md']]) {
    assert.equal((await document(library!, path!)).status, 404, `${library} ${path}`);
  }

  assert.equal((await call('/api/wiki')).status, 404);
  assert.equal((await call('/api/wiki/page?scope=global&vertical=&path=x.md')).status, 404);
  const { events } = await get<{ events: Record<string, unknown>[] }>('/api/knowledge/feed?limit=2');
  assert.deepEqual(events, [
    { ts: at(3), path: 'research/SKILL.md', title: 'Research as this operator wants it' },
    { ts: at(2), path: 'quant/check.mjs', title: 'quant/check.mjs' },
  ]);
  await write('quant/check.mjs', 'console.log("no lookahead, checked");\n', 60);
  const updated = await get<{ events: Record<string, unknown>[] }>('/api/knowledge/feed?limit=1');
  assert.deepEqual(updated.events, [{ ts: at(60), path: 'quant/check.mjs', title: 'quant/check.mjs' }]);

  await write('bench/SKILL.md', skill('A workbench for crystal structures'), 4);
  assert.equal((await call('/api/plugins')).status, 404);
  assert.equal((await post('/api/plugins/bench/launch', {})).status, 404);
});

test('a message typed while a reply is under way is answered after it', async t => {
  let release = (): void => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  const { get, post } = await setup(t, [
    { role: 'manager', to: 'operator', letter: 'First answered.', until: gate },
    { role: 'manager', to: 'operator', letter: 'Second answered.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const first = frames(await post(`${P}/message/stream`, { text: 'first', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles[0]!.active);
  const queued = await (await post(`${P}/message/followup`, { text: 'second' })).json() as { ok: boolean; queued: boolean; accepted: boolean; request_id: string; status: string };
  assert.equal(queued.ok, true);assert.equal(queued.queued, true);assert.equal(queued.accepted, true);assert.equal(typeof queued.request_id, 'string');assert.equal(queued.status, 'waiting');
  assert.equal((await post(`${P}/message/followup`, { text: ' ' })).status, 400);
  release();
  await first;
  const { rows: turns } = await eventually(() => get<{ rows: EventMsg[] }>(`${P}/conversation`), ({ rows }) => rows.some(row => row.text === 'Second answered.'));
  assert.deepEqual(turns.map(turn => [turn.type, turn.text]), [
    ['ui.operator', 'first'], ['ui.argus', 'First answered.'], ['ui.operator', 'second'], ['ui.argus', 'Second answered.'],
  ]);
});

test('two messages sent to a stopped project show only the actual call as running', async t => {
  const record = JSON.parse(await readFile(new URL('fixtures/message-queue/record.json', import.meta.url), 'utf8'));
  let firstDone!: () => void;
  let secondDone!: () => void;
  const firstGate = new Promise<void>(resolve => { firstDone = resolve; });
  const secondGate = new Promise<void>(resolve => { secondDone = resolve; });
  const { store, get, post, requests } = await setup(t, [
    { role: 'manager', to: 'operator', letter: 'First answered.', until: firstGate },
    { role: 'manager', to: 'operator', letter: 'Second answered.', until: secondGate },
  ]);
  // Both turns start from the stopped state in the report, with no background work.
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  project.status = 'stopped';
  store.save(project);
  const P = `/api/projects/${project.id}`;
  const snap = () => get<Snapshot>(`${P}/snapshot`);
  const rows = () => get<ConversationPage>(`${P}/conversation`);
  const replies = async () => (await rows()).rows.filter(row => row.type === 'ui.argus')
    .sort((a, b) => String(a.request_id).localeCompare(String(b.request_id)));
  const first = frames(await post(`${P}/message/stream`, record.messages[0]));
  try {
    await eventually(snap, value => value.roles[0]!.active);
    assert.equal((await post(`${P}/message/followup`, record.messages[1])).status, 200);
    assert.deepEqual((await snap()).manager_requests, [
      { request_id: 'r1', status: 'running' }, { request_id: 'r2', status: 'waiting' },
    ]);
    assert.deepEqual((await replies()).map(row => [row.request_id, row.status, row.live]),
      [['r1', 'running', true], ['r2', 'waiting', true]]);
    assert.equal(requests.length, 1);
    firstDone();
    await first;
    await eventually(snap, value => value.manager_requests?.length === 1 && requests.length === 2);
    assert.deepEqual((await snap()).manager_requests, [{ request_id: 'r2', status: 'running' }]);
    assert.deepEqual((await replies()).map(row => [row.request_id, row.status, row.text]),
      [['r1', undefined, 'First answered.'], ['r2', 'running', '']]);
    secondDone();
    const ended = await eventually(snap, value => !value.daemon.alive);
    assert.deepEqual(ended.manager_requests, []);
    assert.deepEqual((await replies()).map(row => [row.request_id, row.live, row.text]),
      [['r1', undefined, 'First answered.'], ['r2', undefined, 'Second answered.']]);
  } finally {
    firstDone();
    secondDone();
    await first;
    await eventually(snap, value => !value.daemon.alive);
  }
});

test('the Manager rewrites a draft in a turn of its own, and a failed rewrite leaves the draft as it was', async t => {
  const { store, post, requests } = await setup(t, [
    { role: 'manager', to: 'operator', letter: '把 data.csv 每列求和，结果写进 report.md。' },
    { role: 'manager' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await post(`${P}/config/set`, { name: 'ARGUS_SKILL_MODEL', value: 'gpt-x' });
  await post(`${P}/config/set`, { name: 'ARGUS_SKILL_REASONING_EFFORT', value: 'low' });
  const rewrite = async (body: Record<string, unknown>) => await (await post(`${P}/prompt/rewrite`, body)).json();
  assert.deepEqual(await rewrite({ text: ' data.csv 求和 写 report ' }),
    { original: 'data.csv 求和 写 report', rewritten: '把 data.csv 每列求和，结果写进 report.md。', changes: [], questions: [], error: '' });
  const [asked] = requests;
  assert.deepEqual([asked!.role, asked!.form, asked!.access, asked!.model, asked!.effort, asked!.resume], ['manager', 'manager', 'read', 'gpt-x', 'low', undefined]);
  assert.ok(asked!.prompt.endsWith('## Draft\ndata.csv 求和 写 report'));
  assert.equal(asked!.usageFile, join(store.dir(sid), 'usage.jsonl'));
  assert.deepEqual(await rewrite({ text: 'data.csv 求和' }),
    { original: 'data.csv 求和', rewritten: 'data.csv 求和', changes: [], questions: [], error: 'The turn ended without the hand-off form.', error_code: 'rewrite_failed' });
  // An empty draft has nothing to rewrite, and does not call the Manager.
  assert.equal((await post(`${P}/prompt/rewrite`, { text: ' ' })).status, 400);
  assert.equal(requests.length, 2);
  // Neither the record nor the Manager's session knows of it.
  assert.deepEqual([readEvents(store, sid), store.load(sid).sessions], [[], {}]);
});

test('a model tried or a draft rewritten counts in what was spent, but not as what the project runs on', async t => {
  const { post, get } = await setup(t, [
    { role: 'manager', to: 'operator', letter: 'Hello.' },
    { role: 'manager', to: 'operator', letter: 'ok' },
    { role: 'manager', to: 'operator', letter: 'Rewritten.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await (await post(`${P}/message`, { text: 'Hi' })).json();
  const ran = await get<Snapshot>(`${P}/snapshot`);
  assert.equal((await (await post(`${P}/model-choices/probe`, { model: 'gpt-probe' })).json() as { ok: boolean }).ok, true);
  assert.equal((await (await post(`${P}/prompt/rewrite`, { text: 'draft' })).json() as { rewritten: string }).rewritten, 'Rewritten.');
  const after = await get<Snapshot>(`${P}/snapshot`);
  assert.deepEqual([after.usage_summary?.call_count, after.spend_usd], [3, 0.03]);
  assert.deepEqual(after.last_call, ran.last_call);
  assert.deepEqual([after.last_call?.model, after.roles.find(role => role.role === 'manager')?.model], ['fake-model', 'fake-model']);
});

test('deleting a project stops its work and moves its directory to the trash, its workdir left as it is', async t => {
  const { home, call, get, post } = await setup(t, [{ role: 'manager', to: 'engineer', letter: brief }, { role: 'engineer', hang: true }]);
  const remove = (sid: string) => call(`/api/projects/${sid}`, { method: 'DELETE' });
  // A project made and never used, which the page deletes when the operator leaves it.
  const unused = await (await post('/api/daemons', {})).json() as { sid: string; workdir: string };
  const deleted = await (await remove(unused.sid)).json() as Record<string, unknown>;
  assert.deepEqual(deleted, { ok: true, sid: unused.sid, trash_path: deleted.trash_path, workdir: unused.workdir, workdir_preserved: true });
  assert.match(String(deleted.trash_path), new RegExp(`^trash[/\\\\]${unused.sid}-\\d{14}$`));
  assert.ok((await stat(join(home, String(deleted.trash_path), 'project.json'))).isFile());
  assert.ok((await stat(unused.workdir)).isDirectory());
  assert.deepEqual((await get<{ projects: unknown[] }>('/api/projects')).projects, []);
  assert.equal((await remove(unused.sid)).status, 404);

  // A project at work: the request stops the work, and once it has stopped the project goes.
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles.find(role => role.role === 'engineer')!.active);
  assert.equal((await remove(sid)).status, 409);
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.equal((await remove(sid)).status, 200);
  assert.equal((await call(`${P}/snapshot`)).status, 404);
  assert.deepEqual((await get<{ projects: unknown[] }>('/api/projects')).projects, []);
});

interface Attachment { attachment_id: string; relative_path: string; original_name: string; stored_name: string; mime: string; size_bytes: number }
interface UploadState { attachment_id: string; offset: number; size_bytes: number; complete: boolean; attachment?: Attachment }

// Reuse the recorded clarification and uploaded message, with the recorded task as its recipient.
// The fixture sources and controlled role/state variations are described beside the input.
for (const route of ['resolve', 'domain_answer']) for (const role of ['engineer', 'reviewer', 'planner'] as const) for (const stopped of [false, true]) {
  test(`${route}: an attachment answer reaches only its ${role}${stopped ? ' after Resume' : ''}`, async t => {
    const recorded = JSON.parse(await readFile(new URL('fixtures/pending-answer/record.json', import.meta.url), 'utf8'));
    const { store, call, get, post, requests } = await setup(t, [{ role, to: 'operator', letter: 'Next question.' }]);
    const work = store.create({ workdir: '', request: 'Recorded question and attachment', backend: 'codex', model: null });
    const task = { ...recorded.task, holder: role };
    const taskId = role === 'planner' ? null : task.id;
    work.tasks = taskId ? [task] : [];
    work.questions = [{ role, task: taskId, question: recorded.question }];
    work.status = stopped ? 'stopped' : 'asking';
    store.save(work);
    if (taskId) store.append(work.id, { type: 'task.added', task });
    store.append(work.id, { type: 'handoff', role, ...taskId ? { task: taskId } : {}, to: 'operator', letter: recorded.question });
    const P = `/api/projects/${work.id}`;
    const card = `${role}${taskId ? `-${taskId}` : ''}`;
    const answer = (attachments: unknown) => route === 'resolve'
      ? post(`${P}/decisions/${card}/resolve`, { option_id: 'custom', note: `  ${recorded.message.text}  `, attachments })
      : post(`${P}/message`, { domain_answer: { id: card, note: `  ${recorded.message.text}  ` }, attachments });
    // Both answer routes use ordinary messages' receipt validation before consuming the question.
    assert.equal((await answer(recorded.message.attachments)).status, 400);
    const begun = await (await post(`${P}/attachments/uploads`, { name: 'unfinished.txt', size_bytes: 8 })).json() as UploadState;
    assert.equal((await answer([{ ...recorded.message.attachments[0], attachment_id: begun.attachment_id }])).status, 400);
    assert.equal(store.load(work.id).questions.length, 1);
    assert.equal(readEvents(store, work.id).filter(event => event.type === 'message').length, 0);

    const form = new FormData();
    form.append('files', new Blob(['a,b\n1,2\n'], { type: 'text/csv' }), recorded.message.attachments[0].original_name);
    const uploaded = await (await call(`${P}/attachments`, { method: 'POST', body: form })).json() as { attachments: Attachment[] };
    assert.equal((await answer(Array(6).fill(uploaded.attachments[0]))).status, 400);
    assert.equal((await answer(uploaded.attachments)).status, 200);
    const messages = readEvents(store, work.id).filter(event => event.type === 'message');
    assert.equal(messages.length, 1);
    assert.deepEqual(messages[0], { ts: messages[0]!.ts, type: 'message', from: 'operator', to: role,
      ...taskId ? { task: taskId } : {}, text: recorded.message.text, attachments: uploaded.attachments });
    const { rows } = await get<{ rows: EventMsg[] }>(`${P}/conversation`);
    const projected = rows.filter(row => row.type === 'ui.operator');
    assert.equal(projected.length, 1);
    assert.deepEqual(projected[0]!.attachments, uploaded.attachments);
    assert.equal(projected[0]!.text, recorded.message.text);
    if (stopped) {
      assert.equal(store.load(work.id).status, 'stopped');
      assert.equal(requests.length, 0);
      assert.equal((await get<Snapshot>(`${P}/snapshot`)).daemon.alive, false);
      await post(`${P}/daemon/start`, {});
    }
    await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => requests.length === 1 && !snapshot.daemon.alive);
    assert.equal(requests[0]!.role, role);
    assert.ok(requests[0]!.prompt.includes(`You asked the operator: ${recorded.question}\nThey answered: ${recorded.message.text}\n\nAttached, in the workdir:\n- ${uploaded.attachments[0]!.relative_path}`));
    assert.equal(store.load(work.id).tasks.length, taskId ? 1 : 0);
  });
}

test('attached files land in the workdir, and the message that carries them says where', async t => {
  const { store, call, get, post, requests } = await setup(t, [{ role: 'manager', to: 'manager', letter: 'Read them.' }]);
  const { sid, workdir } = await (await post('/api/daemons', {})).json() as { sid: string; workdir: string };
  const P = `/api/projects/${sid}`;
  assert.deepEqual(await get(`${P}/attachments/limits`), { max_count: 5, max_bytes_per_file: 100 << 20, max_total_bytes: 500 << 20, chunk_bytes: 4 << 20 });

  // Small files in one request, as the page sends them.
  const form = new FormData();
  form.append('files', new Blob(['a,b\n1,2\n'], { type: 'text/csv' }), 'data.csv');
  form.append('files', new Blob(['# Notes'], { type: 'text/markdown' }), '../.notes.md. ');
  const { attachments: [csv, notes] } = await (await call(`${P}/attachments`, { method: 'POST', body: form })).json() as { attachments: Attachment[] };
  assert.deepEqual(csv, {
    attachment_id: csv!.attachment_id, relative_path: `.argus/attachments/${csv!.attachment_id}/data.csv`,
    original_name: 'data.csv', stored_name: 'data.csv', mime: 'text/csv', size_bytes: 8,
  });
  assert.equal(notes!.stored_name, 'notes.md');
  assert.equal(await readFile(join(workdir, csv!.relative_path), 'utf8'), 'a,b\n1,2\n');
  const opened = `${P}/artifact/raw?${new URLSearchParams({ path: csv!.relative_path })}`;
  assert.equal(await (await call(opened)).text(), 'a,b\n1,2\n');
  assert.equal((await get<{ path: string }>(`${P}/artifact?${new URLSearchParams({ path: csv!.relative_path })}`)).path, csv!.relative_path);
  await writeFile(join(workdir, '.argus', 'secret.txt'), 'private');
  assert.equal((await call(`${P}/artifact/raw?path=.argus/secret.txt`)).status, 404);
  assert.equal((await call(`${P}/artifact/raw?path=.argus/attachments/${csv!.attachment_id}/../../secret.txt`)).status, 404);
  if (process.platform !== 'win32') {
    const link = join(workdir, csv!.relative_path, '..', 'linked.csv');
    await symlink(join(workdir, '.argus', 'secret.txt'), link);
    assert.equal((await call(`${P}/artifact/raw?path=.argus/attachments/${csv!.attachment_id}/linked.csv`)).status, 404);
    await rm(link);
  }

  // A large file in parts, each appended where the last ended.
  const begun = await (await post(`${P}/attachments/uploads`, { name: 'weights.bin', mime: 'application/octet-stream', size_bytes: 6 })).json() as UploadState;
  assert.deepEqual(begun, { attachment_id: begun.attachment_id, offset: 0, size_bytes: 6, complete: false });
  const U = `${P}/attachments/uploads/${begun.attachment_id}`;
  const put = (offset: number, body: string) => call(`${U}?offset=${offset}`, { method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body });
  assert.equal((await (await put(0, 'abc')).json() as UploadState).offset, 3);
  assert.equal((await put(0, 'abc')).status, 409);
  assert.equal((await post(`${U}/complete`, {})).status, 409);
  assert.equal((await put(3, 'defg')).status, 413);
  assert.equal((await (await put(3, 'def')).json() as UploadState).offset, 6);
  const weights = (await (await post(`${U}/complete`, {})).json() as UploadState).attachment!;
  assert.equal(weights.relative_path, `.argus/attachments/${begun.attachment_id}/weights.bin`);
  assert.deepEqual(await get(U), { attachment_id: begun.attachment_id, offset: 6, size_bytes: 6, complete: true, attachment: weights });
  assert.equal(await readFile(join(workdir, weights.relative_path), 'utf8'), 'abcdef');

  // One taken out of the draft, one begun and cancelled: neither is kept.
  assert.deepEqual(await (await call(`${P}/attachments/${notes!.attachment_id}`, { method: 'DELETE' })).json(),
    { discarded: true, attachment_id: notes!.attachment_id });
  const cancelled = `${P}/attachments/uploads/${(await (await post(`${P}/attachments/uploads`, { name: 'x.txt', size_bytes: 1 })).json() as UploadState).attachment_id}`;
  assert.deepEqual(await (await call(cancelled, { method: 'DELETE' })).json(), { cancelled: true });
  assert.equal((await call(cancelled)).status, 404);
  assert.deepEqual((await readdir(join(workdir, '.argus', 'attachments'))).sort(), [csv!.attachment_id, weights.attachment_id].sort());
  // The operator's files are not results of the work.
  assert.deepEqual((await get<{ artifacts: unknown[] }>(`${P}/artifacts`)).artifacts, []);

  // The Manager is told where they are, and reads them there.
  assert.equal((await post(`${P}/message`, { text: 'Use these', attachments: [{ attachment_id: notes!.attachment_id }] })).status, 400);
  const files = [csv!, weights];
  assert.equal((await post(`${P}/message`, { text: 'Use these', request_id: 'browser-upload', attachments: files })).status, 200);
  const message = readEvents(store, sid).find(event => event.type === 'message');
  assert.deepEqual(message && { text: message.text, request_id: message.request_id, attachments: message.attachments },
    { text: 'Use these', request_id: 'browser-upload', attachments: files });
  assert.ok(requests[0]!.prompt.includes(`Use these\n\nAttached, in the workdir:\n- ${csv!.relative_path}\n- ${weights.relative_path}`));
});

test('an uploaded HTML file opens on the static origin and raw downloads stay sandboxed', async t => {
  const { call, post } = await setup(t, []);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const form = new FormData();
  form.append('files', new Blob(['<h1>Material HTML</h1>'], { type: 'text/plain' }), 'material.html');
  const { attachments: [file] } = await (await call(`${P}/attachments`, { method: 'POST', body: form })).json() as { attachments: Attachment[] };
  const query = new URLSearchParams({ path: file!.relative_path });
  const preview = await call(`${P}/artifact/preview?${query}`);
  assert.equal(preview.status, 200);
  const { url } = await preview.json() as { url: string };
  const page = await previewFetch(url);
  assert.equal(page.headers.get('content-security-policy'), null);
  assert.equal(await page.text(), '<h1>Material HTML</h1>');
  const raw = await call(`${P}/artifact/raw?${query}`);
  assert.equal(raw.headers.get('content-security-policy'), 'sandbox');
  assert.equal(await raw.text(), '<h1>Material HTML</h1>');
});

test('a stream connected again is sent every line after the last it had, however many came meanwhile', async t => {
  const { store, call, post } = await setup(t, [{ role: 'manager', to: 'operator', letter: 'Hello.', says: 'Reading the steps.' }]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const S = `/api/projects/${sid}/stream?replay=40`;
  const step = (n: number) => store.append(sid, { type: 'step', role: 'engineer', task: 't1', kind: 'command', text: `step ${n}` });
  for (let n = 0; n < 50; n++) step(n);
  const first = new AbortController();
  const replayed = await frames(await call(S, { signal: first.signal }), frame => frame.event_id === 49);
  first.abort();
  assert.deepEqual(replayed.map(frame => frame.event_id), Array.from({ length: 40 }, (_, n) => n + 10));
  // More lines than a replay holds are recorded while the page is away, and more once it is back.
  for (let n = 50; n < 150; n++) step(n);
  const again = new AbortController();
  const resumed = await call(S, { signal: again.signal, headers: { 'last-event-id': '49' } });
  await post(`/api/projects/${sid}/message`, { text: 'Hi' });
  const sent = await frames(resumed, frame => frame.type === 'ui.argus');
  again.abort();
  assert.deepEqual(sent, webEvents(readEvents(store, sid)).slice(50));
  assert.deepEqual(sent.slice(-3).map(event => [event.event_id, event.type]), [[152, 'engineer.progress'], [153, 'provider.request.completed'], [154, 'ui.argus']]);
});

test('a reading that goes on where it stopped folds the record as one whole read does', async t => {
  const { store, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Total data.csv' },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?', says: 'Reading data.csv: 总计 ✓' },
    { role: 'manager', to: 'engineer', letter: 'Total data2.csv' },
    { role: 'engineer', to: 'reviewer', letter: 'data2.csv totalled', says: 'awk -F, …' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    { role: 'manager', to: 'operator', letter: 'data2.csv is totalled; data.csv waits for your answer.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Total data.csv', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.pending_questions?.length === 1 && !snapshot.daemon.alive);
  await frames(await post(`${P}/message/stream`, { text: 'Total data2.csv too', request_id: 'r2' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[1]?.status === 'done' && !snapshot.daemon.alive);
  // The record, appended to another project's a few bytes at a time, with a line a killed process cut
  // short, which the next process to write ended first.
  const record = await readFile(join(store.dir(sid), 'events.jsonl'));
  const cut = record.indexOf('\n', record.length / 2) + 1;
  const bytes = Buffer.concat([record.subarray(0, cut), Buffer.from('{"ts":"2026-10-28T00:00:00.000Z","type":"st\n'), record.subarray(cut)]);
  const { id } = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  const read = follow(store, id);
  const folded: EventMsg[] = [];
  for (let at = 0; at < bytes.length; at += 7) {
    await appendFile(join(store.dir(id), 'events.jsonl'), bytes.subarray(at, at + 7));
    folded.push(...read());
  }
  // Only the cut line is lost.
  assert.deepEqual(folded, webEvents(readEvents(store, sid)));
  // The next process to write ends a cut line before its own.
  const crashed = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  await appendFile(join(store.dir(crashed.id), 'events.jsonl'), '{"ts":"2026-10-28T00:00:00.000Z","type":"st');
  const restarted = new Store(store.home);
  restarted.append(crashed.id, { type: 'status', status: 'idle' });
  restarted.append(crashed.id, { type: 'status', status: 'stopped' });
  // As does the same process after a write it made was cut short (a full disk).
  await appendFile(join(store.dir(crashed.id), 'events.jsonl'), '{"ts":"2026-10-28T00:00:01.000Z","type":"st');
  restarted.append(crashed.id, { type: 'status', status: 'working' });
  assert.deepEqual(readEvents(store, crashed.id).map(event => event.type === 'status' && event.status), ['idle', 'stopped', 'working']);
});

test("the page is sent each project's state as it changes: the question a turn asks with the letter that asks it", { timeout: 20_000 }, async t => {
  const { call, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Total data.csv' },
    { role: 'engineer', hang: true },
    { role: 'manager', to: 'engineer', letter: 'Total data2.csv' },
    { role: 'engineer', to: 'operator', letter: 'Which encoding?', says: 'Looking.' },
  ]);
  const live = new AbortController();
  t.after(() => live.abort());
  const home = frames(await call('/api/projects/stream', { signal: live.signal }), frame => frame.row === null);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const selected = frames(await call(`${P}/stream?view=rows`, { signal: live.signal }), frame => frame.row === null);
  const { sid: other } = await (await post('/api/daemons', {})).json() as { sid: string };
  const elsewhere = frames(await call(`/api/projects/${other}/stream?view=rows`, { signal: live.signal }), frame => frame.row === null);
  await frames(await post(`${P}/message/stream`, { text: 'Total data.csv', request_id: 'r1' }));
  await frames(await post(`${P}/message/stream`, { text: 'Total data2.csv too', request_id: 'r2' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => Boolean(snapshot.pending_questions?.length));
  await post(`${P}/daemon/stop`, {});
  await eventually(() => call(P, { method: 'DELETE' }), response => response.status === 200);
  const [states, reading, others] = await Promise.all([home, selected, elsewhere]);
  live.abort();
  assert.equal(states[0]?.type, 'connected');
  assert.equal(inspectApiMeta(states[0]?.meta).compatible, true);
  assert.equal(states[0]?.entry, '/assets/app-1.js');
  assert.deepEqual(states[1], { type: 'projects', projects: [], local_cwd: process.cwd() });
  assert.equal((states[2]?.row as { id: string })?.id, sid);
  assert.ok(states.every(frame => !('snapshot' in frame)), 'the home stream sends only list rows');
  assert.ok(others.filter(frame => frame.type === 'snapshot').every(frame => frame.id === other), 'another project never receives the selected snapshot');
  assert.ok(reading.filter(frame => frame.type === 'snapshot').every(frame => frame.id === sid));
  // Nothing is recorded after the letter while the other task works on: the question comes with it.
  const asked = reading.find(frame => (frame.snapshot as Snapshot | undefined)?.pending_questions?.length);
  assert.equal((asked?.snapshot as Snapshot)?.daemon.alive, true);
  assert.ok(reading.some(frame => frame.snapshot && (frame.snapshot as Snapshot).daemon.alive === false));
  assert.ok(reading.some(frame => frame.type === 'ui.projection'));
  assert.ok(reading.some(frame => frame.type === 'project' && frame.id === other), 'new projects reach the selected stream');
  for (const received of [states, reading, others]) assert.deepEqual(received.at(-1), { type: 'project', id: sid, row: null });
});

test('home and selected streams name the currently served entry again on reconnection', async t => {
  const { home, call, post } = await setup(t, []);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  for (const path of ['/api/projects/stream', `/api/projects/${sid}/stream?view=rows`]) {
    for (const entry of ['app-1.js', 'app-2.js']) {
      await writeFile(join(home, 'web/index.html'), `<script type="module" crossorigin src="./assets/${entry}"></script>`);
      const [first] = await frames(await call(path), frame => frame.type === 'connected');
      assert.equal(first?.entry, `./assets/${entry}`);
      assert.equal(inspectApiMeta(first?.meta).compatible, true);
      assert.equal(first?.event_id, undefined, 'the connection frame does not change the row cursor');
    }
  }
});

test("each call's end is paired with its own start, when tasks run side by side", () => {
  const ts = '2026-10-28T00:00:00.000Z';
  const end = { callId: 'x', threadId: null, completed: true, error: null, model: null, usage: null, ms: 1 };
  const rows = webEvents([
    { ts, type: 'call.start', role: 'engineer', task: 't1' }, { ts, type: 'call.start', role: 'engineer', task: 't2' },
    { ts, type: 'call.end', role: 'engineer', task: 't1', ...end }, { ts, type: 'call.end', role: 'engineer', task: 't2', ...end },
  ] satisfies Recorded[]);
  assert.deepEqual(rows.map(row => [row.type, row.item_id, row.call_id]), [
    ['provider.request.started', 't1', 'c0'], ['provider.request.started', 't2', 'c1'],
    ['provider.request.completed', 't1', 'c0'], ['provider.request.completed', 't2', 'c1'],
  ]);
});

test('a result the Manager hands the operator shows as its result card, its files as the Manager named them and as they were', () => {
  const ts = '2026-10-28T00:00:00.000Z';
  const snapshots = [
    { path: 'report/index.html', version: 'a'.repeat(32), size: 120, stored: true }, { path: 'figures/totals.png', version: '2048:1.5', size: 2048, stored: false },
  ];
  const [reply, plain] = webEvents([
    { ts, type: 'handoff', role: 'manager', to: 'operator', letter: '# The [totals report](/work/report/index.html) **done**\nIt checks against data.csv.', open: ['report/index.html', 'figures/totals.png'], snapshots },
    { ts, type: 'handoff', role: 'manager', to: 'operator', letter: 'Nothing to open.' },
  ] satisfies Recorded[]);
  assert.deepEqual(reply!.delivery, {
    schema_version: 1, delivery_id: 'd0', kind: 'result', item_id: '', title: 'The totals report done',
    summary: '# The [totals report](/work/report/index.html) **done**\nIt checks against data.csv.', status: 'done', review_status: '', delivered_at: Date.parse(ts) / 1000,
    primary_target: { path: 'report/index.html', label: 'index.html', source: 'manager', delivery_number: 1 },
    targets: [
      { path: 'report/index.html', label: 'index.html', source: 'manager', delivery_number: 1 },
      { path: 'figures/totals.png', label: 'totals.png', source: 'manager', delivery_number: 1 },
    ],
    snapshots,
  });
  assert.equal(plain!.delivery, undefined);
});

test('validation errors identify the reason without making the browser parse English', async t => {
  const { home, post } = await setup(t, []);
  const file = join(home, 'web', 'index.html');
  const response = await post('/api/daemons', { workdir: file });
  assert.equal(response.status, 400);
  assert.equal((await response.json() as { code: string }).code, 'workdir_not_directory');
  // A folder that cannot be made anywhere is refused at once, under /proc too, where a recursive mkdir never returns.
  for (const workdir of [join(file, 'deeper', 'still'), ...process.platform === 'linux' ? ['/proc/argus-nope/x'] : []]) {
    assert.equal((await post('/api/daemons', { workdir })).status, 400, workdir);
  }
  // Folders that are missing are made, each in turn.
  const made = join(home, 'new', 'deep');
  assert.equal((await (await post('/api/daemons', { workdir: made })).json() as { workdir: string }).workdir, made);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const empty = await post(`/api/projects/${sid}/prompt/rewrite`, { text: '' });
  assert.equal(empty.status, 400);
  assert.deepEqual(await empty.json(), { code: 'input_required', detail: 'empty prompt' });
});

test('row stream, refreshed conversation and current-round role log share one projection through a question and answer', async t => {
  const { get, post, call, store } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Run the experiment.' },
    { role: 'engineer', to: 'operator', letter: 'Which seed?', says: 'Read the setup.' },
    { role: 'engineer', to: 'reviewer', letter: 'Seed 42 evaluated.' },
    { role: 'reviewer', to: 'manager', letter: 'Checked.' },
    { role: 'manager', to: 'operator', letter: 'Delivered.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const live = new AbortController();
  t.after(() => live.abort());
  let closed = false;
  const stream = frames(await call(`${P}/stream?view=rows&after=-1`, { signal: live.signal }), frame => {
    closed ||= (frame.rows as EventMsg[] ?? []).some(row => row.type === 'ui.task' && row.state === 'done');
    // Use this frame's cursor: reading the current project can see a later state.
    return closed && readEvents(store, sid).slice(0, Number(frame.cursor) + 1).filter(event => event.type === 'status').at(-1)?.status === 'idle';
  });
  await post(`${P}/message`, { text: 'Evaluate a seed.' });
  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot`), state => Boolean(state.pending_questions?.length) && !state.daemon.alive);
  const card = (asking.pending_questions![0] as { operator_decision: { id: string } }).operator_decision;
  await post(`${P}/decisions/${card.id}/resolve`, { note: '42' });
  const sent = await stream;
  live.abort();
  const rows = new Map<string, EventMsg>();
  let roles: EventMsg[] = [];
  for (const frame of sent) {
    for (const row of frame.rows as EventMsg[] ?? []) rows.set(String(row.row_id), row);
    if (frame.type !== 'ui.projection') continue;
    roles = [...roles.filter(row => Number(row.event_id) >= Number(frame.round)), ...frame.role_events as EventMsg[]];
  }
  const refreshed = await get<{ rows: EventMsg[] }>(`${P}/conversation?limit=120`);
  // A row is sent again when what it shows changes, not the line it was read at.
  const shown = (list: EventMsg[]) => list.map(row => ({ ...row, event_id: 0 }));
  assert.deepEqual(shown([...rows.values()].sort((a, b) => Number(a.order) - Number(b.order))), shown(refreshed.rows));
  const question = refreshed.rows.findIndex(row => row.type === 'ui.question');
  assert.deepEqual(refreshed.rows.slice(question, question + 2).map(row => row.type), ['ui.question', 'ui.operator']);
  const task = refreshed.rows.findIndex(row => row.type === 'ui.task');
  assert.ok(question + 1 < task, 'the task comes below the answer to it');
  assert.equal(refreshed.rows[question + 1]!.text, '42');
  assert.equal(refreshed.rows[task]!.state, 'done');
  const ended = await eventually(() => get<Snapshot>(`${P}/snapshot`), state => !state.daemon.alive);
  const replay = frames(await call(`${P}/stream?view=rows&after=${Number(sent.at(-1)!.cursor)}`), frame => frame.type === 'ui.projection');
  const initial = (await replay).find(frame => frame.type === 'ui.projection')!;
  assert.deepEqual(roles, initial.role_events);
  assert.equal('role_events' in ended, false);
  assert.equal('role_round' in ended, false);
  assert.ok(!roles.some(row => row.text === 'Read the setup.'));
  // The answer names its asker, as the decision card does.
  assert.ok(readEvents(store, sid).some(event => event.type === 'message' && `${event.to}-${event.task}` === card.id));
  assert.equal((await call(`${P}/conversation?limit=0`)).status, 400);
  assert.equal((await call(`${P}/transcript`)).status, 404);
});

test('answering a question outside the loaded page sends the whole pair and retains it on a cold paginated read', async t => {
  const { get, post, call } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Run the experiment.' },
    { role: 'engineer', to: 'operator', letter: 'Which seed?' },
    ...Array.from({ length: 20 }, (_, i): Step => ({ role: 'manager', to: 'operator', letter: `Later reply ${i}` })),
    { role: 'engineer', to: 'reviewer', letter: 'Seed evaluated.' },
    { role: 'reviewer', to: 'manager', letter: 'Checked.' },
    { role: 'manager', to: 'operator', letter: 'Delivered.' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await post(`${P}/message`, { text: 'Evaluate a seed.' });
  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot`), state => Boolean(state.pending_questions?.length) && !state.daemon.alive);
  const card = (asking.pending_questions![0] as { operator_decision: { id: string } }).operator_decision;
  for (let i = 0; i < 20; i++) await post(`${P}/message`, { text: `Later message ${i}` });
  const initial = await get<ConversationPage>(`${P}/conversation?limit=30`);
  assert.equal(initial.rows.length, 30);
  assert.ok(initial.before !== null);
  assert.ok(!initial.rows.some(row => row.type === 'ui.question'));

  const live = new AbortController();
  t.after(() => live.abort());
  const stream = frames(await call(`${P}/stream?view=rows&after=${initial.cursor}`, { signal: live.signal }), frame =>
    (frame.rows as EventMsg[] ?? []).some(row => row.text === '424242'));
  await post(`${P}/decisions/${card.id}/resolve`, { note: '424242' });
  const sent = await stream;
  live.abort();
  const delta = sent.find(frame => (frame.rows as EventMsg[] ?? []).some(row => row.text === '424242'))!.rows as EventMsg[];
  const changedQuestion = delta.findIndex(row => row.type === 'ui.question');
  assert.ok(changedQuestion >= 0, 'the answer delta must also contain the question absent from the loaded page');
  assert.equal(delta[changedQuestion + 1]?.text, '424242');

  const merged = new Map(initial.rows.map(row => [String(row.row_id), row]));
  for (const frame of sent) for (const row of frame.rows as EventMsg[] ?? []) merged.set(String(row.row_id), row);
  const shown = [...merged.values()].sort((a, b) => Number(a.order) - Number(b.order));
  const liveQuestion = shown.findIndex(row => row.type === 'ui.question');
  assert.equal(shown[liveQuestion + 1]?.text, '424242');
  assert.ok(liveQuestion > 0, 'the answered pair belongs at the answer time, after later Manager dialogue');

  await eventually(() => get<Snapshot>(`${P}/snapshot`), state => !state.daemon.alive);
  const refreshed = await get<ConversationPage>(`${P}/conversation?limit=30`);
  const question = refreshed.rows.findIndex(row => row.type === 'ui.question');
  assert.ok(question >= 0, 'a cold first-page read must retain the newly answered old question');
  assert.equal(refreshed.rows[question + 1]?.text, '424242');
  const reconnect = new AbortController();
  t.after(() => reconnect.abort());
  const replayed = await frames(await call(`${P}/stream?view=rows`, {
    signal: reconnect.signal, headers: { 'last-event-id': String(initial.cursor) },
  }), frame => (frame.rows as EventMsg[] ?? []).some(row => row.text === '424242'));
  reconnect.abort();
  const replay = replayed.at(-1)!.rows as EventMsg[];
  const replayQuestion = replay.findIndex(row => row.type === 'ui.question');
  assert.ok(replayQuestion >= 0, 'reconnection must replay both rows of the newly answered pair');
  assert.equal(replay[replayQuestion + 1]?.text, '424242');
  const all: EventMsg[] = [];
  let page = refreshed;
  for (;;) {
    all.unshift(...page.rows);
    if (page.before === null) break;
    page = await get<ConversationPage>(`${P}/conversation?limit=30&before=${page.before}`);
  }
  assert.equal(all.filter(row => row.type === 'ui.question').length, 1);
  assert.equal(all.filter(row => row.text === '424242').length, 1);
  // Even a one-row page must keep the actual question and answer together.
  const pair = await get<ConversationPage>(`${P}/conversation?limit=1&before=${Number(refreshed.rows[question + 1]!.order) + 0.01}`);
  assert.deepEqual(pair.rows.map(row => row.text), ['Which seed?', '424242']);
});

test('rows SSE sends task progress and durable Manager messages without onWords callbacks', async t => {
  let finishEngineer!: () => void, finishManager!: () => void;
  const engineer = new Promise<void>(resolve => { finishEngineer = resolve; });
  const manager = new Promise<void>(resolve => { finishManager = resolve; });
  const title = '完整长任务标题需要在对话中读完所有要求'.repeat(10) + '标题结束';
  const { get, post, call, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: `${title}\n核对报告并交付文件。` },
    { role: 'engineer', to: 'manager', says: '正在核对报告的输入。', until: engineer, files: { 'report.md': '# 报告\n已核对。' }, open: ['report.md'], letter: '报告已核对。\n文件可以打开。' },
    { role: 'manager', to: 'operator', until: manager, letter: '已核对你询问的状态。' },
    { role: 'manager', to: 'operator', letter: '任务报告已交付。', open: ['report.md'] },
  ]);
  t.after(() => { finishEngineer(); finishManager(); });
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: '开始核对', request_id: 'r-task' }));
  await eventually(async () => requests, rows => rows.length === 2);
  // The scripted file write precedes its first progress message; wait for both before replacing it.
  await eventually(() => get<ConversationPage>(`${P}/conversation`), page =>
    page.rows.some(row => row.progress === '正在核对报告的输入。'));
  const reply = frames(await post(`${P}/message/stream`, { text: '现在做到哪', request_id: 'r-live' }));
  await eventually(async () => requests, rows => rows.length === 3);
  const response = await call(`${P}/stream?view=rows`);
  const streamed = frames(response, frame => (frame.role_events as EventMsg[])?.some(row => row.text === '经理的第二句实时文字。'));
  requests[2]!.onStep?.({ kind: 'message', text: '经理的第一句实时文字。' });
  await new Promise(resolve => setImmediate(resolve));
  requests[1]!.onStep?.({ kind: 'message', text: '已核对输入，正在检查输出。\n详细记录留在角色日志。' });
  requests[2]!.onStep?.({ kind: 'message', text: '经理的第二句实时文字。' });
  const received = await streamed;
  const managerStep = received.find(frame => (frame.role_events as EventMsg[])?.some(row => row.text === '经理的第一句实时文字。'))!;
  const firstReply = (managerStep.rows as EventMsg[]).find(row => row.type === 'ui.argus' && row.request_id === 'r-live')!;
  assert.deepEqual(firstReply.steps, ['经理的第一句实时文字。']);
  assert.equal(firstReply.text, '');
  const progress = received.flatMap(frame => frame.rows as EventMsg[] ?? []).find(row => row.progress === '已核对输入，正在检查输出。')!;
  assert.equal(progress.title, title);
  const page = await get<ConversationPage>(`${P}/conversation`);
  assert.equal(page.rows.find(row => row.type === 'ui.argus' && row.request_id === 'r-live')?.text, '');
  assert.equal(page.rows.find(row => row.type === 'ui.task')?.progress, progress.progress);
  const runningReply = page.rows.find(row => row.type === 'ui.argus' && row.request_id === 'r-live')!;
  assert.equal(runningReply.row_id, firstReply.row_id);
  assert.deepEqual(runningReply.steps, ['经理的第一句实时文字。', '经理的第二句实时文字。']);
  assert.equal(page.rows.filter(row => row.type === 'ui.argus' && row.request_id === 'r-live').length, 1);
  finishManager();
  await reply;
  const completedReply = (await get<ConversationPage>(`${P}/conversation`)).rows.find(row => row.type === 'ui.argus' && row.request_id === 'r-live')!;
  assert.equal(completedReply.row_id, firstReply.row_id);
  assert.equal(completedReply.text, '已核对你询问的状态。');
  assert.deepEqual(completedReply.process, runningReply.steps);
  finishEngineer();
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snap => snap.backlog[0]?.status === 'done' && !snap.daemon.alive);
  const done = (await get<ConversationPage>(`${P}/conversation`)).rows.find(row => row.type === 'ui.task')!;
  assert.equal(done.progress, '报告已核对。');
  assert.deepEqual(done.result_paths, ['report.md']);
  const file = await get<{ preview: string }>(`${P}/artifact?path=report.md`);
  assert.match(file.preview, /已核对/);
});

test('the words a role is writing reach the page as they come, the reply\'s in its row and a task\'s as its progress, and change no state', async t => {
  let finishEngineer!: () => void, finishManager!: () => void;
  const engineer = new Promise<void>(resolve => { finishEngineer = resolve; });
  const manager = new Promise<void>(resolve => { finishManager = resolve; });
  const { get, post, call, requests } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Train it\nTrain the model.' },
    { role: 'engineer', to: 'manager', until: engineer, letter: 'Trained.' },
    { role: 'manager', to: 'operator', until: manager, letter: 'Training is under way.' },
    { role: 'manager', to: 'operator', letter: 'Noted.' },
  ]);
  t.after(() => { finishEngineer(); finishManager(); });
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Train it', request_id: 'r-task' }));
  await eventually(async () => requests, rows => rows.length === 2);
  const reply = frames(await post(`${P}/message/stream`, { text: 'How far?', request_id: 'r-live' }));
  await eventually(async () => requests, rows => rows.length === 3);
  const response = await call(`${P}/stream?view=rows`);
  const streamed = frames(response, frame => (frame.rows as EventMsg[])?.some(row => row.text === 'Training is under'));
  requests[1]!.onWords?.('Loading the data');
  requests[2]!.onWords?.('Training is', 'answer');
  requests[2]!.onWords?.('Training is under', 'answer');
  const received = await streamed;
  const rows = received.flatMap(frame => frame.rows as EventMsg[] ?? []);
  assert.ok(rows.some(row => row.type === 'ui.task' && row.progress === 'Loading the data'));
  // Past the frames a stream begins with, only rows: words change no project's state.
  assert.deepEqual([...new Set(received.slice(4).map(frame => frame.type))], ['ui.projection']);
  const live = await get<ConversationPage>(`${P}/conversation`);
  assert.equal(live.rows.find(row => row.type === 'ui.argus' && row.request_id === 'r-live')?.text, 'Training is under');
  // Once said, the words are what was said: the call's words end with it.
  finishManager();
  await reply;
  const said = await get<ConversationPage>(`${P}/conversation`);
  assert.equal(said.rows.find(row => row.type === 'ui.argus' && row.request_id === 'r-live')?.text, 'Training is under way.');
  finishEngineer();
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snap => !snap.daemon.alive);
});

test('the row stream shows a task asking once it has asked, while another task runs only commands', { timeout: 10_000 }, async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-asking-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: join(home, 'work'), request: 'Two things', backend: 'codex', model: null });
  Object.assign(project, { status: 'stopped', tasks: ['t1', 't2'].map(id => ({ id, title: `Task ${id}`, from: 'manager', objective: `Do ${id}`, status: 'open', holder: 'engineer', note: null })) });
  store.save(project);
  for (const task of project.tasks) store.append(project.id, { type: 'task.added', task });
  const ended = new AbortController();
  const callAgent: CallAgent = async request => {
    if (request.session?.startsWith(`${project.id}/t1.`)) {
      return { callId: 'a', threadId: 'a', completed: true, error: null, model: 'x', usage: null, ms: 1,
        handoff: { form: request.form, to: 'operator', letters: ['Which column is the date?'] } };
    }
    // t2 runs commands, which move no row, till the test ends.
    while (!ended.signal.aborted && !request.signal?.aborted) {
      request.onStep?.({ kind: 'command', text: 'python train.py' });
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return { callId: 'b', threadId: 'b', completed: false, error: 'Run cancelled.', model: 'x', usage: null, ms: 1, handoff: null };
  };
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => { ended.abort(); return served.close(); });
  const P = `${served.url}/api/projects/${project.id}`;
  const stream = frames(await fetch(`${P}/stream?view=rows&after=-1`, { signal: ended.signal }), frame =>
    (frame.rows as EventMsg[] ?? []).some(row => row.row_id === 'task-t1' && row.state === 'asking'));
  const states = frames(await fetch(`${P}/stream?view=rows`, { signal: ended.signal }), frame =>
    (frame.snapshot as Snapshot | undefined)?.pending_questions?.length === 1);
  await fetch(`${P}/daemon/start`, { method: 'POST', body: '{}' });
  const [rows, projects] = await Promise.all([stream, states]);
  const pushed = projects.at(-1)!.snapshot as Snapshot;
  const fresh = await (await fetch(`${P}/snapshot`)).json() as Snapshot;
  assert.equal(pushed.backlog.find(task => task.id === 't1')?.status, 'blocked');
  assert.deepEqual(pushed.pending_questions, fresh.pending_questions);
  assert.equal((rows.at(-1)!.rows as EventMsg[]).find(row => row.item_id === 't1')?.state, 'asking');
});

test('project list and artifact routes expose only the fields their readers use', async t => {
  const { store, get } = await setup(t, []);
  const work = store.create({ workdir: '', request: 'The full objective belongs in project detail.', backend: 'codex', model: null });
  await writeFile(join(work.workdir, 'result.md'), '# Ready');
  const index = await get<{ projects: Record<string, unknown>[] }>('/api/projects');
  const row = index.projects.find(row => row.id === work.id)!;
  assert.equal(row.workdir, work.workdir);
  for (const field of ['objective', 'cwd', 'launch_cwd', 'daemon_source_owned', 'daemon_upgrade_pending', 'continuous_enabled', 'continuous_objective']) {
    assert.equal(Object.hasOwn(row, field), false, field);
  }
  const detail = await get<Snapshot>(`/api/projects/${work.id}/snapshot`);
  assert.equal(detail.session.objective, work.request);
  assert.equal(detail.session.workdir, work.workdir);
  const file = await get<Record<string, unknown>>(`/api/projects/${work.id}/artifact?path=result.md`);
  assert.equal(file.preview, '# Ready');
  for (const field of ['why', 'exists', 'storage_path']) assert.equal(Object.hasOwn(file, field), false, field);
});


test('map and task detail use the same live request cards and snapshot request mapping', async t => {
  const finishes: Array<() => void> = [];
  const waiting = [0, 1].map(() => new Promise<void>(resolve => { finishes.push(resolve); }));
  const { store, post, get, call, requests } = await setup(t, [
    { role: 'manager', to: 'operator', until: waiting[0]!, letter: 'Recorded.' },
    { role: 'manager', to: 'operator', until: waiting[1]!, letter: 'Recorded.' },
  ]);
  t.after(() => finishes.forEach(finish => finish()));
  for (const [fixture, count, answers, text] of [
    ['map-clarification', 4, 'operator-answer', '都用 6 面。'],
    ['manager-task-update', 4, 'live-request', '请检查周均线。'],
  ] as const) {
    const work = store.create({ request: '', workdir: '', backend: 'codex', model: null });
    const lines = (await readFile(new URL(`./fixtures/${fixture}/events.jsonl`, import.meta.url), 'utf8')).trim().split('\n').slice(0, count);
    await writeFile(join(store.dir(work.id), 'events.jsonl'), lines.join('\n') + '\n');
    work.status = 'stopped';
    store.save(work);
    const P = `/api/projects/${work.id}`;
    const reply = frames(await post(`${P}/message/stream`, { text, request_id: answers }));
    await eventually(() => get<Snapshot>(`${P}/snapshot`), snap => snap.daemon.calls?.some(call => call.answers === answers) === true);
    if (fixture === 'manager-task-update') {
      // A newer request follows while this stopped project's Manager still answers the middle one.
      store.append(work.id, { type: 'message', from: 'operator', text: '下一项', request_id: 'later-request' });
    }
    const snap = await get<Snapshot>(`${P}/snapshot`);
    const active = snap.daemon.calls!.find(call => call.answers === answers)!;
    assert.equal(active.request_card_id, fixture === 'map-clarification' ? 'q0' : 'q4');
    const map = await get<{ tasks: Array<{ id: string; latest_action?: unknown }> }>(`${P}/map`);
    assert.ok(map.tasks.some(card => card.id === active.request_card_id));
    for (const card of map.tasks) {
      const detail = await call(`${P}/backlog/${card.id}`);
      assert.equal(detail.status, 200, `detail missing map card ${card.id}`);
      const { item } = await detail.json() as { item: Record<string, unknown> };
      for (const [key, value] of Object.entries(card)) assert.deepEqual(item[key], value, key);
    }
    finishes[fixture === 'map-clarification' ? 0 : 1]!();
    await reply;
    // Give the next case its own held call.
    if (fixture === 'map-clarification') {
      // The scripted second Manager is reached only by the next project's message.
      assert.equal(requests.length, 1);
    }
  }
});

test('map excerpts leave the conversation and on-demand task detail in full', async t => {
  const { store, get } = await setup(t, []);
  const project = store.create({ workdir: '', request: 'Check the source', backend: 'codex', model: null });
  const added = JSON.parse(await readFile(new URL('./fixtures/operator-task/events.jsonl', import.meta.url), 'utf8')) as Extract<Recorded, { type: 'task.added' }>;
  project.tasks = [added.task];
  project.status = 'stopped';
  store.save(project);
  store.append(project.id, added);
  const text = 'Measured the implementation and verified the requested behavior. '.repeat(40) + 'FULL_ACTION_END';
  store.append(project.id, { type: 'step', role: 'engineer', task: added.task.id, kind: 'message', text });
  const P = `/api/projects/${project.id}`;
  const map = await get<{ tasks: Array<{ id: string; latest_action: { text: string } }> }>(`${P}/map`);
  const card = map.tasks.find(task => task.id === added.task.id)!;
  assert.ok(card.latest_action.text.length <= 160);
  assert.ok(!card.latest_action.text.includes('FULL_ACTION_END'));
  const detail = await get<{ item: { latest_action: { text: string }; objective: string } }>(`${P}/backlog/${added.task.id}`);
  assert.equal(detail.item.latest_action.text, text);
  assert.equal(detail.item.objective, added.task.objective);
  const conversation = await get<ConversationPage>(`${P}/conversation`);
  assert.equal(conversation.rows.find(row => row.type === 'ui.task')?.progress, text);
  // Reading a map never mutates the shared full-text projection.
  const again = await get<{ item: { latest_action: { text: string } } }>(`${P}/backlog/${added.task.id}`);
  assert.equal(again.item.latest_action.text, text);
});


test('map summaries retain the recorded drawing while explicit task and letter reads return the body', async t => {
  const { store, get, call } = await setup(t, []);
  const source = new URL('./fixtures/manager-task-update/events.jsonl', import.meta.url);
  const record = (await import('node:fs')).readFileSync(source, 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const task = record.find(event => event.type === 'task.added').task;
  // Replay the real supplement on the already opened task, as in map.test.ts.
  record.filter(event => event.type === 'handoff').at(-1).task = task.id;
  const project = store.create({ workdir: '', request: 'Replay the recorded task update', backend: 'codex', model: null });
  store.save({ ...project, status: 'stopped', tasks: [{ ...task, status: 'closed' }] });
  const { writeFileSync } = await import('node:fs');
  writeFileSync(`${store.dir(project.id)}/events.jsonl`, record.map(event => JSON.stringify(event)).join('\n') + '\n');
  const P = `/api/projects/${project.id}`;
  const map = await get<ProgressMap>(`${P}/map`);
  assert.ok(map.events.length > 0);
  assert.ok(map.events.every(event => event.text.length <= 160 && !event.text.includes('\n')));
  const index = record.findIndex(event => event.type === 'handoff' && event.task === task.id && event.letter.includes('\n'));
  const original = record[index];
  const event = map.events.find(event => event.id === `e${index}`)!;
  assert.deepEqual([event.item_id, event.role, event.to], [task.id, original.role, original.to]);
  assert.notEqual(event.text, original.letter);
  const detail = await get<{ item: { notes: string; objective: string } }>(`${P}/backlog/${task.id}?letter=${event.id}`);
  assert.equal(detail.item.notes, original.letter);
  assert.equal(detail.item.objective, task.objective);
  assert.deepEqual((await get<ProgressMap>(`${P}/map?after=${map.cursor}`)).events, []);
  assert.equal((await call(`${P}/backlog/${task.id}?letter=missing`)).status, 404);
});

test('task details expose recorded return reasons, operator letters and only the latest Engineer turn edits', async t => {
  const { store, get } = await setup(t, []);
  const work = store.create({ request: 'Draw a chart', workdir: '', backend: 'codex', model: null });
  work.status = 'stopped';
  const task = { id: 't1', title: 'Chart', objective: 'Draw a chart.', from: 'planner' as const,
    status: 'closed' as const, holder: 'engineer' as const, note: null };
  work.tasks.push(task);
  store.save(work);
  store.append(work.id, { type: 'task.added', task });
  store.append(work.id, { type: 'call.start', role: 'engineer', task: task.id });
  store.append(work.id, { type: 'step', role: 'engineer', task: task.id, kind: 'edit', text: 'old.ts' });
  store.append(work.id, { type: 'handoff', role: 'reviewer', task: task.id, to: 'engineer', letter: '退回：缺少坐标单位。' });
  store.append(work.id, { type: 'message', from: 'operator', to: 'engineer', task: task.id, text: '请用秒为单位。' });
  store.append(work.id, { type: 'call.start', role: 'engineer', task: task.id });
  for (const path of ['chart.ts', 'chart.png', 'chart.ts']) store.append(work.id, { type: 'step', role: 'engineer', task: task.id, kind: 'edit', text: path });
  store.append(work.id, { type: 'handoff', role: 'reviewer', task: task.id, to: 'planner', letter: '已经修正。', open: ['chart.ts', 'chart.png', 'second.png'] });
  store.append(work.id, { type: 'task.closed', task: task.id });
  const { item } = await get<{ item: import('@argus/contracts').BacklogItem }>(`/api/projects/${work.id}/backlog/${task.id}`);
  assert.deepEqual(item.changed_files, ['chart.ts', 'chart.png']);
  assert.deepEqual(item.result_paths, ['chart.ts', 'chart.png', 'second.png']);
  assert.deepEqual(item.letters!.map(({ role, to, text }) => [role, to, text]), [['reviewer', 'engineer', '退回：缺少坐标单位。'],
    ['operator', 'engineer', '请用秒为单位。'], ['reviewer', 'planner', '已经修正。']]);
});

test('expanded task files come from its explicit letter before review completes, without marking the task done', async t => {
  const { store, get } = await setup(t, []);
  const work = store.create({ request: 'Chart', workdir: '', backend: 'codex', model: null });
  work.status = 'stopped';
  const task = { id: 't1', title: 'Chart', objective: 'Draw a chart.', from: 'planner' as const,
    status: 'open' as const, holder: 'reviewer' as const, note: null };
  work.tasks.push(task);
  store.save(work);
  store.append(work.id, { type: 'task.added', task });
  store.append(work.id, { type: 'handoff', role: 'engineer', task: task.id, to: 'reviewer', letter: 'Chart ready for review.', open: ['chart.png'] });
  const path = `/api/projects/${work.id}`;
  const { item } = await get<{ item: import('@argus/contracts').BacklogItem }>(`${path}/backlog/${task.id}`);
  assert.deepEqual(item.result_paths, ['chart.png']);
  assert.equal(item.status, 'in_progress');
  const snap = await get<Snapshot>(`${path}/snapshot`);
  assert.equal(snap.backlog[0]!.status, 'in_progress');
  const map = await get<{ tasks: Array<{ id: string; result_paths: string[] }> }>(`${path}/map`);
  assert.deepEqual(map.tasks.find(row => row.id === task.id)!.result_paths, []);
});
