import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Store } from '../../../../packages/core/src/store';
import { readEvents, snapshot, projectRecord, conversationRows, conversationPage, type Reading } from '../../../../packages/core/src/serve/views';
import type { EventMsg } from '../api';
import { EventStream } from '../components/EventStream';
import { mergeConversationEvents, mergeOptimisticManagerReply, optimisticOperatorEvent } from '../lib/conversationEvents';

// Real records: the Manager doing small work itself, and a brief it handed the Planner for three tasks.
const store = new Store(fileURLToPath(new URL('./fixtures/home', import.meta.url)));
const PROJECTS = ['p-0444b8d3', 'p-c855623c'];

describe('one record, one conversation', () => {
  it.each(PROJECTS)('%s reads the same line by line, as the page that sent each message saw it, as loaded at once', (id) => {
    const record = readEvents(store, id);
    const snap = snapshot(store, store.load(id), { since: null, calls: [], requests: [] });
    const render = (events: EventMsg[]) => renderToStaticMarkup(<EventStream events={events} snapshot={snap} connected showReasoning={false} embedded />);
    // The page open as the work went on: each line as the stream sends it, each message as the page
    // put it up when it was sent, and each reply as its message's stream brought it.
    let reading: Reading | undefined;
    let local: EventMsg[] = [];
    let waiting: { request: number; reply: string } | null = null;
    record.forEach((event, line) => {
      if (event.type === 'message' && !event.to) {
        const request = line + 1;
        local = [...local, optimisticOperatorEvent(id, request, event.text, Date.parse(event.ts) - 1_000)];
        waiting = { request, reply: `m${line}-argus` };
      }
      reading = projectRecord([event], reading);
      const streamed = conversationRows(reading, store.load(id), { since: null, calls: [], requests: [] });
      for (const row of streamed) {
        if (waiting && row.type === 'ui.argus' && row.message_id === waiting.reply) {
          local = mergeOptimisticManagerReply(local, waiting.request, row);
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
    const sent = optimisticOperatorEvent('p-c855623c', 1, String(rows[0]!.text), Number(rows[0]!.ts) * 1_000 - 1_000);
    const live = mergeOptimisticManagerReply([sent], 1, brief);
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
