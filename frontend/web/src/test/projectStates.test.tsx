import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ProjectIndex, type ProjectRow, type ProjectState, type Snapshot } from '../api';
import { useProjects, useSnapshot } from '../hooks';
import { useConversationHistory } from '../useConversationHistory';
import { Capabilities, POLL_CAPABILITY } from '../lib/capabilities';
import { SNAPSHOT_SCHEMA_VERSION } from '../../../core/src/protocol';

const stream = vi.hoisted(() => ({ active: 0, paths: [] as (string | null)[], push: undefined as undefined | ((state: ProjectState) => void) }));
vi.mock('../api', async importOriginal => {
  const open = (sid: string | null, onState: (state: ProjectState) => void) => {
    stream.active += 1;
    stream.paths.push(sid);
    stream.push = onState;
    return () => { stream.active -= 1; stream.push = undefined; };
  };
  return {
    ...await importOriginal<typeof import('../api')>(),
    openProjectStates: (onState: (state: ProjectState) => void) => open(null, onState),
    openConversation: (sid: string, _after: number, _onFrame: unknown, opts: { onState: (state: ProjectState) => void }) => open(sid, opts.onState),
  };
});

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => {
  act(() => renderer?.unmount()); renderer = undefined; client?.clear();
  expect(stream.active).toBe(0); stream.paths = []; vi.restoreAllMocks(); vi.unstubAllGlobals();
});

const row = (id: string) => ({ id, label: id, objective: '', last_active: 1 }) as unknown as ProjectRow;
const ids = () => client!.getQueryData<ProjectIndex>(['projects'])?.projects.map(project => project.id);
function Page({ sid }: { sid: string | null }) {
  useProjects();
  useSnapshot(sid);
  useConversationHistory(sid, true, [], null);
  return null;
}
const read = (read_at: number) => ({ schema_version: SNAPSHOT_SCHEMA_VERSION, session: { id: 'p-a', display_name: '', objective: '', last_active: 1, workdir: '' }, daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
  spend_usd: 0, spend_status: 'empty', usage_summary: {}, roles: [], backlog: [], read_at }) as unknown as Snapshot;
const tree = (sid: string | null, capabilities = ['manager.sse.v1']) =>
  <QueryClientProvider client={client!}><Capabilities.Provider value={capabilities}><Page sid={sid} /></Capabilities.Provider></QueryClientProvider>;
async function mount(sid: string | null, capabilities?: string[]) {
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.spyOn(api, 'snapshot').mockResolvedValue(read(1));
  vi.spyOn(api, 'conversation').mockResolvedValue({ rows: [], cursor: -1, before: null });
  vi.spyOn(api, 'projectIndex').mockResolvedValue({ projects: [row('p-a')], local_cwd: '/synthetic' });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => { renderer = create(tree(sid, capabilities)); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
}

it('follows list rows at home, then replaces that stream when selecting, switching and leaving a project', async () => {
  await mount(null);
  expect(stream.paths).toEqual([null]);
  act(() => stream.push!({ type: 'project', id: 'p-b', row: row('p-b') }));
  expect(ids()).toEqual(['p-a', 'p-b']);
  expect(client!.getQueryData(['snapshot', 'p-b'])).toBeUndefined();
  await act(async () => { renderer!.update(tree('p-a')); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  expect(stream.paths).toEqual([null, 'p-a']);
  expect(stream.active).toBe(1);
  act(() => stream.push!({ type: 'snapshot', id: 'p-a', snapshot: read(3) }));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(3));
  act(() => stream.push!({ type: 'project', id: 'p-a', row: null }));
  expect(ids()).toEqual(['p-b']);
  await act(async () => { renderer!.update(tree('p-b')); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  expect(stream.paths).toEqual([null, 'p-a', 'p-b']);
  expect(stream.active).toBe(1);
  act(() => renderer!.update(tree(null)));
  expect(stream.paths).toEqual([null, 'p-a', 'p-b', null]);
  expect(stream.active).toBe(1);
  // Reconnection replaces the list without re-reading snapshots.
  const reads = vi.mocked(api.snapshot).mock.calls.length;
  act(() => stream.push!({ type: 'projects', projects: [row('p-c')], local_cwd: '/synthetic' }));
  expect(ids()).toEqual(['p-c']);
  expect(api.snapshot).toHaveBeenCalledTimes(reads);
});

it('polls a server that declares it, without following its home state', async () => {
  await mount(null, ['manager.sse.v1', POLL_CAPABILITY]);
  expect(stream.paths).toEqual([]);
  expect(ids()).toEqual(['p-a']);
});

it('keeps a pushed snapshot over an older read that arrives after it', async () => {
  await mount('p-a');
  expect(stream.paths).toEqual(['p-a']);
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(1));
  let reply: (snapshot: Snapshot) => void = () => {};
  vi.mocked(api.snapshot).mockReturnValueOnce(new Promise(resolve => { reply = resolve; }));
  void act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  act(() => stream.push!({ type: 'snapshot', id: 'p-a', snapshot: read(3) }));
  await act(async () => reply(read(2)));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(3));
  vi.mocked(api.snapshot).mockResolvedValueOnce(read(4));
  await act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(4));
});
