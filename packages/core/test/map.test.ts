import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { firstLine } from '../src/flow.js';
import { Store, type Task } from '../src/store.js';
import { conversationRows, progressMap, readRecord, type Recorded } from '../src/serve/views.js';

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

  const first = progressMap(store, project, 0, []);
  assert.equal(first.cursor, '2');
  assert.equal(first.tasks[0]!.latest_action!.text, JSON.parse(record.toString().split('\n')[1]!).text);
  assert.deepEqual(positions(), [[0, record.length]]);
  reads.mock.resetCalls();
  assert.deepEqual(progressMap(store, project, Number(first.cursor), []), { ...first, events: [] });
  assert.deepEqual(positions(), [[record.length, 0]]);

  reads.mock.resetCalls();
  const ts = '2026-10-29T11:00:00.000Z';
  const appended = JSON.stringify({ ts, type: 'handoff', task: 't1', role: 'engineer', to: 'reviewer', letter: '增量读取完成。', open: ['report.md'] }) + '\n';
  fs.appendFileSync(path, appended);
  project.tasks = [{ ...task, holder: 'reviewer' }];
  const later = progressMap(store, project, 2, []);
  assert.deepEqual(positions(), [[record.length, Buffer.byteLength(appended)]]);
  assert.equal(later.cursor, '3');
  assert.deepEqual(later.events, [{ id: 'e2', item_id: 't1', type: 'handoff', ts: Date.parse(ts) / 1000, role: 'engineer', to: 'reviewer', text: '增量读取完成。' }]);
  assert.equal(later.tasks[0]!.role, 'reviewer');
  assert.deepEqual(later.tasks[0]!.result_paths, []);
  // An older page or a newly opened page can still request history after another page advanced.
  assert.deepEqual(progressMap(store, project, 0, []), later);
  assert.deepEqual(progressMap(store, project, 2, []), later);
  assert.deepEqual(progressMap(store, project, 3, []).events, []);
  assert.deepEqual(progressMap(store, project, Infinity, []).events, []);

  project.questions = [{ role: 'reviewer', task: 't1', question: '现在交付？' }];
  const asking = progressMap(store, project, 3, []).tasks[0]!;
  assert.equal(asking.status, 'blocked');
  assert.equal('pending_question' in asking, false);
  project.questions = [];
  project.tasks = [{ ...task, holder: 'reviewer', status: 'closed' }];
  fs.appendFileSync(path, JSON.stringify({ ts, type: 'handoff', task: 't1', role: 'reviewer', to: 'manager', letter: 'Accepted.', open: ['report.md'] }) + '\n' + JSON.stringify({ ts, type: 'task.closed', task: 't1' }) + '\n');
  const done = progressMap(store, project, 3, []);
  assert.equal(done.cursor, '5');
  assert.equal(done.tasks[0]!.status, 'done');
  assert.deepEqual(done.tasks[0]!.result_paths, ['report.md']);
  assert.equal(done.tasks[0]!.finished_ts, Date.parse(ts) / 1000);
  fs.appendFileSync(path, JSON.stringify({ ts, type: 'message', from: 'operator', to: 'reviewer', task: 't1', text: '可以交付。' }) + '\n');
  const answered = progressMap(store, project, Number(done.cursor), []);
  assert.equal(answered.tasks.length, 1);
  assert.deepEqual(answered.events.map(letter => [letter.item_id, letter.role, letter.to, letter.text]), [['t1', 'operator', 'reviewer', '可以交付。']]);
});

// Real records, which the page's replay test reads too.
const records = new Store(fileURLToPath(new URL('../../../frontend/web/src/test/fixtures/home', import.meta.url)));
const at = (iso: string) => Date.parse(iso) / 1000;

