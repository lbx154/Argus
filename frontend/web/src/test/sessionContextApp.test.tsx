import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import type { DeliveryReceipt, EventMsg, Snapshot } from '../../../core/src/types';
import App from '../App';
import { api } from '../api';
import { ApiError } from '../../../core/src/http';
import { ChatBox } from '../components/ChatBox';
import { ComposerRuntime } from '../components/ComposerRuntime';
import { Sidebar } from '../components/Sidebar';
import { TopBar } from '../components/TopBar';
import { Landing } from '../components/Landing';
import { ArtifactModal } from '../components/ArtifactModal';
import { ResearchCanvas } from '../components/ResearchCanvas';
import { MapPanel } from '../map/MapPanel';
import { DaemonManageModal } from '../components/DaemonManageModal';
import { ActionNotice } from '../components/ActionNotice';
import { EventStream } from '../components/EventStream';
import { Capabilities } from '../lib/capabilities';
import pendingAnswer from '../../../../packages/core/test/fixtures/pending-answer/record.json';
import { mergeConversationEvents } from '../lib/conversationEvents';

const fixture = vi.hoisted(() => {
  const idle = { data: undefined as unknown, isPending: false, isLoading: false, isError: false, isSuccess: true, isFetching: false, isFetchedAfterMount: true, refetch: vi.fn(async () => ({})) };
  const mutation = { isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() };
  return {
    idle,
    index: { ...idle, data: { projects: [] as unknown[], local_cwd: '/synthetic' } },
    snapshots: new Map<string, typeof idle & { data?: unknown; error?: Error }>(),
    actions: Object.fromEntries(['startDaemon', 'stopDaemon', 'updateProject', 'deleteProject'].map(key => [key, mutation])),
    turns: [] as unknown[],
    events: [],
    artifacts: [],
  };
});
vi.mock('../hooks', async importOriginal => ({
  ...await importOriginal<typeof import('../hooks')>(),
  useProjects: () => fixture.index,
  useSnapshot: (sid: string) => fixture.snapshots.get(sid) || fixture.idle,
  useArtifacts: () => ({ ...fixture.idle, data: fixture.artifacts }),
  useProjectActions: () => fixture.actions,
}));
vi.mock('../useConversationHistory', () => ({ useConversationHistory: (sid: string, _enabled: boolean, optimistic: EventMsg[], optimisticSid: string) => ({
  query: { ...fixture.idle, data: fixture.turns, hasNextPage: false, isFetchedAfterMount: true },
  events: mergeConversationEvents(fixture.turns as EventMsg[], optimisticSid === sid ? optimistic : []), connected: true, status: 'ready',
}) }));
// Keep App's actual selection, composer controller, command dispatch and delivery
// controller. Heavy presentation children are irrelevant to these lifetime races;
// the browser fixture separately exercises their real DOM and artifact HTTP path.
vi.mock('../components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../components/ArtifactModal', () => ({ ArtifactModal: () => null }));
vi.mock('../components/EventStream', async importOriginal => ({ ...await importOriginal<object>(), EventStream: () => null }));
vi.mock('../components/ResearchCanvas', async importOriginal => ({ ...await importOriginal<object>(), ResearchCanvas: () => null }));
vi.mock('../components/TopBar', () => ({ TopBar: () => null }));
vi.mock('../components/CommandPalette', async importOriginal => ({ ...await importOriginal<object>(), CommandPalette: () => null }));
vi.mock('../components/KeybindingHelp', () => ({ KeybindingHelp: () => null }));
vi.mock('../components/InfoModals', () => ({ ConfigModal: () => null, TranscriptModal: () => null }));
vi.mock('../components/Modal', () => ({ Modal: () => null, ModalHeader: () => null }));
vi.mock('../components/ProjectInspectorModal', () => ({ ProjectInspectorModal: () => null }));
vi.mock('../components/TaskDetailModal', () => ({ TaskDetailModal: () => null }));
vi.mock('../components/MobileTabBar', () => ({ MobileTabBar: () => null }));
vi.mock('../components/ConnectionProblemBanner', () => ({ ConnectionProblemBanner: () => null }));
vi.mock('../useVisualViewport', () => ({ useVisualViewport: () => false }));
vi.mock('../useGlobalKeyboardShortcuts', () => ({ useGlobalKeyboardShortcuts: () => undefined }));
vi.mock('../useWorkbenchTheme', () => ({ useWorkbenchTheme: () => ({ themeMode: 'light', cycleTheme: () => undefined }) }));
vi.mock('../map/MapPanel', () => ({ MapPanel: () => null }));

let renderer: ReturnType<typeof create>;
let client: QueryClient;
let parent: { postMessage: ReturnType<typeof vi.fn> };
function snapshot(sid: string): Snapshot {
  return { session: { id: sid, display_name: sid, objective: '', last_active: 1, workdir: `/synthetic/${sid}` },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null}, roles: [], backlog: [], };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const props = () => renderer.root.findByType(ChatBox).props as ComponentProps<typeof ChatBox>;
const select = (sid: string) => act(() => renderer.root.findByType(Sidebar).props.onSelect(sid));
const type = (text: string) => act(() => props().onChange(text));
const mountedApp = () => <QueryClientProvider client={client}><App /></QueryClientProvider>;
const refresh = () => act(() => renderer.update(mountedApp()));
const openPaths = () => [
  ...renderer.root.findAllByType(ArtifactModal).filter(node => node.props.path).map(node => ({ sid: node.props.sid, path: node.props.path })),
  ...renderer.root.findAllByType(ResearchCanvas).filter(node => node.props.requestedPath).map(node => ({ sid: node.props.sid, path: node.props.requestedPath })),
];
const toast = async (sessionId?: string, deliveryId = 'completion:s-A:task-A', path: string | undefined = 'results/report.md') => {
  await act(async () => {
    window.dispatchEvent(Object.assign(new Event('message'), { source: parent, origin: 'http://tauri.localhost', data: { type: 'argus:open-delivery', payload: { deliveryId, title: 'Synthetic result', summary: 'Fixture only', ...(sessionId === undefined ? {} : { sessionId }), ...(path === undefined ? {} : { path }) } } }));
  });
};

