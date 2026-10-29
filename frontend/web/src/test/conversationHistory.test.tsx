import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type EventMsg } from '../api';
import { roleSnapshot } from './serverSnapshot';
import { useConversationHistory } from '../useConversationHistory';

vi.mock('../api', async importOriginal => ({ ...await importOriginal<typeof import('../api')>(), openConversation: vi.fn(() => () => {}) }));

type Turns = Awaited<ReturnType<typeof api.conversation>>;
let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
let latest: ReturnType<typeof useConversationHistory>;
let rendered: { sid: string; text: string }[];
const optimistic: EventMsg[] = [{ type: 'ui.operator', text: 'Only session A has this draft.', ts: 3 }];
const turn = (text: string): Turns => ({ rows: [{ type: 'ui.argus', row_id: 'a', text, ts: 1, order: 1 }], before: null, cursor: 1 });
const cached = (text: string) => ({ pages: [turn(text)], pageParams: [undefined] });

function Probe({ sid }: { sid: string }) {
  latest = useConversationHistory(sid, true, optimistic, 'a');
  const text = latest.events.map(event => event.text).join('|');
  rendered.push({ sid, text });
  return <div>{latest.status}:{text}</div>;
}
const tree = (sid: string) => <QueryClientProvider client={client}><Probe sid={sid} /></QueryClientProvider>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  rendered = [];
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('keeps history pending until its independent query resolves, then distinguishes an empty session', async () => {
  let resolve!: (value: Turns) => void;
  const request = vi.spyOn(api, 'conversation').mockReturnValue(new Promise(done => { resolve = done; }));
  act(() => { renderer = create(tree('empty')); });
  expect(latest.status).toBe('loading');
  expect(latest.events).toEqual([]);
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { resolve({ rows: [], before: null, cursor: -1 }); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('ready');
  expect(latest.events).toEqual([]);
});

it('reports an initial history failure and recovers only from the explicit read retry', async () => {
  const request = vi.spyOn(api, 'conversation').mockRejectedValueOnce(new Error('History unavailable'));
  act(() => { renderer = create(tree('b')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('error');
  expect(latest.events).toEqual([]);
  expect(request).toHaveBeenCalledTimes(1);
  request.mockResolvedValueOnce(turn('Recovered session B answer.'));
  await act(async () => { await latest.query.refetch(); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('ready');
  expect(latest.events.map(event => event.text)).toEqual(['Recovered session B answer.']);
  expect(request).toHaveBeenCalledTimes(2);
});

it('retains current history when a background refresh fails', async () => {
  client.setQueryData(['conversation', 'b'], cached('Saved session B answer.'));
  vi.spyOn(api, 'conversation').mockRejectedValue(new Error('Refresh unavailable'));
  act(() => { renderer = create(tree('b')); });
  expect(latest.status).toBe('ready');
  await act(async () => { await latest.query.refetch(); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('error');
  expect(latest.events.map(event => event.text)).toEqual(['Saved session B answer.']);
});

it('never lends cached or optimistic history to another session, including a late old response', async () => {
  client.setQueryData(['conversation', 'a'], cached('Saved session A answer.'));
  client.setQueryData(['conversation', 'b'], cached('Saved session B answer.'));
  let resolve!: (value: Turns) => void;
  vi.spyOn(api, 'conversation').mockReturnValue(new Promise(done => { resolve = done; }));
  act(() => { renderer = create(tree('a')); });
  expect(latest.events.map(event => event.text)).toEqual(['Saved session A answer.', optimistic[0].text]);
  act(() => { void latest.query.refetch(); });
  act(() => renderer!.update(tree('b')));
  expect(latest.status).toBe('ready');
  expect(latest.events.map(event => event.text)).toEqual(['Saved session B answer.']);
  await act(async () => { resolve(turn('Late session A answer.')); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.events.map(event => event.text)).toEqual(['Saved session B answer.']);
  expect(rendered.filter(row => row.sid === 'b').every(row => !row.text.includes('session A'))).toBe(true);
});

it('updates a task across loaded pages without moving it behind the question and answer', async () => {
  const task = { type: 'ui.task', row_id: 'task-t1', item_id: 't1', state: 'running', event_id: 1, order: 1 };
  const question = { type: 'ui.question', row_id: 'q1', text: 'Choose?', event_id: 10, order: 10 };
  client.setQueryData(['conversation', 'b'], {
    pages: [{ rows: [question], before: 10, cursor: 10 }, { rows: [task], before: null, cursor: 10 }],
    pageParams: [undefined, 10],
  });
  const { openConversation } = await import('../api');
  act(() => { renderer = create(tree('b')); });
  const receive = vi.mocked(openConversation).mock.calls.at(-1)![2];
  act(() => receive({ type: 'ui.projection', cursor: 15, round: 12, role_events: [], rows: [
    { ...question, event_id: 12, order: 12 },
    { type: 'ui.operator', row_id: 'answer', text: 'Yes', event_id: 12, order: 12.1 },
    { ...task, state: 'done', event_id: 15 },
  ] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(latest.events.map(row => row.row_id)).toEqual(['task-t1', 'q1', 'answer']);
  expect(latest.events[0]?.state).toBe('done');
});

it('adds an answered pair absent from the current page and uses the same paging controller to load older rows', async () => {
  const recent = { type: 'ui.argus', row_id: 'm30', text: 'Later Manager reply', event_id: 30, order: 30 };
  client.setQueryData(['conversation', 'b'], {
    pages: [{ rows: [recent], before: 30, cursor: 30 }], pageParams: [undefined],
  });
  const request = vi.spyOn(api, 'conversation').mockResolvedValue({
    rows: [{ type: 'ui.operator', row_id: 'm1', text: 'Original request', event_id: 1, order: 1 }],
    before: null, cursor: 35,
  });
  const { openConversation } = await import('../api');
  act(() => { renderer = create(tree('b')); });
  expect(latest.events).toEqual([recent]);
  const receive = vi.mocked(openConversation).mock.calls.at(-1)![2];
  act(() => receive({ type: 'ui.projection', cursor: 35, round: 35, role_events: [], rows: [
    { type: 'ui.question', row_id: 'q1', question_id: 'seed', text: 'Which seed?', event_id: 35, order: 35 },
    { type: 'ui.operator', row_id: 'answer', answer_to: 'seed', text: '424242', event_id: 35, order: 35.1 },
  ] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(latest.events.map(row => row.text)).toEqual(['Later Manager reply', 'Which seed?', '424242']);
  expect(latest.pagination.hasOlder).toBe(true);
  act(() => latest.pagination.onLoadOlder());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(request).toHaveBeenCalledWith('b', 30, expect.any(AbortSignal));
  expect(latest.pagination.hasOlder).toBe(false);
  expect(latest.events.map(row => row.text)).toEqual(['Original request', 'Later Manager reply', 'Which seed?', '424242']);
});

it('keeps first-frame and appended role logs independent of snapshots and isolated through project switches', async () => {
  const { openConversation } = await import('../api');
  const { useRoleLog, roleLogKey } = await import('../useRoleLog');
  const { ProjectRoleLogs } = await import('../components/EventStream');
  let log!: ReturnType<typeof useRoleLog>;
  const snap = roleSnapshot([], {
    session: { id: 'a', display_name: '', objective: '', workdir: '', last_active: 1 },
    roles: [], work_status: { state: 'running', roles: ['engineer'], role: 'engineer', taskId: '', title: '', reason: '', foreground: false,
      activityAt: null, label: { 'zh-CN': '', en: '' } },
  });
  function Surfaces({ sid, done = false }: { sid: string; done?: boolean }) {
    useConversationHistory(sid, true, [], null);
    log = useRoleLog(sid);
    const snapshot = { ...snap, session: { ...snap.session, id: sid },
      work_status: done ? { ...snap.work_status!, state: 'idle' as const, roles: [] } : snap.work_status };
    return <><div data-surface="conversation"><ProjectRoleLogs snapshot={snapshot} connected /></div>
      <div data-surface="map"><ProjectRoleLogs snapshot={snapshot} connected details /></div></>;
  }
  const surfaces = (sid: string, done = false) => <QueryClientProvider client={client}><Surfaces sid={sid} done={done} /></QueryClientProvider>;
  client.setQueryData(['conversation', 'a'], cached('A'));
  client.setQueryData(['conversation', 'b'], cached('B'));
  act(() => { renderer = create(surfaces('a')); });
  const initialA = vi.mocked(openConversation).mock.calls.at(-1)!;
  expect(initialA.slice(0, 2)).toEqual(['a', 1]);
  const event = (event_id: number, text: string): EventMsg => ({ type: 'engineer.progress', agent_layer: 'engineer', kind: 'agent_message', event_id, ts: event_id, text });
  // The first rows frame includes logs older than the conversation cursor, even without a snapshot cache.
  const receiveA = initialA[2];
  act(() => receiveA({ type: 'ui.projection', cursor: 9, round: 0, rows: [], role_events: [event(0, 'First saved finding')] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(log.events.map(row => row.text)).toEqual(['First saved finding']);
  expect(client.getQueryData(['snapshot', 'a'])).toBeUndefined();
  act(() => receiveA({ type: 'ui.projection', cursor: 10, round: 0, rows: [], role_events: [event(10, 'Streamed finding')] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const text = (node: import('react-test-renderer').ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
  const words = () => text(renderer!.root);
  expect(words()).not.toContain('Streamed finding');
  act(() => renderer!.root.findAllByProps({ 'data-role': 'engineer' }).forEach(group => group.findAllByType('button')[0].props.onClick()));
  expect(words().match(/Streamed finding/g)).toHaveLength(2);
  const saved = log;
  client.setQueryData(['snapshot', 'a'], snap);
  client.setQueryData(['snapshot', 'a'], { ...snap, session: { ...snap.session, last_active: 20 } });
  expect(client.getQueryData(roleLogKey('a'))).toEqual(saved);
  act(() => renderer!.update(surfaces('b')));
  expect(log.events).toEqual([]);
  const receiveB = vi.mocked(openConversation).mock.calls.at(-1)![2];
  act(() => receiveA({ type: 'ui.projection', cursor: 11, round: 0, rows: [], role_events: [event(11, 'Late A finding')] }));
  act(() => receiveB({ type: 'ui.projection', cursor: 4, round: 3, rows: [], role_events: [event(4, 'Only B finding')] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(log.events.map(row => row.text)).toEqual(['Only B finding']);
  expect(words()).not.toContain('A finding');
  act(() => renderer!.update(surfaces('a')));
  expect(log.events.at(-1)?.text).toBe('Streamed finding');
  const reconnect = vi.mocked(openConversation).mock.calls.at(-1)![2];
  act(() => reconnect({ type: 'ui.projection', cursor: 13, round: 12, rows: [], role_events: [event(13, 'New round finding')] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(log).toEqual({ round: 12, events: [event(13, 'New round finding')] });
  act(() => renderer!.update(surfaces('a', true)));
  const conversation = renderer!.root.findByProps({ 'data-surface': 'conversation' });
  expect(conversation.findAllByProps({ 'data-project-work': true })).toHaveLength(0);
  act(() => renderer!.root.findByProps({ 'data-surface': 'map' }).findByProps({ 'data-role': 'engineer' }).findAllByType('button')[0].props.onClick());
  expect(words().match(/New round finding/g)).toHaveLength(1);
  expect(log.events).toHaveLength(1);
});

it('a Manager step updates the reply through the role log when the rows frame contains no reply row', async () => {
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn(), requestAnimationFrame: vi.fn(() => 1), cancelAnimationFrame: vi.fn() });
  const { openConversation } = await import('../api');
  const { EventStream } = await import('../components/EventStream');
  const { projectRecord } = await import('../../../../packages/core/src/serve/views');
  const { readFileSync } = await import('node:fs');
  const record = readFileSync(new URL('../../../../packages/core/test/fixtures/conversation-stop/events.jsonl', import.meta.url), 'utf8')
    .trim().split('\n').map(line => JSON.parse(line));
  const reading = projectRecord(record);
  const message = reading.roleEvents.slice().reverse().find(row => row.agent_layer === 'manager' && row.kind === 'agent_message')!;
  const snap = roleSnapshot([], { session: { id: 'b', display_name: '', objective: '', workdir: '', last_active: 1 } });
  client.setQueryData(['conversation', 'b'], { pages: [{ rows: [{ type: 'ui.argus', row_id: 'reply', event_id: 1, order: 1,
    text: 'stale text from a row', live: true, status: 'running' }], before: null, cursor: 1 }], pageParams: [undefined] });
  function Conversation() {
    const history = useConversationHistory('b', true, [], null);
    return <EventStream events={history.events} snapshot={snap} connected showReasoning={false} />;
  }
  act(() => { renderer = create(<QueryClientProvider client={client}><Conversation /></QueryClientProvider>); });
  const receive = vi.mocked(openConversation).mock.calls.at(-1)![2];
  act(() => receive({ type: 'ui.projection', cursor: reading.fold.line - 1, round: reading.round, rows: [], role_events: reading.roleEvents }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const replyText = () => renderer!.root.findByProps({ 'data-manager-reply': 'running' }).findAllByType('span').map(row => row.children.join('')).join('');
  expect(replyText()).toContain(message.text);
  expect(replyText()).not.toContain('stale text from a row');
  act(() => receive({ type: 'ui.projection', cursor: reading.fold.line, round: reading.round, rows: [], role_events: [{ ...message, event_id: reading.fold.line, text: '经理下一句实时记录。' }] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(replyText()).toBe('经理下一句实时记录。');
});
