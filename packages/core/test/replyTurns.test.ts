import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { conversationRows, projectRecord, type Live, type Reading, type Recorded } from '../src/serve/views.js';
import type { Project } from '../src/store.js';
import { plannerBatchReplay } from './replyTurnsReplay.js';
import { words } from './helpers/words.js';

const idle: Live = { since: null, calls: [], requests: [] };
const fixture = new URL('./fixtures/progress-messages/', import.meta.url);
const record = readFileSync(new URL('events.jsonl', fixture), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const project = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;

test('#6 real service record: team turns retain process; only the top bar owns running/stopped state', () => {
  const at = record.findIndex(event => event.type === 'call.start' && event.role === 'manager' && !event.answers);
  assert.ok(at > 0);
  const prefix = record.slice(0, at + 1);
  const live: Live = { since: 1, calls: [{ role: 'manager', task: undefined, started_ts: 1, words: '核对结果', wordsKind: 'process' }], requests: [] };
  const running = conversationRows(projectRecord(prefix), { ...project, status: 'working' }, live);
  const team = running.find(row => (row.steps as string[] | undefined)?.includes('核对结果'))!;
  assert.equal(team.request_id, undefined);
  assert.equal(team.status, undefined);
  assert.equal(team.live, undefined);
  const endedAt = record.findIndex((event, line) => line > at && event.type === 'handoff' && event.role === 'manager');
  const stopped = conversationRows(projectRecord(record.slice(0, endedAt)), { ...project, status: 'stopped' }, idle);
  const owner = stopped.find(row => row.row_id === team.row_id)!;
  assert.ok((owner.steps as string[]).length);
  assert.equal(owner.status, undefined);
  const resumed = conversationRows(projectRecord(record.slice(0, endedAt)), project, live).find(row => row.row_id === team.row_id)!;
  assert.deepEqual(resumed.steps, [...owner.steps as string[], '核对结果']);
});

for (const reverse of [false, true]) test(`#9 new real Flow batch replay (${reverse ? 'B then A' : 'A then B'}): mixed call and descendants have their own reply`, async () => {
  const { record, project } = await plannerBatchReplay({ reverse });
  const rows = conversationRows(projectRecord(record), project, idle);
  const mixed = rows.find(row => words(row).includes('我来安排 B 的 b.py 任务。'))!;
  assert.equal(mixed.request_id, undefined, 'a mixed call cannot claim either operator request');
  assert.deepEqual(words(mixed), ['我来安排 B 的 b.py 任务。', 'b.py 收到，B 完成。']);
  for (const reply of rows.filter(row => row.request_id)) {
    assert.ok(!words(reply).includes('我来安排 B 的 b.py 任务。'));
    assert.ok(!words(reply).includes('b.py 收到，B 完成。'));
  }
  const mixedStart = record.findIndex(event => event.type === 'call.start' && event.role === 'planner' &&
    record.slice(0, record.indexOf(event)).filter(item => item.type === 'call.start' && item.role === 'planner').length === 2);
  assert.equal((record[mixedStart] as Extract<Recorded, { type: 'call.start' }>).planner_inputs, 2);
  assert.equal(projectRecord(record).fold.taskReplies.get('t3'), mixed.message_id);
  let incremental: Reading | undefined;
  for (const [line, event] of record.entries()) {
    incremental = projectRecord([event], incremental);
    assert.deepEqual(conversationRows(incremental, project, idle), conversationRows(projectRecord(record.slice(0, line + 1)), project, idle));
  }
  // Older records lack the count but contain the same accepted call/letter boundaries.
  const legacy = record.map(event => {
    if (event.type !== 'call.start') return event;
    const { planner_inputs: _count, ...old } = event;
    return old;
  });
  assert.deepEqual(conversationRows(projectRecord(legacy), project, idle), rows);
});


test('#9 actual Flow form correction consumes only the original read prefix; new letters wait', async () => {
  const { record, project } = await plannerBatchReplay({ correctSecond: true });
  const rows = conversationRows(projectRecord(record), project, idle);
  const original = rows.find(row => words(row).includes('我把 A 拆成两个任务。'))!;
  assert.deepEqual(words(original), ['我把 A 拆成两个任务。', 't1 收到。', 't1 修正表单。']);
  const mixed = rows.find(row => words(row).includes('我来安排 B 的 b.py 任务。'))!;
  assert.equal(mixed.request_id, undefined);
  assert.deepEqual(words(mixed), ['我来安排 B 的 b.py 任务。', 'b.py 收到，B 完成。']);
  assert.deepEqual(record.filter((event): event is Extract<Recorded, { type: 'call.start' }> => event.type === 'call.start' && event.role === 'planner')
    .map(event => event.planner_inputs), [1, 1, 1, 2, 1]);
});
