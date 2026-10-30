import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ProjectIndex, type ProjectRow } from '../api';
import { useProjectActions, useSnapshot } from '../hooks';
import { Sidebar } from '../components/Sidebar';

let view: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => view?.unmount()); client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const row = { id: 'p-deleted', display_name: 'Saved project', label: 'Saved project', last_active: 1, daemon_alive: false, daemon_pid: null, workdir: '/fixture' } satisfies ProjectRow;

it('deleting a selected project stops snapshot and attachment reads before clearing its cache', async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData<ProjectIndex>(['projects'], { projects: [row], local_cwd: '/fixture' });
  const snapshot = vi.spyOn(api, 'snapshot').mockResolvedValue({ session: { id: row.id }, daemon: {} } as never);
  const attachments = vi.spyOn(api, 'attachmentLimits').mockResolvedValue({} as never);
  vi.spyOn(api, 'deleteProject').mockResolvedValue({ ok: true, sid: row.id } as never);
  let remove!: () => Promise<unknown>;
  function Page() {
    const projects = useQuery<ProjectIndex>({ queryKey: ['projects'], staleTime: Infinity, queryFn: async () => ({ projects: [], local_cwd: '/fixture' }) });
    const sid = projects.data?.projects.some(project => project.id === row.id) ? row.id : null;
    useSnapshot(sid);
    useQuery({ queryKey: ['attachment-limits', sid], queryFn: ({ signal }) => api.attachmentLimits(sid!, signal), enabled: !!sid });
    remove = useProjectActions(row.id).deleteProject.mutateAsync;
    return null;
  }
  await act(async () => { view = create(<QueryClientProvider client={client!}><Page /></QueryClientProvider>); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  expect(snapshot).toHaveBeenCalledOnce();
  expect(attachments).toHaveBeenCalledOnce();
  snapshot.mockClear(); attachments.mockClear();
  await act(async () => { await remove(); });
  await act(() => new Promise(resolve => setTimeout(resolve, 10)));
  expect(snapshot).not.toHaveBeenCalled();
  expect(attachments).not.toHaveBeenCalled();
});

it('reads organization once as the initial project list arrives, then preserves focus refresh and edits', async () => {
  const focus: { handler?: () => void } = {};
  vi.stubGlobal('window', { addEventListener: (event: string, handler: () => void) => { if (event === 'focus') focus.handler = handler; }, removeEventListener: vi.fn() });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
  const read = vi.spyOn(api, 'organization').mockResolvedValue({ value: { [row.id]: { pinned: true, archived: false, followup: false } }, revision: 1 });
  const organize = vi.spyOn(api, 'organize').mockResolvedValue({ value: {}, revision: 2 });
  client = new QueryClient();
  const props = { activeId: row.id, localCwd: '/fixture', onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(), onNew: vi.fn(), onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn() };
  const render = (loading: boolean, projects: ProjectRow[]) => <QueryClientProvider client={client!}><Sidebar {...props} loading={loading} projects={projects} /></QueryClientProvider>;
  await act(async () => { view = create(render(true, [])); });
  expect(read).not.toHaveBeenCalled();
  await act(async () => view!.update(render(false, [row])));
  await act(async () => view!.update(render(false, [row, { ...row, id: 'p-next' }])));
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => focus.handler!());
  expect(read).toHaveBeenCalledTimes(2);
  act(() => view!.root.findAllByType('button').find(node => node.props['aria-label'] === 'Manage Saved project')!.props.onClick({ stopPropagation: vi.fn() }));
  const pin = view!.root.findAllByType('button').find(node => node.children.includes('Unpin'));
  expect(pin).toBeDefined();
  await act(async () => { await pin!.props.onClick({ stopPropagation: vi.fn() }); });
  expect(organize).toHaveBeenCalledWith(row.id, 'pinned', false);
});
