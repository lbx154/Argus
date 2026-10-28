import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { appendFile, cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import type { Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** A cockpit whose Manager answers at once, or, to a message that says `<hang>`, once it is cancelled. */
async function setup(t: TestContext, token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-projects-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(web);
  await writeFile(join(web, 'manifest.webmanifest'), '{"name":"Argus"}');
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    if (request.prompt.includes('<hang>')) await new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }));
    const handoff = request.signal?.aborted ? null : { form: request.form, to: 'operator', letters: ['Hello.'] };
    return { callId: 'c', threadId: 'thread', completed: Boolean(handoff), error: handoff ? null : 'interrupted', handoff, model: null, usage: null, ms: 1 };
  };
  const store = new Store(home);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web, ...(token ? { token } : {}) });
  t.after(() => served.close());
  const call = (path: string, init: RequestInit = {}) =>
    fetch(served.url + path, { ...init, headers: { ...token ? { authorization: `Bearer ${token}` } : {}, ...init.headers } });
  const send = (method: string, path: string, body?: unknown) =>
    call(path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });
  const get = async <T = Record<string, unknown>>(path: string): Promise<T> => {
    const response = await call(path);
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return await response.json() as T;
  };
  const create = async (body = {}): Promise<string> => (await (await send('POST', '/api/daemons', body)).json() as { sid: string }).sid;
  /** A Manager turn under way on the project until the returned function cancels it. */
  const busy = async (sid: string): Promise<() => Promise<void>> => {
    const reply = send('POST', `/api/projects/${sid}/message/stream`, { text: '<hang>', request_id: 'r' });
    for (let tries = 0; !(await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive; tries++) {
      assert.ok(tries < 200, 'the turn never started');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return async () => {
      await send('POST', `/api/projects/${sid}/message/cancel`, { request_id: 'r' });
      await (await reply).text();
      while ((await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive) await new Promise(resolve => setTimeout(resolve, 10));
    };
  };
  return { home, store, requests, call, send, get, create, busy };
}

test('a project works in an existing directory, set when it is made or later, while nothing is under way on it', async t => {
  const { home, store, send, get, create, busy } = await setup(t);
  const repo = join(home, 'repo');
  await mkdir(repo);
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.workdir.v1'));
  const sid = await create({ workdir: repo });
  assert.equal(store.load(sid).workdir, repo);
  assert.equal((await send('POST', '/api/daemons', { workdir: join(home, 'missing') })).status, 400);

  const other = join(home, 'other');
  await mkdir(other);
  const fresh = await create();
  assert.equal(store.load(fresh).workdir, join(home, 'workspaces', fresh));
  store.save({ ...store.load(fresh), sessions: { manager: 'thread' } });
  for (const workdir of ['', join(home, 'missing'), join(home, 'web', 'manifest.webmanifest')]) {
    assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir })).status, 400, workdir);
  }
  assert.deepEqual(await (await send('POST', `/api/projects/${fresh}/workdir`, { workdir: other })).json(), { ok: true, workdir: other });
  // The Manager's session worked in the old directory: it starts afresh.
  assert.deepEqual([store.load(fresh).workdir, store.load(fresh).sessions], [other, {}]);
  assert.equal((await get<Snapshot>(`/api/projects/${fresh}/snapshot`)).session.workdir, other);

  const cancel = await busy(fresh);
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 409);
  await cancel();
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 200);
  assert.equal((await send('POST', '/api/projects/p-00000000/workdir', { workdir: repo })).status, 404);
});

