import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { Capabilities } from '../lib/capabilities';
import { ResourceStatusView } from '../components/ResourceStatus';
import { RuntimeControlPage } from '../research-workbench/pages/RuntimeControlPage';
import type { ActiveWorkbenchPageProps } from '../research-workbench/pages/pageTypes';

const snap: ActiveWorkbenchPageProps['snapshot'] = {
  session: { id: 's-test', display_name: 'Test project', cwd: '/fixture', last_active: 1, objective: '' },
  daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
  roles: [], backlog: [], recent_events: [], daemon_commands: { schema_version: 1, revision: 7, recent: [] },
};
let client: QueryClient;
let tree: ReactTestRenderer | undefined;
let props: ActiveWorkbenchPageProps & { readOnly: boolean; onRestored: (sid: string) => void; onCompose: () => void };
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  props = {
    sid: 's-test', snapshot: snap, active: false, connected: true, snapshotUpdatedAt: 1, events: [],
    project: { id: 's-test', label: 'Test project', objective: '', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    refresh: vi.fn(), controls: { start: vi.fn(), stop: vi.fn(), busy: false, error: '' }, navigate: vi.fn(), readOnly: false, onRestored: vi.fn(), onCompose: vi.fn(),
  };
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Unexpected unmocked request'); }));
  vi.stubGlobal('window', { confirm: vi.fn(() => true) });
  vi.spyOn(api, 'resources').mockResolvedValue({ schema_version: 1, enforcement: 'strict', accelerators: [] });
  vi.spyOn(api, 'metrics').mockRejectedValue(new Error('synthetic metrics failure'));
  vi.spyOn(api, 'trash').mockResolvedValue({ entries: [], total: 0 });
  vi.spyOn(api, 'sourceUpdateStatus').mockRejectedValue(new Error('synthetic unavailable source'));
  vi.spyOn(api, 'checkSourceUpdate');
});
afterEach(() => { act(() => tree?.unmount()); tree = undefined; client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const element = () => <QueryClientProvider client={client}><RuntimeControlPage {...props} /></QueryClientProvider>;
const mount = async () => { await act(async () => { tree = create(element()); }); };
const button = (label: string) => tree!.root.findAllByType('button').find(node => node.children.includes(label))!;

describe('embedded runtime control page', () => {
  it('neither shows nor reads the machine\'s resources where the server has none', async () => {
    props.active = true;
    await act(async () => { tree = create(<Capabilities.Provider value={['manager.sse.v1']}>{element()}</Capabilities.Provider>); });
    expect(tree!.root.findAllByType(ResourceStatusView)).toHaveLength(0);
    act(() => button('Refresh').props.onClick());
    expect(api.resources).not.toHaveBeenCalled();
    expect(props.refresh).toHaveBeenCalledOnce();
  });
  it('takes an empty project to its conversation without claiming it was paused or starting work', async () => {
    props.snapshot = { ...snap, session: { ...snap.session, display_name: '' } };
    props.controls.error = 'synthetic command error';
    await mount();
    const html = JSON.stringify(tree!.toJSON());
    expect(html).toContain('Ready for a new task');
    expect(html).not.toContain('Continuous work paused');
    expect(tree!.root.findByType('h2').children).toEqual(['Untitled project']);
    expect(html).toContain('synthetic command error');
    act(() => button('Describe a task').props.onClick());
    expect(props.onCompose).toHaveBeenCalledOnce();
    expect(props.controls.start).not.toHaveBeenCalled();
  });
  it('does not offer to start a second executor during a foreground request', async () => {
    props.snapshot = { ...snap, manager_requests: [{ request_id: 'foreground', status: 'running' }] };
    await mount();
    expect(JSON.stringify(tree!.toJSON())).toContain('Working on your request');
    expect(button('Working…').props.disabled).toBe(true);
    expect(props.controls.start).not.toHaveBeenCalled();
  });
  it('distinguishes queued work from an interrupted active task', async () => {
    const task = { id: 'task-1', title: 'A task', objective: '', status: 'pending', priority: 1 };
    props.snapshot = { ...snap, backlog: [task] };
    await mount();
    expect(button('Start work').props.disabled).toBe(false);
    props.snapshot = { ...snap, backlog: [{ ...task, status: 'in_progress' }] };
    await act(async () => tree!.update(element()));
    expect(JSON.stringify(tree!.toJSON())).toContain('The current task is not running');
    expect(button('Resume').props.disabled).toBe(false);
  });
  it('is a page, not a modal, with no duplicate work composer or skill entry', () => {
    const html = renderToStaticMarkup(element());
    expect(html).toContain('Runtime controls');
    expect(html).not.toContain('role="dialog"');
    expect(html).not.toContain('<textarea');
    expect(html).not.toContain('Open skill library');
  });
  it('only reads GPU data when active, and lazy loads advanced sections without automatic update checks', async () => {
    await mount();
    expect(api.resources).not.toHaveBeenCalled();
    props.active = true;
    await act(async () => tree!.update(element()));
    expect(api.resources).toHaveBeenCalledTimes(1);
    expect(api.metrics).not.toHaveBeenCalled();
    expect(api.trash).not.toHaveBeenCalled();
    expect(api.checkSourceUpdate).not.toHaveBeenCalled();
  });
  it('keeps read-only and external controls disabled', async () => {
    props.readOnly = true;
    await mount();
    expect(button('Describe a task').props.disabled).toBe(true);
    expect(button('Reset conversation context').props.disabled).toBe(true);
    props.readOnly = false;
    props.snapshot = { ...snap, daemon: { ...snap.daemon, alive: true, control_available: false } };
    await act(async () => tree!.update(element()));
    expect(button('Pause safely').props.disabled).toBe(true);
    expect(button('Restart safely').props.disabled).toBe(true);
  });
  it('preserves safe drain when pausing and displays control errors', async () => {
    props.snapshot = { ...snap, daemon: { ...snap.daemon, alive: true } };
    props.controls.error = 'synthetic command failure';
    await mount();
    await act(async () => button('Pause safely').props.onClick());
    expect(props.controls.stop).toHaveBeenCalledWith(true);
    expect(tree!.root.findAllByProps({ role: 'alert' }).some(node => node.children.includes('synthetic command failure'))).toBe(true);
  });
  it('confirms maintenance and preserves revision, exposing rejected commands instead of success', async () => {
    vi.spyOn(api, 'upgradeDaemon').mockResolvedValue({ rc: 1, command_status: 'rejected', error: 'revision conflict' } as Awaited<ReturnType<typeof api.upgradeDaemon>>);
    await mount();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await act(async () => button('Restart safely').props.onClick());
    expect(api.upgradeDaemon).not.toHaveBeenCalled();
    await act(async () => button('Restart safely').props.onClick());
    expect(api.upgradeDaemon).toHaveBeenCalledWith('s-test', 7);
    expect(tree!.root.findAllByProps({ role: 'status' }).some(node => node.children.includes('Safe restart requested.'))).toBe(false);
    expect(tree!.root.findAllByType('pre').some(node => node.children.join('').includes('revision conflict'))).toBe(true);
  });
});