test('answering a recorded task question keeps its earlier progress until the asker reports again', t => {
  const added = JSON.parse(fs.readFileSync(new URL('./fixtures/operator-task/events.jsonl', import.meta.url), 'utf8')) as Extract<Recorded, { type: 'task.added' }>;
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-answered-progress-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  project.tasks = [added.task];
  project.status = 'stopped';
  const question = '请问输出用中文还是英文？';
  project.questions = [{ role: 'engineer', task: added.task.id, question }];
  const ts = '2026-10-29T12:21:00.000Z';
  const path = join(store.dir(project.id), 'events.jsonl');
  fs.writeFileSync(path, [added,
    { type: 'step', role: 'engineer', task: added.task.id, kind: 'message', text: '正在检查已有脚本。', ts },
    { type: 'handoff', role: 'engineer', task: added.task.id, to: 'operator', letter: question, ts },
  ].map(event => JSON.stringify(event)).join('\n') + '\n');
  const progress = () => ({
    map: progressMap(store, project, 0, []).tasks[0]?.latest_action?.text,
    row: conversationRows(readRecord(store, project.id), project, { since: null, calls: [], requests: [] })
      .find(row => row.type === 'ui.task')?.progress,
  });
  assert.deepEqual(progress(), { map: undefined, row: '' });
  project.questions = [];
  fs.appendFileSync(path, JSON.stringify({ type: 'message', from: 'operator', to: 'engineer', task: added.task.id, text: '请用中文。', ts }) + '\n');
  assert.deepEqual(progress(), { map: '正在检查已有脚本。', row: '正在检查已有脚本。' });
  fs.appendFileSync(path, JSON.stringify({ type: 'step', role: 'engineer', task: added.task.id, kind: 'message', text: '已开始按中文输出修改。', ts }) + '\n');
  assert.deepEqual(progress(), { map: '已开始按中文输出修改。', row: '已开始按中文输出修改。' });
});

