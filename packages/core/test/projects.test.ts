import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** A cockpit whose Manager answers at once, or, to a message that says `<hang>`, once it is cancelled. */
async function setup(t: TestContext) {
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
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web });
  t.after(() => served.close());
  const call = (path: string, init: RequestInit = {}) => fetch(served.url + path, init);
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
