import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { conversationRows, projectRecord, type Recorded } from '../../../../packages/core/src/serve/views';
import { api, type EventMsg } from '../api';
import type { Project } from '../../../../packages/core/src/store';
import { useArtifacts } from '../hooks';
import { useConversationHistory } from '../useConversationHistory';
import { useRoleLog } from '../useRoleLog';

// Captured real project: the first frame includes saved role steps and a delivered result.
const record = readFileSync(new URL('./fixtures/home/projects/p-c855623c/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
const reading = projectRecord(record);
const savedRows = [...reading.rows.values()];
const cursor = record.length - 1;
type Connection = { path: string; signal: AbortSignal; body: ReadableStreamDefaultController<Uint8Array>; headers: HeadersInit | undefined };
let connections: Connection[];
let client: QueryClient;
let view: ReactTestRenderer | undefined;
let latest: ReturnType<typeof useConversationHistory>;
let log: ReturnType<typeof useRoleLog>;
const reads = new Map<string, ReturnType<typeof vi.fn<() => Promise<{ value: string }>>>>();
function Probe({ sid }: { sid: string }) {
  latest = useConversationHistory(sid, true, [], null);
  log = useRoleLog(sid);
  useArtifacts(sid);
  for (const key of ['workspace-tree', 'workspace-git', 'workspace-file', 'runtime-resources']) {
    const queryKey = key === 'runtime-resources' ? [key] : [key, sid, 'project', 'result.md'];
    useQuery({ queryKey, queryFn: reads.get(`${key}:${key === 'runtime-resources' ? '' : sid}`)! });
  }
  return null;
}
const tree = (sid: string) => <QueryClientProvider client={client}><Probe sid={sid} /></QueryClientProvider>;
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(10); });
function send(connection: Connection, value: EventMsg) {
  connection.body.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`));
}
const frame = (n: number, rows: EventMsg[] = [], role_events: EventMsg[] = []): EventMsg => ({
  type: 'ui.projection', event_id: n, cursor: n, round: 0, rows, role_events,
});
const changed = (event_id: number): EventMsg => ({ type: 'engineer.progress', agent_layer: 'engineer', kind: 'file_change', event_id, text: 'result.md' });
function count(sid: string) {
  return ['workspace-tree', 'workspace-git', 'workspace-file', 'runtime-resources'].map(key => reads.get(`${key}:${key === 'runtime-resources' ? '' : sid}`)!.mock.calls.length);
}
beforeEach(() => {
  vi.useFakeTimers();
  connections = []; reads.clear();
  for (const sid of ['a', 'b']) for (const key of ['workspace-tree', 'workspace-git', 'workspace-file', 'runtime-resources']) {
    reads.set(`${key}:${key === 'runtime-resources' ? '' : sid}`, vi.fn(async () => ({ value: 'current' })));
  }
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  vi.stubGlobal('window', { location: { search: '' }, addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.spyOn(api, 'conversation').mockResolvedValue({ rows: savedRows, cursor, before: null });
  vi.spyOn(api, 'artifacts').mockResolvedValue([]);
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit) => new Response(new ReadableStream<Uint8Array>({
    start(body) {
      connections.push({ path, signal: init.signal as AbortSignal, headers: init.headers, body });
      init.signal!.addEventListener('abort', () => body.error(new DOMException('Aborted', 'AbortError')), { once: true });
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })));
});
afterEach(() => {
  act(() => view?.unmount()); view = undefined; client.clear();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});
it('opens one rows connection, replays saved logs and deliveries without re-reading artifacts, then refreshes on a file step and delivery', async () => {
  act(() => { view = create(tree('a')); }); await flush();
  expect(connections.map(connection => connection.path)).toEqual([`/api/projects/a/stream?view=rows&after=${cursor}`]);
  expect(latest.connected).toBe(true);
  expect(savedRows.some(row => row.delivery)).toBe(true);
  send(connections[0], frame(cursor, [], reading.roleEvents)); await flush();
  expect(log.events.length).toBeGreaterThan(0);
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(count('a')).toEqual([1, 1, 1, 1]);
  send(connections[0], frame(cursor + 1, [], [changed(cursor + 1)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  expect(count('a')).toEqual([2, 2, 2, 2]);
  send(connections[0], frame(cursor + 2, [{ ...savedRows.find(row => row.delivery)!, event_id: cursor + 2 }])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(3);
  expect(count('a')).toEqual([3, 3, 3, 3]);
  // Idle stopped projects do not schedule the former 5/8/10-second reads.
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(api.artifacts).toHaveBeenCalledTimes(3);
  expect(count('a')).toEqual([3, 3, 3, 3]);
});
it('updates task progress from a captured message without re-reading files', async () => {
  const project = JSON.parse(readFileSync(new URL('./fixtures/home/projects/p-c855623c/project.json', import.meta.url), 'utf8')) as Project;
  const step = record.find(event => event.type === 'step' && event.role === 'engineer' && event.task === 't1' && event.kind === 'message')!;
  const next = projectRecord([...record, step]);
  const task = conversationRows(next, project, { since: null, calls: [], requests: [] }).find(row => row.type === 'ui.task' && row.item_id === 't1')!;
  expect(task.progress).toBeTruthy();
  expect(task.event_id).toBe(cursor + 1);
  act(() => { view = create(tree('a')); }); await flush();
  send(connections[0], frame(cursor, [], reading.roleEvents)); await flush();
  send(connections[0], frame(cursor + 1, [task], next.roleEvents.filter(event => Number(event.event_id) > cursor))); await flush();
  expect(latest.events.find(row => row.row_id === task.row_id)?.progress).toBe(task.progress);
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(count('a')).toEqual([1, 1, 1, 2]);
});
it('counts a new event between REST and the first frame, and resumes a reconnect without treating old role logs as new', async () => {
  act(() => { view = create(tree('a')); }); await flush();
  send(connections[0], frame(cursor + 1, [], [...reading.roleEvents, changed(cursor + 1)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  connections[0].body.close(); await flush();
  expect(latest.connected).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(latest.connected).toBe(true);
  expect(connections[1].headers).toEqual({ 'Last-Event-ID': String(cursor + 1) });
  send(connections[1], frame(cursor + 1, [], [...reading.roleEvents, changed(cursor + 1)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  send(connections[1], frame(cursor + 2, [], [changed(cursor + 2)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(3);
});
it('isolates switched projects and uses the held cursor on a revisit', async () => {
  act(() => { view = create(tree('a')); }); await flush();
  send(connections[0], frame(cursor + 1, [], [changed(cursor + 1)])); await flush();
  act(() => view!.update(tree('b')));
  expect(latest.connected).toBe(false);
  expect(log.events).toEqual([]);
  await flush();
  expect(connections[0].signal.aborted).toBe(true);
  expect(connections[1].path).toContain('/projects/b/');
  send(connections[1], frame(cursor, [], reading.roleEvents)); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(3); // a initially, a new event, b initially
  expect(count('b')).toEqual([1, 1, 1, 2]);
  act(() => view!.update(tree('a'))); await flush();
  expect(connections[1].signal.aborted).toBe(true);
  expect(connections[2].path).toBe(`/api/projects/a/stream?view=rows&after=${cursor + 1}`);
  send(connections[2], frame(cursor + 1, [], [...reading.roleEvents, changed(cursor + 1)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(3);
  expect(count('a')).toEqual([2, 2, 2, 2]);
  send(connections[2], frame(cursor + 2, [], [changed(cursor + 2)])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(4);
  expect(count('a')).toEqual([3, 3, 3, 3]);
});
it('leaves artifact and workspace reads alone for a message step while refreshing resources on new project activity', async () => {
  act(() => { view = create(tree('a')); }); await flush();
  send(connections[0], frame(cursor)); await flush();
  send(connections[0], frame(cursor + 1, [], [{ ...changed(cursor + 1), kind: 'agent_message' }])); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(count('a')).toEqual([1, 1, 1, 2]);
});

it('waits for the opening refresh of stale cached history before following its cursor', async () => {
  client.setQueryDefaults(['conversation'], { staleTime: 0 });
  client.setQueryData(['conversation', 'a'], { pages: [{ rows: [], cursor: -1, before: null }], pageParams: [undefined] });
  let finish!: (page: Awaited<ReturnType<typeof api.conversation>>) => void;
  vi.mocked(api.conversation).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  act(() => { view = create(tree('a')); }); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(connections).toHaveLength(0);
  await act(async () => { finish({ rows: savedRows, cursor, before: null }); await vi.advanceTimersByTimeAsync(10); });
  expect(connections.map(connection => connection.path)).toEqual([`/api/projects/a/stream?view=rows&after=${cursor}`]);
  send(connections[0], frame(cursor, [], reading.roleEvents)); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(count('a')).toEqual([1, 1, 1, 1]);
});

it('refreshes missed file changes after reconnecting beyond the role log window while the call is still running', async () => {
  // Captured through the built server: one edit, then 35 messages of the same role,
  // with the call still running. Its bounded replay contains only the last 30 messages.
  const gap = readFileSync(new URL('./fixtures/reconnect-role-window.events.jsonl', import.meta.url), 'utf8')
    .trim().split('\n').map(line => JSON.parse(line) as Recorded);
  expect(gap.filter(event => event.type === 'step' && event.kind === 'edit')).toHaveLength(1);
  expect(gap.filter(event => event.type === 'step' && event.kind === 'message')).toHaveLength(35);
  expect(gap.filter(event => event.type === 'call.end')).toHaveLength(0);
  const resumed = projectRecord(gap);
  expect(resumed.roleEvents).toHaveLength(30);
  expect(resumed.roleEvents.every(event => event.kind === 'agent_message')).toBe(true);
  vi.mocked(api.conversation).mockResolvedValue({ rows: [], cursor: -1, before: null });
  act(() => { view = create(tree('a')); }); await flush();
  send(connections[0], frame(-1)); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(1);
  expect(count('a')).toEqual([1, 1, 1, 1]);
  connections[0].body.close(); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(connections[1].headers).toEqual({ 'Last-Event-ID': '-1' });
  const catchup = frame(gap.length - 1, [], resumed.roleEvents);
  send(connections[1], catchup); await flush();
  expect(latest.connected).toBe(true);
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  expect(count('a')).toEqual([2, 2, 2, 2]);
  send(connections[1], catchup); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  expect(count('a')).toEqual([2, 2, 2, 2]);
  connections[1].body.close(); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  send(connections[2], catchup); await flush();
  expect(api.artifacts).toHaveBeenCalledTimes(2);
  expect(count('a')).toEqual([2, 2, 2, 2]);
});
