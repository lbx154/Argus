import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type EventMsg } from '../api';
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
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  rendered = [];
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
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

it('replaces a moved task across loaded pages and retains the server order of question and answer', async () => {
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
    { ...task, state: 'done', event_id: 15, order: 15.2 },
  ] }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(latest.events.map(row => row.row_id)).toEqual(['q1', 'answer', 'task-t1']);
  expect(latest.events.at(-1)?.state).toBe('done');
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
