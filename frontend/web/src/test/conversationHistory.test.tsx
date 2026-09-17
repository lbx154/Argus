import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type EventMsg } from '../api';
import { useConversationHistory } from '../useConversationHistory';

type Turns = Awaited<ReturnType<typeof api.transcript>>;
let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
let latest: ReturnType<typeof useConversationHistory>;
let rendered: { sid: string; text: string }[];
const live: EventMsg[] = [];
const optimistic: EventMsg[] = [{ type: 'ui.operator', text: 'Only session A has this draft.', ts: 3 }];
const turn = (text: string): Turns => [{ role: 'argus', text, ts: 1 }];

function Probe({ sid }: { sid: string }) {
  latest = useConversationHistory(sid, true, live, optimistic, 'a');
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
  const request = vi.spyOn(api, 'transcript').mockReturnValue(new Promise(done => { resolve = done; }));
  act(() => { renderer = create(tree('empty')); });
  expect(latest.status).toBe('loading');
  expect(latest.events).toEqual([]);
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { resolve([]); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('ready');
  expect(latest.events).toEqual([]);
});

it('reports an initial history failure and recovers only from the explicit read retry', async () => {
  const request = vi.spyOn(api, 'transcript').mockRejectedValueOnce(new Error('History unavailable'));
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
  client.setQueryData(['transcript', 'b', 120], turn('Saved session B answer.'));
  vi.spyOn(api, 'transcript').mockRejectedValue(new Error('Refresh unavailable'));
  act(() => { renderer = create(tree('b')); });
  expect(latest.status).toBe('ready');
  await act(async () => { await latest.query.refetch(); await vi.advanceTimersByTimeAsync(10); });
  expect(latest.status).toBe('error');
  expect(latest.events.map(event => event.text)).toEqual(['Saved session B answer.']);
});

it('never lends cached or optimistic history to another session, including a late old response', async () => {
  client.setQueryData(['transcript', 'a', 120], turn('Saved session A answer.'));
  client.setQueryData(['transcript', 'b', 120], turn('Saved session B answer.'));
  let resolve!: (value: Turns) => void;
  vi.spyOn(api, 'transcript').mockReturnValue(new Promise(done => { resolve = done; }));
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