beforeEach(async () => {
  fixture.turns = [];
  fixture.artifacts = [];
  fixture.index.isError = false;
  fixture.index.isSuccess = true;
  fixture.index.isFetchedAfterMount = true;
  fixture.idle.refetch.mockReset().mockResolvedValue({});
  for (const mutation of Object.values(fixture.actions)) mutation.mutateAsync.mockReset();
  fixture.snapshots.clear();
  fixture.index.data.projects = ['s-A', 's-B'].map(id => ({ workdir: `/synthetic/${id}`, id, label: id, last_active: 1, daemon_alive: false, daemon_pid: null, }));
  for (const sid of ['s-A', 's-B']) fixture.snapshots.set(sid, { ...fixture.idle, data: snapshot(sid) });
  const stored = new Map<string, string>();
  const storage = { getItem: (key: string) => stored.get(key) ?? null, setItem: vi.fn((key: string, value: string) => stored.set(key, value)), removeItem: (key: string) => stored.delete(key) };
  parent = { postMessage: vi.fn() };
  const windowMock = Object.assign(new EventTarget(), { parent, location: new URL('http://127.0.0.1:18799/?project=s-A&view=activity'), sessionStorage: storage,
    innerWidth: 1280, setTimeout, clearTimeout, requestAnimationFrame: vi.fn(), cancelAnimationFrame: vi.fn(),
    history: { pushState: (_: unknown, _title: string, url: string) => { windowMock.location = new URL(url); }, replaceState: (_: unknown, _title: string, url: string) => { windowMock.location = new URL(url); } } });
  vi.stubGlobal('window', windowMock);
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('document', Object.assign(new EventTarget(), { referrer: 'http://tauri.localhost/', body: { style: {} } }));
  // Fail closed: no unmocked test request can reach a user backend or provider.
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Unexpected request outside the synthetic fixture'); }));
  vi.spyOn(api, 'conversation').mockResolvedValue({ rows: [], before: null, cursor: -1 });
  vi.spyOn(api, 'projectIndex').mockImplementation(async () => fixture.index.data as Awaited<ReturnType<typeof api.projectIndex>>);
  vi.spyOn(api, 'artifact').mockImplementation(async (sid, path) => ({ path, name: 'report.md', kind: 'markdown', mime: 'text/markdown', size: 1, mtime: 1, preview: `${sid} ONLY` }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await act(async () => { renderer = create(mountedApp()); });
});
afterEach(() => {
  act(() => renderer?.unmount());
  client?.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('App session composer ownership', () => {
  it('opens each project in the conversation and preserves a later map choice', () => {
    const view = () => renderer.root.findAll(node => node.props.className === 'workspace-tab')
      .find(node => node.props['aria-current'] === 'page')!.children.join('');
    const map = () => act(() => renderer.root.findAll(node => node.props.className === 'workspace-tab')[1].props.onClick());
    const cached = snapshot('s-A');
    const asked = { ...cached, pending_questions: [{ id: pendingAnswer.task.id, operator_decision: {
      id: `engineer-${pendingAnswer.task.id}`, item_id: pendingAnswer.task.id,
      status: 'pending', question: pendingAnswer.question, options: [],
    } }] };
    map();
    select('s-B');
    fixture.snapshots.set('s-A', { ...fixture.idle, data: cached, isFetching: true, isFetchedAfterMount: false });
    select('s-A');
    expect(view()).toBe('Conversation');
    fixture.snapshots.set('s-A', { ...fixture.idle, data: asked });
    refresh();
    expect(view()).toBe('Conversation');
    expect(props().answeringRole).toBe('engineer');
    map();
    fixture.snapshots.set('s-A', { ...fixture.idle, data: { ...asked, read_at: 2 } });
    refresh();
    expect(view()).toBe('Map');
    fixture.snapshots.set('s-B', { ...fixture.idle, data: snapshot('s-B'), isFetching: true, isFetchedAfterMount: false });
    select('s-B');
    select('s-A');
    expect(view()).toBe('Conversation');
  });

  it('recovers when another device deletes the selected project after initial resolution', () => {
    type('unsent in A');
    fixture.index.data.projects = fixture.index.data.projects.filter(row => (row as { id: string }).id !== 's-A');
    refresh();
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
    expect(props().value).toBe('');
    expect(renderer.root.findByType(ActionNotice).props.notice.message).toContain('Switched to s-B');
    expect(localStorage.getItem('argus.composer.draft.v1:s-A')).toContain('unsent in A');
    fixture.index.data.projects = [];
    refresh();
    expect(renderer.root.findByType(Landing)).toBeDefined();
    expect(new URL(window.location.href).searchParams.get('project')).toBeNull();
  });

  it('shows a rejected management action inside its open dialog, with no background duplicate', async () => {
    const update = fixture.actions.updateProject as { mutateAsync: ReturnType<typeof vi.fn> };
    update.mutateAsync.mockRejectedValueOnce(new Error('Failed to fetch'));
    act(() => renderer.root.findByType(TopBar).props.onManage());
    await act(async () => { expect(await renderer.root.findByType(DaemonManageModal).props.onRename('offline name')).toBe(false); });
    const feedback = renderer.root.findByType(DaemonManageModal).props.feedback;
    expect(feedback.type).toBe(ActionNotice);
    expect(feedback.props.notice.tone).toBe('error');
    expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();
  });

  it('finishes a reply initiated elsewhere from the row stream without rereading the conversation', () => {
    const remote = { ...snapshot('s-A'), manager_requests: [{ request_id: 'remote-request', status: 'running' }] };
    fixture.snapshots.set('s-A', { ...fixture.idle, data: remote });
    refresh();
    fixture.idle.refetch.mockClear();
    fixture.snapshots.set('s-A', { ...fixture.idle, data: snapshot('s-A') });
    refresh();
    expect(fixture.idle.refetch).not.toHaveBeenCalled();
    expect(props().pending).toBe(false);
  });

  it('waits for the mounted project list before reconciling a link outside the cached list', () => {
    act(() => renderer.unmount());
    window.location.search = '?project=s-B';
    fixture.index.isFetchedAfterMount = false;
    fixture.index.data.projects = fixture.index.data.projects.filter(row => (row as { id: string }).id === 's-A');
    act(() => { renderer = create(mountedApp()); });
    expect(new URLSearchParams(window.location.search).get('project')).toBe('s-B');
    fixture.index.data.projects = [...fixture.index.data.projects, { id: 's-B', label: 's-B', last_active: 1 }];
    fixture.index.isFetchedAfterMount = true;
    refresh();
    expect(renderer.root.findByType(TopBar).props.snap.session.id).toBe('s-B');
    expect(new URLSearchParams(window.location.search).get('project')).toBe('s-B');
  });

  it('does not replace a map choice with a question that arrives after the opening refresh', () => {
    select('s-B');
    fixture.snapshots.set('s-A', { ...fixture.idle, data: snapshot('s-A'), isFetching: true, isFetchedAfterMount: false });
    select('s-A');
    act(() => renderer.root.findAll(node => node.props.className === 'workspace-tab')[1].props.onClick());
    fixture.snapshots.set('s-A', { ...fixture.idle, data: snapshot('s-A') });
    refresh();
    const asked = { ...snapshot('s-A'), pending_questions: [{ id: pendingAnswer.task.id, operator_decision: {
      id: `engineer-${pendingAnswer.task.id}`, item_id: pendingAnswer.task.id,
      status: 'pending', question: pendingAnswer.question, options: [],
    } }] };
    fixture.snapshots.set('s-A', { ...fixture.idle, data: asked });
    refresh();
    expect(renderer.root.findAll(node => node.props.className === 'workspace-tab')[1].props['aria-current']).toBe('page');
  });

  it('does not replay a composer focus request when switching projects', () => {
    act(() => renderer.root.findByType(EventStream).props.onCompose('Focus this draft'));
    expect(props().focusSignal).toBe(1);
    select('s-B');
    expect(props().focusSignal).toBe(0);
    act(() => renderer.root.findByType(Sidebar).props.onNew());
    expect(renderer.root.findByType(Landing).props.focusSignal).toBe(1);
  });

  it('requests composer focus on every New activation, including while already on the start page', () => {
    for (const signal of [1, 2, 3]) {
      act(() => renderer.root.findByType(Sidebar).props.onNew());
      expect(renderer.root.findByType(Landing).props.focusSignal).toBe(signal);
    }
  });

  it('keeps project chrome and conversation loading when selecting an uncached project', () => {
    fixture.snapshots.set('s-B', { ...fixture.idle, data: undefined, isLoading: true, isFetching: true });
    select('s-B');
    expect(renderer.root.findAllByType(Landing)).toHaveLength(0);
    expect(renderer.root.findAllByType(EventStream)[0].props.historyStatus).toBe('loading');
    expect(renderer.root.findAll(node => node.props.className === 'workspace-tab').map(node => node.children.join('')))
      .toEqual(['Conversation', 'Map', 'Workbench']);
    fixture.snapshots.set('s-B', { ...fixture.idle, data: snapshot('s-B') });
    refresh();
    expect(renderer.root.findByType(TopBar).props.snap.session.id).toBe('s-B');
  });

  it('keeps conversation loading after deleting the selected project and selecting an uncached one', async () => {
    fixture.snapshots.set('s-B', { ...fixture.idle, data: undefined, isLoading: true });
    fixture.actions.deleteProject.mutateAsync.mockResolvedValueOnce({ ok: true, trash_path: 'trash/s-A' });
    fixture.index.refetch.mockResolvedValueOnce({ data: { projects: fixture.index.data.projects.filter(row => (row as { id: string }).id === 's-B') } });
    act(() => renderer.root.findByType(Sidebar).props.onManage('s-A'));
    await act(async () => renderer.root.findByType(DaemonManageModal).props.onDelete());
    expect(new URLSearchParams(window.location.search).get('project')).toBe('s-B');
    expect(renderer.root.findAllByType(Landing)).toHaveLength(0);
    expect(renderer.root.findByType(EventStream).props.historyStatus).toBe('loading');
  });

  it('answers the displayed question through the main composer without sending a Manager message', async () => {
    const current = snapshot('s-A');
    current.pending_questions = [{ id: 't1', operator_decision: {
      id: 'reviewer-t1', item_id: 't1', status: 'pending', question: 'Which encoding?', options: [],
    } }];
    fixture.snapshots.set('s-A', { ...fixture.idle, data: current });
    refresh();
    const answer = vi.spyOn(api, 'resolveDecision').mockResolvedValue({ resolved: true });
    const stream = vi.spyOn(api, 'messageStream').mockResolvedValue();
    const followup = vi.spyOn(api, 'queueFollowup').mockResolvedValue({ ok: true, queued: true, message_id: 'followup' });
    type('Use UTF-8');
    await act(async () => { expect(await props().onSend('Use UTF-8', [], { whileRunning: true })).toBe(true); });
    expect(answer).toHaveBeenCalledExactlyOnceWith('s-A', 'reviewer-t1', 'custom', 'Use UTF-8', []);
    expect(stream).not.toHaveBeenCalled();
    expect(followup).not.toHaveBeenCalled();
    expect(props().value).toBe('');
  });

  it('keeps the main answer draft on failure and does not send its attachments as a Manager message', async () => {
    const current = snapshot('s-A');
    current.pending_questions = [{ id: 't1', operator_decision: {
      id: 'engineer-t1', item_id: 't1', status: 'pending', question: 'Which encoding?', options: [],
    } }];
    fixture.snapshots.set('s-A', { ...fixture.idle, data: current });
    refresh();
    const answer = vi.spyOn(api, 'resolveDecision').mockRejectedValue(new Error('Connection refused'));
    const stream = vi.spyOn(api, 'messageStream').mockResolvedValue();
    type('Use UTF-8');
    await act(async () => { expect(await props().onSend('Use UTF-8')).toBe(false); });
    expect(props().value).toBe('Use UTF-8');
    expect(renderer.root.findByType(ActionNotice).props.notice.tone).toBe('error');
    const file = new File(['data'], 'data.txt');
    vi.spyOn(api, 'uploadAttachments').mockRejectedValue(new Error('Upload unavailable'));
    await act(async () => props().onAttachmentsChange([file]));
    await act(async () => { expect(await props().onSend('Use UTF-8', [file])).toBe(false); });
    expect(answer).toHaveBeenCalledOnce();
    expect(stream).not.toHaveBeenCalled();
    expect(props().value).toBe('Use UTF-8');
  });

  it('shows no continuous work control or command for the server snapshot', () => {
    expect(renderer.root.findAllByProps({ role: 'switch' })).toHaveLength(0);
    expect(JSON.stringify(renderer.toJSON())).not.toMatch(/Keep going|continuous campaign|持续推进/);
  });
  it.each(['trash/s-A-20261029000000', 'trash\\s-A-20261029000000'])('offers Undo without server paths after deletion and restores the project (%s)', async trash_path => {
    act(() => renderer.root.findByType(EventStream).props.onCompose('Focus this draft'));
    fixture.actions.deleteProject.mutateAsync.mockResolvedValueOnce({ ok: true, trash_path, workdir_preserved: true, workdir: '/private/server/workspaces/s-A' });
    const restore = vi.spyOn(api, 'restoreTrash').mockResolvedValue({ ok: true, sid: 's-A' });
    act(() => renderer.root.findByType(Sidebar).props.onManage('s-A'));
    await act(async () => { expect(await renderer.root.findByType(DaemonManageModal).props.onDelete()).toBe(true); });
    const notice = renderer.root.findByType(ActionNotice).props.notice;
    expect(notice.message).toBe('Project moved to the trash.');
    expect(notice.message).not.toContain('/');
    expect(notice.action.label).toBe('Undo');
    expect(renderer.root.findByType(Sidebar).props.mobileOpen).toBe(false);
    expect(renderer.root.findByType(Landing).props.focusSignal).toBe(0);
    await act(async () => notice.action.onClick());
    expect(restore).toHaveBeenCalledExactlyOnceWith('s-A-20261029000000');
    expect(new URLSearchParams(window.location.search).get('project')).toBe('s-A');
    expect(renderer.root.findByType(ActionNotice).props.notice.message).toBe('Project restored.');
  });
  it('reports a failed restore without showing a successful restoration', async () => {
    fixture.actions.deleteProject.mutateAsync.mockResolvedValueOnce({ ok: true, trash_path: 'trash/s-A-20261029000000' });
    vi.spyOn(api, 'restoreTrash').mockRejectedValue(new Error('restore refused'));
    act(() => renderer.root.findByType(Sidebar).props.onManage('s-A'));
    await act(async () => renderer.root.findByType(DaemonManageModal).props.onDelete());
    await act(async () => renderer.root.findByType(ActionNotice).props.notice.action.onClick());
    expect(renderer.root.findByType(ActionNotice).props.notice.tone).toBe('error');
    expect(renderer.root.findByType(ActionNotice).props.notice.details.message).toBe('restore refused');
  });
  it('uses the selected project name as the tab title and follows renaming', () => {
    expect(document.title).toBe('s-A');
    fixture.index.data.projects = [{ id: 's-A', display_name: 'Renamed project' }];
    refresh();
    expect(document.title).toBe('Renamed project');
    act(() => renderer.root.findByType(Sidebar).props.onNew());
    expect(document.title).toBe('Argus');
  });
  it('counts deliveries once for the sidebar and map, keeping the latest delivery as the sidebar target', () => {
    fixture.artifacts = [{ path: 'report.md', name: 'report.md', kind: 'markdown', source: 'delivery' },
      { path: 'data.csv', name: 'data.csv', kind: 'table', source: 'delivery' }] as never[];
    fixture.turns = [1, 2, 3].map(n => ({ type: 'ui.argus', ts: n, event_id: n, delivery: {
      schema_version: 1, delivery_id: `d${n}`, title: 'Result', kind: 'result', item_id: '', summary: '',
      status: 'done', review_status: '', delivered_at: n,
      targets: fixture.artifacts.map((file: { path: string }) => ({ path: file.path, label: file.path, source: 'manager' })), primary_target: null,
    } }));
    refresh();
    expect(renderer.root.findByType(Sidebar).props.resultsCount).toBe(3);
    act(() => renderer.root.findAll(node => node.props.className === 'workspace-tab')[1].props.onClick());
    expect(renderer.root.findByType(MapPanel).props.deliveryCount).toBe(3);
    act(() => renderer.root.findAll(node => node.props.className === 'workspace-tab')[0].props.onClick());
    act(() => renderer.root.findByType(Sidebar).props.onOpenResults());
    const deliveryModal = renderer.root.findAllByType(ArtifactModal).find(node => node.props.delivery)!;
    expect(deliveryModal.props.delivery.delivery_id).toBe('d3');
    act(() => deliveryModal.props.onClose());
    act(() => renderer.root.find(node => node.type === 'button' && node.children.includes('Results & files')).props.onClick());
    expect(renderer.root.findByType(ResearchCanvas).props.artifacts).toHaveLength(2);
  });
  it('counts zero before any delivery exists and keeps files accessible from the file entry', () => {
    fixture.artifacts = [{ path: 'draft.md', name: 'draft.md', kind: 'markdown' }] as never[];
    refresh();
    const sidebar = renderer.root.findByType(Sidebar);
    expect(sidebar.props.resultsCount).toBe(0);
    expect(renderer.root.findAllByType(ResearchCanvas)).toHaveLength(0);
    expect(renderer.root.findAll(node => node.type === 'button' && node.props['data-sidebar-results'] !== undefined).every(node => node.props.disabled)).toBe(true);
    act(() => renderer.root.find(node => node.type === 'button' && node.children.includes('Results & files')).props.onClick());
    const canvas = renderer.root.findByType(ResearchCanvas);
    expect(canvas.props.requestedPath).toBe('__argus_files__');
    expect(canvas.props.filesFirst).toBe(true);
    expect(canvas.props.artifacts.map((file: { path: string }) => file.path)).toEqual(['draft.md']);
    expect(renderer.root.findAllByType(ArtifactModal).filter(node => node.props.delivery)).toHaveLength(0);
    expect(renderer.root.findByType(Sidebar).props.mobileOpen).toBe(false);
  });
  it('uses the existing session objective as the tab title when the project has no display name', () => {
    fixture.index.data.projects = [{ id: 's-A', display_name: '' }];
    fixture.snapshots.set('s-A', { ...fixture.idle, data: { ...snapshot('s-A'), session: {
      ...snapshot('s-A').session, display_name: '', objective: 'Saved request title\nFull request body',
    } } });
    refresh();
    expect(document.title).toBe('Saved request title');
  });
  it('puts the original question straight in the composer and sends the edit as a new message', async () => {
    vi.spyOn(api, 'editions').mockResolvedValue({ original: '', versions: [] });
    const save = vi.spyOn(api, 'saveEdition').mockResolvedValue({ version: 1, content: 'Revised answer' });
    const stream = vi.spyOn(api, 'messageStream').mockImplementation(async (_sid, _text, handlers) => {
      handlers.onAccepted?.();handlers.onDone?.({ kind: 'message', reply: 'Revised answer' });
    });
    const target = 'Original question';
    await act(async () => { renderer.root.findByType(EventStream).props.onConversationAction(target); });
    expect(props().value).toBe('Original question');
    type('/task revised question');
    await act(async () => { expect(await props().onSend('/task revised question')).toBe(true); });
    expect(stream).toHaveBeenCalledWith('s-A', '/task revised question', expect.any(Object),
      expect.objectContaining({ routeOverride: 'auto' }));
    expect(save).not.toHaveBeenCalled();
  });
  it('gives the chat composer the same runtime line as the map composer', () => {
    const footer = props().footer as { type?: unknown; props?: { sid?: string } };
    expect(footer?.type).toBe(ComposerRuntime);
    expect(footer?.props?.sid).toBe('s-A');
  });
  it('+ New opens the start page, whose first message makes the project: none is made empty', async () => {
    const create = vi.spyOn(api, 'createDaemon').mockResolvedValue({ sid: 's-N' } as Awaited<ReturnType<typeof api.createDaemon>>);
    await act(async () => renderer.root.findByType(Sidebar).props.onNew());
    expect(create).not.toHaveBeenCalled();
    expect(new URLSearchParams(window.location.search).get('project')).toBeNull();
    // The page for new work, not the one that asks to pick a project.
    expect(renderer.root.findByType(Landing).props.hasProjects).toBe(false);
    await act(async () => { await renderer.root.findByType(Landing).props.onSubmit('Total the columns of data.csv', []); });
    expect(create).toHaveBeenCalledTimes(1);
  });
  it('keeps creation errors beside the directory until it changes or creation succeeds', async () => {
    const failure = new ApiError('permission denied', 400, 'POST', '/api/daemons', 'workdir_permission_denied');
    const create = vi.spyOn(api, 'createDaemon').mockRejectedValue(failure);
    await act(async () => renderer.root.findByType(Sidebar).props.onNew());
    const landing = () => renderer.root.findByType(Landing);
    await act(async () => { expect(await landing().props.onSubmit('Keep this draft', [], '/denied')).toBe(false); });
    expect(landing().props.createError).toBe(failure);
    expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();
    act(() => landing().props.onWorkdirChange());
    expect(landing().props.createError).toBeNull();
    await act(async () => { await landing().props.onSubmit('Keep this draft', [], '/denied'); });
    expect(landing().props.createError).toBe(failure);
    create.mockImplementation(async () => {
      fixture.index.data.projects = [...fixture.index.data.projects, { id: 's-N', label: 'New project', last_active: 2 }];
      return { sid: 's-N' } as Awaited<ReturnType<typeof api.createDaemon>>;
    });
    await act(async () => { expect(await landing().props.onSubmit('Keep this draft', [], '/writable')).toBe(true); });
    expect(renderer.root.findAllByType(Landing).every(node => !node.props.createError)).toBe(true);
    expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();
    await act(async () => renderer.root.findByType(Sidebar).props.onNew());
    expect(landing().props.createError).toBeNull();
  });
  it('clears the directory error when returning to a new form and keeps the failed request draft', async () => {
    vi.spyOn(api, 'createDaemon').mockRejectedValue(new ApiError('permission denied', 400, 'POST', '/api/daemons', 'workdir_permission_denied'));
    await act(async () => renderer.root.findByType(Sidebar).props.onNew());
    const landing = () => renderer.root.findByType(Landing);
    const folder = () => landing().findByProps({ list: 'landing-workdirs' });
    const composer = () => landing().findByType(ChatBox);
    act(() => composer().props.onChange('Keep the failed request draft'));
    act(() => folder().props.onChange({ target: { value: '/denied' } }));
    await act(async () => { expect(await composer().props.onSend('Keep the failed request draft', [])).toBe(false); });
    expect(folder().props['aria-invalid']).toBe(true);
    expect(composer().props.value).toBe('Keep the failed request draft');
    select('s-A');
    expect(renderer.root.findAllByType(Landing)).toHaveLength(0);
    await act(async () => renderer.root.findByType(Sidebar).props.onNew());
    expect(folder().props.value).toBe('');
    expect(folder().props['aria-invalid']).toBe(false);
    expect(landing().findAllByProps({ id: 'landing-workdir-error' })).toHaveLength(0);
    expect(composer().props.value).toBe('Keep the failed request draft');
  });
  it('reads the project list again once the first message has reached the server, so the new row is named by it', async () => {
    vi.spyOn(api, 'messageStream').mockImplementation(async (_sid, _text, handlers) => {
      handlers.onAccepted?.();
      // The reply is still being written.
      await new Promise(() => {});
    });
    fixture.idle.refetch.mockClear();
    type('Total the columns of data.csv');
    await act(async () => { expect(await props().onSend('Total the columns of data.csv')).toBe(true); });
    expect(fixture.idle.refetch).toHaveBeenCalled();
  });
  it('keeps A text and File references in A, with a clean B draft', () => {
    const file = new File(['A attachment'], 'report.md');
    type('A draft');
    act(() => props().onAttachmentsChange([file]));
    select('s-B');
    expect(props().value).toBe('');
    expect(props().attachments).toEqual([]);
    type('B draft');
    select('s-A');
    expect(props().value).toBe('A draft');
    expect(props().attachments[0]).toBe(file);
  });

  it('does not submit or restore an A upload in B and preserves the server cancellation distinction', async () => {
    const upload = deferred<Awaited<ReturnType<typeof api.uploadAttachments>>>();
    vi.spyOn(api, 'uploadAttachments').mockReturnValue(upload.promise);
    const stream = vi.spyOn(api, 'messageStream').mockResolvedValue();
    const file = new File(['A'], 'report.md');
    type('A upload');
    // The file starts uploading as soon as it is chosen, before any send.
    await act(async () => props().onAttachmentsChange([file]));
    expect(api.uploadAttachments).toHaveBeenCalledWith('s-A', [file], expect.any(AbortSignal),
      expect.objectContaining({ onProgress: expect.any(Function) }));
    let sent!: Promise<boolean>;
    act(() => { sent = Promise.resolve(props().onSend('A upload', [file])); });
    // Sending waits for that upload instead of starting a second one.
    expect(api.uploadAttachments).toHaveBeenCalledTimes(1);
    select('s-B');
    expect(props().value).toBe('');
    type('B untouched');
    await act(async () => { upload.reject(new Error('synthetic upload failure')); expect(await sent).toBe(false); });
    expect(props().value).toBe('B untouched');
    expect(stream).not.toHaveBeenCalled();
    select('s-A');
    expect(props().value).toBe('A upload');
    expect(props().attachments[0]).toBe(file);
  });

  it('does not clear a new revision even if the operator retypes the same text during upload', async () => {
    const upload = deferred<Awaited<ReturnType<typeof api.uploadAttachments>>>();
    vi.spyOn(api, 'uploadAttachments').mockReturnValue(upload.promise);
    vi.spyOn(api, 'messageStream').mockResolvedValue();
    const file = new File(['old'], 'report.md');
    const replacement = new File(['new'], 'report.md', { lastModified: file.lastModified });
    type('same text');
    await act(async () => props().onAttachmentsChange([file]));
    let sent!: Promise<boolean>;
    act(() => { sent = Promise.resolve(props().onSend('same text', [file])); });
    type(''); type('same text');
    act(() => props().onAttachmentsChange([replacement]));
    await act(async () => {
      upload.resolve({ attachments: [{ attachment_id: 'att-000000000001', relative_path: '.argus/attachments/s-A/att-000000000001/report.md',
        original_name: 'report.md', stored_name: 'report.md', mime: 'text/markdown', size_bytes: file.size }],
      limits: { max_count: 8, max_bytes_per_file: 1000, max_total_bytes: 8000 } });
      expect(await sent).toBe(true);
    });
    expect(props().value).toBe('same text');
    expect(props().attachments[0]).toBe(replacement);
  });

  it('keeps rewrite busy and late results with A while B can rewrite independently', async () => {
    const a = deferred<Awaited<ReturnType<typeof api.rewritePrompt>>>();
    const b = deferred<Awaited<ReturnType<typeof api.rewritePrompt>>>();
    vi.spyOn(api, 'rewritePrompt').mockImplementation(sid => sid === 's-A' ? a.promise : b.promise);
    type('A original');
    act(() => props().onRewrite!('A original'));
    select('s-B'); type('B original');
    expect(props().rewriting).toBe(false);
    act(() => props().onRewrite!('B original'));
    await act(async () => a.resolve({ original: 'A original', rewritten: 'A rewritten', changes: [], questions: [], error: '' }));
    expect(props().value).toBe('B original');
    expect(props().rewriting).toBe(true);
    await act(async () => b.resolve({ original: 'B original', rewritten: 'B rewritten', changes: [], questions: [], error: '' }));
    expect(props().value).toBe('B rewritten');
    // Done, the rewrite announces itself by offering the original back, not by a notice over the composer.
    expect(JSON.stringify(renderer.toJSON())).not.toContain('Rewritten.');
    select('s-A'); expect(props().value).toBe('A rewritten');
    act(() => props().onUndoRewrite!());
    expect(props().value).toBe('A original');
    expect(props().onUndoRewrite).toBeUndefined();
  });

  it.each(['new text', 'same text'])('rejects a rewrite after clearing and typing %s', async text => {
    const rewrite = deferred<Awaited<ReturnType<typeof api.rewritePrompt>>>();
    vi.spyOn(api, 'rewritePrompt').mockReturnValue(rewrite.promise);
    type('same text');
    act(() => props().onRewrite!('same text'));
    type(''); type(text);
    await act(async () => rewrite.resolve({ original: 'same text', rewritten: 'stale rewrite', changes: [], questions: [], error: '' }));
    expect(props().value).toBe(text);
  });

  it.each([
    ['the server', 'The Manager wrote no rewrite.', async () => ({ original: 'draft', rewritten: 'draft', changes: [], questions: [], error: 'The Manager wrote no rewrite.' })],
    ['the network', 'connection reset', async () => { throw new Error('connection reset'); }],
  ])('says why a rewrite failed when %s says so, and leaves the draft as it was', async (_, reason, outcome) => {
    vi.spyOn(api, 'rewritePrompt').mockImplementation(outcome);
    type('draft');
    await act(async () => props().onRewrite!('draft'));
    expect(props().value).toBe('draft');
    const notice = JSON.stringify(renderer.toJSON());
    expect(notice).toContain("The rewrite failed; your text is unchanged.");
    expect(notice).toContain(reason);
    const details = renderer.root.findAllByType('details').find(detail => detail.findAllByType('pre').some(pre => pre.children.join('') === reason))!;
    expect(details.props.open).not.toBe(true);
    expect(JSON.stringify(details.findByType('pre').children)).toContain(reason);
  });

  it('keeps the same draft on map/activity changes and resets only the existing route override on project changes', async () => {
    const file = new File(['A'], 'report.md');
    type('shared');
    act(() => { props().onAttachmentsChange([file]); props().onRouteOverrideChange!('task'); });
    await act(async () => renderer.root.findAllByType('button').find(node => node.children[0] === 'Map')!.props.onClick());
    const map = renderer.root.findByType(MapPanel);
    expect(renderer.root.findByType(TopBar).props.snap.session.id).toBe('s-A');
    expect(renderer.root.findByType(TopBar).props.showRunControl).toBe(false);
    expect(map.props).not.toHaveProperty('draft');
    expect(map.props).not.toHaveProperty('onSend');
    expect(props().value).toBe('shared');
    select('s-B'); expect(props().routeOverride).toBe('auto');
    select('s-A'); expect(props().value).toBe('shared');
  });

  it('offers settings, the model chooser, the route choice and editions only where the server declares them', async () => {
    const send = vi.spyOn(api, 'messageStream').mockResolvedValue();
    // A server that declares only what every server has, as the TypeScript core does.
    await act(async () => { renderer.update(<Capabilities.Provider value={['manager.sse.v1']}>{mountedApp()}</Capabilities.Provider>); });
    expect(renderer.root.findByType(ComposerRuntime).props.onOpenSettings).toBeUndefined();
    expect(props().onRouteOverrideChange).toBeUndefined();
    expect(renderer.root.findByType(EventStream).props.onConversationAction).toBeTypeOf('function');
    type('/help');
    await act(async () => { expect(await props().onSend('/help')).toBe(true); });
    expect(send).toHaveBeenCalledWith('s-A', '/help', expect.any(Object), expect.objectContaining({ routeOverride: 'auto' }));
  });

  it('retains drafts through transient index errors but drops them only after explicit successful deletion', async () => {
    type('A retained');
    fixture.index.isError = true; fixture.index.isSuccess = false;
    refresh(); select('s-B'); select('s-A');
    expect(props().value).toBe('A retained');
    fixture.index.isError = false; fixture.index.isSuccess = true;
    select('s-B');
    act(() => renderer.root.findByType(Sidebar).props.onManage('s-A'));
    fixture.actions.deleteProject.mutateAsync.mockRejectedValueOnce(new Error('synthetic deletion refused'));
    await act(async () => { expect(await renderer.root.findByType(DaemonManageModal).props.onDelete()).toBe(false); });
    select('s-A'); expect(props().value).toBe('A retained'); select('s-B');
    fixture.actions.deleteProject.mutateAsync.mockResolvedValueOnce({ ok: true, workdir_preserved: true, workdir: '/synthetic/s-A' });
    await act(async () => { expect(await renderer.root.findByType(DaemonManageModal).props.onDelete()).toBe(true); });
    select('s-A'); expect(props().value).toBe('');
  });

  it('keeps unconfirmed text only in the composer until the server acknowledges it', async () => {
    const stream=deferred<void>();const send=vi.spyOn(api,'messageStream').mockReturnValue(stream.promise);
    type('waiting for receipt');await act(async () => {await props().onSend('waiting for receipt');});
    expect(props().value).toBe('waiting for receipt');
    expect(renderer.root.findByType(EventStream).props.events.some((event:EventMsg)=>event.type==='ui.operator')).toBe(false);
    await act(async () => {send.mock.calls[0][2].onAccepted?.();stream.resolve();});
    expect(props().value).toBe('');
    expect(renderer.root.findByType(EventStream).props.events.filter((event:EventMsg)=>event.type==='ui.operator')).toHaveLength(1);
  });

  it('keeps an unconfirmed draft and request identity across navigation, then resends that identity', async () => {
    const send = vi.spyOn(api,'messageStream').mockRejectedValue(new TypeError('Confirmation lost'));
    type('same draft');const original=JSON.parse(localStorage.getItem('argus.composer.draft.v1:s-A')!).request_id;
    await act(async () => {await props().onSend('same draft');});
    expect(props().value).toBe('same draft');select('s-B');select('s-A');
    expect(props().value).toBe('same draft');
    send.mockImplementation(async (_sid,_text,handlers) => {handlers.onAccepted?.();handlers.onDone?.({kind:'received',status:'replied'});});
    await act(async () => {await props().onSend('same draft');});
    expect(send.mock.calls.map(call => (call[3] as {requestId:string}).requestId)).toEqual([original,original]);
    expect(props().value).toBe('');expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();
    expect(api.conversation).not.toHaveBeenCalled();
  });

  it.each(['edited',''])('keeps newer text when an old send is confirmed: %s', async edited => {
    const stream=deferred<void>();const send=vi.spyOn(api,'messageStream').mockReturnValue(stream.promise);
    type('original');const original=JSON.parse(localStorage.getItem('argus.composer.draft.v1:s-A')!).request_id;
    await act(async () => {await props().onSend('original');});
    expect(props().value).toBe('original');type(edited);
    const current=JSON.parse(localStorage.getItem('argus.composer.draft.v1:s-A')||'null');
    if(current)expect(current.request_id).not.toBe(original);
    await act(async () => {send.mock.calls[0][2].onAccepted?.();stream.resolve();});
    expect(props().value).toBe(edited);
  });

  it('allows a new draft after an unconfirmed send with a new identity', async () => {
    const send=vi.spyOn(api,'messageStream').mockRejectedValueOnce(new TypeError('Lost'));
    type('first');await act(async () => {await props().onSend('first');});
    type('new intent');send.mockImplementation(async (_sid,_text,handlers)=>{handlers.onAccepted?.();});
    await act(async () => {await props().onSend('new intent');});
    expect((send.mock.calls[1][3] as {requestId:string}).requestId).not.toBe((send.mock.calls[0][3] as {requestId:string}).requestId);
    expect(props().value).toBe('');expect(api.conversation).not.toHaveBeenCalled();
  });

  it('clears the old notice when another tab confirms and removes the shared draft', async () => {
    vi.spyOn(api,'messageStream').mockRejectedValue(new TypeError('Lost'));
    type('shared draft');await act(async () => {await props().onSend('shared draft');});
    expect(renderer.root.findByType(ActionNotice).props.notice).not.toBeNull();
    act(() => {localStorage.removeItem('argus.composer.draft.v1:s-A');window.dispatchEvent(Object.assign(new Event('storage'),{key:'argus.composer.draft.v1:s-A'}));});
    expect(props().value).toBe('');expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();
  });

  it('sends the canonical text and identity together when a tab notification has not redrawn the input yet', async () => {
    const send=vi.spyOn(api,'messageStream').mockRejectedValueOnce(new TypeError('Lost'));
    type('old draft');await act(async () => {await props().onSend('old draft');});
    localStorage.setItem('argus.composer.draft.v1:s-A',JSON.stringify({text:'from another tab',request_id:'other-tab-identity',attachments:[]}));
    send.mockImplementation(async (_sid,_text,handlers)=>{handlers.onAccepted?.();});
    await act(async () => {await props().onSend('old draft');});
    expect(send.mock.calls[1][1]).toBe('from another tab');
    expect((send.mock.calls[1][3] as {requestId:string}).requestId).toBe('other-tab-identity');
    expect(props().value).toBe('');
  });

  it('uses the same draft identity for a follow-up retry while the original reply runs', async () => {
    const send=vi.spyOn(api,'messageStream').mockRejectedValueOnce(new TypeError('Lost'));
    const queued=vi.spyOn(api,'queueFollowup').mockResolvedValue({ok:true,queued:true,message_id:'existing'});
    type('same draft');await act(async () => {await props().onSend('same draft');});
    const requestId=(send.mock.calls[0][3] as {requestId:string}).requestId;
    await act(async () => {await props().onSend('same draft',[],{whileRunning:true});});
    expect(queued).toHaveBeenCalledExactlyOnceWith('s-A','same draft','auto',requestId);
    expect(props().value).toBe('');
  });

  it('shows an unconfirmed send only in the composer, with no receipt probe or automatic POST replay', async () => {
    const send=vi.spyOn(api,'messageStream').mockRejectedValue(new TypeError('Offline'));
    type('not yet confirmed');await act(async () => {await props().onSend('not yet confirmed');});
    expect(props().value).toBe('not yet confirmed');expect(send).toHaveBeenCalledOnce();expect(api.conversation).not.toHaveBeenCalled();
    expect(renderer.root.findByType(EventStream).props.events.some((event:EventMsg)=>event.type==='ui.operator')).toBe(false);
    expect(renderer.root.findByType(ActionNotice).props.notice.message).toContain('did not confirm receipt');
  });

  it.each(['error frame','lost connection'])('leaves an accepted failure to its original conversation row: %s', async ending => {
    const stream=deferred<void>();const send=vi.spyOn(api,'messageStream').mockImplementation((_sid,_text,handlers)=>{handlers.onAccepted?.();return stream.promise;});
    type('one request');await act(async () => {await props().onSend('one request');});expect(props().value).toBe('');
    await act(async () => {if(ending==='error frame'){send.mock.calls[0][2].onError?.(new Error('Manager failed'));stream.resolve();}else stream.reject(new Error('Lost'));});
    expect(props().value).toBe('');expect(renderer.root.findByType(ActionNotice).props.notice).toBeNull();expect(send).toHaveBeenCalledOnce();
  });

});

describe('App desktop delivery session navigation', () => {
  it('waits for A to load before opening an A path, keeping B draft and background request intact', async () => {
    select('s-B'); type('B draft');
    fixture.snapshots.set('s-A', { ...fixture.idle, isPending: true, data: undefined });
    await toast('s-A');
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-A');
    expect(api.artifact).not.toHaveBeenCalled();
    fixture.snapshots.set('s-A', { ...fixture.idle, data: snapshot('s-A') });
    await act(async () => { refresh(); });
    expect(api.artifact).toHaveBeenCalledWith('s-A', 'results/report.md', expect.any(AbortSignal));
    select('s-B'); expect(props().value).toBe('B draft');
  });

  it('cancels a pending index lookup even when the user reselects the same project', async () => {
    select('s-B');
    const index = deferred<Awaited<ReturnType<typeof api.projectIndex>>>();
    vi.mocked(api.projectIndex).mockReturnValueOnce(index.promise);
    await toast('s-A');
    select('s-B');
    await act(async () => index.resolve(fixture.index.data as Awaited<ReturnType<typeof api.projectIndex>>));
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
    expect(api.artifact).not.toHaveBeenCalled();
    expect(openPaths()).toEqual([]);
  });

  it('does not open a late artifact after manual selection, even of the same target sid', async () => {
    select('s-B');
    const artifact = deferred<Awaited<ReturnType<typeof api.artifact>>>();
    vi.mocked(api.artifact).mockReturnValueOnce(artifact.promise);
    await toast('s-A');
    expect(api.artifact).toHaveBeenCalledTimes(1);
    select('s-A');
    await act(async () => artifact.resolve({ path: 'results/report.md', name: 'report.md', kind: 'markdown', mime: 'text/markdown', size: 1, mtime: 1 }));
    expect(openPaths()).toEqual([]);
  });

  it('lets the newest notification win over an already-started artifact request', async () => {
    const artifact = deferred<Awaited<ReturnType<typeof api.artifact>>>();
    vi.mocked(api.artifact).mockReturnValueOnce(artifact.promise);
    await toast('s-A');
    await toast('s-B', 'completion:s-B:fixture');
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
    await act(async () => artifact.resolve({ path: 'results/report.md', name: 'report.md', kind: 'markdown', mime: 'text/markdown', size: 1, mtime: 1 }));
    expect(openPaths()).toEqual([{ sid: 's-B', path: 'results/report.md' }]);
  });

  it('opens a legacy notification only when it uniquely matches a current real receipt', async () => {
    const receipt: DeliveryReceipt = { schema_version: 1, delivery_id: 'delivery:known', item_id: 'fixture', kind: 'task_completed', title: 'Synthetic result', summary: 'Fixture only', status: 'done', review_status: 'pending', delivered_at: 2,
      primary_target: { path: 'results/report.md', label: 'Report', source: 'delivery', }, targets: [] };
    fixture.turns = [{ role: 'argus', ts: 2, text: 'Synthetic result', delivery: receipt }];
    refresh();
    expect(openPaths()).toEqual([]);
    expect(renderer.root.findAll(node => node.type === 'div' && node.props.className === 'delivery-toast')).toEqual([]);
    await toast(undefined, receipt.delivery_id);
    expect(api.artifact).toHaveBeenCalledWith('s-A', 'results/report.md', expect.any(AbortSignal));
    expect(openPaths()).toEqual([{ sid: 's-A', path: 'results/report.md' }]);
  });


  it('does not guess a project from a legacy path-only notification', async () => {
    select('s-B');
    await toast();
    expect(openPaths()).toEqual([]);
    expect(api.artifact).not.toHaveBeenCalled();
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
  });
});
