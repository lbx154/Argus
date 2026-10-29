import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it } from 'vitest';
import { Store, type Project } from '../../../../packages/core/src/store';
import { conversationRows, projectRecord, progressMap, snapshot, type Recorded } from '../../../../packages/core/src/serve/views';
import { EventStream, setRoleLog } from './serverSnapshot';

// A real task's exact title/ID, with a controlled question/answer extension.
const added = JSON.parse(readFileSync(new URL('../../../../packages/core/test/fixtures/operator-task/events.jsonl', import.meta.url), 'utf8')) as Extract<Recorded, { type: 'task.added' }>;
const question = '请问输出用中文还是英文？';
const ask: Recorded = { type: 'handoff', role: 'engineer', task: added.task.id, to: 'operator', letter: question, ts: '2026-10-29T12:21:00.000Z' };
const answer: Recorded = { type: 'message', from: 'operator', to: 'engineer', task: added.task.id, text: '请用中文。', ts: '2026-10-29T12:21:01.000Z' };
const home = mkdtempSync(join(tmpdir(), 'argus-question-replay-'));
afterEach(() => rmSync(home, { recursive: true, force: true }));
function replay(events: Recorded[]) {
  const store = new Store(home);
  const project: Project = { ...store.create({ workdir: home, request: '', backend: 'codex', model: null }),
    tasks: [added.task], status: 'asking', questions: [{ role: 'engineer', task: added.task.id, question }] };
  writeFileSync(join(store.dir(project.id), 'events.jsonl'), events.map(event => JSON.stringify(event)).join('\n') + '\n');
  const reading = projectRecord(events);
  const live = { since: null, calls: [], requests: [] };
  const snap = snapshot(store, project, live);
  setRoleLog(project.id, reading.roleEvents);
  const rows = conversationRows(reading, project, live);
  const html = renderToStaticMarkup(<EventStream snapshot={snap} events={rows} connected showReasoning={false} embedded onConversationAction={() => {}} />);
  return { store, project, snap, html };
}

it('keeps the pending question out of conversation rows, role headlines, map progress and repeated status explanations', () => {
  const { store, project, snap, html } = replay([added, ask]);
  expect(snap.pending_questions?.[0]).toMatchObject({ operator_decision: { id: 'engineer-t1', item_id: 't1', question } });
  expect(html).not.toContain(question);
  expect(snap.work_status?.label.en).toBe('Paused');
  expect(progressMap(store, project, 0).tasks[0].latest_action).toBeUndefined();
});

it('retains the answered question as history and removes resend only from the actual answer bubble', () => {
  const { html } = replay([added, ask, answer]);
  expect(html.match(new RegExp(question, 'g'))).toHaveLength(1);
  expect(html).toContain('请用中文。');
  expect(html).not.toContain('Edit and resend');
  const regular = replay([added, { ...answer, to: undefined, task: undefined, request_id: 'ordinary' }]);
  expect(regular.html).toContain('Edit and resend');
});
