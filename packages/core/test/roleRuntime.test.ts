import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';
import { Flow } from '../src/flow.js';
import { applyRoleRuntimes, roleRuntime } from '../src/roleRuntime.js';
import { serve } from '../src/serve/index.js';
import { piModels } from '../src/agent/models.js';
import { dshHandOff, dshOverlay } from '../src/agent/dsh.js';
import { readFileSync } from 'node:fs';
import type { AgentRequest, CallAgent } from '../src/types.js';

const answer = (request: AgentRequest, to = 'operator') => ({ callId: 'test', threadId: `${request.backend}-session`, completed: true, error: null,
  handoff: { form: request.form, to, letters: ['ok'] }, model: request.model ?? `${request.backend}/default`, usage: null, ms: 1 });

async function fixture(t: { after(fn: () => unknown): void }) {
  const home = await mkdtemp(join(tmpdir(), 'argus-role-routes-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  return { home, store, project };
}

test('legacy defaults resolve independently; a Pi Manager never borrows a DSH model or resets other sessions', async t => {
  const { store, project } = await fixture(t);
  project.sessions = { manager: 'old-codex', planner: 'keep-planner', 't1.engineer': 'old-engineer', 't1.reviewer': 'keep-reviewer' };
  applyRoleRuntimes(project, {
    manager: { backend: 'pi', model: 'copilot/sol', effort: 'high' },
    engineer: { backend: 'dsh', model: 'deepseek-official/flash' },
  });
  store.save(project);
  const loaded = store.load(project.id);
  assert.equal(roleRuntime(loaded, 'manager').backend, 'pi');
  assert.equal(roleRuntime(loaded, 'engineer').model, 'deepseek-official/flash');
  assert.equal(roleRuntime(loaded, 'planner').backend, 'codex');
  assert.deepEqual(loaded.sessions, { planner: 'keep-planner', 't1.reviewer': 'keep-reviewer' });
  assert.equal(loaded.configRevision, 1);
});

test('a mixed-CLI delivery calls the selected backend and model for each role, including the Planner', async t => {
  const { store, project } = await fixture(t);
  applyRoleRuntimes(project, {
    manager: { backend: 'pi', model: 'copilot/sol' }, planner: { backend: 'claude', model: 'opus' },
    engineer: { backend: 'dsh', model: 'deepseek-official/flash' }, reviewer: { backend: 'pi', model: 'copilot/astra' },
  });
  store.save(project);
  const requests: AgentRequest[] = [];
  const sequence = ['planner', 'engineer', 'reviewer', 'planner', 'manager', 'operator'];
  const call: CallAgent = async request => { requests.push(request); return answer(request, sequence[requests.length - 1]); };
  const flow = new Flow(store, call);
  await flow.say(project.id, 'Build and review a delivery');
  await flow.settled(project.id);
  assert.deepEqual(requests.map(r => [r.role, r.backend, r.model]), [
    ['manager', 'pi', 'copilot/sol'], ['planner', 'claude', 'opus'], ['engineer', 'dsh', 'deepseek-official/flash'],
    ['reviewer', 'pi', 'copilot/astra'], ['planner', 'claude', 'opus'], ['manager', 'pi', 'copilot/sol'],
  ]);
});

test('an in-flight A→B→A route change cannot resurrect an old session or pin its stale model', async t => {
  const { store, project } = await fixture(t);
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const requests: AgentRequest[] = [];
  const flow = new Flow(store, async request => { requests.push(request); entered(); await gate; return answer(request); });
  const first = flow.say(project.id, 'Hello');
  await started;
  flow.edit(project.id, p => applyRoleRuntimes(p, { manager: { backend: 'pi', model: null } }));
  flow.edit(project.id, p => applyRoleRuntimes(p, { manager: { backend: 'codex', model: null } }));
  release(); await first; await flow.settled(project.id);
  assert.equal(requests[0]!.backend, 'codex');
  assert.equal(store.load(project.id).sessions.manager, undefined);
  assert.equal(roleRuntime(store.load(project.id), 'manager').model, null);
});

test('role API saves atomically, rejects stale revisions/injection, lists a draft CLI without saving, and inherits all four routes', async t => {
  const { home, store, project } = await fixture(t);
  const requests: AgentRequest[] = [];
  const call: CallAgent = async request => {
    requests.push(request);
    return request.model === 'provider/no-form' ? { ...answer(request), handoff: null, error: 'The turn ended without the hand-off form.' } : answer(request);
  };
  const discoveries: unknown[] = [];
  call.models = async (...args) => { discoveries.push(args); return [{ model: 'provider/sol', default: true }]; };
  const server = await serve({ store, callAgent: call, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => server.close());
  const path = `/api/projects/${project.id}`;
  const send = (method: string, route: string, body?: unknown) => fetch(server.url + route, { method, headers: { 'content-type': 'application/json' }, ...body ? { body: JSON.stringify(body) } : {} });
  const rows = [{ role: 'manager', backend: 'pi', model: 'provider/sol', effort: 'high' }, { role: 'engineer', backend: 'dsh', model: 'deepseek-official/flash', effort: '' }];
  assert.equal((await send('PUT', path + '/config/roles', { revision: 0, roles: [...rows, { role: 'reviewer', backend: 'invalid', model: '' }] })).status, 400);
  assert.equal(store.load(project.id).configRevision, undefined);
  assert.equal((await send('PUT', path + '/config/roles', { revision: 0, roles: [{ backend: 'pi', model: 'provider/sol' }] })).status, 400, 'a missing role must not silently target Manager');
  assert.equal(store.load(project.id).configRevision, undefined);
  assert.equal((await send('PUT', path + '/config/roles', { revision: 0, roles: rows })).status, 200);
  assert.equal((await send('PUT', path + '/config/roles', { revision: 0, roles: rows })).status, 409);
  assert.equal((await send('PUT', path + '/config/roles', { revision: 1, roles: [{ ...rows[0], model: '--evil' }] })).status, 400);
  const choices = await (await send('GET', path + '/model-choices?role=engineer&backend=pi&refresh=1')).json() as { backend: string };
  assert.equal(choices.backend, 'pi');
  assert.deepEqual(discoveries, [['pi', project.workdir, true]]);
  assert.equal(requests.length, 0, 'catalog lookup does not call a model');
  assert.equal(roleRuntime(store.load(project.id), 'engineer').backend, 'dsh');
  const probe = await send('POST', path + '/model-choices/probe', { role: 'engineer', backend: 'pi', model: 'provider/astra', effort: 'high' });
  assert.equal(probe.status, 200);
  assert.deepEqual([requests[0]!.role, requests[0]!.backend, requests[0]!.model, requests[0]!.access, requests[0]!.session], ['engineer', 'pi', 'provider/astra', 'read', undefined]);
  const invalidProbe = await (await send('POST', path + '/model-choices/probe', { role: 'manager', backend: 'pi', model: 'provider/no-form' })).json() as { ok: boolean; error: string };
  assert.equal(invalidProbe.ok, false, 'a successful provider turn without the role protocol must not be a green compatibility test');
  assert.match(invalidProbe.error, /hand-off form/);
  const created = await (await send('POST', '/api/daemons', {})).json() as { sid: string };
  assert.equal(roleRuntime(store.load(created.sid), 'engineer').backend, 'dsh');
  assert.equal(roleRuntime(store.load(created.sid), 'manager').model, 'provider/sol');
});

test('Pi catalog keeps qualified IDs/custom aliases while discarding private transport fields', () => {
  const rows = piModels([{ id: 'sol', provider: 'one', name: 'Sol', headers: { authorization: 'not-for-the-browser' }, baseUrl: 'private-endpoint', reasoning: true },
    { id: 'sol', provider: 'two', name: 'Custom Sol' }], { provider: 'one', id: 'sol' });
  assert.deepEqual(rows.map(row => [row.model, row.default]), [['one/sol', true], ['two/sol', false]]);
  assert.equal(JSON.stringify(rows).includes('not-for-the-browser'), false);
  assert.equal(JSON.stringify(rows).includes('private-endpoint'), false);
});

test('DSH overlay pins an explicit provider/model without editing global settings, and read-only cannot delegate writes', async t => {
  const { home } = await fixture(t);
  const file = dshOverlay(join(home, 'test'), { model: 'custom/model/with/slash', effort: 'high', readOnly: true });
  const rows = JSON.parse(readFileSync(file, 'utf8')) as { id?: string; config?: unknown; disabled?: boolean }[];
  assert.deepEqual(rows.find(row => row.id === 'agent-default-model')?.config, { provider: 'custom', model: 'model/with/slash', reasoningEffort: 'high' });
  assert.equal(rows.find(row => row.id === 'tool-subagent')?.disabled, true);
  assert.deepEqual(dshHandOff('{"to":"reviewer","letter":"ok","open":[]}'), { to: 'reviewer', letter: 'ok', open: [] });
  assert.equal(dshHandOff('Here is some JSON: {"to":"reviewer"}'), undefined);
});
