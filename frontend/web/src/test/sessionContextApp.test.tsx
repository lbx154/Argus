import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import type { DeliveryReceipt, Snapshot } from '../../../core/src/types';
import { emptyMissionView } from '../../../core/src/missionView';
import App from '../App';
import { api } from '../api';
import { ChatBox } from '../components/ChatBox';
import { Sidebar } from '../components/Sidebar';
import { ArtifactModal } from '../components/ArtifactModal';
import { ResearchCanvas } from '../components/ResearchCanvas';
import { MapPanel } from '../map/MapPanel';
import { DaemonManageModal } from '../components/DaemonManageModal';

const fixture = vi.hoisted(() => {
  const idle = { data: undefined as unknown, isPending: false, isLoading: false, isError: false, isSuccess: true, refetch: vi.fn(async () => ({})) };
  const mutation = { isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() };
  return {
    idle,
    index: { ...idle, data: { projects: [] as unknown[], local_cwd: '/synthetic' } },
    snapshots: new Map<string, typeof idle & { data?: unknown; error?: Error }>(),
    actions: Object.fromEntries(['startDaemon', 'stopDaemon', 'forceStopDaemon', 'updateProject', 'deleteProject', 'disposeBacklog', 'stopBacklog', 'setContinuous'].map(key => [key, mutation])),
    turns: [] as unknown[],
    events: [],
    artifacts: [],
  };
});
vi.mock('../hooks', async importOriginal => ({
  ...await importOriginal<typeof import('../hooks')>(),
  useProjects: () => fixture.index,
  useProjectCosts: () => fixture.idle,
  useSnapshot: (sid: string) => fixture.snapshots.get(sid) || fixture.idle,
  useArtifacts: () => ({ ...fixture.idle, data: fixture.artifacts }),
  useGitDiff: () => fixture.idle,
  useJournal: () => fixture.idle,
  useTranscript: () => ({ ...fixture.idle, data: fixture.turns }),
  useEventStream: () => ({ events: fixture.events, connected: true }),
  useProjectActions: () => fixture.actions,
}));
// Keep App's actual selection, composer controller, command dispatch and delivery
// controller. Heavy presentation children are irrelevant to these lifetime races;
// the browser fixture separately exercises their real DOM and artifact HTTP path.
vi.mock('../components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../components/ArtifactModal', () => ({ ArtifactModal: () => null }));
vi.mock('../components/EventStream', async importOriginal => ({ ...await importOriginal<object>(), EventStream: () => null }));
vi.mock('../components/ResearchCanvas', async importOriginal => ({ ...await importOriginal<object>(), ResearchCanvas: () => null }));
vi.mock('../components/TopBar', () => ({ TopBar: () => null }));
vi.mock('../components/PendingBanner', () => ({ PendingBanner: () => null }));
vi.mock('../components/PendingReplyDialog', () => ({ PendingReplyDialog: () => null }));
vi.mock('../components/GuardianBanner', () => ({ GuardianBanner: () => null }));
vi.mock('../components/CommandPalette', async importOriginal => ({ ...await importOriginal<object>(), CommandPalette: () => null }));
vi.mock('../components/KeybindingHelp', () => ({ KeybindingHelp: () => null }));
vi.mock('../components/InfoModals', () => ({ DoctorModal: () => null, ConfigModal: () => null, IdentityModal: () => null, TranscriptModal: () => null }));
vi.mock('../components/Modal', () => ({ Modal: () => null, ModalHeader: () => null }));
vi.mock('../components/ProjectInspectorModal', () => ({ ProjectInspectorModal: () => null }));
vi.mock('../components/OperationsModal', () => ({ OperationsModal: () => null }));
vi.mock('../components/VerticalStore', () => ({ VerticalStore: () => null }));
vi.mock('../components/TaskDetailModal', () => ({ TaskDetailModal: () => null }));
vi.mock('../components/MobileTabBar', () => ({ MobileTabBar: () => null }));
vi.mock('../components/ConnectionProblemBanner', () => ({ ConnectionProblemBanner: () => null }));
vi.mock('../research-brief/ProgressQuestions', () => ({ ProgressQuestionsProvider: ({ children }: { children: unknown }) => children, ProgressQuestionHistoryButton: () => null }));
vi.mock('../useVisualViewport', () => ({ useVisualViewport: () => false }));
vi.mock('../useGlobalKeyboardShortcuts', () => ({ useGlobalKeyboardShortcuts: () => undefined }));
vi.mock('../useWorkbenchTheme', () => ({ useWorkbenchTheme: () => ({ themeMode: 'light', cycleTheme: () => undefined }) }));
vi.mock('../map/MapPanel', () => ({ MapPanel: () => null }));

