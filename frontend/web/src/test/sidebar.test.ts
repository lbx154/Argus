import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import type { ProjectRow } from '../api';
import { recommendedSidebarScope, Sidebar } from '../components/Sidebar';

const rows: ProjectRow[] = [
  {
    id: 'local', label: 'Local', display_name: 'Local', objective: '',
    launch_cwd: '/workspace/local', last_active: 1, daemon_alive: false,
    daemon_pid: null, uptime_seconds: null,
  },
  {
    id: 'remote', label: 'Remote', display_name: 'Remote', objective: '',
    launch_cwd: '/workspace/remote', last_active: 1, daemon_alive: false,
    daemon_pid: null, uptime_seconds: null,
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
    createElement(Sidebar, {
      projects: projects.map((project) => ({ ...project, launch_cwd: '/workspace/test', workdir: '/workspace/test' })),
      activeId: projects[0]?.id ?? null,
      localCwd: '/workspace/test',
      onSelect: () => undefined,
      onManage: () => undefined,
      onResume: () => undefined,
      onOpenPanel: () => undefined,
      onNew: () => undefined,
      loading: false,
      onToggleCollapse: () => undefined,
      themeMode: 'dark',
      onCycleTheme: () => undefined,
    }),
  );
}

describe('Sidebar session identity and health', () => {
  it('shows stable identifiers when unnamed sessions would otherwise look identical', () => {
    const unnamed = rows.map((project, index) => ({
      ...project,
      id: `session-${index + 1}`,
      label: `session-${index + 1}`,
      display_name: '',
    }));

    const markup = sidebarMarkup(unnamed);

    expect(markup).toContain('title="session-1"');
    expect(markup).toContain('title="session-2"');
    expect(markup).toContain('>session-1</span>');
    expect(markup).toContain('>session-2</span>');
    expect(markup).toContain('aria-label="Resume"');
    expect(markup).not.toContain('Codex · gpt-5');
  });

  it('flags a live incompatible daemon instead of presenting it as healthy', () => {
    const markup = sidebarMarkup([{
      ...rows[0],
      daemon_alive: true,
      daemon_protocol_compatible: false,
      uptime_seconds: 120,
    }]);

    expect(markup).toContain('Update required');
    expect(markup).not.toContain('title="Argus running"');
    expect(markup).not.toContain('running · 2m');
    expect(markup).not.toContain('aria-label="Resume"');
  });

  it('uses a compact, collapsible project group without exposing the full path', () => {
    const markup = sidebarMarkup([rows[0]]);
    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain('title="/workspace/test"');
    expect(markup).toContain('>test</span>');
    expect(markup).not.toContain('>/workspace/test</');
  });
});
