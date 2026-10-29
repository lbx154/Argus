import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ProjectIndex, type ProjectRow, type ProjectState, type Snapshot } from '../api';
import { useProjectCosts, useProjects, useSnapshot } from '../hooks';
import { Capabilities, POLL_CAPABILITY } from '../lib/capabilities';

const stream = vi.hoisted(() => ({ opened: 0, push: undefined as undefined | ((state: ProjectState) => void) }));
vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  openProjectStates: (onState: (state: ProjectState) => void) => {
    stream.opened += 1;
    stream.push = onState;
    return () => { stream.push = undefined; };
  },
}));

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); stream.opened = 0; vi.restoreAllMocks(); });

const row = (id: string) => ({ id, label: id, objective: '', last_active: 1 }) as unknown as ProjectRow;
const ids = () => client!.getQueryData<ProjectIndex>(['projects'])?.projects.map(project => project.id);

function Page() {
  useProjects();
  useProjectCosts();
  useSnapshot('p-a');
  return null;
}

const read = (read_at: number) => ({ session: { id: 'p-a' }, daemon: {}, read_at }) as Snapshot;

async function mount(capabilities: string[]) {
  vi.spyOn(api, 'snapshot').mockResolvedValue(read(1));
  vi.spyOn(api, 'projectIndex').mockResolvedValue({ projects: [row('p-a')], local_cwd: '/synthetic' });
  const costs = vi.spyOn(api, 'projectCosts').mockResolvedValue({ projects: [], generated_at: 0 });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    renderer = create(<QueryClientProvider client={client!}><Capabilities.Provider value={capabilities}><Page /></Capabilities.Provider></QueryClientProvider>);
  });
  // The list arrives, and the page renders with it.
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  return costs;
}

it('keeps the project list and each snapshot as a server that pushes them sends them, and asks for no costs', async () => {
  const costs = await mount(['manager.sse.v1']);
  expect(stream.opened).toBe(1);
  expect(costs).not.toHaveBeenCalled();
  const snapshot = { session: { id: 'p-b' } } as Snapshot;
  act(() => stream.push!({ type: 'project', id: 'p-b', row: row('p-b'), snapshot }));
  expect(ids()).toEqual(['p-a', 'p-b']);
  expect(client!.getQueryData(['snapshot', 'p-b'])).toBe(snapshot);
  act(() => stream.push!({ type: 'project', id: 'p-a', row: null, snapshot: null }));
  expect(ids()).toEqual(['p-b']);
  // Connected again, the list it sends replaces the one the page had.
  act(() => stream.push!({ type: 'projects', projects: [row('p-c')], local_cwd: '/synthetic' }));
  expect(ids()).toEqual(['p-c']);
});

it('polls a server that declares it, without following its state', async () => {
  const costs = await mount(['manager.sse.v1', POLL_CAPABILITY]);
  expect(stream.opened).toBe(0);
  expect(costs).toHaveBeenCalledTimes(1);
  expect(ids()).toEqual(['p-a']);
});

it('keeps a pushed snapshot over an older read that arrives after it', async () => {
  await mount(['manager.sse.v1']);
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(1));
  let reply: (snapshot: Snapshot) => void = () => {};
  vi.mocked(api.snapshot).mockReturnValueOnce(new Promise(resolve => { reply = resolve; }));
  // Read again: the server reads it, then a change is pushed, then the reply arrives.
  void act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  act(() => stream.push!({ type: 'project', id: 'p-a', row: row('p-a'), snapshot: read(3) }));
  await act(async () => reply(read(2)));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(3));
  // A later read replaces it.
  vi.mocked(api.snapshot).mockResolvedValueOnce(read(4));
  await act(() => client!.invalidateQueries({ queryKey: ['snapshot', 'p-a'] }));
  expect(client!.getQueryData(['snapshot', 'p-a'])).toEqual(read(4));
});
