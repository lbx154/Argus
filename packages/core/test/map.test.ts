import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { Store, type Task } from '../src/store.js';
import { progressMap } from '../src/serve/views.js';

const task: Task = { id: 't1', title: 'Read the map incrementally', from: 'manager', objective: 'Read the map incrementally', status: 'open', holder: 'engineer', note: null };
const record = Buffer.from([
  { ts: '2026-10-29T10:57:26.527Z', type: 'task.added', task },
  { ts: '2026-10-29T10:57:29.255Z', type: 'step', role: 'engineer', task: 't1', kind: 'message', text: 'Reading the map code first.' },
].map(row => `${JSON.stringify(row)}\n`).join(''));

test('map polling reads only appended bytes while task state and each reader’s cursor stay current', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-map-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  project.tasks = [task];
  const path = join(store.dir(project.id), 'events.jsonl');
  fs.writeFileSync(path, record);
  const reads = t.mock.method(fs, 'readSync');
  syncBuiltinESMExports();
  t.after(() => { reads.mock.restore(); syncBuiltinESMExports(); });
  const positions = () => reads.mock.calls.map(call => {
    const args = Array.from(call.arguments);
    return [args[4], args[3]]; // File offset and requested byte count.
  });

  const first = progressMap(store, project, 0);
  assert.equal(first.cursor, '2');
  assert.equal(first.tasks[0]!.latest_action!.text, JSON.parse(record.toString().split('\n')[1]!).text);
  assert.deepEqual(positions(), [[0, record.length]]);
  reads.mock.resetCalls();
  assert.deepEqual(progressMap(store, project, Number(first.cursor)), { ...first, events: [] });
  assert.deepEqual(positions(), [[record.length, 0]]);

  reads.mock.resetCalls();
  const ts = '2026-10-29T11:00:00.000Z';
  const appended = JSON.stringify({ ts, type: 'handoff', task: 't1', role: 'engineer', to: 'reviewer', letter: '增量读取完成。', open: ['report.md'] }) + '\n';
  fs.appendFileSync(path, appended);
  project.tasks = [{ ...task, holder: 'reviewer' }];
  const later = progressMap(store, project, 2);
  assert.deepEqual(positions(), [[record.length, Buffer.byteLength(appended)]]);
  assert.equal(later.cursor, '3');
  assert.deepEqual(later.events, [{ id: 'e2', item_id: 't1', type: 'handoff', ts: Date.parse(ts) / 1000, role: 'engineer', to: 'reviewer', text: '增量读取完成。' }]);
  assert.equal(later.tasks[0]!.role, 'reviewer');
  assert.deepEqual(later.tasks[0]!.result_paths, []);
  // An older page or a newly opened page can still request history after another page advanced.
  assert.deepEqual(progressMap(store, project, 0), later);
  assert.deepEqual(progressMap(store, project, 2), later);
  assert.deepEqual(progressMap(store, project, 3).events, []);
  assert.deepEqual(progressMap(store, project, Infinity).events, []);

  project.questions = [{ role: 'reviewer', task: 't1', question: '现在交付？' }];
  const asking = progressMap(store, project, 3).tasks[0]!;
  assert.equal(asking.status, 'blocked');
  assert.equal(asking.pending_question, '现在交付？');
  project.questions = [];
  project.tasks = [{ ...task, holder: 'reviewer', status: 'closed' }];
  fs.appendFileSync(path, JSON.stringify({ ts, type: 'task.closed', task: 't1' }) + '\n');
  const done = progressMap(store, project, 3);
  assert.equal(done.cursor, '4');
  assert.equal(done.tasks[0]!.status, 'done');
  assert.deepEqual(done.tasks[0]!.result_paths, ['report.md']);
  assert.equal(done.tasks[0]!.finished_ts, Date.parse(ts) / 1000);
});

// Real records, which the page's replay test reads too.
const records = new Store(fileURLToPath(new URL('../../../frontend/web/src/test/fixtures/home', import.meta.url)));
const at = (iso: string) => Date.parse(iso) / 1000;

test('what the Manager did itself and handed over is a piece of work on the map, done, with its result and when it was done', () => {
  const project = records.load('p-0444b8d3');
  const map = progressMap(records, project, 0);
  assert.deepEqual(map.tasks.map(card => [card.id, card.status, card.role, card.title, card.result_paths, card.deps, card.ts, card.finished_ts]), [
    ['d22', 'done', 'manager', '25 分钟番茄钟', ['timer.html'], [],
      at('2026-10-29T12:05:48.023Z'), at('2026-10-29T12:07:48.872Z')],
    ['d44', 'done', 'manager', map.tasks[1]!.title, ['timer.html'], [], at('2026-10-29T12:13:17.436Z'), at('2026-10-29T12:15:53.902Z')],
    ['d51', 'done', 'manager', map.tasks[2]!.title, ['README.md', 'check_timer.py'], [], at('2026-10-29T12:17:48.506Z'), at('2026-10-29T12:18:33.492Z')],
  ]);
  // Its letter is the step on it, as a task's letters are on the task.
  assert.deepEqual(map.events.map(letter => [letter.id, letter.item_id]), [['e22', 'd22'], ['e44', 'd44'], ['e51', 'd51']]);
  assert.equal(map.tasks[2]!.latest_action?.text, map.events[2]!.text);
});

test("a result the Manager hands over after the team's tasks follows the last of them", () => {
  const map = progressMap(records, records.load('p-c855623c'), 0);
  assert.deepEqual(map.tasks.map(card => [card.id, card.status, card.deps]), [['t1', 'done', []], ['t2', 'done', []], ['t3', 'done', ['t1', 't2']], ['d102', 'done', ['t3']]]);
  assert.deepEqual(map.tasks.at(-1)!.result_paths, ['result.md']);
});
