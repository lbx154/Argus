import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ProjectRow } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { repeating, untilEvery, untilNext } from '../src/serve/repeat.js';

async function setup(t: TestContext) {
  const home = await mkdtemp(join(tmpdir(), 'argus-chief-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(web);
  await writeFile(join(web, 'manifest.webmanifest'), '{"name":"Argus"}');
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    // '<task>' has the Manager open a task, whose Engineer reports back: the project's work runs, then rests.
    const to = request.role === 'manager' && request.prompt.includes('<task>') ? 'engineer' : request.role === 'engineer' ? 'manager' : 'operator';
    return { callId: 'c', threadId: 'thread', completed: true, error: null, handoff: { form: request.form, to, letters: ['Hello.'] }, model: null, usage: null, ms: 1 };
  };
  const store = new Store(home);
  // As `argus` starts it.
  store.chief({ backend: 'codex', model: null });
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web });
  t.after(() => served.close());
  const send = (method: string, path: string, body?: unknown) =>
    fetch(served.url + path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });
  const rows = async () => (await (await fetch(served.url + '/api/projects')).json() as { projects: ProjectRow[] }).projects;
  return { home, store, requests, send, rows };
}

test('every few hours falls on whole hours counted from midnight UTC', () => {
  assert.equal(untilEvery(3, new Date('2026-11-02T07:30:00Z')), 90 * 60_000);
  assert.equal(untilEvery(24, new Date('2026-11-02T23:00:00Z')), 60 * 60_000);
});

test('the next daily time is later today, or tomorrow once it has passed or is now', () => {
  const now = new Date('2026-11-02T07:30:00Z');
  assert.equal(untilNext('08:00', now), 30 * 60_000);
  assert.equal(untilNext('07:30', now), 24 * 3600_000);
  assert.equal(untilNext('00:15', now), (16 * 60 + 45) * 60_000);
});

test('the chief is there from the start, one per state root; the operator sets, changes or clears its daily message', async t => {
  const { store, requests, send, rows } = await setup(t);
  const chiefs = (await rows()).filter(project => project.chief);
  assert.equal(chiefs.length, 1);
  const sid = chiefs[0]!.id;
  assert.equal(store.load(sid).request, '');
  assert.equal(chiefs[0]!.repeat, null);
  // Nothing runs till the operator says what they need.
  assert.equal(requests.length, 0);

  assert.equal((await send('PUT', `/api/projects/${sid}/wakes`, { repeat: { at: '25:00', text: 'x' } })).status, 400);
  assert.equal((await send('PUT', `/api/projects/${sid}/wakes`, { repeat: { at: '9:00', text: 'x' } })).status, 400);
  assert.equal((await send('PUT', `/api/projects/${sid}/wakes`, { repeat: { at: '08:00', text: 'Today\'s letter.' } })).status, 200);
  assert.deepEqual((await rows()).find(project => project.id === sid)!.repeat, { at: '08:00', text: 'Today\'s letter.' });
  await (await send('POST', `/api/projects/${sid}/message/stream`, { text: 'Hi.', request_id: 'r' })).text();
  assert.match(requests.at(-1)!.instructions!, /Each day at 08:00 UTC Argus sends this project the operator's message: Today's letter\./);
  assert.equal((await send('PUT', `/api/projects/${sid}/wakes`, { repeat: null })).status, 200);
  assert.equal(store.load(sid).repeat, undefined);
  assert.equal((await rows()).find(project => project.id === sid)!.repeat, null);
});

test('the chief is made once: a second start finds it', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-chief-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const first = new Store(home).chief({ backend: 'codex', model: null });
  assert.equal(new Store(home).chief({ backend: 'claude', model: 'x' }).id, first.id);
  assert.equal(new Store(home).list().filter(project => project.chief).length, 1);
});

test('the chief\'s roles read every project\'s record; another project\'s read its own', async t => {
  const { home, store, requests, send } = await setup(t);
  const { sid: other } = await (await send('POST', '/api/daemons', {})).json() as { sid: string };
  const chief = store.list().find(project => project.chief)!.id;
  for (const sid of [other, chief]) await (await send('POST', `/api/projects/${sid}/message/stream`, { text: 'Hi.', request_id: `r-${sid}` })).text();
  const of = (sid: string) => requests.find(request => request.cwd === store.load(sid).workdir)!;
  assert.ok(of(chief).readDirs!.includes(join(home, 'projects')));
  assert.match(of(chief).instructions!, /across all their projects/);
  assert.match(of(chief).instructions!, new RegExp(`- ${other}: Hi\\.`));
  assert.ok(!of(other).readDirs!.includes(join(home, 'projects')));
  assert.ok(of(other).readDirs!.includes(store.dir(other)));
  assert.doesNotMatch(of(other).instructions!, /across all their projects/);
});

test('a daily message goes out at its time, in the words the record holds then, each day', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-repeat-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null, modelAutomatic: true });
  store.save({ ...project, repeat: { at: '08:00', text: 'First words.' } });
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: new Date('2026-11-02T07:00:00Z') });
  const sent: string[] = [];
  const daily = repeating(store, (id, text) => sent.push(`${id}: ${text}`));
  t.after(() => daily.close());
  daily.arm();
  t.mock.timers.tick(59 * 60_000);
  assert.deepEqual(sent, []);
  store.save({ ...store.load(project.id), repeat: { at: '08:00', text: 'Changed words.' } });
  t.mock.timers.tick(60_000);
  assert.deepEqual(sent, [`${project.id}: Changed words.`]);
  t.mock.timers.tick(24 * 3600_000);
  assert.equal(sent.length, 2);
});

test('every few hours and when another project rests, the chief hears the operator\'s words; other projects are not told', async t => {
  const { store, requests, send } = await setup(t);
  const chief = store.list().find(project => project.chief)!.id;
  const { sid: other } = await (await send('POST', '/api/daemons', {})).json() as { sid: string };
  assert.equal((await send('PUT', `/api/projects/${other}/wakes`, { on_rest: 'x' })).status, 400);
  assert.equal((await send('PUT', `/api/projects/${chief}/wakes`, { every: { hours: 0, text: 'x' } })).status, 400);
  const set = await (await send('PUT', `/api/projects/${chief}/wakes`, { every: { hours: 4, text: 'A look.' }, on_rest: 'A project rested.' })).json();
  assert.deepEqual(set, { repeat: null, every: { hours: 4, text: 'A look.' }, on_rest: 'A project rested.' });
  // A task's work comes to rest on the other project: the chief is told which, and how.
  store.save({ ...store.load(other), name: 'Other' });
  const chiefTurns = () => requests.filter(request => request.cwd === store.load(chief).workdir && request.role === 'manager');
  const answered = await send('POST', `/api/projects/${other}/message/stream`, { text: '<task>', request_id: 'r' });
  await answered.text();
  for (let tries = 0; !chiefTurns().length; tries++) {
    assert.ok(tries < 200, 'the chief was never told');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.match(chiefTurns()[0]!.prompt, new RegExp(`A project rested\\.\\n\\nOther \\(${other}\\): idle`));
  assert.match(chiefTurns()[0]!.instructions!, /Every 4 hours/);
});
