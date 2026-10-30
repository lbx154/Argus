import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { statSync } from 'node:fs';
import { appendFile, cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import type { Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** A cockpit whose Manager answers at once, except its first `<hang>` turn, which waits for Stop. */
async function setup(t: TestContext, token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-projects-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(web);
  await writeFile(join(web, 'manifest.webmanifest'), '{"name":"Argus"}');
  const requests: AgentRequest[] = [];
  const held = new Set<string>();
  const callAgent: CallAgent = async request => {
    requests.push(request);
    if (request.prompt.includes('<hang>') && !held.has(request.cwd)) {
      held.add(request.cwd);
      await new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }));
    }
    const handoff = request.signal?.aborted ? null : { form: request.form, to: 'operator', letters: ['Hello.'] };
    return { callId: 'c', threadId: 'thread', completed: Boolean(handoff), error: handoff ? null : 'interrupted', handoff, model: null, usage: null, ms: 1 };
  };
  // Codex names its models; the others name none here.
  callAgent.models = async backend => backend === 'codex' ? [{ model: 'gpt-a', default: true }, { model: 'gpt-b', default: false }] : [];
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
  /** A Manager turn under way on the project until the returned function stops the project. */
  const busy = async (sid: string): Promise<() => Promise<void>> => {
    const reply = send('POST', `/api/projects/${sid}/message/stream`, { text: '<hang>', request_id: 'r' });
    for (let tries = 0; !(await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive; tries++) {
      assert.ok(tries < 200, 'the turn never started');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return async () => {
      await send('POST', `/api/projects/${sid}/daemon/stop`);
      await (await reply).text();
      while ((await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive) await new Promise(resolve => setTimeout(resolve, 10));
    };
  };
  return { home, store, requests, call, send, get, create, busy };
}

test('a project works in a directory, made if it is new, set when the project is made or later, while nothing is under way on it', async t => {
  const { home, store, send, get, create, busy } = await setup(t);
  const repo = join(home, 'repo');
  await mkdir(repo);
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.workdir.v1'));
  const sid = await create({ workdir: repo });
  assert.equal(store.load(sid).workdir, repo);
  const made = await create({ workdir: join(home, 'new', 'dir') });
  assert.equal(store.load(made).workdir, join(home, 'new', 'dir'));
  assert.ok(statSync(join(home, 'new', 'dir')).isDirectory());

  const other = join(home, 'other');
  await mkdir(other);
  const fresh = await create();
  assert.equal(store.load(fresh).workdir, join(home, 'workspaces', fresh));
  store.save({ ...store.load(fresh), sessions: { manager: 'thread' } });
  for (const workdir of ['', join(home, 'web', 'manifest.webmanifest')]) {
    assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir })).status, 400, workdir);
  }
  assert.deepEqual(await (await send('POST', `/api/projects/${fresh}/workdir`, { workdir: other })).json(), { ok: true, workdir: other });
  // The Manager's session worked in the old directory: it starts afresh.
  assert.deepEqual([store.load(fresh).workdir, store.load(fresh).sessions], [other, {}]);
  assert.equal((await get<Snapshot>(`/api/projects/${fresh}/snapshot`)).session.workdir, other);

  const stop = await busy(fresh);
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 409);
  await stop();
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 200);
  assert.equal((await send('POST', '/api/projects/p-00000000/workdir', { workdir: repo })).status, 404);
});