test('each message the Manager takes up is a question and its answer on the map, answered by its letter with the files it hands over', () => {
  const project = records.load('p-0444b8d3');
  const map = progressMap(records, project, 0, []);
  const record = fs.readFileSync(join(records.dir(project.id), 'events.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const said = record.filter(event => event.type === 'message');
  const answers = record.filter(event => event.type === 'handoff');
  assert.deepEqual(map.tasks.map(card => [card.id, 'turn_kind' in card && card.turn_kind, card.status, card.role, card.title, card.result_paths, card.deps, card.started_ts, card.finished_ts]),
    [['q0', ['timer.html']], ['q23', ['timer.html']], ['q45', ['README.md', 'check_timer.py']]].map(([id, paths], index) =>
      [id, 'qa', 'done', 'manager', firstLine(said[index].text), paths, [], at(said[index].ts), at(answers[index].ts)]));
  // Named by what the operator asked, so a file handed over twice names two cards apart.
  assert.deepEqual(map.tasks.map(card => card.title), said.map(event => event.text.split('\n')[0]));
  // Its answer is the letter, where the map's reader of a question and its answer finds it.
  assert.deepEqual(map.events.map(letter => [letter.id, letter.item_id, letter.type]), [['e22', 'q0', 'turn.replied'], ['e44', 'q23', 'turn.replied'], ['e51', 'q45', 'turn.replied']]);
  assert.ok(map.tasks[2]!.latest_action!.text.length <= 160);
  assert.ok(String(map.events[2]!.text).startsWith(map.tasks[2]!.latest_action!.text.replace(/…$/, '')));
});

test("the tasks a message's answer opens follow it; a result handed over from the team's work is no card of its own", () => {
  const map = progressMap(records, records.load('p-c855623c'), 0, []);
  assert.deepEqual(map.tasks.map(card => [card.id, 'turn_kind' in card ? card.turn_kind : 'task', card.status, card.deps]),
    [['q0', 'qa', 'done', []], ['t1', 'task', 'done', ['q0']], ['t2', 'task', 'done', ['q0']], ['t3', 'task', 'done', ['t1', 't2']]]);
  assert.deepEqual(map.tasks.at(-1)!.result_paths, ['result.md']);
  assert.ok(!map.events.some(letter => letter.item_id === 'q0' && letter.id === 'e102'));
});

test('recorded Manager supplement stays a task letter, and its request completes when the work rests', t => {
  const record = fs.readFileSync(new URL('./fixtures/manager-task-update/events.jsonl', import.meta.url), 'utf8').trim().split('\n')
    .map(line => JSON.parse(line) as Recorded);
  const added = record.find(event => event.type === 'task.added');
  assert.ok(added?.type === 'task.added');
  const messages = record.filter(event => event.type === 'message');
  const message = messages[1]!;
  const supplement = record.filter(event => event.type === 'handoff').find(event => event.answers === message.request_id)!;
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-map-supplement-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: messages[0]!.text, backend: 'codex', model: null });
  project.tasks = [{ ...added.task }];
  project.status = 'working';
  const path = join(store.dir(project.id), 'events.jsonl');
  // Replay the original messages and letters, targeting t1 instead of opening the recorded t2.
  fs.writeFileSync(path, record.filter(event => event.type !== 'task.added' || event.task.id === 't1')
    .filter(event => event.type !== 'call.start' || event.task !== 't2')
    .map(event => JSON.stringify(event === supplement ? { ...event, task: 't1' } : event)).join('\n') + '\n');
  const before = progressMap(store, project, 0, []);
  const question = before.tasks.find(card => card.title === firstLine(message.text))!;
  const incoming = before.events.filter(letter => letter.text === firstLine(supplement.letter));

  // Controlled completion after the excerpt: the same task closes and the project rests.
  const finished = '2026-10-29T21:51:00.000Z';
  fs.appendFileSync(path, [
    { type: 'handoff', role: 'reviewer', task: 't1', to: 'manager', letter: 'Accepted.', ts: finished },
    { type: 'task.closed', task: 't1', ts: finished },
    { type: 'status', status: 'idle', ts: finished },
  ].map(event => JSON.stringify(event)).join('\n') + '\n');
  project.tasks[0]!.status = 'closed';
  project.status = 'idle';
  const done = progressMap(store, project, Number(before.cursor), []);
  const answered = done.tasks.find(card => card.id === question.id)!;
  assert.deepEqual({
    working: [question.status, question.finished_ts, question.latest_action?.text],
    idle: [answered.status, answered.finished_ts !== null, answered.latest_action?.text],
    incoming: incoming.map(letter => [letter.item_id, letter.type, letter.role, letter.to]),
  }, {
    // A brief to the team is the task's to show, not the message's.
    working: ['in_progress', null, undefined],
    idle: ['done', true, undefined],
    incoming: [['t1', 'handoff', 'manager', 'engineer']],
  });
  // The page merges history by event id: neither projection may overwrite the other.
  assert.equal(new Set(before.events.map(letter => letter.id)).size, before.events.length);
  assert.equal(done.tasks.find(card => card.id === 't1')!.status, 'done');
  assert.deepEqual(done.events.map(letter => letter.text), ['Accepted.']);
  assert.deepEqual(progressMap(store, project, 0, []).events.filter(letter => letter.text === firstLine(supplement.letter)), incoming);
  assert.deepEqual(progressMap(store, project, Number(done.cursor), []), { ...done, events: [] });
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
  const cards = (status: typeof project.status) => progressMap(store, { ...project, status }, 0, []).tasks.map(card => [card.id, card.status, card.deps, card.result_paths]);
  const done = [['q0', 'done', [], []], ['t1', 'done', ['q0'], []], ['t2', 'done', ['t1'], []], ['t3', 'in_progress', [], []]];
  // A reply alone is the conversation's; one a failure left unanswered waits for Resume, off the map.
  project.forManager = [{ text: 'Chart it.', request_id: 'r15' }];
  assert.deepEqual(cards('failed'), done);
  assert.deepEqual(cards('working'), [...done, ['q15', 'in_progress', [], []]]);
  write([{ type: 'handoff', role: 'manager', to: 'operator', letter: 'Here it is.', open: ['chart.png'], answers: 'r15' }]);
  project.forManager = [];
  assert.deepEqual(cards('working').at(-1), ['q15', 'done', [], ['chart.png']]);
  // A later reply alone stays till the next message, to be read on the map.
  write([{ type: 'message', from: 'operator', text: 'Thanks!', request_id: 'r20' }, manager('r20'),
    { type: 'handoff', role: 'manager', to: 'operator', letter: 'You are welcome.', answers: 'r20' }]);
  assert.deepEqual(cards('working').slice(-2).map(card => card.slice(0, 2)), [['q15', 'done'], ['q20', 'done']]);
  write([{ type: 'message', from: 'operator', text: 'One more thing.', request_id: 'r23' }]);
  project.forManager = [{ text: 'One more thing.', request_id: 'r23' }];
  assert.deepEqual(cards('working').slice(-2).map(card => card.slice(0, 2)), [['q15', 'done'], ['q23', 'in_progress']]);
  // One a later message took the place of is answered by none, off the map.
  project.forManager = [];
  assert.ok(!cards('working').some(([id]) => id === 'q23'));
  const visible = progressMap(store, project, 0, []);
  const ids = new Set(visible.tasks.map(task => task.id));
  assert.ok(visible.events.every(event => ids.has(String(event.item_id))));
  assert.equal(visible.events.some(event => event.item_id === 'q6'), false);
  assert.deepEqual(Object.keys(visible).sort(), ['cursor', 'events', 'id', 'tasks']);
});

