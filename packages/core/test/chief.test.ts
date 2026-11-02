import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ProjectRow } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { repeating, untilNext } from '../src/serve/repeat.js';

async function setup(t: TestContext) {
  const home = await mkdtemp(join(tmpdir(), 'argus-chief-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(web);
  await writeFile(join(web, 'manifest.webmanifest'), '{"name":"Argus"}');
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    return { callId: 'c', threadId: 'thread', completed: true, error: null, handoff: { form: request.form, to: 'operator', letters: ['Hello.'] }, model: null, usage: null, ms: 1 };
  };
  const store = new Store(home);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web });
  t.after(() => served.close());
  const send = (method: string, path: string, body?: unknown) =>
    fetch(served.url + path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });
  const rows = async () => (await (await fetch(served.url + '/api/projects')).json() as { projects: ProjectRow[] }).projects;
  return { home, store, requests, send, rows };
}

test('the next daily time is later today, or tomorrow once it has passed or is now', () => {
  const now = new Date('2026-11-02T07:30:00Z');
  assert.equal(untilNext('08:00', now), 30 * 60_000);
  assert.equal(untilNext('07:30', now), 24 * 3600_000);
  assert.equal(untilNext('00:15', now), (16 * 60 + 45) * 60_000);
});

test('one chief per state root, made with its daily message, which the operator changes or clears', async t => {
  const { store, requests, send, rows } = await setup(t);
  assert.equal((await send('POST', '/api/chief', { repeat: { at: '25:00', text: 'x' } })).status, 400);
  const made = await send('POST', '/api/chief', { name: 'Chief', repeat: { at: '08:00', text: 'Today\'s letter.' } });
  assert.equal(made.status, 200);
  const { sid } = await made.json() as { sid: string };
  assert.deepEqual({ ...store.load(sid) }.repeat, { at: '08:00', text: 'Today\'s letter.' });
  const row = (await rows()).find(project => project.id === sid)!;
  assert.equal(row.chief, true);
  assert.equal(row.label, 'Chief');
  assert.deepEqual(row.repeat, { at: '08:00', text: 'Today\'s letter.' });
  assert.equal((await send('POST', '/api/chief', {})).status, 409);

  await (await send('POST', `/api/projects/${sid}/message/stream`, { text: 'Hi.', request_id: 'r' })).text();
  assert.match(requests.at(-1)!.instructions!, /Each day at 08:00 UTC Argus sends this project the operator's message: Today's letter\./);
  assert.equal((await send('PUT', `/api/projects/${sid}/repeat`, { at: '9:00', text: 'x' })).status, 400);
  assert.equal((await send('PUT', `/api/projects/${sid}/repeat`, { at: '21:30', text: 'Evening.' })).status, 200);
  assert.deepEqual(store.load(sid).repeat, { at: '21:30', text: 'Evening.' });
  assert.equal((await send('PUT', `/api/projects/${sid}/repeat`, {})).status, 200);
  assert.equal(store.load(sid).repeat, undefined);
  assert.equal((await rows()).find(project => project.id === sid)!.repeat, null);
});

test('the chief\'s roles read every project\'s record; another project\'s read its own', async t => {
  const { home, store, requests, send } = await setup(t);
  const { sid: other } = await (await send('POST', '/api/daemons', {})).json() as { sid: string };
  const { sid: chief } = await (await send('POST', '/api/chief', {})).json() as { sid: string };
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
