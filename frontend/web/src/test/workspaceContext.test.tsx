import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import type { Snapshot } from '../api';
import { TopBar } from '../components/TopBar';
import { I18nProvider, type Locale } from '../i18n';
import { WORKBENCH_MODULES } from '../research-workbench/modules';
import { IdePage } from '../research-workbench/pages/IdePage';
import { ProjectOverviewPage } from '../research-workbench/pages/ProjectOverviewPage';
import type { ActiveWorkbenchPageProps } from '../research-workbench/pages/pageTypes';

const snap: Snapshot = {
  session: { id: 's-A', display_name: 'Synthetic A', objective: '', last_active: 1,
    cwd: '/loaded/legacy-cwd', workdir: 'D:\\Synthetic\\研究\\session-A' },
  daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'memory', global_daily_cap_usd: null },
  roles: [], backlog: [], recent_events: [],
};
const props: ActiveWorkbenchPageProps = {
  sid: 's-A', active: false, snapshot: snap,
  project: { id: 's-A', label: 'A', objective: '', last_active: 1, daemon_alive: false, daemon_pid: null,
    uptime_seconds: null, workdir: '/stale/project-index' },
  events: [], connected: false, snapshotUpdatedAt: 1, refresh: vi.fn(),
  controls: { start: vi.fn(), stop: vi.fn(), busy: false, error: '' }, navigate: vi.fn(),
};
let client: QueryClient;
const fetch = vi.fn(() => { throw new Error('Context presentation must not fetch'); });
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.stubGlobal('fetch', fetch); fetch.mockClear();
});
afterEach(() => { client.clear(); vi.unstubAllGlobals(); });
function markup(element: ReactElement, locale: Locale = 'en') {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  return renderToStaticMarkup(<QueryClientProvider client={client}><I18nProvider>{element}</I18nProvider></QueryClientProvider>);
}
function topbar(snapshot = snap) {
  return <TopBar snap={snapshot} streamOk onStart={vi.fn()} onStop={vi.fn()} onManage={vi.fn()} busy={false} />;
}

describe('A3 snapshot-only session context', () => {
  it.each(['en', 'zh-CN'] as const)('shows the session directory and honest bounds in %s without requests', locale => {
    const html = markup(topbar(), locale);
    expect(html).toContain(locale === 'en' ? 'Session directory' : '会话目录');
    expect(html).toContain(snap.session.workdir);
    expect(html).toContain('<summary');
    expect(html).toContain(locale === 'en' ? 'not each task’s actual cwd or a security sandbox boundary' : '不是每个任务的实际 cwd，也不是安全沙箱边界');
    expect(html).toContain(locale === 'en' ? 'Manage session' : '管理会话');
    expect(html).toContain(locale === 'en' ? 'Run Argus' : '运行 Argus');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('uses only the loaded snapshot in the overview, not a stale project-index directory', () => {
    const html = markup(<ProjectOverviewPage {...props} />);
    expect(html).toContain(snap.session.workdir);
    expect(html).not.toContain('/stale/project-index');
    expect(html).not.toMatch(/Both modules|两个模块/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('retains all module IDs, entry order and navigation actions', () => {
    expect(WORKBENCH_MODULES.map(row => row.id)).toEqual(['overview', 'timeline', 'experiments', 'ide']);
    const navigate = vi.fn();
    let view: ReturnType<typeof create>;
    act(() => { view = create(<ProjectOverviewPage {...props} navigate={navigate} />); });
    const cards = view!.root.findAllByProps({ className: 'module-card' });
    expect(cards).toHaveLength(3);
    act(() => { for (const card of cards) card.props.onClick(); });
    expect(navigate.mock.calls.map(args => args[0])).toEqual(['timeline', 'experiments', 'ide']);
    act(() => view!.unmount());
  });

  it('falls back to the snapshot cwd and labels missing information as pending', () => {
    expect(markup(topbar({ ...snap, session: { ...snap.session, workdir: '' } }))).toContain('/loaded/legacy-cwd');
    const missing = { ...snap, session: { ...snap.session, workdir: '', cwd: '' } };
    expect(markup(topbar(missing))).toContain('Pending confirmation');
    expect(markup(topbar(missing), 'zh-CN')).toContain('待确认');
    expect(markup(<ProjectOverviewPage {...props} snapshot={missing} />)).toContain('Pending confirmation');
  });

  it('keeps complete long and HTML-like paths as escaped text, never directory actions', () => {
    const path = `D:\\Synthetic\\${'long-path\\'.repeat(100)}<script>not executable</script>`;
    const html = markup(topbar({ ...snap, session: { ...snap.session, workdir: path } }));
    expect(html).toContain('&lt;script&gt;not executable&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('long-path\\'.repeat(100));
    expect(html).not.toContain('setWorkdir');
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['en', 'zh-CN'] as const)('describes the IDE view, not Agent write permissions, in %s', locale => {
    const html = markup(<IdePage {...props} />, locale);
    expect(html).toContain(locale === 'en' ? 'Code workspace' : '代码工作区');
    expect(html).toContain(locale === 'en' ? 'This view is read-only' : '此视图只读');
    expect(html).toContain(locale === 'en' ? 'does not restrict the Agent’s file write permissions' : '不限制 Agent 本身的文件写入权限');
    expect(html).not.toMatch(/Server code workspace|服务器代码工作区|Read-only safe mode|只读安全模式/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