test('the original request survives Manager clarification, with the answer and resulting task in the same exchange', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-map-clarification-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const record = fs.readFileSync(new URL('./fixtures/map-clarification/events.jsonl', import.meta.url), 'utf8');
  const events = record.trim().split('\n').map(line => JSON.parse(line) as Recorded);
  const original = events[0]!;
  const answer = events[4]!;
  const added = events.find(event => event.type === 'task.added');
  assert.ok(original.type === 'message' && answer.type === 'message' && added?.type === 'task.added');
  const project = store.create({ request: original.text, workdir: '', backend: 'codex', model: null });
  const path = join(store.dir(project.id), 'events.jsonl');
  fs.writeFileSync(path, events.slice(0, 4).map(event => JSON.stringify(event)).join('\n') + '\n');
  const before = progressMap(store, project, 0, []);
  assert.deepEqual(before.tasks.map(card => card.title), [firstLine(project.request)]);
  // Poll after the clarification arrives: the existing card id and title survive.
  fs.appendFileSync(path, events.slice(4).map(event => JSON.stringify(event)).join('\n') + '\n');
  project.tasks = [{ ...added.task, status: 'closed' }];
  const after = progressMap(store, project, Number(before.cursor), []);
  assert.deepEqual(after.tasks.map(card => [card.id, card.title, card.deps]), [
    ['q0', firstLine(original.text), []], ['t1', added.task.title, ['q0']],
  ]);
  assert.ok(after.events.some(letter => letter.item_id === 'q0' && letter.type === 'turn.asked' && letter.text === answer.text));
  assert.ok(!after.events.some(letter => letter.item_id === 'q0' && letter.type === 'turn.replied' && letter.to === 'engineer'));
  assert.ok(!after.tasks.some(card => card.title === firstLine(answer.text)));
  assert.equal(new Set(after.tasks.map(card => card.id)).size, after.tasks.length);
  assert.deepEqual(progressMap(store, { ...project, status: 'stopped' }, 0, []).tasks[0]!.title, firstLine(project.request));
  assert.deepEqual(progressMap(store, { ...project, status: 'failed' }, 0, []).tasks[0]!.title, firstLine(project.request));
});

test("the Manager's word for a task under way is answered once that task closes, and the task's row comes below it", t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-map-word-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: 'Build it.', backend: 'codex', model: null });
  project.tasks = [{ ...task, from: 'planner', status: 'closed' }, { ...task, id: 't2', from: 'planner' }];
  project.status = 'working';
  let second = 0;
  fs.writeFileSync(join(store.dir(project.id), 'events.jsonl'), ([
    { type: 'message', from: 'operator', text: 'Build it.', request_id: 'r0' }, { type: 'call.start', role: 'manager', answers: 'r0' },
    { type: 'handoff', role: 'manager', to: 'planner', letter: 'Plan it.', answers: 'r0' },
    { type: 'task.added', task: project.tasks[0] }, { type: 'task.added', task: project.tasks[1] },
    { type: 'message', from: 'operator', text: 'Use the newer model.', request_id: 'r5' }, { type: 'call.start', role: 'manager', answers: 'r5' },
    { type: 'handoff', role: 'manager', to: 'engineer', task: 't1', letter: 'Use the newer model.', answers: 'r5' },
    { type: 'task.closed', task: 't1' },
  ] as object[]).map(row => `${JSON.stringify({ ts: new Date(Date.UTC(2026, 9, 29, 11, 0, second++)).toISOString(), ...row })}\n`).join(''));
  const card = progressMap(store, project, 0, []).tasks.find(card => card.id === 'q5')!;
  assert.deepEqual([card.deps, card.status], [['t1'], 'done']);
  const rows = conversationRows(readRecord(store, project.id), project, { since: 1, calls: [], requests: [] });
  assert.deepEqual(rows.filter(row => row.type === 'ui.task').map(row => [row.item_id, row.order]), [['t2', 4.2], ['t1', 7.2]]);
});
