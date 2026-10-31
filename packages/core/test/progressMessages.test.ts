import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import type { Project } from '../src/store.js';
import { conversationPage, conversationRows, projectRecord, type Reading, type Recorded } from '../src/serve/views.js';

const fixture = new URL('./fixtures/progress-messages/', import.meta.url);
const record = readFileSync(new URL('events.jsonl', fixture), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const project = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;
const idle = { since: null, calls: [], requests: [] };
const messages = record.filter((event): event is Extract<Recorded, { type: 'step' }> =>
  event.type === 'step' && event.kind === 'message' && (event.role === 'manager' || event.role === 'planner'));

test('new real-service reproduction retains every Manager/Planner message and question across incremental reads, completion and pagination', () => {
  assert.equal(messages.length, 6);
  assert.equal(messages.filter(event => event.text.endsWith('？')).length, 2);
  let reading: Reading | undefined;
  for (const [line, event] of record.entries()) {
    reading = projectRecord([event], reading);
    const prefix = record.slice(0, line + 1);
    const rows = conversationRows(reading, project, idle);
    assert.deepEqual(rows, conversationRows(projectRecord(prefix), project, idle));
    const expected = prefix.filter(event => messages.includes(event as typeof messages[number]));
    const retained = rows.filter(row => row.type === 'ui.argus').flatMap(row => [
      ...(row.progress as string[] | undefined ?? []),
      ...messages.some(event => event.text === row.text) ? [String(row.text)] : [],
    ]);
    assert.deepEqual(retained.slice().sort(), expected.map(event => (event as typeof messages[number]).text).sort());
    const replies = rows.filter(row => row.type === 'ui.argus' && row.request_id);
    assert.equal(new Set(replies.map(row => row.request_id)).size, replies.length);
  }
  const rows = conversationRows(projectRecord(record), project, idle);
  const paged = [];
  let before: number | undefined;
  do {
    const page = conversationPage(rows, record.length - 1, 2, before);
    paged.unshift(...page.rows);
    before = page.before ?? undefined;
  } while (before !== undefined);
  assert.deepEqual(paged, rows);
  assert.ok(!rows.some(row => row.type === 'ui.argus' && row.text === '内部核对已经完成。'));
});

test('task card waits for the Manager and takes only the first selected result; internal task files stay in the record', () => {
  const delivery = record.findIndex(event => event.type === 'handoff' && event.role === 'manager' && event.to === 'operator');
  const task = (events: Recorded[]) => conversationRows(projectRecord(events), project, idle).find(row => row.type === 'ui.task')!;
  assert.deepEqual(task(record.slice(0, delivery)).result_paths, []);
  assert.deepEqual(task(record).result_paths, ['老板沟通稿.md']);
  assert.ok(record.some(event => event.type === 'handoff' && event.role === 'reviewer' && event.open?.includes('validation.json')));
  // An unrelated subsequent request does not replace the first request's selection.
  const later: Recorded[] = [...record,
    { type: 'message', from: 'operator', text: '另一件事', request_id: 'other', ts: record.at(-1)!.ts },
    { type: 'handoff', role: 'manager', to: 'operator', answers: 'other', letter: '另一个结果', open: ['other.md', 'internal.json'], ts: record.at(-1)!.ts }];
  assert.deepEqual(task(later).result_paths, ['老板沟通稿.md']);
  // Later words without a new selection keep the already delivered primary result.
  assert.deepEqual(task([...record, { type: 'handoff', role: 'manager', to: 'operator',
    letter: '补充说明', ts: record.at(-1)!.ts }]).result_paths, ['老板沟通稿.md']);
  // A report with no selected files does not fall back to internal task files.
  const noFiles = record.map(event => event === record[delivery] ? { ...event, open: [] } : event) as Recorded[];
  assert.deepEqual(task(noFiles).result_paths, []);
});
