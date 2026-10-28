import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { appendFile, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspectApiMeta, RELEASE_ID, requireSnapshotContract, type EventMsg, type Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent, HandOff, Role } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** One scripted turn: the role expected, its letter (no `to`: none), and what it does first. */
interface Step {
  role: Role;
  to?: string;
  letter?: string;
  /** Files the turn writes into the workdir. */
  files?: Record<string, string>;
  /** The turn lasts until it is interrupted. */
  hang?: boolean;
}

function agent(steps: Step[]): { callAgent: CallAgent; requests: AgentRequest[] } {
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    const step = steps.shift();
    assert.ok(step, `no turn scripted for the ${request.role}`);
    assert.equal(request.role, step.role);
    for (const [name, body] of Object.entries(step.files ?? {})) await writeFile(join(request.cwd, name), body);
    if (step.hang) {
      await new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }));
      return { callId: 'c', threadId: null, completed: false, error: 'interrupted', handoff: null, model: 'fake', usage: null, ms: 1 };
    }
    const handoff: HandOff | null = step.to === undefined ? null
      : { form: request.form, to: step.to, letter: step.letter ?? `${step.role} to ${step.to}` };
    if (handoff) assert.equal(request.check?.(handoff) ?? null, null);
    const tokens = { input_tokens: 10, cached_input_tokens: 0, output_tokens: 5, reasoning_output_tokens: 0 };
    await appendFile(request.usageFile, `${JSON.stringify({ ts: Date.now() / 1000, role: request.role, tokens, cost_usd: 0.01 })}\n`);
    return {
      callId: `call-${requests.length}`, threadId: `${request.role}-${requests.length}`, completed: true,
      error: handoff ? null : 'The turn ended without the hand-off form.', handoff, model: 'fake', usage: { ...tokens, cost_usd: 0.01 }, ms: 1,
    };
  };
  return { callAgent, requests };
}

async function setup(t: TestContext, steps: Step[], token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-serve-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(join(web, 'assets'), { recursive: true });
  await writeFile(join(web, 'index.html'), '<!doctype html><title>Argus</title>');
  await writeFile(join(web, 'assets', 'app-1.js'), 'export {};');
  const store = new Store(home);
  const { callAgent, requests } = agent(steps);
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
  return { home, store, requests, call, get, post };
}

/** Every `data:` frame of a server-sent event stream; `until` stops reading an endless one. */
async function frames(response: Response, until?: (frame: EventMsg) => boolean): Promise<EventMsg[]> {
  const read: EventMsg[] = [];
  const decoder = new TextDecoder();
  let buffer = '';
  for await (const chunk of response.body!) {
    buffer += decoder.decode(chunk, { stream: true });
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const frame = JSON.parse(buffer.slice(0, end).replace(/^data: /, '')) as EventMsg;
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
    { role: 'engineer', to: 'reviewer', letter: 'report.md written',
      files: { 'report.md': '# Totals\n\n| a | b |\n', 'page.html': '<script>alert(1)</script>', 'data.bin': '0123456789', '.hidden': 'secret' } },
    { role: 'reviewer', to: 'operator', letter: 'Ship it as is?' },
    { role: 'reviewer', to: 'done', letter: 'accepted' },
  ]);

  const metaResponse = await call('/api/meta');
  assert.equal(metaResponse.headers.get('x-argus-release'), RELEASE_ID);
  const compatibility = inspectApiMeta(await metaResponse.json(), { releaseId: RELEASE_ID });
  assert.equal(compatibility.compatible, true, compatibility.reason);
  assert.deepEqual(compatibility.meta?.authentication, { required: false, authenticated: true });
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
  assert.deepEqual(reply[1], { type: 'delta', text: brief, message_id: 'm0-argus', fragment_mode: 'snapshot' });
  assert.deepEqual(reply[2], { type: 'done', result: { kind: 'task', reply: brief } });

  const asking = await eventually(() => get<Snapshot>(`${P}/snapshot?compact=true&events_limit=1`),
    snapshot => Boolean(snapshot.pending_questions?.length) && !snapshot.daemon.alive);
  requireSnapshotContract(asking);
  assert.equal(asking.session.objective, 'Total the columns of data.csv');
  assert.deepEqual(asking.backlog.map(item => [item.id, item.status]), [['t1', 'blocked']]);
  assert.equal(asking.usage_summary?.call_count, 3);
  assert.equal(asking.spend_usd, 0.03);
  const card = (asking.pending_questions![0] as { operator_decision: { id: string; question: string; options: unknown[] } }).operator_decision;
  assert.deepEqual([card.question, card.options], ['Ship it as is?', []]);

  const { events } = await get<{ events: EventMsg[] }>(`${P}/events?limit=120&view=ui`);
  assert.deepEqual(events.map(event => event.type), [
    'ui.operator', 'provider.request.started', 'provider.request.completed', 'ui.argus', 'life.mission.started',
    'provider.request.started', 'provider.request.completed', 'engineer.progress',
    'provider.request.started', 'provider.request.completed', 'round.review.completed',
  ]);
  const { turns } = await get<{ turns: { role: string; text: string; message_id: string }[] }>(`${P}/transcript?n=120`);
  assert.deepEqual(turns.map(turn => [turn.role, turn.message_id]), [['operator', 'm0-operator'], ['argus', 'm0-argus']]);

  assert.equal((await post(`${P}/decisions/q-stale/resolve`, { option_id: 'custom', note: 'yes' })).status, 409);
  assert.equal((await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: ' ' })).status, 400);
  assert.deepEqual(await (await post(`${P}/decisions/${card.id}/resolve`, { option_id: 'custom', note: 'Ship it today' })).json(), { resolved: true });
  const done = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done' && !snapshot.daemon.alive);
  assert.deepEqual(done.pending_questions, []);
  assert.match(requests.at(-1)!.prompt, /They answered: Ship it today/);

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
    assert.equal((await call(`${P}/artifact/raw?path=${encodeURIComponent(outside)}`)).status, 404);
  }
});