test('a project has its own backend, model and effort, which the settings change and every call uses', async t => {
  const { store, requests, send, get, create, busy } = await setup(t);
  // The page offers its settings and the model chooser where the server declares them.
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.config.v1'));
  const sid = await create();
  const P = `/api/projects/${sid}`;
  const set = (name: string, value: string) => send('POST', `${P}/config/set`, { name, value });
  type Config = {
    roles: { role: string; backend: string; model: string; reasoning_effort: string | null }[];
    operator_knobs: { name: string; value: string }[]; backends: Record<string, string>;
  };
  const config = await get<Config>(`${P}/config`);
  assert.deepEqual(config.roles.map(role => [role.role, role.backend, role.model, role.reasoning_effort]),
    ['manager', 'planner', 'engineer', 'reviewer'].map(role => [role, 'codex', '', null]));
  assert.deepEqual(config.operator_knobs.map(knob => [knob.name, knob.value]), [['ARGUS_SKILL_RUNNER_BACKEND', 'codex'], ['ARGUS_SKILL_MODEL', '']]);
  // The backends the page offers are those there are.
  assert.deepEqual(config.backends, { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' });
  type Choices = { backend: string; options: { model: string; default: boolean }[] };
  assert.deepEqual((await get<Choices>(`${P}/model-choices`)).options.map(({ model, default: chosen }) => [model, chosen]),
    [['gpt-a', true], ['gpt-b', false]]);
  // A model is tried with a short turn of its own, apart from the Manager's session.
  const probe = await (await send('POST', `${P}/model-choices/probe`, { model: 'gpt-b' })).json() as { model: string; ok: boolean; error: string };
  assert.deepEqual([probe.model, probe.ok, probe.error], ['gpt-b', true, '']);
  assert.deepEqual(requests.splice(0).map(({ model, access, session, resume }) => ({ model, access, session, resume })),
    [{ model: 'gpt-b', access: 'read', session: undefined, resume: undefined }]);

  store.save({ ...store.load(sid), sessions: { manager: 'codex-thread' } });
  assert.equal((await set('ARGUS_SKILL_RUNNER_BACKEND', 'cursor')).status, 400);
  assert.equal((await set('ARGUS_SKILL_RUNNER_BACKEND', 'claude')).status, 200);
  assert.deepEqual(store.load(sid).sessions, {}, 'a Codex session is no use to Claude');
  assert.equal((await set('ARGUS_SKILL_MODEL', 'claude-opus-4-1')).status, 200);
  assert.equal((await set('ARGUS_SKILL_REASONING_EFFORT', 'high')).status, 200);
  // One model and effort for every role, and nothing a backend's command line would read as an option.
  for (const [name, value] of [['ARGUS_SKILL_ENGINEER_MODEL', 'gpt-5.5'], ['ARGUS_SKILL_LIGHT_MODEL', 'gpt-5.5'],
    ['ARGUS_SKILL_MANAGER_REASONING_EFFORT', 'low'], ['ARGUS_SKILL_MODEL', '--dangerously-skip-permissions'],
    ['ARGUS_SKILL_REASONING_EFFORT', 'very high']] as const) {
    assert.equal((await set(name, value)).status, 400, `${name}=${value}`);
  }
  assert.deepEqual((await get<Config>(`${P}/config`)).roles[2], {
    role: 'engineer', backend: 'claude', backend_label: 'Claude Code', backend_source: 'persisted', model: 'claude-opus-4-1',
    model_source: 'persisted', reasoning_effort: 'high', reasoning_effort_source: 'persisted', description: '',
  });
  assert.deepEqual((await get<Snapshot>(`${P}/snapshot`)).roles.map(role => [role.backend, role.model, role.effort]),
    Array(4).fill(['claude', 'claude-opus-4-1', 'high']));
  const choices = await get<Choices>(`${P}/model-choices`);
  assert.deepEqual([choices.backend, choices.options.map(option => option.model)], ['claude', ['claude-opus-4-1']]);

  await (await send('POST', `${P}/message`, { text: 'Hi' })).json();
  assert.deepEqual(requests.map(({ backend, model, effort }) => ({ backend, model, effort })), [{ backend: 'claude', model: 'claude-opus-4-1', effort: 'high' }]);
  assert.equal((await set('ARGUS_SKILL_MODEL', 'auto')).status, 200);
  assert.equal((await set('ARGUS_SKILL_REASONING_EFFORT', '')).status, 200);
  await (await send('POST', `${P}/message`, { text: 'Hi again' })).json();
  assert.deepEqual([requests[1]!.model, requests[1]!.effort], [undefined, undefined]);

  // While work is under way the model changes in the record the work holds, for the calls after.
  const stop = await busy(sid);
  assert.equal((await set('ARGUS_SKILL_MODEL', 'claude-sonnet-4-5')).status, 200);
  assert.equal((await set('ARGUS_SKILL_RUNNER_BACKEND', 'codex')).status, 409);
  await stop();
  assert.deepEqual([store.load(sid).model, store.load(sid).backend], ['claude-sonnet-4-5', 'claude']);
});

test('a project is named by the operator, marked in the sidebar, and restored from the trash', async t => {
  const { home, store, send, get, create, busy } = await setup(t);
  const sid = await create();
  type Rows = { projects: { id: string; label: string; display_name: string }[] };
  const row = async () => (await get<Rows>('/api/projects')).projects.find(project => project.id === sid);
  assert.deepEqual(await (await send('PATCH', `/api/projects/${sid}`, { name: '  Column\n  totals ' })).json(), { ok: true, sid, name: 'Column totals' });
  assert.deepEqual([(await row())?.label, (await row())?.display_name], ['Column totals', 'Column totals']);
  assert.equal((await get<Snapshot>(`/api/projects/${sid}/snapshot`)).session.display_name, 'Column totals');
  await send('PATCH', `/api/projects/${sid}`, { name: ' ' });
  // Unnamed again, it is named by its request.
  store.save({ ...store.load(sid), request: 'Total the columns of data.csv\nand plot them' });
  assert.deepEqual([(await row())?.label, (await row())?.display_name], [sid, 'Total the columns of data.csv']);
  const stop = await busy(sid);
  assert.equal((await send('PATCH', `/api/projects/${sid}`, { name: 'Later' })).status, 200);
  assert.equal((await row())?.label, 'Later');
  await stop();
  assert.equal(store.load(sid).name, 'Later');

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

test('list rows and snapshots share ledger spend, without a separate cost feed', async t => {
  const { home, get, create, call } = await setup(t);
  assert.equal((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.cost-feed.v1'), false);
  const spent = await create();
  for (const cost of [0.25, 0.5]) await appendFile(join(home, 'projects', spent, 'usage.jsonl'), `${JSON.stringify({ ts: 1, role: 'engineer', cost_usd: cost })}\n`);
  const snapshot = await get<Record<string, unknown>>(`/api/projects/${spent}/snapshot`);
  assert.equal(snapshot.spend_usd, 0.75);
  assert.equal(snapshot.spend_status, 'priced');
  assert.equal((await call('/api/projects/costs')).status, 404);
  const rows = await get<{ projects: Record<string, unknown>[] }>('/api/projects');
  const row = rows.projects.find(row => row.id === spent)!;
  assert.equal(row.spend_usd, 0.75);
  assert.equal(row.spend_status, 'priced');
  assert.equal(row.spend_usd, snapshot.spend_usd);
  assert.equal(row.spend_status, snapshot.spend_status);
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
  const stop = await busy(sid);
  assert.deepEqual(await (await send('POST', `${P}/reset`)).json(), { ok: true });
  await stop();
  assert.deepEqual(store.load(sid).sessions, {});
  assert.equal(store.load(sid).request, 'Hi');
  await (await send('POST', `${P}/message`, { text: 'Hi again' })).json();
  // The next message is answered in a new session, in place of the one the stop interrupted.
  assert.deepEqual(requests.map(request => request.resume), [undefined, 'thread', undefined]);
  assert.deepEqual(store.load(sid).forManager, []);
});

test('the cockpit is served beyond this machine only with a token', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-host-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const options = { store: new Store(home), callAgent: (() => { throw new Error('no call'); }) as CallAgent, backend: 'codex' as const, model: null, port: 0, web: home };
  await assert.rejects(serve({ ...options, host: '0.0.0.0' }), /needs ARGUS_WEB_TOKEN/);
  const served = await serve({ ...options, host: '0.0.0.0', token: 'secret' });
  t.after(() => served.close());
  const { port } = new URL(served.url);
  assert.equal(served.url, `http://0.0.0.0:${port}`);
  assert.equal((await fetch(`http://127.0.0.1:${port}/api/projects`)).status, 401);
  assert.equal((await fetch(`http://127.0.0.1:${port}/api/projects`, { headers: { authorization: 'Bearer secret' } })).status, 200);
});

test('directory errors distinguish files, unavailable parents and real permission failures', async t => {
  const { home, store, send } = await setup(t);
  const file = join(home, 'a-file');
  await writeFile(file, 'not a directory');
  const cases: Array<[string, string, string]> = [
    ['relative/dir', 'workdir_relative', 'relative'],
    ['./relative', 'workdir_relative', 'relative'],
    ['../parent', 'workdir_relative', 'relative'],
    [file, 'workdir_not_directory', 'file'],
    [join(file, 'child'), 'workdir_not_directory', 'ENOTDIR'],
  ];
  if (process.platform === 'linux') cases.push(['/proc/argus-missing-parent/child', 'workdir_parent_missing', 'ENOENT']);
  if (process.platform !== 'win32' && process.getuid?.() !== 0) {
    const denied = join(home, 'denied');
    await mkdir(denied, { mode: 0o500 });
    cases.push([denied, 'workdir_permission_denied', 'EACCES'], [join(denied, 'child'), 'workdir_permission_denied', 'EACCES']);
  }
  for (const [workdir, code, reason] of cases) {
    const response = await send('POST', '/api/daemons', { workdir });
    assert.equal(response.status, 400, workdir);
    const body = await response.json() as { code: string; detail: string };
    assert.equal(body.code, code, workdir);
    assert.ok(body.detail.includes(workdir), body.detail);
    assert.ok(body.detail.includes(reason), body.detail);
  }
  assert.equal(store.list().length, 0);
  const created = await (await send('POST', '/api/daemons', { workdir: home })).json() as { sid: string };
  const changed = await send('POST', `/api/projects/${created.sid}/workdir`, { workdir: 'relative/dir' });
  assert.equal(changed.status, 400);
  assert.equal((await changed.json() as { code: string }).code, 'workdir_relative');
  assert.equal(store.load(created.sid).workdir, home);
});

test('project names stay complete through request naming, automatic naming, rename and trash restore', async t => {
  const { store, create, send, get } = await setup(t);
  const title = '完整项目名称 long word '.repeat(12) + '结尾';
  const sid = await create();
  await (await send('POST', `/api/projects/${sid}/message/stream`, { text: title, request_id: 'long-name' })).text();
  const snapshot = () => get<Snapshot>(`/api/projects/${sid}/snapshot`);
  assert.equal((await snapshot()).session.display_name, title);
  const renamed = title + ' renamed';
  assert.equal((await send('PATCH', `/api/projects/${sid}`, { name: renamed })).status, 200);
  assert.equal(store.load(sid).name, renamed);
  assert.equal((await snapshot()).session.display_name, renamed);
  assert.equal((await get<{ projects: { id: string; display_name: string }[] }>('/api/projects')).projects.find(row => row.id === sid)!.display_name, renamed);
  const { trash_path } = await (await send('DELETE', `/api/projects/${sid}`)).json() as { trash_path: string };
  const entry = (await get<{ entries: { label: string; trashed_at: number }[] }>('/api/trash')).entries[0]!;
  assert.equal(entry.label, renamed);
  assert.ok(entry.trashed_at > 0);
  assert.equal((await send('POST', `/api/trash/${basename(trash_path)}/restore`)).status, 200);
  assert.equal((await snapshot()).session.display_name, renamed);
});
