import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it } from 'vitest';
import { Store, type Project } from '../../../../packages/core/src/store';
import { conversationRows, projectRecord, progressMap, readEvents, snapshot, type Recorded } from '../../../../packages/core/src/serve/views';
import { EventStream, setRoleLog } from './serverSnapshot';

// A real task's exact title/ID, with a controlled question/answer extension.
const added = JSON.parse(readFileSync(new URL('../../../../packages/core/test/fixtures/operator-task/events.jsonl', import.meta.url), 'utf8')) as Extract<Recorded, { type: 'task.added' }>;
// First line: the operator's report (project p-f6a790f1, event 232), trimmed to
// this issue. The next two lines extend the real task with all reported markup.
const [, ask, answer] = readFileSync(new URL('../../../../packages/core/test/fixtures/question-display/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line)) as [Recorded, Extract<Recorded, { type: 'handoff' }>, Extract<Recorded, { type: 'message' }>];
const question = ask.letter;
// The Reviewer's exact leading-table example, trimmed from the returned letter.
const tableFirst = JSON.parse(readFileSync(new URL('../../../../packages/core/test/fixtures/question-display/table-first.jsonl', import.meta.url), 'utf8')) as Extract<Recorded, { type: 'handoff' }>;
const tableFirstAsk = { ...ask, letter: tableFirst.letter };
const home = mkdtempSync(join(tmpdir(), 'argus-question-replay-'));
afterEach(() => rmSync(home, { recursive: true, force: true }));
function replay(events: Recorded[], pending = ask) {
  const store = new Store(home);
  const project: Project = { ...store.create({ workdir: home, request: '', backend: 'codex', model: null }),
    tasks: [added.task], status: 'asking', questions: events.includes(pending) && !events.includes(answer)
      ? [{ role: 'engineer', task: added.task.id, question: pending.letter }] : [] };
  writeFileSync(join(store.dir(project.id), 'events.jsonl'), events.map(event => JSON.stringify(event)).join('\n') + '\n');
  const reading = projectRecord(readEvents(store, project.id));
  const live = { since: null, calls: [], requests: [] };
  const snap = snapshot(store, project, live);
  setRoleLog(project.id, reading.roleEvents);
  const rows = conversationRows(reading, project, live);
  const html = renderToStaticMarkup(<EventStream snapshot={snap} events={rows} connected showReasoning={false} embedded onConversationAction={() => {}} />);
  return { store, project, snap, rows, html };
}

function expectOneFormattedQuestion(html: string) {
  expect(html.match(/data-conversation-question=/g)).toHaveLength(1);
  expect(html.match(/请确认运行/g)).toHaveLength(1);
  expect(html).toMatch(/<strong\b[^>]*>3 次<\/strong>/);
  expect(html).toMatch(/<code\b[^>]*>report.md<\/code>/);
  expect(html).toMatch(/<table\b/);
  expect(html).toMatch(/<th\b[^>]*>项目<\/th>/);
  expect(html).toMatch(/<td\b[^>]*>实验<\/td>/);
  expect(html).not.toContain('**3 次**');
}

it('shows the pending question once in conversation with Markdown, keeping waiting status and map progress separate', () => {
  const { store, project, snap, html } = replay([added, ask]);
  expect(snap.pending_questions?.[0]).toMatchObject({ operator_decision: { id: 'engineer-t1', item_id: 't1', question } });
  expectOneFormattedQuestion(html);
  expect(snap.work_status?.label.en).toBe('Waiting for your answer');
  expect(progressMap(store, project, 0, []).tasks[0].latest_action).toBeUndefined();
});

it('retains the answered question as history and removes resend only from the actual answer bubble', () => {
  const { html, store, project } = replay([added, ask, answer]);
  const reading = projectRecord([added, ask, answer]);
  expect(conversationRows(reading, project, { since: null, calls: [], requests: [] })
    .find(row => row.type === 'ui.task')?.progress).toBe('');
  expect(progressMap(store, project, 0, []).tasks[0].latest_action).toBeUndefined();
  expectOneFormattedQuestion(html);
  expect(html).toContain(answer.text);
  expect(html).not.toContain('Edit and resend');
  const regular = replay([added, { ...answer, to: undefined, task: undefined, request_id: 'ordinary' }]);
  expect(regular.html).toContain('Edit and resend');
});

it('keeps one formatted question after a cold replay, before and after the answer', () => {
  const original = replay([added, ask]).rows.find(row => row.type === 'ui.question')!.question_id;
  for (const events of [[added, ask], [added, ask, answer]]) {
    const refreshed = replay(events);
    expectOneFormattedQuestion(refreshed.html);
    expect(refreshed.rows.find(row => row.type === 'ui.question')!.question_id).toBe(original);
    if (events.includes(answer)) expect(refreshed.rows.find(row => row.answer_to)?.answer_to).toBe(original);
  }
});

it.each([false, true])('keeps a leading table as Markdown before and after a refresh (answered=%s)', answered => {
  const events = answered ? [added, tableFirstAsk, answer] : [added, tableFirstAsk];
  const shown = replay(events, tableFirstAsk);
  const refreshed = replay(events, tableFirstAsk);
  for (const { html, rows } of [shown, refreshed]) {
    expectOneFormattedQuestion(html);
    expect(html).toMatch(/<\/p>\s*<table\b/);
    expect(rows.find(row => row.type === 'ui.question')?.question_id).toBe('q1');
    if (answered) expect(rows.find(row => row.answer_to)?.answer_to).toBe('q1');
  }
});
