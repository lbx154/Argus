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
  config: {
    activeBackend?: string;
    activeModel?: string;
    activeConfigLoading?: boolean;
    activeConfigError?: boolean;
  } = { activeBackend: 'codex', activeModel: 'gpt-5' },
): string {
  return renderToStaticMarkup(
    createElement(Sidebar, {
      projects: projects.map((project) => ({ ...project, launch_cwd: '/workspace/test', workdir: '/workspace/test' })),
      activeId: projects[0]?.id ?? null,
      localCwd: '/workspace/test',
      onSelect: () => undefined,
      onManage: () => undefined,
      onResume: () => undefined,
      ...config,
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

    expect(markup).toContain('title="Unnamed session · session-1"');
    expect(markup).toContain('title="Unnamed session · session-2"');
    expect(markup).toContain('>session-1</div>');
    expect(markup).toContain('>session-2</div>');
    expect(markup).toContain('Codex · gpt-5');
    expect(markup).toContain('>Resume</button>');
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
    expect(markup).not.toContain('>Resume</button>');
  });

  it('shows loading and fallback states for active model metadata', () => {
    expect(sidebarMarkup([rows[0]], { activeConfigLoading: true })).toContain('Loading backend and model…');
    expect(sidebarMarkup([rows[0]], { activeConfigError: true })).toContain('Backend and model unavailable');
    expect(sidebarMarkup([rows[0]], { activeBackend: 'codex' })).toContain('Codex · default model');
  });
});