let renderer: ReturnType<typeof create>;
let client: QueryClient;
let parent: { postMessage: ReturnType<typeof vi.fn> };
function snapshot(sid: string): Snapshot {
  return { session: { id: sid, display_name: sid, objective: '', last_active: 1, cwd: `/synthetic/${sid}` },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, global_daily_cap_usd: null }, roles: [], backlog: [], recent_events: [] };
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
  fixture.index.isError = false;
  fixture.index.isSuccess = true;
  fixture.idle.refetch.mockReset().mockResolvedValue({});
  for (const mutation of Object.values(fixture.actions)) mutation.mutateAsync.mockReset();
  fixture.snapshots.clear();
  fixture.index.data.projects = ['s-A', 's-B'].map(id => ({ id, label: id, last_active: 1, daemon_alive: false, objective: '', launch_cwd: `/synthetic/${id}`, daemon_pid: null, uptime_seconds: null }));
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
  vi.spyOn(api, 'cancelMessage').mockResolvedValue({ requested: true, status: 'cancelled' });
  vi.spyOn(api, 'projectIndex').mockImplementation(async () => fixture.index.data as Awaited<ReturnType<typeof api.projectIndex>>);
  vi.spyOn(api, 'artifact').mockImplementation(async (sid, path) => ({ path, name: 'report.md', kind: 'markdown', mime: 'text/markdown', why: 'Synthetic fixture', exists: true, size: 1, mtime: 1, preview: `${sid} ONLY` }));
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
    act(() => props().onAttachmentsChange([file]));
    let sent!: Promise<boolean>;
    act(() => { sent = Promise.resolve(props().onSend('A upload', [file])); });
    expect(api.uploadAttachments).toHaveBeenCalledWith('s-A', [file], expect.any(AbortSignal));
    select('s-B');
    expect(props().value).toBe('');
    type('B untouched');
    await act(async () => { upload.reject(new Error('synthetic upload failure')); expect(await sent).toBe(false); });
    expect(props().value).toBe('B untouched');
    expect(stream).not.toHaveBeenCalled();
    expect(api.cancelMessage).not.toHaveBeenCalled();
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
    act(() => props().onAttachmentsChange([file]));
    let sent!: Promise<boolean>;
    act(() => { sent = Promise.resolve(props().onSend('same text', [file])); });
    type(''); type('same text');
    act(() => props().onAttachmentsChange([replacement]));
    await act(async () => {
      upload.resolve({ attachments: [], limits: { max_count: 8, max_bytes_per_file: 1000, max_total_bytes: 8000 } });
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
    select('s-A'); expect(props().value).toBe('A rewritten');
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

  it('keeps the same draft on map/activity changes and resets only the existing route override on project changes', async () => {
    const file = new File(['A'], 'report.md');
    type('shared');
    act(() => { props().onAttachmentsChange([file]); props().onRouteOverrideChange!('task'); });
    await act(async () => renderer.root.findAllByType('button').find(node => node.children[0] === 'Map')!.props.onClick());
    const map = renderer.root.findByType(MapPanel);
    expect(map.props.draft).toBe('shared'); expect(map.props.attachments[0]).toBe(file);
    act(() => map.props.onDraftChange('map edit'));
    expect(props().value).toBe('map edit');
    select('s-B'); expect(props().routeOverride).toBe('auto');
    select('s-A'); expect(props().value).toBe('map edit');
  });

  it('preserves slash rewrite results and clears ordinary handled commands in their original session', async () => {
    const rewrite = deferred<Awaited<ReturnType<typeof api.rewritePrompt>>>();
    vi.spyOn(api, 'rewritePrompt').mockReturnValue(rewrite.promise);
    type('/rewrite explain this');
    await act(async () => { expect(await props().onSend('/rewrite explain this')).toBe(true); });
    await act(async () => rewrite.resolve({ original: 'explain this', rewritten: 'a better explanation', changes: [], questions: [], error: '' }));
    expect(props().value).toBe('a better explanation');
    type('/help');
    await act(async () => { expect(await props().onSend('/help')).toBe(true); });
    expect(props().value).toBe('');
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

  it('does not start a message after a project switch during the command-dispatch microtask', async () => {
    const send = vi.spyOn(api, 'messageStream').mockResolvedValue();
    type('A');
    let sent!: Promise<boolean>;
    act(() => { sent = Promise.resolve(props().onSend('A')); });
    select('s-B');
    await act(async () => { expect(await sent).toBe(false); });
    expect(send).not.toHaveBeenCalled();
    expect(api.cancelMessage).not.toHaveBeenCalled();
  });

  it('does not restore a broken-stream draft over new input or automatically resend after acceptance', async () => {
    const stream = deferred<void>();
    const send = vi.spyOn(api, 'messageStream').mockReturnValue(stream.promise);
    type('send once');
    await act(async () => { expect(await props().onSend('send once')).toBe(true); });
    type('next input'); type('');
    await act(async () => stream.reject(new Error('SSE disconnected after server acceptance')));
    expect(props().value).toBe('');
    expect(send).toHaveBeenCalledTimes(1);
    expect(api.cancelMessage).not.toHaveBeenCalled();
  });

  it('retains the explicit stop-waiting request identity and never automatically replays a broken stream', async () => {
    const stream = deferred<void>();
    const send = vi.spyOn(api, 'messageStream').mockReturnValue(stream.promise);
    type('send once');
    await act(async () => { expect(await props().onSend('send once')).toBe(true); });
    expect(props().value).toBe('');
    const options = send.mock.calls[0][3] as { requestId: string; signal: AbortSignal };
    expect(options.requestId).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
    act(() => props().onCancel());
    expect(api.cancelMessage).toHaveBeenCalledWith('s-A', options.requestId);
    await act(async () => stream.reject(new Error('SSE disconnected after acceptance')));
    expect(send).toHaveBeenCalledTimes(1);
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
    expect(api.cancelMessage).not.toHaveBeenCalled();
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
    await act(async () => artifact.resolve({ path: 'results/report.md', name: 'report.md', kind: 'markdown', mime: 'text/markdown', exists: true, why: 'Synthetic', size: 1, mtime: 1 }));
    expect(openPaths()).toEqual([]);
  });

  it('lets the newest notification win over an already-started artifact request', async () => {
    const artifact = deferred<Awaited<ReturnType<typeof api.artifact>>>();
    vi.mocked(api.artifact).mockReturnValueOnce(artifact.promise);
    await toast('s-A');
    await toast('s-B', 'completion:s-B:fixture');
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
    await act(async () => artifact.resolve({ path: 'results/report.md', name: 'report.md', kind: 'markdown', mime: 'text/markdown', exists: true, why: 'Synthetic', size: 1, mtime: 1 }));
    expect(openPaths()).toEqual([{ sid: 's-B', path: 'results/report.md' }]);
  });

  it('opens a legacy notification only when it uniquely matches a current real receipt', async () => {
    const receipt: DeliveryReceipt = { schema_version: 1, delivery_id: 'delivery:known', item_id: 'fixture', kind: 'task_completed', title: 'Synthetic result', summary: 'Fixture only', status: 'done', review_status: 'pending', delivered_at: 2,
      primary_target: { path: 'results/report.md', label: 'Report', source: 'delivery', why: 'Synthetic fixture' }, targets: [] };
    fixture.turns = [{ role: 'argus', ts: 2, text: 'Synthetic result', delivery: receipt }];
    refresh();
    act(() => renderer.root.findAllByType(ArtifactModal).find(node => node.props.path)!.props.onClose());
    await toast(undefined, receipt.delivery_id);
    expect(api.artifact).toHaveBeenCalledWith('s-A', 'results/report.md', expect.any(AbortSignal));
    expect(openPaths()).toEqual([{ sid: 's-A', path: 'results/report.md' }]);
  });

  it('attaches the loaded session to new completion notifications without replaying historical completion on selection', async () => {
    vi.useFakeTimers();
    // The stub window was created before fake timers; keep its clock aligned
    // with the global one instead of leaving the real 500ms timer running.
    Object.assign(window, { setTimeout, clearTimeout });
    const complete = emptyMissionView();
    complete.mission = { ...complete.mission, id: 'fixture-task', title: 'Synthetic result', status: 'complete', started_at: 1, completed_at: 2 };
    fixture.snapshots.set('s-A', { ...fixture.idle, data: { ...snapshot('s-A'), mission_view: complete,
      backlog: [{ id: 'fixture-task', title: 'Synthetic result', objective: 'Fixture only', priority: 0, status: 'done', ts: 1 }] } });
    refresh();
    await act(async () => { await vi.advanceTimersByTimeAsync(501); });
    const outgoing = () => parent.postMessage.mock.calls.filter(([message]) => message.type === 'argus:notify-completion');
    expect(outgoing()).toHaveLength(1);
    expect(outgoing()[0][0].payload).toMatchObject({ sessionId: 's-A', deliveryId: 'completion:s-A:fixture-task' });
    select('s-B'); select('s-A');
    await act(async () => { await vi.advanceTimersByTimeAsync(501); });
    expect(outgoing()).toHaveLength(1);
  });

  it('does not guess a project from a legacy path-only notification', async () => {
    select('s-B');
    await toast();
    expect(openPaths()).toEqual([]);
    expect(api.artifact).not.toHaveBeenCalled();
    expect(new URL(window.location.href).searchParams.get('project')).toBe('s-B');
  });
});
