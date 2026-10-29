import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { ROLE_LOG, type EventMsg } from '@argus/contracts';
import { firstLine } from '../src/flow.js';
import { Store, type Project } from '../src/store.js';
import { conversationPage, conversationRows, projectRecord, snapshot, webEvents, progressMap, taskTrace, type Live, type Reading, type Recorded } from '../src/serve/views.js';

// Real three-task project already checked into the repository, with machine paths sanitized.
const fixture = new URL('../../../frontend/web/src/test/fixtures/home/projects/p-c855623c/', import.meta.url);
const record = readFileSync(new URL('events.jsonl', fixture), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const project = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;
const idle: Live = { since: null, calls: [], requests: [] };
const at = (line: number) => new Date(Date.parse(record.at(-1)!.ts) + line * 1000).toISOString();
const rowsOf = (events: Recorded[], work = project, live = idle) => conversationRows(projectRecord(events), work, live);

// Extend the real replay with a controlled long tail of its recorded command steps.
// This is an amplified replay, not the unavailable 2,000-event RL source mentioned in t1.
const commands = record.filter(event => event.type === 'step' && event.kind === 'command');
const longReplay = [...record, ...Array.from({ length: 140 }, (_, i) => ({ ...commands[i % commands.length]!, ts: at(i + 1) }))];

test('real three-task replay: completed tasks survive a 140-step tail and a fresh paginated read', () => {
  assert.ok(longReplay.length > 120);
  assert.ok(!webEvents(longReplay).slice(-120).some(row => row.type === 'life.mission.completed'));
  const expected = rowsOf(longReplay);
  assert.deepEqual(expected.filter(row => row.type === 'ui.task').map(row => [row.item_id, row.state]).sort(),
    [['t1', 'done'], ['t2', 'done'], ['t3', 'done']]);
  const pages: EventMsg[] = [];
  let before: number | undefined;
  do {
    const page = conversationPage(expected, longReplay.length - 1, 3, before);
    pages.unshift(...page.rows);
    before = page.before ?? undefined;
  } while (before !== undefined);
  assert.deepEqual(pages, expected);
  assert.ok(!pages.some(row => row.type === 'engineer.progress'));
});

test("a round's role log keeps each role's newest entries, however long the round runs", () => {
  const roles = projectRecord(longReplay).roleEvents.map(event => event.agent_layer);
  for (const role of new Set(roles)) assert.ok(roles.filter(other => other === role).length <= ROLE_LOG);
  assert.equal(roles.filter(role => role === 'engineer').length, ROLE_LOG);
  assert.equal(projectRecord(longReplay).roleEvents.at(-1)!.ts, Date.parse(longReplay.at(-1)!.ts) / 1000);
});

test('each prefix of the real record has identical live and refreshed rows and current-round logs', () => {
  let live: Reading | undefined;
  longReplay.forEach((event, line) => {
    live = projectRecord([event], live);
    const refreshed = projectRecord(longReplay.slice(0, line + 1));
    assert.deepEqual(conversationRows(live, project, idle), conversationRows(refreshed, project, idle), `rows at ${line}`);
    assert.deepEqual(live.roleEvents, refreshed.roleEvents, `role log at ${line}`);
  });
});

test('answers stay beside their question, task lines move to their latest state change, and steps do not move them', () => {
  const work: Project = { ...project, tasks: project.tasks.slice(0, 2).map(task => ({ ...task, status: 'open' })), status: 'working', questions: [] };
  const questions = work.tasks.map(task => ({ role: 'engineer' as const, task: task.id, question: `Choose for ${task.id}?` }));
  const events: Recorded[] = [
    ...work.tasks.map((task, i) => ({ type: 'task.added' as const, task, ts: at(i) })),
    ...questions.map((q, i) => ({ type: 'handoff' as const, role: q.role, task: q.task!, to: 'operator', letter: q.question, ts: at(i + 2) })),
    { type: 'message', from: 'operator', text: 'Second choice', to: 'engineer', task: questions[1]!.task, ts: at(4) },
    { type: 'message', from: 'operator', text: 'First choice', to: 'engineer', task: questions[0]!.task, ts: at(5) },
    { type: 'task.closed', task: work.tasks[0]!.id, ts: at(6) },
    { type: 'step', role: 'engineer', task: work.tasks[1]!.id, kind: 'command', text: 'python train.py', ts: at(7) },
  ];
  work.tasks[0]!.status = 'closed';
  const rows = rowsOf(events, work, { since: 1, calls: [{ role: 'engineer', task: work.tasks[1]!.id }], requests: [] });
  const dialogue = rows.filter(row => row.type !== 'ui.task');
  assert.deepEqual(dialogue.map(row => row.text), [questions[1]!.question, 'Second choice', questions[0]!.question, 'First choice']);
  assert.equal(rows.at(-1)?.state, 'done');
  assert.equal(rows.find(row => row.item_id === work.tasks[1]!.id && row.type === 'ui.task')?.event_id, 4);
  const page = conversationPage(rows, events.length - 1, 3);
  assert.equal(page.rows[0]?.type, 'ui.question');
  const reading = projectRecord(events);
  assert.deepEqual(reading.roleEvents.map(row => row.text), ['python train.py']);
});

test('snapshot status distinguishes stopped tasks, unanswered questions and concurrent work with one server label', t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-rows-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: 'Research', workdir: home, backend: 'codex', model: null });
  work.tasks = project.tasks.slice(0, 2).map(task => ({ ...task, status: 'open' }));
  work.status = 'stopped';
  store.save(work);
  writeFileSync(join(store.dir(work.id), 'events.jsonl'), longReplay.map(event => JSON.stringify(event)).join('\n') + '\n');
  assert.equal(snapshot(store, work, idle).work_status?.label['zh-CN'], '已停止：2 个任务暂停');
  work.questions = [{ role: 'engineer', task: work.tasks[0]!.id, question: 'Choose?' }];
  work.status = 'asking';
  assert.equal(snapshot(store, work, idle).work_status?.label['zh-CN'], '等你回答');
  const live: Live = { since: 1, calls: [{ role: 'engineer', task: work.tasks[1]!.id }], requests: [] };
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '1 个任务进行中');
  work.questions = [];
  work.status = 'working';
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '1 个任务进行中');
  live.calls.push({ role: 'reviewer', task: work.tasks[0]!.id });
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '2 个任务进行中');
  work.questions = [{ role: 'engineer', task: work.tasks[0]!.id, question: 'Choose?' }];
  live.requests.push('r1');
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '正在回复你的消息');
});