test('a project has its own backend, model and effort, which the settings change and every call uses', async t => {
  const { store, requests, send, get, create, busy } = await setup(t);
  // The page offers its settings and the model chooser where the server declares them.
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.config.v1'));
  const sid = await create();
  const P = `/api/projects/${sid}`;
  const set = (name: string, value: string) => send('POST', `${P}/config/set`, { name, value, apply_to_roles: true });
  type Config = { roles: { role: string; backend: string; model: string; reasoning_effort: string | null }[]; operator_knobs: { name: string; value: string }[] };
  const config = await get<Config>(`${P}/config`);
  assert.deepEqual(config.roles.map(role => [role.role, role.backend, role.model, role.reasoning_effort]),
    ['manager', 'planner', 'engineer', 'reviewer'].map(role => [role, 'codex', '', null]));
  assert.deepEqual(config.operator_knobs.map(knob => [knob.name, knob.value]), [['ARGUS_SKILL_RUNNER_BACKEND', 'codex'], ['ARGUS_SKILL_MODEL', '']]);
  assert.deepEqual((await get(`${P}/model-choices`)).options, []);

  store.save({ ...store.load(sid), sessions: { manager: 'codex-thread' } });
  assert.equal((await set('ARGUS_SKILL_RUNNER_BACKEND', 'cursor')).status, 400);
  assert.equal((await set('ARGUS_SKILL_RUNNER_BACKEND', 'claude')).status, 200);
  assert.deepEqual(store.load(sid).sessions, {}, 'a Codex session is no use to Claude');
  assert.equal((await set('ARGUS_SKILL_MODEL', 'claude-opus-4-1')).status, 200);
  for (const role of ['MANAGER', 'PLANNER', 'ENGINEER', 'REVIEWER']) assert.equal((await set(`ARGUS_SKILL_${role}_REASONING_EFFORT`, 'high')).status, 200);
  // One model for every role, and nothing a backend's command line would read as an option.
  for (const [name, value] of [['ARGUS_SKILL_ENGINEER_MODEL', 'gpt-5.5'], ['ARGUS_SKILL_LIGHT_MODEL', 'gpt-5.5'],
    ['ARGUS_SKILL_MODEL', '--dangerously-skip-permissions'], ['ARGUS_SKILL_MANAGER_REASONING_EFFORT', 'very high']] as const) {
    assert.equal((await set(name, value)).status, 400, `${name}=${value}`);
  }
  assert.deepEqual((await get<Config>(`${P}/config`)).roles[2], {
    role: 'engineer', backend: 'claude', backend_label: 'Claude Code', backend_source: 'persisted', model: 'claude-opus-4-1',
    model_source: 'persisted', reasoning_effort: 'high', reasoning_effort_source: 'persisted', description: '',
  });
  assert.deepEqual((await get<Snapshot>(`${P}/snapshot`)).roles.map(role => [role.backend, role.model, role.effort]),
    Array(4).fill(['claude', 'claude-opus-4-1', 'high']));
  const choices = await get<{ backend: string; options: { model: string }[] }>(`${P}/model-choices`);
  assert.deepEqual([choices.backend, choices.options.map(option => option.model)], ['claude', ['claude-opus-4-1']]);

  await (await send('POST', `${P}/message`, { text: 'Hi' })).json();
  assert.deepEqual(requests.map(({ backend, model, effort }) => ({ backend, model, effort })), [{ backend: 'claude', model: 'claude-opus-4-1', effort: 'high' }]);
  assert.equal((await set('ARGUS_SKILL_MODEL', 'auto')).status, 200);
  assert.equal((await set('ARGUS_SKILL_MANAGER_REASONING_EFFORT', '')).status, 200);
  await (await send('POST', `${P}/message`, { text: 'Hi again' })).json();
  assert.deepEqual([requests[1]!.model, requests[1]!.effort], [undefined, undefined]);

  const cancel = await busy(sid);
  assert.equal((await set('ARGUS_SKILL_MODEL', 'claude-sonnet-4-5')).status, 409);
  await cancel();
  assert.equal(store.load(sid).model, null);
});

