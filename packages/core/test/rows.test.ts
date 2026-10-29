import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { EventMsg } from '@argus/contracts';
import { Store, questionId, type Project } from '../src/store.js';
import { conversationPage, conversationRows, projectRecord, snapshot, webEvents, type Live, type Reading, type Recorded } from '../src/serve/views.js';

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
    { type: 'message', from: 'operator', text: 'Second choice', to: 'engineer', answer_to: questionId(questions[1]!), ts: at(4) },
    { type: 'message', from: 'operator', text: 'First choice', to: 'engineer', answer_to: questionId(questions[0]!), ts: at(5) },
    { type: 'task.closed', task: work.tasks[0]!.id, ts: at(6) },
    { type: 'step', role: 'engineer', task: work.tasks[1]!.id, kind: 'command', text: 'python train.py', ts: at(7) },
  ];
  work.tasks[0]!.status = 'closed';
  const rows = rowsOf(events, work, { since: 1, calls: [{ role: 'engineer', task: work.tasks[1]!.id }], requests: [] });
  const dialogue = rows.filter(row => row.type !== 'ui.task');
  assert.deepEqual(dialogue.map(row => row.text), [questions[0]!.question, 'First choice', questions[1]!.question, 'Second choice']);
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
  assert.equal(snapshot(store, work, idle).work_status?.label['zh-CN'], '等你回答 1 个问题');
  const live: Live = { since: 1, calls: [{ role: 'engineer', task: work.tasks[1]!.id }], requests: [] };
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '等你回答 1 个问题 · 1 个任务继续进行');
  work.questions = [];
  work.status = 'working';
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '1 个任务进行中');
  live.calls.push({ role: 'reviewer', task: work.tasks[0]!.id });
  assert.equal(snapshot(store, work, live).work_status?.label['zh-CN'], '2 个任务进行中');
});
