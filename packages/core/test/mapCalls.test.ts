import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';
import { Runs } from '../src/serve/runs.js';
import { progressMap, snapshot, type Live, type Recorded } from '../src/serve/views.js';
import type { CallAgent } from '../src/types.js';

const clarification = readFileSync(new URL('./fixtures/map-clarification/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
const updates = readFileSync(new URL('./fixtures/manager-task-update/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);

test('snapshot joins a live raw request to its map card, including a clarification sharing the original card', t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-map-calls-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  const path = join(store.dir(project.id), 'events.jsonl');
  const write = (events: Recorded[]) => writeFileSync(path, events.map(event => JSON.stringify(event)).join('\n') + '\n');
  // The actual clarification record's second request is still answering q0.
  write(clarification.slice(0, 6));
  const started = Date.parse(clarification[5]!.ts) / 1000;
  project.forManager = [{ text: '都用 6 面。', request_id: 'operator-answer' }];
  const live: Live = { since: started - 60, calls: [{ role: 'manager', task: undefined, answers: 'operator-answer', started_ts: started }], requests: ['operator-answer'] };
  const reading = snapshot(store, project, live);
  assert.deepEqual(reading.daemon.calls, [{ role: 'manager', answers: 'operator-answer', started_ts: started, request_card_id: 'q0' }]);
  assert.deepEqual(progressMap(store, project, 0).tasks.map(card => card.id), ['q0']);
  // Two recorded requests have distinct cards; only the second is being answered now.
  const second = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  writeFileSync(join(store.dir(second.id), 'events.jsonl'), updates.slice(0, 5).map(event => JSON.stringify(event)).join('\n') + '\n');
  const added = updates[2];
  assert.ok(added?.type === 'task.added');
  second.tasks = [added.task];
  second.status = 'working';
  const request = updates[4];
  assert.ok(request?.type === 'message' && request.request_id);
  second.forManager = [{ text: request.text, request_id: request.request_id }];
  live.calls[0]!.answers = request.request_id;
  live.requests = [request.request_id!];
  const multiple = snapshot(store, second, live);
  assert.deepEqual(multiple.daemon.calls, [{ role: 'manager', answers: request.request_id, started_ts: started, request_card_id: 'q4' }]);
  assert.deepEqual(progressMap(store, second, 0).tasks.filter(card => 'turn_kind' in card).map(card => card.id), ['q0', 'q4']);
  // Work status distinguishes a question at rest from an operator stop, regardless of liveness.
  second.questions = [{ role: 'engineer', task: added.task.id, question: '请问输出用中文还是英文？' }];
  second.forManager = [];
  const resting: Live = { since: null, calls: [], requests: [] };
  second.status = 'asking';
  assert.equal(snapshot(store, second, resting).work_status?.reason, 'operator_input');
  second.status = 'stopped';
  assert.equal(snapshot(store, second, resting).work_status?.reason, 'not_running');
  assert.deepEqual(snapshot(store, second, resting).daemon.calls, []);
});

test('Runs records the new call time while the project run and another task continue', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-map-call-clock-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ request: 'Continue the work.', workdir: home, backend: 'codex', model: null });
  const added = updates[2];
  assert.ok(added?.type === 'task.added');
  project.tasks = [added.task];
  project.status = 'stopped';
  store.save(project);
  let now = 1_000_000;
  t.mock.method(Date, 'now', () => now);
  let engineerReady!: () => void, managerReady!: () => void, finish!: () => void;
  const engineerStarted = new Promise<void>(resolve => { engineerReady = resolve; });
  const managerStarted = new Promise<void>(resolve => { managerReady = resolve; });
  const finished = new Promise<void>(resolve => { finish = resolve; });
  const callAgent: CallAgent = async request => {
    (request.role === 'engineer' ? engineerReady : managerReady)();
    await Promise.race([finished, new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }))]);
    return { callId: request.role, threadId: null, completed: true, error: null, model: 'fixture', usage: null, ms: 1,
      handoff: request.role === 'manager' ? { form: 'manager', to: 'operator', letters: ['Recorded.'] } : null };
  };
  const runs = new Runs(store, callAgent, () => {});
  t.after(() => runs.stopAll());
  runs.resume(project.id);
  await engineerStarted;
  assert.equal(runs.live(project.id).since, 1000);
  now += 75_000;
  const reply = runs.message(project.id, 'How is it going?', 'later-request', () => {});
  await managerStarted;
  assert.deepEqual(runs.live(project.id).calls.map(call => [call.role, call.answers, call.started_ts]), [
    ['engineer', undefined, 1000], ['manager', 'later-request', 1075],
  ]);
  const current = snapshot(store, store.load(project.id), runs.live(project.id));
  assert.equal(current.daemon.started_at_iso, new Date(1_000_000).toISOString());
  assert.equal(current.daemon.calls?.find(call => call.role === 'manager')?.started_ts, 1075);
  // The completed manager call leaves the live projection, not a persisted duplicate.
  finish();
  await reply;
  assert.ok(!runs.live(project.id).calls.some(call => call.role === 'manager'));
});
