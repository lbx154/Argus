import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ProjectIndex, type ProjectRow, type ProjectState, type Snapshot } from '../api';
import { applyProjectState, useSnapshot } from '../hooks';
import { Capabilities, POLL_CAPABILITY } from '../lib/capabilities';
import { useConversationHistory } from '../useConversationHistory';

const streams = vi.hoisted(() => ({ projects: 0, open: new Set<string>() }));
vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  openProjectStates: () => {
    streams.projects += 1;
    streams.open.add('projects');
    return () => { streams.open.delete('projects'); };
  },
  openConversation: (sid: string) => {
    streams.open.add(sid);
    return () => { streams.open.delete(sid); };
  },
}));

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client?.clear();
  streams.projects = 0;
  streams.open.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const row = (id: string) => ({ id, label: id, objective: '', last_active: 1 }) as unknown as ProjectRow;
const ids = () => client!.getQueryData<ProjectIndex>(['projects'])?.projects.map(project => project.id);
const read = (read_at: number) => ({ session: { id: 'p-a' }, daemon: {}, read_at }) as Snapshot;
const push = (state: ProjectState) => act(() => applyProjectState(client!, state));

function Snapshots() {
  useSnapshot('p-a');
  return null;
}

function Page({ sid }: { sid: string | null }) {
  useConversationHistory(sid, true, [], null);
  return null;
}

const wrap = (element: JSX.Element, capabilities: string[]) =>
  <QueryClientProvider client={client!}><Capabilities.Provider value={capabilities}>{element}</Capabilities.Provider></QueryClientProvider>;

async function mount(element: JSX.Element, capabilities: string[] = ['manager.sse.v1']) {
  vi.stubGlobal('window', new EventTarget());
  vi.spyOn(api, 'snapshot').mockResolvedValue(read(1));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData<ProjectIndex>(['projects'], { projects: [row('p-a')], local_cwd: '/synthetic' });
  await act(async () => { renderer = create(wrap(element, capabilities)); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
}

it('keeps the project list as the stream sends it, and the snapshot of the project shown', async () => {
  await mount(<Snapshots />);
  const snapshot = { session: { id: 'p-b' } } as Snapshot;
  push({ type: 'project', id: 'p-b', row: row('p-b'), snapshot });
  expect(ids()).toEqual(['p-a', 'p-b']);
  expect(client!.getQueryData(['snapshot', 'p-b'])).toBe(snapshot);
  // Another project's change comes as its row alone: the snapshot held of it is stale, not replaced.
  push({ type: 'project', id: 'p-b', row: row('p-b') });
  expect(client!.getQueryData(['snapshot', 'p-b'])).toBe(snapshot);
  expect(client!.getQueryState(['snapshot', 'p-b'])?.isInvalidated).toBe(true);
  push({ type: 'project', id: 'p-a', row: null, snapshot: null });
  expect(ids()).toEqual(['p-b']);
  // Connected again, the list it sends replaces the one the page had.
  push({ type: 'projects', projects: [row('p-c')], local_cwd: '/synthetic' });
  expect(ids()).toEqual(['p-c']);
});

it('follows one stream: the projects’ while no project is shown, the project’s once one is', async () => {
  vi.spyOn(api, 'conversation').mockResolvedValue({ rows: [], before: null, cursor: 3 });
  await mount(<Page sid={null} />);
  expect([...streams.open]).toEqual(['projects']);
  await act(async () => renderer!.update(wrap(<Page sid="p-a" />, ['manager.sse.v1'])));
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  expect([...streams.open]).toEqual(['p-a']);
});

it('follows no projects stream on a server that is polled', async () => {
  await mount(<Page sid={null} />, ['manager.sse.v1', POLL_CAPABILITY]);
  expect(streams.projects).toBe(0);
});

it('keeps a pushed snapshot over an older read that arrives after it', async () => {
  await mount(<Snapshots />);
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(1));
  let reply: (snapshot: Snapshot) => void = () => {};
  vi.mocked(api.snapshot).mockReturnValueOnce(new Promise(resolve => { reply = resolve; }));
  // Read again: the server reads it, then a change is pushed, then the reply arrives.
  void act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  push({ type: 'project', id: 'p-a', row: row('p-a'), snapshot: read(3) });
  await act(async () => reply(read(2)));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(3));
  // A later read replaces it.
  vi.mocked(api.snapshot).mockResolvedValueOnce(read(4));
  await act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(4));
});

it('reloads a page the server no longer sends, as the stream connects', async () => {
  await mount(<Snapshots />);
  const reload = vi.fn();
  vi.stubGlobal('document', { querySelector: () => ({ src: 'http://127.0.0.1:8798/assets/index-Old1.js' }) });
  vi.stubGlobal('window', Object.assign(new EventTarget(), { location: { reload } }));
  push({ type: 'projects', build: 'assets/index-Old1.js', projects: [row('p-a')], local_cwd: '/synthetic' });
  expect(reload).not.toHaveBeenCalled();
  push({ type: 'projects', build: 'assets/index-New2.js', projects: [row('p-z')], local_cwd: '/synthetic' });
  expect(reload).toHaveBeenCalledOnce();
  expect(ids()).toEqual(['p-a']);
});