// Captured from the baseline build on port 18802 with the controllable Codex proxy.
const stoppedRecord = readFileSync(new URL('./fixtures/conversation-stop/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
test('real stopped conversation: refresh retains the original request and Resume reuses its reply row', () => {
  const stop = stoppedRecord.findIndex(event => event.type === 'call.end');
  // The message the stop interrupted waits for Resume.
  const asked = stoppedRecord.find(event => event.type === 'message')!;
  const work = { ...project, tasks: [], status: 'stopped' as const, forManager: [{ text: asked.type === 'message' ? asked.text : '', request_id: 'stop-request-1' }] };
  const stopped = rowsOf(stoppedRecord.slice(0, stop + 1), work);
  assert.equal(stopped.length, 2);
  assert.match(String(stopped[0]?.text), /不要丢掉这条消息/);
  assert.equal(stopped[1]?.status, 'stopped');
  const resumed = rowsOf(stoppedRecord, { ...work, status: 'working' }, { since: 1, calls: [{ role: 'manager', task: undefined, answers: 'stop-request-1' }], requests: [] });
  assert.equal(resumed[1]?.message_id, stopped[1]?.message_id);
  assert.equal(resumed[1]?.live, true);
  assert.equal(resumed[1]?.text, '');
  assert.equal(projectRecord(stoppedRecord).roleEvents.slice().reverse().find(row => row.agent_layer === 'manager' && row.kind === 'agent_message')?.text, '我正在核对第 14 项停止与继续运行记录。');
  let reading: Reading | undefined;
  stoppedRecord.forEach((event, line) => {
    reading = projectRecord([event], reading);
    assert.deepEqual(conversationRows(reading, work, idle), rowsOf(stoppedRecord.slice(0, line + 1), work));
  });
});

test('a queued operator message keeps its own reply and a letter from the team does not answer it', () => {
  const events: Recorded[] = [
    { type: 'handoff', role: 'engineer', to: 'manager', letter: 'Team report', ts: at(1) },
    { type: 'call.start', role: 'manager', ts: at(2) },
    { type: 'message', from: 'operator', text: 'My new question', request_id: 'r-new', ts: at(3) },
    { type: 'handoff', role: 'manager', to: 'operator', letter: 'On the team report', ts: at(4) },
    { type: 'call.start', role: 'manager', answers: 'r-new', ts: at(5) },
    { type: 'step', role: 'manager', kind: 'message', text: 'Checking your question', ts: at(6) },
    { type: 'message', from: 'operator', text: 'One more question', request_id: 'r-more', ts: at(7) },
  ];
  const live: Live = { since: 1, calls: [{ role: 'manager', task: undefined, answers: 'r-new' }], requests: [] };
  const rows = rowsOf(events, { ...project, tasks: [], status: 'working', forManager: [{ text: 'One more question', request_id: 'r-more' }] }, live);
  assert.equal(rows.find(row => row.text === 'On the team report')?.request_id, undefined);
  assert.equal(rows.find(row => row.request_id === 'r-new' && row.type === 'ui.argus')?.text, '');
  assert.equal(rows.find(row => row.request_id === 'r-more' && row.type === 'ui.argus')?.status, 'waiting');
});

// Real sends, refreshes and replies from the controlled browser service on port 18801.
test('browser record: request identities and structured attachment metadata survive every replay prefix', () => {
  const captured = JSON.parse(readFileSync(new URL('./fixtures/conversation-fixes/after.json', import.meta.url), 'utf8')) as {
    events: Recorded[]; requests: Array<{ text: string; request_id: string; attachments?: unknown[] }>;
  };
  let reading: Reading | undefined;
  captured.events.forEach((event, line) => {
    reading = projectRecord([event], reading);
    assert.deepEqual(conversationRows(reading, project, idle), rowsOf(captured.events.slice(0, line + 1)));
  });
  const rows = rowsOf(captured.events);
  const messages = rows.filter(row => row.type === 'ui.operator');
  assert.deepEqual(messages.map(row => ({ text: row.text, request_id: row.request_id, ...(row.attachments ? { attachments: row.attachments } : {}) })), captured.requests);
  assert.deepEqual(rows.filter(row => row.type === 'ui.argus').map(row => row.request_id), captured.requests.map(request => request.request_id));
  assert.ok(rows.filter(row => row.type === 'ui.argus').every(row => (row.delivery as { kind: string }).kind === 'result'));
});

test('real task fragments: progress and files share the map projection; incoming Planner letters keep their own author', t => {
  const events = readFileSync(new URL('./fixtures/conversation-task/events.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
  const home = mkdtempSync(join(tmpdir(), 'argus-task-letters-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  work.tasks = project.tasks.map(task => ({ ...task }));
  work.tasks[2]!.status = 'open';
  work.status = 'working';
  const path = join(store.dir(work.id), 'events.jsonl');
  const write = (rows: Recorded[]) => writeFileSync(path, rows.map(row => JSON.stringify(row)).join('\n') + '\n');
  const before = events.slice(0, 6);
  write(before);
  const line = rowsOf(before, work).find(row => row.type === 'ui.task')!;
  assert.equal(line.progress, 'text' in events[3]! ? events[3]!.text : '');
  assert.deepEqual(line.result_paths, []);
  const trace = taskTrace(store, work, 't3');
  assert.deepEqual(trace.groups.map(group => group.role), ['planner', 'planner']);
  assert.equal(trace.groups[1]!.entries[0]!.text, 'letter' in events[5]! ? events[5]!.letter : '');
  assert.ok(!before.some(event => event.type === 'call.end' && event.task === 't3'));
  // Append rather than replace the file: both views continue from their shared cursor.
  writeFileSync(path, events.slice(6).map(row => JSON.stringify(row)).join('\n') + '\n', { flag: 'a' });
  work.tasks[2]!.status = 'closed';
  const map = progressMap(store, work, 0).tasks.find(task => task.id === 't3')!;
  const done = rowsOf(events, work).find(row => row.type === 'ui.task')!;
  assert.deepEqual(done.result_paths, ['result.md']);
  assert.deepEqual(done.result_paths, map.result_paths);
  assert.equal(done.progress, firstLine(map.latest_action!.text));
  assert.equal(map.latest_action!.role, 'reviewer');
  assert.deepEqual(taskTrace(store, work, 't3').groups.map(group => group.role), ['planner', 'planner', 'engineer', 'reviewer', 'planner']);
});
