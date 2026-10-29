import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { STREAM_RENDER_BATCH_MS, useEventStream } from '../hooks';
import type { EventMsg } from '../api';

type Connection = { path: string; signal: AbortSignal; body: ReadableStreamDefaultController<Uint8Array>; headers: HeadersInit | undefined };
let connections: Connection[];
let renderer: ReactTestRenderer | undefined;
let latest: ReturnType<typeof useEventStream>;
let fetchMock: ReturnType<typeof vi.fn>;
let client: QueryClient;

function Probe({ sid }: { sid: string | null }) {
  latest = useEventStream(sid);
  return <div>{latest.events.map(event => event.text).join('|')}</div>;
}
const tree = (sid: string | null) => <QueryClientProvider client={client}><Probe sid={sid} /></QueryClientProvider>;
const send = (connection: Connection, ...events: EventMsg[]) => connection.body.enqueue(
  new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')),
);
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(STREAM_RENDER_BATCH_MS); });

beforeEach(() => {
  vi.useFakeTimers();
  connections = [];
  client = new QueryClient();
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn(), setTimeout, clearTimeout, location: { search: '' } });
  vi.stubGlobal('localStorage', { getItem: () => null });
  fetchMock = vi.fn(async (path: string, init: RequestInit) => {
    // The old REST preloader would be visible here as an extra request.
    if (path.includes('/events')) return Response.json({ events: [] });
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      connections.push({ path, signal: init.signal as AbortSignal, body: controller, headers: init.headers });
      init.signal!.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')), { once: true });
    } });
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('opens only the replay stream and displays its first frames followed by live appends', async () => {
  await act(async () => { renderer = create(tree('a')); });
  expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/projects/a/stream?replay=40&view=ui']);
  expect(latest.connected).toBe(true);
  send(connections[0], { type: 'engineer.progress', event_id: 1, text: 'Recorded progress' });
  await flush();
  expect(renderer!.toJSON()).toMatchObject({ children: ['Recorded progress'] });
  send(connections[0], { type: 'engineer.progress', event_id: 2, text: 'Live progress' });
  await flush();
  expect(renderer!.toJSON()).toMatchObject({ children: ['Recorded progress|Live progress'] });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('discards queued old-project events and establishes each switched or revisited window from replay', async () => {
  await act(async () => { renderer = create(tree('a')); });
  const first = connections[0];
  send(first, { type: 'ui.argus', event_id: 1, text: 'A before switch' });
  await flush();
  await act(async () => {
    send(first, { type: 'ui.argus', event_id: 2, text: 'A queued' });
  });
  await act(async () => { renderer!.update(tree('b')); });
  expect(first.signal.aborted).toBe(true);
  expect(latest.events).toEqual([]);
  send(connections[1], { type: 'ui.argus', event_id: 1, text: 'B replay' });
  await flush();
  expect(renderer!.toJSON()).toMatchObject({ children: ['B replay'] });
  await act(async () => { renderer!.update(tree('a')); });
  expect(latest.events).toEqual([]);
  send(connections[2], { type: 'ui.argus', event_id: 3, text: 'A fresh replay' });
  await flush();
  expect(renderer!.toJSON()).toMatchObject({ children: ['A fresh replay'] });
  await act(async () => { renderer!.update(tree(null)); });
  expect(latest.events).toEqual([]);
  expect(latest.connected).toBe(false);
  expect(connections[2].signal.aborted).toBe(true);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it('keeps the replay window across reconnects and resumes after the last event without duplicates', async () => {
  await act(async () => { renderer = create(tree('a')); });
  send(connections[0], { type: 'ui.argus', event_id: 8, text: 'Replay' });
  await flush();
  await act(async () => { connections[0].body.close(); });
  expect(latest.connected).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(connections[1].headers).toMatchObject({ 'Last-Event-ID': '8' });
  send(connections[1], { type: 'ui.argus', event_id: 8, text: 'Replay' }, { type: 'ui.argus', event_id: 9, text: 'Resumed' });
  await flush();
  expect(renderer!.toJSON()).toMatchObject({ children: ['Replay|Resumed'] });
  expect(latest.connected).toBe(true);
  expect(fetchMock.mock.calls.every(([path]) => path.includes('/stream?'))).toBe(true);
});
