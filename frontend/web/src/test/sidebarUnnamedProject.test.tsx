import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../components/Sidebar';

function markup(project: Record<string, unknown>) {
  const props = {
    projects: [{ id: 's-0f0f0f0f', label: 's-0f0f0f0f', display_name: '', objective: '', last_active: 1,
      daemon_alive: false, daemon_pid: null, uptime_seconds: null, launch_cwd: '/synthetic', workdir: '/synthetic', ...project }],
    activeId: 's-0f0f0f0f', localCwd: '/synthetic', onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(),
    onOpenSkills: vi.fn(), onOpenWiki: vi.fn(), onOpenVerticals: vi.fn(), onNew: vi.fn(), loading: false,
    onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(), collapsed: false,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><Sidebar {...props} /></QueryClientProvider>); }
  finally { client.clear(); }
}

function primaryLabel(html: string): string {
  const match = html.match(/<span class="min-w-0 flex-1 truncate text-sm font-medium">([^<]*)<\/span>/);
  return match?.[1] ?? '';
}

describe('project names in the sidebar', () => {
  it('never shows the raw session id as the primary label; keeps it in the tooltip', () => {
    const html = markup({});
    expect(primaryLabel(html)).not.toBe('s-0f0f0f0f');
    expect(primaryLabel(html)).toBeTruthy();
    expect(html).toMatch(/title="[^"]*· s-0f0f0f0f/);
  });

  it('shows a readable name as soon as the backend provides one', () => {
    const html = markup({ label: '你好', display_name: '你好' });
    expect(primaryLabel(html)).toBe('你好');
  });
});