test('a project is named by the operator, marked in the sidebar, and restored from the trash', async t => {
  const { home, send, get, create, busy } = await setup(t);
  const sid = await create();
  type Rows = { projects: { id: string; label: string; display_name: string }[] };
  const row = async () => (await get<Rows>('/api/projects')).projects.find(project => project.id === sid);
  assert.deepEqual(await (await send('PATCH', `/api/projects/${sid}`, { name: '  Column\n  totals ' })).json(), { ok: true, sid, name: 'Column totals' });
  assert.deepEqual([(await row())?.label, (await row())?.display_name], ['Column totals', 'Column totals']);
  assert.equal((await get<Snapshot>(`/api/projects/${sid}/snapshot`)).session.display_name, 'Column totals');
  await send('PATCH', `/api/projects/${sid}`, { name: ' ' });
  // Unnamed again, the page names it by its request.
  assert.deepEqual([(await row())?.label, (await row())?.display_name], [sid, '']);
  const cancel = await busy(sid);
  assert.equal((await send('PATCH', `/api/projects/${sid}`, { name: 'Later' })).status, 409);
  await cancel();

  assert.deepEqual(await get('/api/browser-state/organization'), { value: {}, revision: 0 });
  const organize = (body: unknown) => send('POST', '/api/browser-state/organization', body);
  assert.equal((await organize({ sid, field: 'pinned', value: true })).status, 200);
  const marked = { value: { [sid]: { pinned: true, archived: true, followup: false } }, revision: 2 };
  assert.deepEqual(await (await organize({ sid, field: 'archived', value: true })).json(), marked);
  assert.deepEqual(await get('/api/browser-state/organization'), marked);
  assert.deepEqual(JSON.parse(await readFile(join(home, 'browser-state.json'), 'utf8')), { organization: marked });
  for (const [body, status] of [[{ sid: 'p-00000000', field: 'pinned', value: true }, 404], [{ sid, field: 'hidden', value: true }, 400],
    [{ sid, field: 'pinned', value: 'yes' }, 400]] as const) assert.equal((await organize(body)).status, status, JSON.stringify(body));

  await send('PATCH', `/api/projects/${sid}`, { name: 'Column totals' });
  const { trash_path } = await (await send('DELETE', `/api/projects/${sid}`)).json() as { trash_path: string };
  assert.equal(await row(), undefined);
  type Trash = { entries: { trash_id: string; sid: string; label: string; launch_cwd: string; trash_path: string; trashed_at: number }[]; total: number };
  const { entries, total } = await get<Trash>('/api/trash?query=&limit=100&offset=0');
  assert.equal(total, 1);
  const [entry] = entries;
  assert.deepEqual({ ...entry, trashed_at: entry!.trashed_at > 0 }, {
    trash_id: basename(trash_path), sid, label: 'Column totals', launch_cwd: join(home, 'workspaces', sid), trash_path, trashed_at: true,
  });
  assert.equal((await get<Trash>('/api/trash?query=column')).total, 1);
  assert.equal((await get<Trash>('/api/trash?query=nothing')).total, 0);
  for (const id of ['..', 'p-00000000-1', encodeURIComponent('../projects')]) assert.equal((await send('POST', `/api/trash/${id}/restore`)).status, 404, id);
  assert.deepEqual(await (await send('POST', `/api/trash/${entry!.trash_id}/restore`)).json(), { ok: true, sid });
  assert.equal((await row())?.label, 'Column totals');
  assert.deepEqual((await get<Trash>('/api/trash')).entries, []);
  // A copy in the trash of a project that is back is not restored over it.
  await cp(join(home, 'projects', sid), join(home, 'trash', `${sid}-copy`), { recursive: true });
  assert.equal((await send('POST', `/api/trash/${sid}-copy/restore`)).status, 409);
});

test('the spend of every project, which the page asks for every few seconds', async t => {
  const { home, get, create } = await setup(t);
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.cost-feed.v1'));
  const [spent, idle] = [await create(), await create()];
  const tokens = { input_tokens: 10, cached_input_tokens: 0, output_tokens: 5, reasoning_output_tokens: 0 };
  for (const cost of [0.25, 0.5]) {
    await appendFile(join(home, 'projects', spent, 'usage.jsonl'), `${JSON.stringify({ ts: 1, role: 'engineer', tokens, cost_usd: cost })}\n`);
  }
  const { projects, generated_at } = await get<{ projects: Record<string, unknown>[]; generated_at: number }>('/api/projects/costs');
  assert.ok(generated_at > 0);
  const rows = Object.fromEntries(projects.map(row => [row.id, row]));
  const { updated_at, ...cost } = rows[spent]!;
  assert.deepEqual(cost, { id: spent, spend_usd: 0.75, known_cost_usd: 0.75, spend_status: 'priced', usage_calls: 2, premium_requests: 0 });
  assert.ok(Number(updated_at) > 0);
  assert.deepEqual([rows[idle]!.usage_calls, rows[idle]!.known_cost_usd], [0, 0]);
});

test('the manifest that makes the cockpit installable is the built one, and needs no token', async t => {
  const { call } = await setup(t, 'secret');
  const manifest = await call('/api/application-brand/manifest.webmanifest', { headers: { authorization: '' } });
  assert.equal(manifest.status, 200);
  assert.equal(manifest.headers.get('content-type'), 'application/manifest+json');
  assert.deepEqual(await manifest.json(), { name: 'Argus' });
  assert.equal((await call('/api/projects', { headers: { authorization: '' } })).status, 401);
});

test('resetting the conversation gives the Manager a new session, and keeps the record', async t => {
  const { store, requests, send, create, busy } = await setup(t);
  const sid = await create();
  const P = `/api/projects/${sid}`;
  await (await send('POST', `${P}/message`, { text: 'Hi' })).json();
  assert.equal(store.load(sid).sessions.manager, 'thread');
  const cancel = await busy(sid);
  assert.equal((await send('POST', `${P}/reset`)).status, 409);
  await cancel();
  assert.deepEqual(await (await send('POST', `${P}/reset`)).json(), { ok: true });
  assert.deepEqual(store.load(sid).sessions, {});
  assert.equal(store.load(sid).request, 'Hi');
  await (await send('POST', `${P}/message`, { text: 'Hi again' })).json();
  assert.deepEqual(requests.map(request => request.resume), [undefined, 'thread', undefined]);
});