test('a refused form goes back to the Manager within the same message', async t => {
  const { post } = await setup(t, [{ role: 'manager' }, { role: 'manager', to: 'manager', letter: 'Hello.' }]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const reply = await frames(await post(`/api/projects/${sid}/message/stream`, { text: 'Hi', request_id: 'r1' }));
  assert.deepEqual(reply.map(frame => frame.type), ['phase', 'phase', 'delta', 'done']);
  assert.deepEqual(reply.at(-1), { type: 'done', result: { kind: 'chat', reply: 'Hello.' } });
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

test('a message can be cancelled during the Manager turn, and stop and start interrupt and resume the work', async t => {
  const { get, post, requests } = await setup(t, [
    { role: 'manager', hang: true },
    { role: 'manager', to: 'engineer', letter: brief },
    { role: 'engineer', hang: true },
    { role: 'engineer', to: 'reviewer' },
    { role: 'reviewer', to: 'done' },
  ]);
  const { sid } = await (await post('/api/daemons', {})).json() as { sid: string };
  const P = `/api/projects/${sid}`;

  const cancelled = frames(await post(`${P}/message/stream`, { text: 'first', request_id: 'r1' }));
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.manager_requests?.[0]?.request_id === 'r1' && snapshot.roles[0]!.active);
  assert.deepEqual(await (await post(`${P}/message/cancel`, { request_id: 'r1' })).json(), { requested: true, status: 'cancelled' });
  assert.deepEqual((await cancelled).at(-1), { type: 'done', result: { kind: 'cancelled', reply: null } });
  assert.deepEqual(await (await post(`${P}/message/cancel`, { request_id: 'r1' })).json(), { requested: false, status: 'finished' });

  const reply = await frames(await post(`${P}/message/stream`, { text: 'second', request_id: 'r2' }));
  assert.equal(reply.at(-1)?.type, 'done');
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.roles.find(role => role.role === 'engineer')!.active);
  const stopped = await (await post(`${P}/daemon/stop`, { command_id: 'c1' })).json() as { rc: number };
  assert.equal(stopped.rc, 0);
  const paused = await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => !snapshot.daemon.alive);
  assert.deepEqual(paused.backlog.map(item => item.status), ['in_progress']);
  assert.equal((await (await post(`${P}/daemon/stop`, { after_current: true })).json() as { rc: number }).rc, 1);

  assert.equal((await (await post(`${P}/daemon/start`, { command_id: 'c2' })).json() as { rc: number }).rc, 0);
  await eventually(() => get<Snapshot>(`${P}/snapshot`), snapshot => snapshot.backlog[0]?.status === 'done');
  assert.deepEqual(requests.map(request => request.role), ['manager', 'manager', 'engineer', 'engineer', 'reviewer']);
  // The interrupted Engineer starts afresh: its turn did not end.
  assert.equal(requests[3]!.resume, undefined);
});
