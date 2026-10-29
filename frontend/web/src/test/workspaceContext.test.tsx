import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import type { Snapshot } from '../api';
import { TopBar } from '../components/TopBar';
import { I18nProvider, type Locale } from '../i18n';
import { WORKBENCH_MODULES } from '../research-workbench/modules';
import { IdePage } from '../research-workbench/pages/IdePage';
import type { ActiveWorkbenchPageProps } from '../research-workbench/pages/pageTypes';
import stoppedSnapshot from './fixtures/conversation-shell-stopped.json';

const snap: Snapshot = {
  session: { id: 's-A', display_name: 'Synthetic A', objective: '', last_active: 1, workdir: 'D:\\Synthetic\\研究\\session-A' },
  daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'memory'},
  roles: [], backlog: [], };
const props: ActiveWorkbenchPageProps = {
  sid: 's-A', active: false, snapshot: snap,
  events: [], refresh: vi.fn(),
};
let client: QueryClient;
const fetch = vi.fn(() => { throw new Error('Context presentation must not fetch'); });
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.stubGlobal('fetch', fetch); fetch.mockClear();
});
afterEach(() => { client.clear(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });
function markup(element: ReactElement, locale: Locale = 'en') {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  return renderToStaticMarkup(<QueryClientProvider client={client}><I18nProvider>{element}</I18nProvider></QueryClientProvider>);
}
function topbar(snapshot = snap) {
  return <TopBar snap={snapshot} streamOk onStart={vi.fn()} onStop={vi.fn()} onManage={vi.fn()} busy={false} />;
}

describe('A3 snapshot-only session context', () => {
  it('dates the recorded activity and shows ledger spend directly in the status row', () => {
    // Recorded through the isolated built Argus HTTP service during t3's browser reproduction.
    const recorded = stoppedSnapshot as Snapshot;
    vi.stubEnv('TZ', 'UTC');
    vi.useFakeTimers();
    vi.setSystemTime(new Date((recorded.work_status!.activityAt! + 3 * 3600) * 1000));
    for (const locale of ['zh-CN', 'en'] as const) {
      const html = markup(topbar(recorded), locale);
      const status = html.match(/class="topbar-status[^>]*>([\s\S]*?)<div class="hidden sm:block"/)?.[1];
      expect(status).toContain(locale === 'zh-CN' ? '已停止' : 'Stopped');
      expect(status).toContain(locale === 'zh-CN' ? '3小时前' : '3h ago');
      expect(status).toContain(locale === 'zh-CN' ? '估算 ≈$94.5（按 API 价）' : 'Est. ≈$94.5 (API prices)');
      expect(status).not.toContain('truncate');
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('uses the session activity time when no work activity is recorded, without inventing unknown spend', () => {
    const recorded = stoppedSnapshot as Snapshot;
    const html = markup(topbar({ ...recorded, work_status: { ...recorded.work_status!, activityAt: null }, spend_usd: null, spend_status: 'unpriced' }));
    expect(html).toContain(new Date(recorded.session.last_active * 1000).toISOString());
    expect(html).not.toContain('Spent $');
    expect(markup(topbar({ ...recorded, spend_usd: 0, spend_status: 'priced' }))).toContain('Est. ≈$0.00 (API prices)');
    expect(markup(topbar({ ...recorded, spend_status: 'partial' }))).toContain('Est. ≥$94.5 (API prices)');
  });

  it.each(['en', 'zh-CN'] as const)('shows the session directory in %s without requests', locale => {
    const html = markup(topbar(), locale);
    expect(html).toContain(locale === 'en' ? 'Project folder' : '项目目录');
    expect(html).toContain(snap.session.workdir);
    expect(html).toContain('<summary');
    expect(html.match(/<summary[^>]*>[\s\S]*?<\/summary>/)?.[0]).not.toContain(snap.session.workdir);
    expect(html).toContain(locale === 'en' ? 'Manage project' : '管理项目');
    expect(html).not.toContain(locale === 'en' ? 'Run Argus' : '运行 Argus');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('offers Resume only for work the server says Resume takes up, and Stop for a live team', () => {
    const queued = { ...snap, backlog: [{ id: 'queued', title: 'Continue work', objective: 'Continue work', status: 'pending', priority: 1 }] };
    expect(markup(topbar(queued))).not.toContain('Resume');
    expect(markup(topbar({ ...queued, daemon: { ...snap.daemon, resumable: true } }))).toContain('Resume');
    expect(markup(topbar({ ...snap, daemon: { ...snap.daemon, alive: true, pid: 1 } }))).toContain('data-stop-work');
  });

  it('keeps only what the conversation, the map and the results do not show: the code, then the upkeep', () => {
    expect(WORKBENCH_MODULES.map(row => row.id)).toEqual(['ide', 'runtime']);
  });

  it('uses the snapshot workdir and labels missing information as pending', () => {
    expect(markup(topbar({ ...snap, session: { ...snap.session, workdir: '/loaded/workdir' } }))).toContain('/loaded/workdir');
    const missing = { ...snap, session: { ...snap.session, workdir: '', } };
    expect(markup(topbar(missing))).toContain('Pending confirmation');
    expect(markup(topbar(missing), 'zh-CN')).toContain('待确认');
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

  it.each(['en', 'zh-CN'] as const)('describes the IDE view in %s', locale => {
    const html = markup(<IdePage {...props} />, locale);
    expect(html).toContain(locale === 'en' ? 'Code workspace' : '代码工作区');
    expect(html).toContain(locale === 'en' ? 'This view is read-only' : '此视图只读');
    expect(html).not.toMatch(/Server code workspace|服务器代码工作区|Read-only safe mode|只读安全模式/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
