import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { replyReplay } from '../../../../packages/core/test/replyReplay';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '../../../../packages/core/src/store';
import { readEvents, snapshot, projectRecord, conversationRows, conversationPage, type Reading, type Recorded } from '../../../../packages/core/src/serve/views';
import type { EventMsg } from '../api';
import { EventStream } from './serverSnapshot';
import { mergeConversationEvents, mergeManagerReply, optimisticOperatorEvent } from '../lib/conversationEvents';
import failureRecord from '../../../../packages/core/test/fixtures/message-failure/record.json';
import type { Project } from '../../../../packages/core/src/store';
import { I18nProvider } from '../i18n';

// Real records: the Manager doing small work itself, and a brief it handed the Planner for three tasks.
// Each message is named, and the Manager's turn and letter on it name it, as the record now holds them.
const store = new Store(fileURLToPath(new URL('./fixtures/home', import.meta.url)));
const PROJECTS = ['p-0444b8d3', 'p-c855623c'];
afterEach(() => vi.unstubAllGlobals());

describe('one record, one conversation', () => {
  it.each(['zh-CN', 'en'])('keeps the recorded Manager failure reason visible once, including after reload (%s)', locale => {
    vi.stubGlobal('localStorage', { getItem: () => locale });
    const project = failureRecord.project as Project;
    const reading = projectRecord(failureRecord.events as Recorded[]);
    const rows = conversationRows(reading, project, { since: null, calls: [], requests: [] });
    const failed = rows.find(row => row.type === 'ui.argus')!;
    expect(failed.status).toBe('failed');
    expect(failed.text).toBe(project.failures![0]!.error);
    const local = optimisticOperatorEvent(project.id, project.requestId!, project.request);
    for (const events of [mergeConversationEvents(rows, [local]), rows]) {
      const html = renderToStaticMarkup(<I18nProvider><EventStream events={events} connected showReasoning={false} embedded /></I18nProvider>);
      expect(html.split(String(failed.text))).toHaveLength(2);
      expect(html).not.toContain('<details');
    }
  });
  it.each(PROJECTS)('%s reads the same line by line, as the page that sent each message saw it, as loaded at once', (id) => {
    const record = readEvents(store, id);
    const snap = snapshot(store, store.load(id), { since: null, calls: [], requests: [] });
    const render = (events: EventMsg[]) => renderToStaticMarkup(<EventStream events={events} snapshot={snap} connected showReasoning={false} embedded />);
    // The page open as the work went on: each line as the stream sends it, each message as the page
    // put it up when it was sent, and each reply as its message's stream brought it.
    let reading: Reading | undefined;
    let local: EventMsg[] = [];
    let waiting: { request: string; reply: string } | null = null;
    record.forEach((event, line) => {
      if (event.type === 'message' && !event.to) {
        const request = String(event.request_id);
        local = [...local, optimisticOperatorEvent(id, request, event.text, Date.parse(event.ts) - 1_000)];
        waiting = { request, reply: `m${line}-argus` };
      }
      reading = projectRecord([event], reading);
      const streamed = conversationRows(reading, store.load(id), { since: null, calls: [], requests: [] });
      for (const row of streamed) {
        if (waiting && !row.live && row.type === 'ui.argus' && row.message_id === waiting.reply) {
          local = mergeManagerReply(local, waiting.request, row);
          waiting = null;
        }
      }
      const rows = conversationRows(projectRecord(record.slice(0, line + 1)), store.load(id), { since: null, calls: [], requests: [] });
      expect(render(mergeConversationEvents(streamed, local)), `line ${line}`)
        .toBe(render(mergeConversationEvents(rows, [])));
    });
  });

  it('a brief the Manager hands the team reads as handed over, live and after a reload', () => {
    const rows = conversationRows(projectRecord(readEvents(store, 'p-c855623c')), store.load('p-c855623c'), { since: null, calls: [], requests: [] });
    const brief = rows.find(row => row.type === 'ui.argus' && row.to === 'planner')!;
    expect(brief.message_id).toBe('m0-argus');
    const sent = optimisticOperatorEvent('p-c855623c', 'r1', String(rows[0]!.text), Number(rows[0]!.ts) * 1_000 - 1_000);
    const live = mergeManagerReply([sent], 'r1', brief);
    for (const events of [mergeConversationEvents([], live), mergeConversationEvents(rows, [])]) {
      expect(renderToStaticMarkup(<EventStream events={events} connected showReasoning={false} embedded />))
        .toContain('data-testid="conversation-brief"');
    }
  });

  it.each(PROJECTS)('%s: paging rows retains the complete dialogue and every task', id => {
    const reading = projectRecord(readEvents(store, id));
    const rows = conversationRows(reading, store.load(id), { since: null, calls: [], requests: [] });
    const loaded: EventMsg[] = [];
    let before: number | undefined;
    do {
      const page = conversationPage(rows, reading.fold.line - 1, 3, before);
      loaded.unshift(...page.rows);
      before = page.before ?? undefined;
    } while (before !== undefined);
    expect(loaded).toEqual(rows);
    expect(loaded.some(row => row.type === 'engineer.progress')).toBe(false);
  });
});

