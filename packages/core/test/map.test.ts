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

test('each message the Manager takes up is a question and its answer on the map, answered by its letter with the files it hands over', () => {
  const project = records.load('p-0444b8d3');
  const map = progressMap(records, project, 0);
  const record = fs.readFileSync(join(records.dir(project.id), 'events.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const said = record.filter(event => event.type === 'message');
  const answers = record.filter(event => event.type === 'handoff');
  assert.deepEqual(map.tasks.map(card => [card.id, 'turn_kind' in card && card.turn_kind, card.status, card.role, card.objective, card.result_paths, card.deps, card.started_ts, card.finished_ts]),
    [['q0', ['timer.html']], ['q23', ['timer.html']], ['q45', ['README.md', 'check_timer.py']]].map(([id, paths], index) =>
      [id, 'qa', 'done', 'manager', said[index].text, paths, [], at(said[index].ts), at(answers[index].ts)]));
  // Named by what the operator asked, so a file handed over twice names two cards apart.
  assert.deepEqual(map.tasks.map(card => card.title), said.map(event => event.text.split('\n')[0].slice(0, 100)));
  // Its answer is the letter, where the map's reader of a question and its answer finds it.
  assert.deepEqual(map.events.map(letter => [letter.id, letter.item_id, letter.type]), [['e22', 'q0', 'turn.replied'], ['e44', 'q23', 'turn.replied'], ['e51', 'q45', 'turn.replied']]);
  assert.equal(map.tasks[2]!.latest_action?.text, map.events[2]!.text);
});

test("the tasks a message's answer opens follow it; a result handed over from the team's work is no card of its own", () => {
  const map = progressMap(records, records.load('p-c855623c'), 0);
  assert.deepEqual(map.tasks.map(card => [card.id, 'turn_kind' in card ? card.turn_kind : 'task', card.status, card.deps]),
    [['q0', 'qa', 'done', []], ['t1', 'task', 'done', ['q0']], ['t2', 'task', 'done', ['q0']], ['t3', 'task', 'done', ['t1', 't2']]]);
  assert.deepEqual(map.tasks.at(-1)!.result_paths, ['result.md']);
  assert.ok(!map.events.some(letter => letter.item_id === 'q0' && letter.id === 'e102'));
});

test('a message is on the map while it is answered, and after only if its answer set work going or handed something over; only a letter naming it answers it', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-map-deliveries-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  project.tasks = [{ ...task, from: 'planner', status: 'closed' }, { ...task, id: 't2', from: 'planner', status: 'closed', after: ['t1'] },
    { ...task, id: 't3', title: 'Second part of it' }];
  const manager = (answers?: string) => ({ type: 'call.start', role: 'manager', ...answers ? { answers } : {} });
  const path = join(store.dir(project.id), 'events.jsonl');
  let second = 0;
  const write = (rows: object[]) => fs.appendFileSync(path, rows.map(row =>
    `${JSON.stringify({ ts: new Date(Date.UTC(2026, 9, 29, 11, 0, second++)).toISOString(), ...row })}\n`).join(''));
  write([
    { type: 'message', from: 'operator', text: 'Build it.', request_id: 'r0' }, manager('r0'),
    { type: 'handoff', role: 'manager', to: 'planner', letter: 'Plan it.', answers: 'r0' },
    { type: 'task.added', task: project.tasks[0] }, { type: 'task.added', task: project.tasks[1] }, { type: 'task.closed', task: 't1' },
    { type: 'message', from: 'operator', text: 'How is it going?', request_id: 'r6' }, manager('r6'),
    { type: 'handoff', role: 'manager', to: 'operator', letter: 'Half way.', answers: 'r6' },
    // Turns on the team's letters answer no message, and what they open follows none.
    { type: 'task.closed', task: 't2' }, manager(), { type: 'handoff', role: 'manager', to: 'operator', letter: 'Done.', open: ['report.md'] },
    manager(), { type: 'handoff', role: 'manager', to: 'engineer', letter: 'Second part of it.' }, { type: 'task.added', task: project.tasks[2] },
    { type: 'message', from: 'operator', text: 'Chart it.', request_id: 'r15' }, manager('r15'), manager('r15'),
    { type: 'call.end', role: 'manager', completed: false },
  ]);
  const cards = (status: typeof project.status) => progressMap(store, { ...project, status }, 0).tasks.map(card => [card.id, card.status, card.deps, card.result_paths]);
  const done = [['q0', 'done', [], []], ['t1', 'done', ['q0'], []], ['t2', 'done', ['t1'], []], ['t3', 'in_progress', [], []]];
  // A reply alone is the conversation's; one a failure left unanswered waits for Resume, off the map.
  assert.deepEqual(cards('failed'), done);
  assert.deepEqual(cards('working'), [...done, ['q15', 'in_progress', [], []]]);
  write([{ type: 'handoff', role: 'manager', to: 'operator', letter: 'Here it is.', open: ['chart.png'], answers: 'r15' }]);
  assert.deepEqual(cards('working').at(-1), ['q15', 'done', [], ['chart.png']]);
  // A reply alone stays till the next message, to be read on the map.
  write([{ type: 'message', from: 'operator', text: 'Thanks!', request_id: 'r20' }, manager('r20'),
    { type: 'handoff', role: 'manager', to: 'operator', letter: 'You are welcome.', answers: 'r20' }]);
  assert.deepEqual(cards('working').slice(-2).map(card => card.slice(0, 2)), [['q15', 'done'], ['q20', 'done']]);
  write([{ type: 'message', from: 'operator', text: 'One more thing.', request_id: 'r23' }]);
  assert.deepEqual(cards('working').slice(-2).map(card => card.slice(0, 2)), [['q15', 'done'], ['q23', 'in_progress']]);
});
