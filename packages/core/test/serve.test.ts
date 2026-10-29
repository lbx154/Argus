import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { appendFile, mkdir, mkdtemp, readdir, readFile, rm, stat, symlink, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspectApiMeta, RELEASE_ID, REQUIRED_API_CAPABILITIES, requireSnapshotContract, type EventMsg, type Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent, HandOff, Role } from '../src/types.js';
import { SKILLS } from '../src/prompts.js';
import { Store } from '../src/store.js';
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
      error: handoff ? null : 'The turn ended without the hand-off form.', handoff, model: 'fake', usage: { ...tokens, cost_usd: 0.01 }, ms: 1,
    };
  };
  return { callAgent: Object.assign(callAgent, { close: async (prefix: string) => { closed.push(prefix); } }), requests, closed };
}

async function setup(t: TestContext, steps: Step[], token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-serve-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(join(web, 'assets'), { recursive: true });
  await writeFile(join(web, 'index.html'), '<!doctype html><title>Argus</title>');
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
  return { home, store, requests, closed, call, get, post };
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
  assert.equal(metaResponse.headers.get('x-argus-release'), RELEASE_ID);
  const compatibility = inspectApiMeta(await metaResponse.json(), { releaseId: RELEASE_ID });
  assert.equal(compatibility.compatible, true, compatibility.reason);
  assert.deepEqual(compatibility.meta?.authentication, { required: false, authenticated: true });
  // Only what this server has, so the page hides the rest: settings, the machine's resources, slash commands.
  assert.deepEqual(compatibility.meta?.capabilities, [
    ...REQUIRED_API_CAPABILITIES, 'project.attachments.v1', 'system.resources.v1', 'project.workdir.v1', 'project.config.v1', 'project.cost-feed.v1',
    'project.editions.v1',
  ]);
  assert.equal((await get('/api/application-brand')).id, 'argus');

  const created = await (await post('/api/daemons', { objective: '', name: '', workdir: '' })).json() as { sid: string; rc: number; workdir: string };
  assert.equal(created.rc, 0);
  const sid = created.sid;
  const P = `/api/projects/${sid}`;
  assert.deepEqual((await get<{ projects: { id: string }[] }>('/api/projects')).projects.map(row => row.id), [sid]);

  const live = new AbortController();
  const stream = frames(await call(`${P}/stream?replay=40&view=ui`, { signal: live.signal }), frame => frame.type === 'life.mission.completed');

  const reply = await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r1' }));
  assert.deepEqual(reply.map(frame => frame.type), ['phase', 'delta', 'done']);
  // The operator reads the Manager's letter to the team: the brief is its reply.
  // The reply is its row in the record, as the conversation shows it after a reload: whom it is to, and the message it answers.
  assert.deepEqual(reply[1], { type: 'delta', event: { type: 'ui.argus', ts: (reply[1]!.event as EventMsg).ts, event_id: 3,
    agent_layer: 'manager', to: 'engineer', text: brief, message_id: 'm0-argus' } });
  assert.deepEqual(reply[2], { type: 'done', result: { kind: 'task', reply: brief } });

  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot?compact=true&events_limit=1`),
    snapshot => Boolean(snapshot.pending_questions?.length) && !snapshot.daemon.alive);
  requireSnapshotContract(asking);
  // No continuous campaign here: the page shows no switch for one.
  assert.equal(asking.continuous, undefined);
  assert.equal(asking.session.objective, 'Total the columns of data.csv');
  assert.deepEqual(asking.backlog.map(item => [item.id, item.status]), [['t1', 'blocked']]);
  assert.equal(asking.usage_summary?.call_count, 3);
  assert.equal(asking.spend_usd, 0.03);
  // With no model chosen, each role shows the one its last call ran on.
  assert.deepEqual([asking.roles.find(role => role.role === 'engineer')?.model, asking.last_call?.role, asking.last_call?.model], ['fake-model', 'reviewer', 'fake-model']);
  // Each role's last words, its newest message or letter, come from the whole record: the page holds only its newest part.
  assert.deepEqual(asking.roles.map(role => [role.role, role.last?.text ?? null]),
    [['manager', 'Summarize the CSV.'], ['planner', null], ['engineer', 'report.md written'], ['reviewer', 'Ship it as is?']]);
  const card = (asking.pending_questions![0] as { operator_decision: { id: string; question: string; options: unknown[] } }).operator_decision;
  assert.deepEqual([card.question, card.options], ['Ship it as is?', []]);

  const { events } = await get<{ events: EventMsg[] }>(`${P}/events?limit=120&view=ui`);
  assert.deepEqual(events.map(event => event.type), [
    'ui.operator', 'provider.request.started', 'provider.request.completed', 'ui.argus', 'life.mission.started',
    'provider.request.started', 'engineer.progress', 'provider.request.completed', 'engineer.progress',
    'provider.request.started', 'provider.request.completed', 'engineer.progress',
  ]);
  // What the Engineer said as its turn went on is in the feed while it works.
  assert.deepEqual(events.find(event => event.type === 'engineer.progress'), { ...events.find(event => event.type === 'engineer.progress'),
    kind: 'agent_message', agent_layer: 'engineer', text: 'Totalling the columns.', item_id: 't1' });
  // The transcript is the conversation's rows as the stream sends them.
  const { turns } = await get<{ turns: EventMsg[] }>(`${P}/transcript?n=120`);
  assert.deepEqual(turns, events.filter(event => event.type === 'ui.operator' || event.type === 'ui.argus'));
  assert.deepEqual(turns.map(turn => [turn.type, turn.message_id]), [['ui.operator', 'm0-operator'], ['ui.argus', 'm0-argus']]);
  assert.deepEqual(turns[1], (reply[1]!.event as EventMsg));

  assert.equal((await post(`${P}/decisions/q-stale/resolve`, { option_id: 'custom', note: 'yes' })).status, 409);
  assert.equal((await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: ' ' })).status, 400);
  assert.deepEqual(await (await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: 'Ship it today' })).json(), { resolved: true });
  const done = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done' && !snapshot.daemon.alive);
  assert.deepEqual(done.pending_questions, []);
  assert.match(requests.at(-2)!.prompt, /They answered: Ship it today/);
  // The Manager's report, written after the team's work, is in the conversation too.
  assert.deepEqual((await get<{ turns: { text: string }[] }>(`${P}/transcript?n=120`)).turns.at(-1)?.text, 'report.md gives the totals.');

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
  const download = await call(`${P}/artifact/raw?path=page.html&download=true`);
  assert.equal(download.headers.get('content-type'), 'text/plain; charset=utf-8');
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

interface MapTask { id: string; title: string; status: string; role: string; ts: number; finished_ts: number | null; pending_question?: string;
  latest_action?: import('@argus/contracts').BacklogItem['latest_action']; result_paths: string[] }
interface MapLetter { id: string; item_id: string; type: string; role: string; to: string; text: string }
interface ProgressMap { id: string; tasks: MapTask[]; events: MapLetter[]; cursor: string }
interface Trace { task_id: string; groups: { call_id: string; role: string; entries: { kind: string; text: string }[] }[] }

test('map updates carry each task’s holder, the latest thing said on it (not a command) and only its closing handoff’s files', async t => {
  let releaseEngineer = (): void => {};
  let releaseReviewer = (): void => {};
  const engineer = new Promise<void>(resolve => { releaseEngineer = resolve; });
  const reviewer = new Promise<void>(resolve => { releaseReviewer = resolve; });
  t.after(() => { releaseEngineer(); releaseReviewer(); });
  const { store, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', to: 'reviewer', letter: 'Ready to check.', until: engineer, says: 'Totalling the columns.',
      progress: [{ kind: 'command', text: 'node totals.mjs' }], open: ['draft.md'] },
    { role: 'reviewer', to: 'manager', letter: 'Accepted. Ignore guessed.md in this letter.', until: reviewer,
      says: 'Checking the totals.', open: ['report.md', 'data.csv'], files: { 'report.md': '# Totals', 'data.csv': 'a,b\n1,2' } },
    { role: 'manager', to: 'operator', letter: 'All done.', open: ['unrelated.md'] },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns', request_id: 'r1' }));
  const first = await eventually(() => get<ProgressMap>(`${P}/map`), map => map.tasks[0]?.role === 'engineer' && !!map.tasks[0].latest_action);
  // The command run after the sentence does not replace it.
  assert.deepEqual(first.tasks[0]!.latest_action, { role: 'engineer', text: 'Totalling the columns.', ts: first.tasks[0]!.latest_action!.ts });
  assert.deepEqual(first.tasks[0]!.result_paths, []);
  // Later activity from another task or the Manager must not replace this task's action.
  store.append(sid, { type: 'step', role: 'engineer', task: 't2', kind: 'command', text: 'unrelated command' });
  store.append(sid, { type: 'step', role: 'manager', kind: 'message', text: 'unrelated message' });
  assert.deepEqual((await get<ProgressMap>(`${P}/map?after=${first.cursor}`)).tasks[0]!.latest_action, first.tasks[0]!.latest_action);
  releaseEngineer();
  const checking = await eventually(() => get<ProgressMap>(`${P}/map?after=${first.cursor}`), map => map.tasks[0]?.latest_action?.text === 'Checking the totals.');
  assert.equal(checking.tasks[0]!.role, 'reviewer');
  assert.equal(checking.tasks[0]!.latest_action!.role, 'reviewer');
  assert.deepEqual(checking.tasks[0]!.result_paths, []);
  releaseReviewer();
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  const done = await get<ProgressMap>(`${P}/map?after=${checking.cursor}`);
  assert.equal(done.tasks[0]!.status, 'done');
  assert.deepEqual(done.tasks[0]!.result_paths, ['report.md', 'data.csv']);
  assert.equal(done.tasks[0]!.latest_action!.text, 'Accepted. Ignore guessed.md in this letter.');
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
  const { get, post } = await setup(t, [
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
  assert.deepEqual(await get('/api/map-datasets'), { datasets: [] });
  await frames(await post(`${P}/message/stream`, { text: 'Total the columns of data.csv', request_id: 'r1' }));
  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => Boolean(snapshot.pending_questions?.length) && !snapshot.daemon.alive);

  const first = await get<ProgressMap>(`${P}/map`);
  assert.equal(first.id, `live:${sid}`);
  assert.deepEqual(first.tasks.map(task => [task.id, task.title, task.status, task.role, task.pending_question]),
    [['t1', 'Summarize the CSV.', 'blocked', 'reviewer', 'Ship it as is?']]);
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
    [['reviewer', 'engineer', 'Add the units.'], ['engineer', 'reviewer', 'Units added.'], ['reviewer', 'planner', 'Accepted.']]);
  const [task] = later.tasks;
  assert.deepEqual([task!.status, task!.pending_question], ['done', undefined]);
  assert.ok(task!.ts < task!.finished_ts!);
  assert.deepEqual((await get<ProgressMap>(`${P}/map?after=${later.cursor}`)).events, []);

  // The Planner's call wrote the brief; the Engineer's refused form is a call with no letter.
  const trace = await get<Trace>(`${P}/map-trace?task=t1`);
  assert.equal(trace.task_id, 't1');
  assert.deepEqual(trace.groups.map(group => [group.role, group.entries.map(entry => entry.text)]), [
    ['planner', [brief]], ['engineer', []], ['engineer', ['report.md written']], ['reviewer', ['Ship it as is?']],
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

test('a robot model opens in 3D, with the meshes it names read from beside it', async t => {
  const { get, post, call } = await setup(t, []);
  const { sid, workdir } = await (await post('/api/daemons', {})).json() as { sid: string; workdir: string };
  const P = `/api/projects/${sid}`;
  await mkdir(join(workdir, 'leg', 'urdf'), { recursive: true });
  await mkdir(join(workdir, 'leg', 'meshes'), { recursive: true });
  await writeFile(join(workdir, 'leg', 'urdf', 'leg.urdf'), '<robot name="leg"/>');
  await writeFile(join(workdir, 'leg', 'meshes', 'thigh.stl'), 'solid thigh');

  assert.equal((await get(`${P}/artifact?path=leg/urdf/leg.urdf`)).kind, 'model');
  // XML may carry a script: it is never sent as a document of this origin.
  assert.equal((await call(`${P}/artifact/raw?path=leg/urdf/leg.urdf`)).headers.get('content-type'), 'text/plain; charset=utf-8');
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
  const { get, post, requests } = await setup(t, [{ role: 'manager', hang: true }, { role: 'manager', to: 'operator', letter: '42' }]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  const reply = frames(await post(`${P}/message/stream`, { text: 'What is 6 times 7?', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles[0]!.active);
  await post(`${P}/daemon/stop`, { command_id: 'c1' });
  // The reply ends as stopped, not as a failure.
  assert.deepEqual((await reply).at(-1), { type: 'done', result: { kind: 'stopped', reply: null } });
  const stopped = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.equal(stopped.daemon.resumable, true);
  await post(`${P}/daemon/start`, { command_id: 'c2' });
  const answered = await eventually(() => get<{ turns: EventMsg[] }>(`${P}/transcript`), ({ turns }) => turns.length === 2);
  assert.deepEqual(answered.turns.map(turn => [turn.type, turn.text]), [['ui.operator', 'What is 6 times 7?'], ['ui.argus', '42']]);
  assert.equal((await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive)).daemon.resumable, false);
  assert.equal(requests.length, 2);
});

test("the skill library: Argus's skills and the roles' own, what they changed as the learning feed, and plugins", async t => {
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
  const revision = async (): Promise<number> => {
    const learning = await get<{ jobs: unknown[]; pending: number; revision: number }>(`/api/projects/${id}/learning`);
    assert.deepEqual([learning.jobs, learning.pending], [[], 0]);
    return learning.revision;
  };
  const before = await revision();

  const skill = (description: string) => `---\nname: x\ndescription: ${description}\n---\n`;
  await write('quant/SKILL.md', `${skill('Backtest a trading idea without lookahead')}\nRun check.mjs.\n`, 1);
  await write('quant/check.mjs', 'console.log("no lookahead");\n', 2);
  await write('research/SKILL.md', skill('Research as this operator wants it'), 3);
  const catalog = await get<{ items: Record<string, unknown>[]; errors: unknown }>('/api/skill-library?sid=p-1');
  assert.deepEqual(catalog.errors, []);
  const item = (name: string) => catalog.items.find(item => item.name === name);
  assert.deepEqual(item('quant'), {
    library: 'shared', source: 'shared', scope: 'global', vertical: '', role: '', path: 'quant/SKILL.md', name: 'quant',
    description: 'Backtest a trading idea without lookahead', is_default: false, updated_at: at(1),
  });
  // The team's research skill is listed beside Argus's, which it leaves as it is.
  assert.deepEqual(catalog.items.filter(item => item.name === 'research').map(item => item.source), ['bundled', 'shared']);
  assert.equal(catalog.items.filter(item => item.name === 'research')[1]?.description, 'Research as this operator wants it');
  assert.deepEqual([item('software')?.library, item('software')?.path, item('software')?.is_default, item('software')?.updated_at],
    ['bundled', 'software/SKILL.md', true, null]);

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
  assert.ok(await revision() > before);
  const { events } = await get<{ events: Record<string, unknown>[] }>('/api/knowledge/feed?limit=2');
  const learned = { kind: 'learned', scope: 'global', vertical: '', source_project: '', role: '', note: '' };
  assert.deepEqual(events, [
    { ...learned, ts: at(3), path: 'research/SKILL.md', title: 'Research as this operator wants it' },
    { ...learned, ts: at(2), path: 'quant/check.mjs', title: 'quant/check.mjs' },
  ]);
  const changed = await revision();
  await write('quant/check.mjs', 'console.log("no lookahead, checked");\n', 60);
  assert.ok(await revision() > changed);

  await write('bench/SKILL.md', skill('A workbench for crystal structures'), 4);
  await write('bench/plugin.json', JSON.stringify({ name: 'Bench', description: 'Solve a structure', version: '1.2', url: 'http://127.0.0.1:9100/' }), 4);
  await write('sketch/SKILL.md', skill('A plugin not ready to open'), 5);
  // A url that is not a web address is no url: the page would run it in itself.
  await write('sketch/plugin.json', JSON.stringify({ name: 'Sketch', url: 'javascript:alert(1)' }), 5);
  const { plugins } = await get<{ plugins: Record<string, unknown>[] }>('/api/plugins');
  assert.deepEqual(plugins.map(plugin => [plugin.id, plugin.name, plugin.url]),
    [['bench', 'Bench', 'http://127.0.0.1:9100/'], ['sketch', 'Sketch', '']]);
  assert.deepEqual(await (await post('/api/plugins/bench/launch', {})).json(), { url: 'http://127.0.0.1:9100/' });
  const unready = await post('/api/plugins/sketch/launch', {});
  assert.deepEqual([unready.status, await unready.json()], [400, { detail: `${join(own, 'sketch', 'plugin.json')} names no http(s) url to open.`, code: 'plugin_unavailable' }]);
  assert.equal((await post('/api/plugins/quant/launch', {})).status, 404);
  // A skill whose SKILL.md is a link, or not a file, is no skill: it brings in nothing from elsewhere.
  await writeFile(join(home, 'secret'), 'token=hunter2\n');
  await mkdir(join(own, 'linked'), { recursive: true });
  // Windows lets only some accounts make a link.
  await symlink(join(home, 'secret'), join(own, 'linked', 'SKILL.md')).catch(() => undefined);
  await mkdir(join(own, 'odd', 'SKILL.md'), { recursive: true });
  const names = (await get<{ items: { name: string }[] }>('/api/skill-library')).items.map(item => item.name);
  assert.deepEqual([names.includes('linked'), names.includes('odd'), names.includes('bench')], [false, false, true]);
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
  assert.deepEqual(await (await post(`${P}/message/followup`, { text: 'second' })).json(), { ok: true, queued: true });
  assert.equal((await post(`${P}/message/followup`, { text: ' ' })).status, 400);
  release();
  await first;
  const { turns } = await eventually(() => get<{ turns: EventMsg[] }>(`${P}/transcript`), ({ turns }) => turns.length === 4);
  assert.deepEqual(turns.map(turn => [turn.type, turn.text]), [
    ['ui.operator', 'first'], ['ui.argus', 'First answered.'], ['ui.operator', 'second'], ['ui.argus', 'Second answered.'],
  ]);
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

test('attached files land in the workdir, and the message that carries them says where', async t => {
  const { call, get, post, requests } = await setup(t, [{ role: 'manager', to: 'manager', letter: 'Read them.' }]);
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
  const files = [csv!, weights].map(({ attachment_id }) => ({ attachment_id }));
  assert.equal((await post(`${P}/message`, { text: 'Use these', attachments: files })).status, 200);
  assert.ok(requests[0]!.prompt.includes(`Use these\n\nAttached, in the workdir:\n- ${csv!.relative_path}\n- ${weights.relative_path}`));
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
  // The record, appended to another project's a few bytes at a time, with a line a killed process cut short.
  const record = await readFile(join(store.dir(sid), 'events.jsonl'));
  const cut = record.indexOf('\n', record.length / 2) + 1;
  const bytes = Buffer.concat([record.subarray(0, cut), Buffer.from('{"ts":"2026-10-28T00:00:00.000Z","type":"st'), record.subarray(cut)]);
  const { id } = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  const read = follow(store, id);
  const folded: EventMsg[] = [];
  for (let at = 0; at < bytes.length; at += 7) {
    await appendFile(join(store.dir(id), 'events.jsonl'), bytes.subarray(at, at + 7));
    folded.push(...read());
  }
  assert.deepEqual(folded, webEvents(readEvents(store, id)));
  assert.deepEqual(folded.filter(event => event.type === 'ui.argus').map(event => event.message_id), ['m0-argus', 'm11-argus', 'm26-argus']);
});

test("the page is sent each project's state as it changes: the question a turn asks with the letter that asks it", { timeout: 20_000 }, async t => {
  const { call, get, post } = await setup(t, [
    { role: 'manager', to: 'engineer', letter: 'Total data.csv' },
    { role: 'engineer', hang: true },
    { role: 'manager', to: 'engineer', letter: 'Total data2.csv' },
    { role: 'engineer', to: 'operator', letter: 'Which encoding?', says: 'Looking.' },
  ]);
  type State = { type: string; row?: { id: string; daemon_alive: boolean } | null; snapshot?: Snapshot | null };
  const live = new AbortController();
  const sent = frames(await call('/api/projects/stream', { signal: live.signal }), frame => frame.row === null) as Promise<State[]>;
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;
  await frames(await post(`${P}/message/stream`, { text: 'Total data.csv', request_id: 'r1' }));
  await frames(await post(`${P}/message/stream`, { text: 'Total data2.csv too', request_id: 'r2' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => Boolean(snapshot.pending_questions?.length));
  await post(`${P}/daemon/stop`, {});
  await eventually(() => call(P, { method: 'DELETE' }), response => response.status === 200);
  const states = await sent;
  live.abort();
  assert.deepEqual(states[0], { type: 'projects', projects: [], local_cwd: process.cwd() });
  assert.equal(states[1]?.row?.id, sid);
  // Nothing is recorded after the letter while the other task works on: the question comes with it.
  const asked = states.find(state => state.snapshot?.pending_questions?.length);
  assert.equal(asked?.row?.daemon_alive, true);
  assert.ok(states.some(state => state.row && !state.row.daemon_alive && state.snapshot?.daemon.alive === false));
  assert.deepEqual(states.at(-1), { type: 'project', id: sid, row: null, snapshot: null });
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
    schema_version: 1, delivery_id: 'd0', kind: 'task_completed', item_id: '', title: 'The totals report done',
    summary: '# The [totals report](/work/report/index.html) **done**\nIt checks against data.csv.', status: 'done', review_status: '', delivered_at: Date.parse(ts) / 1000,
    primary_target: { path: 'report/index.html', label: 'index.html', source: 'manager', why: '' },
    targets: [
      { path: 'report/index.html', label: 'index.html', source: 'manager', why: '' },
      { path: 'figures/totals.png', label: 'totals.png', source: 'manager', why: '' },
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
  assert.equal((await response.json() as { code: string }).code, 'invalid_workdir');
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const empty = await post(`/api/projects/${sid}/prompt/rewrite`, { text: '' });
  assert.equal(empty.status, 400);
  assert.deepEqual(await empty.json(), { code: 'input_required', detail: 'empty prompt' });
});
