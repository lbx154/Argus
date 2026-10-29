import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import type { ProjectRow } from '../api';
import { recommendedSidebarScope, Sidebar } from '../components/Sidebar';

let queryClient: QueryClient;
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
afterEach(() => queryClient.clear());

const runningStatus = { state: 'running' as const, role: 'engineer', roles: ['engineer'], taskId: '', title: '', foreground: false, reason: '' as const, activityAt: null, label: { 'zh-CN': '团队正在工作', en: 'The team is working' } };
const rows: ProjectRow[] = [
  { workdir: '/workspace/local',
    id: 'local', label: 'Local', display_name: 'Local', last_active: 1, daemon_alive: false,
    daemon_pid: null,
  },
  { workdir: '/workspace/remote',
    id: 'remote', label: 'Remote', display_name: 'Remote', last_active: 1, daemon_alive: false,
    daemon_pid: null,
  },
];

describe('recommendedSidebarScope', () => {
  it('keeps local scope when it contains the active session', () => {
    expect(recommendedSidebarScope(rows, 'local', '/workspace/local')).toBe('local');
  });

  it('shows all sessions when the selected session is outside local scope', () => {
    expect(recommendedSidebarScope(rows, 'remote', '/workspace/local')).toBe('all');
  });

  it('shows all sessions instead of an empty local sidebar', () => {
    expect(recommendedSidebarScope(rows, null, '/workspace/missing')).toBe('all');
  });
});

function sidebarMarkup(
  projects: ProjectRow[],
): string {
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client: queryClient }, createElement(Sidebar, {
      projects: projects.map((project) => ({ ...project, launch_cwd: '/workspace/test', workdir: '/workspace/test' })),
      activeId: projects[0]?.id ?? null,
      localCwd: '/workspace/test',
      onSelect: () => undefined,
      onManage: () => undefined,
      onOpenPanel: () => undefined,
      onNew: () => undefined,
      loading: false,
      onToggleCollapse: () => undefined,
      themeMode: 'dark',
      onCycleTheme: () => undefined,
    })),
  );
}

describe('Sidebar session identity and health', () => {
  it('keeps stable identifiers in the tooltip when unnamed sessions would otherwise look identical', () => {
    const unnamed = rows.map((project, index) => ({
      ...project,
      id: `session-${index + 1}`,
      label: `session-${index + 1}`,
      display_name: '',
    }));

    const markup = sidebarMarkup(unnamed);

    // The raw id is never the visible name; it stays in each row's tooltip
    // so otherwise identical unnamed sessions remain distinguishable.
    expect(markup).toMatch(/title="[^"]*· session-1"/);
    expect(markup).toMatch(/title="[^"]*· session-2"/);
    expect(markup).not.toContain('>session-1</span>');
    expect(markup).not.toContain('>session-2</span>');
    expect(markup).not.toContain('Codex · gpt-5');
  });

  it('does not show an old release as running after the executor stops', () => {
    const markup = sidebarMarkup([{ work_status: { ...runningStatus, state: 'paused', label: { 'zh-CN': '已停止', en: 'Stopped' } },
      ...rows[0],
      daemon_alive: false,
    }]);

    expect(markup).toContain('title="Stopped"');
    expect(markup).not.toContain('Working · 2m');
    expect(markup).not.toContain('Update available');
  });

  it('shows a single project directly without repeating its directory', () => {
    const markup = sidebarMarkup([rows[0]]);
    expect(markup).not.toContain('aria-expanded="true"');
    expect(markup).not.toContain('>test</span>');
    expect(markup).not.toContain('>/workspace/test</');
  });

  it('uses the server status for every project, including one that is not selected', () => {
    const status = { state: 'paused' as const, role: '', roles: [], taskId: '', title: '', activityAt: null,
      foreground: false, reason: 'operator_input' as const, label: { 'zh-CN': '等你回答 1 个问题', en: 'Waiting for 1 answer' } };
    const markup = sidebarMarkup(rows.map(row => ({ ...row, work_status: status })));
    expect(markup.match(/>Waiting for 1 answer</g)).toHaveLength(2);
    expect(markup).toContain('data-session-work-state="paused"');
  });

});
