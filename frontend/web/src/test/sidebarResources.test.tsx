import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../components/Sidebar';

const props = {
  projects: [{ id: 's-A', label: 'Synthetic session A', display_name: 'Synthetic session A', objective: '',
    last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null, launch_cwd: '/synthetic', workdir: '/synthetic' }],
  activeId: 's-A', localCwd: '/synthetic', onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(),
  onOpenSkills: vi.fn(), onOpenWiki: vi.fn(), onOpenVerticals: vi.fn(), onNew: vi.fn(), loading: false,
  onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(),
};
function markup(collapsed = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><Sidebar {...props} collapsed={collapsed} /></QueryClientProvider>); }
  finally { client.clear(); }
}
describe('top resource navigation', () => {
  it('puts existing plugin/skill/knowledge entries before project search and sessions, not in the footer', () => {
    const html = markup();
    expect(html.indexOf('data-sidebar-resources')).toBeGreaterThan(0);
    for (const label of ['aria-label="Plugins"', 'aria-label="Skill library"', 'aria-label="Knowledge base"']) {
      expect(html).toContain(label);
      expect(html.indexOf(label)).toBeLessThan(html.indexOf('id="daemon-search"'));
      expect(html.indexOf(label)).toBeLessThan(html.indexOf('Synthetic session A'));
      expect(html.split(label)).toHaveLength(2);
    }
    expect(html.indexOf('aria-label="Open settings"')).toBeGreaterThan(html.indexOf('Synthetic session A'));
  });
  it('keeps the existing compact entry rail and collapse controls', () => {
    const html = markup(true);
    expect(html).toContain('aria-label="Expand sessions"');
    expect(html).toContain('aria-label="Skill library"');
    expect(html).toContain('aria-label="Knowledge base"');
    expect(html).not.toContain('id="daemon-search"');
  });
});
