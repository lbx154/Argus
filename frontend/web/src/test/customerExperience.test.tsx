import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type ArtifactInfo, type ProjectRow, type ProjectPreference } from '../api';
import { emptyMissionView } from '../../../core/src/missionView';
import { Landing } from '../components/Landing';
import { StartPrompts } from '../components/StartPrompts';
import { ResearchCanvas, FILES_PATH } from '../components/ResearchCanvas';
import { Sidebar } from '../components/Sidebar';
import { ActionNotice } from '../components/ActionNotice';
import { ChatBox } from '../components/ChatBox';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HistorySearch } from '../components/ExperiencePanels';

vi.mock('../i18n', () => ({ useI18n: () => ({
  locale: 'zh-CN', setLocale: vi.fn(),
  t: (key: string, vars?: Record<string, unknown>) => `${key}${vars?.name ? ` ${vars.name}` : ''}`,
}) }));
vi.mock('../components/PluginLauncher', () => ({ PluginLauncher: () => null }));
vi.mock('../hooks', () => ({ useArtifact: () => ({ data: undefined, isLoading: false, isError: false }) }));
vi.mock('../components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <pre>{children}</pre> }));
let view: ReactTestRenderer;
let storage: Map<string, string>;
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const button = (label: string) => view.root.findAllByType('button').find(node => text(node).includes(label))!;
beforeEach(() => {
  storage = new Map();
  const localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('window', Object.assign(new EventTarget(), { localStorage, setTimeout, clearTimeout }));
  const preferences: Record<string, ProjectPreference> = {};
  let revision = 0;
  vi.spyOn(api, 'organization').mockResolvedValue({ value: {}, revision: 0 });
  vi.spyOn(api, 'organize').mockImplementation(async (sid, field, value) => {
    preferences[sid] = { ...(preferences[sid] ?? { pinned: false, archived: false, followup: false }), [field]: value };
    return { value: { ...preferences }, revision: ++revision };
  });
});
afterEach(() => { act(() => view?.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('starting and revisiting work', () => {
  it('lets the home page prepare a draft without creating a project and preserves it after a rejected send', async () => {
    const onNew = vi.fn();
    const onSubmit = vi.fn(async () => false);
    act(() => { view = create(<Landing loading={false} hasProjects={false} canCreate onNew={onNew} onSubmit={onSubmit} onRetry={vi.fn()} onChoose={vi.fn()} />); });
    act(() => button('调研清楚，帮我做决策').props.onClick());
    expect(onNew).not.toHaveBeenCalled();
    const chat = () => view.root.findByType(ChatBox);
    expect(chat().props.value).toContain('不编造依据');
    expect(view.root.findAllByType('button').some(node => node.props['aria-label'] === 'sidebar.create' || text(node) === 'landing.new')).toBe(false);
    await act(async () => { await chat().props.onSend('first question', []); });
    // With no directory named, the project gets a new one.
    expect(onSubmit).toHaveBeenCalledWith('first question', [], '');
    expect(chat().props.value).toContain('不编造依据');
    // A directory on the server the operator names is where it works.
    act(() => view.root.findByProps({ list: 'landing-workdirs' }).props.onChange({ target: { value: '  ~/repo ' } }));
    await act(async () => { await chat().props.onSend('second question', []); });
    expect(onSubmit).toHaveBeenLastCalledWith('second question', [], '~/repo');
  });

  it('lets a narrow-screen search reader return to results without opening a project', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('window', Object.assign(new EventTarget(), { localStorage, setTimeout, clearTimeout }));
    const result = { sid: 's-A', title: 'Matching project', kind: 'conversation' as const, snippet: 'Needle excerpt', text: 'Full recorded answer' };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
    client.setQueryData(['history-search', 'Needle'], { results: [result], partial: false, errors: [] });
    const onChoose = vi.fn();
    const onClose = vi.fn();
    try {
      act(() => { view = create(<QueryClientProvider client={client}><HistorySearch open onChoose={onChoose} onClose={onClose} /></QueryClientProvider>); });
      await act(async () => {
        view.root.findByProps({ type: 'search' }).props.onChange({ target: { value: 'Needle' } });
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(350); });
      act(() => button('Matching project').props.onClick());
      expect(text(view.root)).toContain('Full recorded answer');
      act(() => button('返回搜索结果').props.onClick());
      expect(text(view.root)).not.toContain('Full recorded answer');
      expect(onChoose).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
      act(() => button('Matching project').props.onClick());
      act(() => button('打开并定位').props.onClick());
      expect(onChoose).toHaveBeenCalledWith(result);
      expect(onClose).toHaveBeenCalledOnce();
    } finally { client.clear(); }
  });
  it('offers four editable starting points without automatically creating or submitting work', () => {
    const onNew = vi.fn();
    act(() => { view = create(<Landing loading={false} hasProjects={false} canCreate onNew={onNew} onRetry={vi.fn()} onChoose={vi.fn()} />); });
    expect(onNew).not.toHaveBeenCalled();
    expect(view.root.findByType(StartPrompts).findAllByType('button')).toHaveLength(4);
    act(() => button('调研清楚，帮我做决策').props.onClick());
    expect(onNew).toHaveBeenCalledWith(expect.stringContaining('不编造依据'));
  });

  it('prevents a second starting-point selection while project creation is pending', () => {
    act(() => { view = create(<Landing loading={false} hasProjects={false} canCreate creating onNew={vi.fn()} onRetry={vi.fn()} onChoose={vi.fn()} />); });
    expect(view.root.findByType(StartPrompts).findAllByType('button').every(node => node.props.disabled)).toBe(true);
  });

  it('opens files rather than progress, and retains the selected file across project reloads', () => {
    const file: ArtifactInfo = { path: 'report.md', name: 'report.md', exists: true, source: 'delivery', kind: 'markdown', mime: 'text/markdown', size: 100, mtime: 1, why: 'fixture' };
    const props = { sid: 's-A', missionView: emptyMissionView(), artifacts: [file], onExpand: vi.fn(), filesFirst: true, deliveriesLoaded: true };
    const onRevise = vi.fn();
    act(() => { view = create(<ResearchCanvas {...props} onRevise={onRevise} />); });
    expect(view.root.findByType('select').props.value).toBe(FILES_PATH);
    expect(text(view.root)).toContain('report.md');
    expect(view.root.findAllByType('option').some(node => node.props.value === '__argus_live_progress__')).toBe(false);
    act(() => button('report.md').props.onClick());
    expect(view.root.findByType('select').props.value).toBe('report.md');
    act(() => button('继续修改').props.onClick());
    expect(onRevise).toHaveBeenCalledWith('report.md');
    act(() => view.update(<ResearchCanvas {...props} requestedPath={FILES_PATH} requestedPathToken={1} />));
    act(() => button('report.md').props.onClick());
    act(() => view.update(<ResearchCanvas {...props} artifacts={[{ ...file, mtime: 2 }]} requestedPath={FILES_PATH} requestedPathToken={1} />));
    expect(view.root.findByType('select').props.value).toBe('report.md');
    act(() => view.unmount());
    act(() => { view = create(<ResearchCanvas {...props} />); });
    expect(view.root.findByType('select').props.value).toBe('report.md');
    act(() => view.update(<ResearchCanvas {...props} sid="s-B" />));
    expect(view.root.findByType('select').props.value).toBe(FILES_PATH);
  });

  it('keeps failures visible instead of dismissing them on a timer', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    act(() => { view = create(<ActionNotice notice={{ id: 1, tone: 'error', message: 'Please retry' }} onClose={onClose} />); });
    act(() => { vi.advanceTimersByTime(20_000); });
    expect(onClose).not.toHaveBeenCalled();
    expect(text(view.root)).toContain('Please retry');
  });
});

describe('project organization', () => {
  const project = (id: string, name: string): ProjectRow => ({
    id, label: name, display_name: name, objective: '', last_active: 1,
    daemon_alive: true, daemon_pid: 1, uptime_seconds: 1, launch_cwd: '/synthetic', workdir: '/synthetic',
  });
  const props = {
    projects: [project('s-A', 'Alpha'), project('s-B', 'Beta')], activeId: 's-A', localCwd: '/synthetic',
    onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(), onNew: vi.fn(), loading: false,
    onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(),
  };
  const manage = (name: string) => view.root.findAllByType('button').find(node => node.props['aria-label'] === `sidebar.manage ${name}`)!;

  it('explains project settings on the empty home page and still opens them for a selected project', async () => {
    const onOpenPanel = vi.fn();
    await act(async () => { view = create(<Sidebar {...props} projects={[]} activeId={null} onOpenPanel={onOpenPanel} />); });
    act(() => view.root.findByProps({ 'aria-label': 'sidebar.openSettings' }).props.onClick());
    expect(onOpenPanel).not.toHaveBeenCalled();
    expect(text(view.root)).toContain('先打开或创建一个项目');
    await act(async () => { view.update(<Sidebar {...props} onOpenPanel={onOpenPanel} />); });
    expect(text(view.root)).not.toContain('先打开或创建一个项目');
    act(() => view.root.findByProps({ 'aria-label': 'sidebar.openSettings' }).props.onClick());
    expect(onOpenPanel).toHaveBeenCalledWith('config');
  });

  it('syncs pins, marks and archives without deleting, pausing or managing server projects', async () => {
    await act(async () => { view = create(<Sidebar {...props} />); });
    act(() => manage('Beta').props.onClick({ stopPropagation: vi.fn() }));
    await act(async () => button('置顶项目').props.onClick());
    expect(text(view.root)).toContain('置顶项目');
    act(() => manage('Beta').props.onClick({ stopPropagation: vi.fn() }));
    await act(async () => button('标记待跟进').props.onClick());
    act(() => button('待跟进').props.onClick());
    expect(text(view.root)).toContain('Beta');
    expect(text(view.root)).not.toContain('Alpha');
    expect(text(view.root)).toContain('你标记为待跟进的项目');
    act(() => manage('Beta').props.onClick({ stopPropagation: vi.fn() }));
    await act(async () => button('归档项目').props.onClick());
    expect(text(view.root)).not.toContain('Beta');
    expect(text(view.root)).toContain('已归档，可在“已归档”中找回。');
    expect(props.onManage).not.toHaveBeenCalled();
    act(() => button('已归档').props.onClick());
    expect(text(view.root)).toContain('Beta');
    act(() => manage('Beta').props.onClick({ stopPropagation: vi.fn() }));
    await act(async () => button('移出归档').props.onClick());
    expect(text(view.root)).toContain('已移回最近。');
    expect(text(view.root)).not.toContain('已归档，可在');
    act(() => button('最近').props.onClick());
    expect(text(view.root)).toContain('Beta');
    expect(storage.get('argus.project.organization.v1')).toContain('"archived":false');
    expect(api.organize).toHaveBeenCalledWith('s-B', 'pinned', true);
  });
});