it.each(['zh-CN', 'en'])('GPU dialogue has one Argus heading per reply, retaining process folded above answers at every replay prefix (%s)', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  const project: Project = { ...store.load(PROJECTS[0]!), tasks: [], status: 'stopped', sessions: {} };
  let reading: Reading | undefined;
  let active: string | undefined;
  for (let line = 0; line < replyReplay.length; line++) {
    const event = replyReplay[line]!;
    if (event.type === 'call.start') active = event.answers;
    if (event.type === 'call.end') active = undefined;
    reading = projectRecord([event], reading);
    project.forManager = [...reading.fold.asked.keys()].map(request_id => ({ text: '', request_id }));
    const live = { since: active ? 1 : null, requests: [], calls: active ? [{ role: 'manager' as const, task: undefined, answers: active, started_ts: 1 }] : [] };
    const rows = conversationRows(reading, project, live);
    const render = (rows: EventMsg[]) => renderToStaticMarkup(<I18nProvider><EventStream events={rows} connected showReasoning={false} embedded /></I18nProvider>);
    const html = render(rows);
    expect(html).toBe(render(conversationRows(projectRecord(replyReplay.slice(0, line + 1)), project, live)));
    expect(html.match(/class="argus-reply-name"/g) ?? []).toHaveLength(rows.filter(row => row.type === 'ui.operator').length);
    const replyHtml = html.split('<article').slice(1).filter(part => part.includes('class="argus-reply-name"'));
    rows.filter(row => row.type === 'ui.argus').forEach((row, i) => {
      // Words said on the way show; once a letter answers, they are its folded process.
      const [said, process] = [row.steps, row.process] as Array<string[] | undefined>;
      for (const text of [...said ?? [], ...process ?? []]) expect(replyHtml[i]).toContain(text);
      if (said?.length) expect(replyHtml[i]).toContain('data-manager-progress="true"');
      if (process?.length) expect(replyHtml[i]).toMatch(/<details data-manager-process="true"(?![^>]*\sopen)/);
      const first = [...said ?? [], ...process ?? []][0];
      if (row.text && first) expect(replyHtml[i]!.indexOf(first)).toBeLessThan(replyHtml[i]!.indexOf('data-manager-answer'));
    });
  }
});

it.each(['planner', 'engineer'])('keeps the recorded process question and one heading visible when a reply hands work to %s, including reload', to => {
  // The existing real-service fixture asks the operator about response times before
  // handing work to the Planner; vary only the recipient for the Engineer boundary.
  const record = readFileSync(new URL('../../../../packages/core/test/fixtures/progress-messages/events.jsonl', import.meta.url), 'utf8')
    .trim().split('\n').slice(0, 6).map(line => JSON.parse(line) as Recorded)
    .map(event => event.type === 'handoff' ? { ...event, to } : event);
  const question = '你们目前承诺的首次回复时限、服务时段是什么？';
  const work = { ...store.load(PROJECTS[0]!), tasks: [], status: 'stopped' as const, forManager: [{ text: '', request_id: 'progress-baseline-1440' }] };
  const render = (reading: Reading, running: boolean) => renderToStaticMarkup(<EventStream
    events={conversationRows(reading, work, { since: running ? 1 : null, requests: [], calls: running ? [{ role: 'manager', task: undefined, answers: 'progress-baseline-1440', started_ts: 1 }] : [] })}
    connected showReasoning={false} embedded />);
  const underway = projectRecord(record.slice(0, 4));
  expect(render(underway, true)).toContain(question);
  const complete = projectRecord(record.slice(4), underway);
  for (const reading of [complete, projectRecord(record)]) {
    const html = render(reading, false);
    expect(html.match(/class="argus-reply-name"/g)).toHaveLength(1);
    expect(html).toContain(question);
    const folded = html.indexOf('<details');
    expect(folded).toBeGreaterThan(html.indexOf('class="argus-reply-name"'));
    expect(folded).toBeGreaterThan(html.indexOf(question));
    expect(html).not.toMatch(/<details[^>]*\sopen(?:[\s=>])/);
    expect(html.slice(folded)).toContain('核对客服回复速度与每周汇总');
    expect(html.slice(folded)).not.toContain(question);
    expect(html.slice(folded)).not.toContain('class="argus-reply-name"');
  }
  expect(render(complete, false)).toBe(render(projectRecord(record), false));
});

it.each(['zh-CN', 'en'])('whole team replay has two Manager replies and keeps all Planner process folded under the brief (%s)', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  const record = readEvents(store, 'p-c855623c');
  const rows = conversationRows(projectRecord(record), store.load('p-c855623c'), { since: null, calls: [], requests: [] });
  const html = renderToStaticMarkup(<I18nProvider><EventStream events={rows} connected showReasoning={false} embedded /></I18nProvider>);
  expect(html.match(/class="argus-reply-name"/g)).toHaveLength(2);
  const replies = html.split('<article').slice(1).filter(part => part.includes('class="argus-reply-name"'));
  for (const event of record) if (event.type === 'step' && event.role === 'planner' && event.kind === 'message') {
    expect(replies[0]).toContain(event.text);
    expect(replies[1]).not.toContain(event.text);
  }
  // The Planner's words, and the Manager's before its letter to the operator, are process: folded
  // after the brief they followed, and above the answer they led to.
  expect(replies[0]!.indexOf('data-manager-process')).toBeGreaterThan(replies[0]!.indexOf('data-testid="conversation-brief"'));
  expect(replies[1]!.indexOf('data-manager-process')).toBeLessThan(replies[1]!.indexOf('data-manager-answer'));
  expect(replies[1]).not.toContain('data-manager-progress');
  expect(replies[1]).toContain('我会核对 result.md 和两个脚本的运行结果，再把文件交给你。');
  expect(replies[1]).toContain('计算结果');
});
