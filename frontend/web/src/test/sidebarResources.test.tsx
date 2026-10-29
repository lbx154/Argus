import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { act, create } from 'react-test-renderer';
import { Sidebar } from '../components/Sidebar';
import { LearningLibraryEntry } from '../components/LearningLibrary';

const props = {
  projects: [{ id: 's-A', label: 'Synthetic session A', display_name: 'Synthetic session A', objective: '',
    last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null, launch_cwd: '/synthetic', workdir: '/synthetic' }],
  activeId: 's-A', localCwd: '/synthetic', onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(),
  onOpenLearning: vi.fn(), onOpenResults: vi.fn(), resultsCount: 2, onNew: vi.fn(), loading: false,
  onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(),
};
function markup(collapsed = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><Sidebar {...props} collapsed={collapsed} /></QueryClientProvider>); }
  finally { client.clear(); }
}
describe('top resource navigation', () => {
  it('puts Results first and folds skills under Advanced', () => {
    const html = markup();
    expect(html.indexOf('data-sidebar-resources')).toBeGreaterThan(0);
    expect(html).toContain('data-sidebar-results');
    expect(html.indexOf('data-sidebar-results')).toBeLessThan(html.indexOf('id="daemon-search"'));
    expect(html).toContain('aria-expanded="false"');
    for (const label of ['aria-label="Plugins"', 'aria-label="Skill library"']) {
      expect(html).not.toContain(label);
    }
    expect(html.indexOf('aria-label="Open settings"')).toBeGreaterThan(html.indexOf('Synthetic session A'));
  });
  it('shows the advanced entries above project search once opened', () => {
    vi.stubGlobal('localStorage', { getItem: (key: string) => key === 'argus.sidebar.advanced.v1' ? 'open' : null, setItem: vi.fn(), removeItem: vi.fn() });
    try {
      const html = markup();
      const labels = ['aria-label="Skill library"'];
      for (const label of labels) {
        expect(html).toContain(label);
        expect(html.indexOf(label)).toBeLessThan(html.indexOf('id="daemon-search"'));
        expect(html.split(label)).toHaveLength(2);
      }
      expect(labels.map(label => html.indexOf(label))).toEqual(labels.map(label => html.indexOf(label)).sort((a, b) => a - b));
      expect(html).not.toContain('aria-label="Learning feed"');
      expect(html).not.toContain('aria-label="Plugins"');
    } finally { vi.unstubAllGlobals(); }
  });
  it('keeps a compact entry rail with Results and skills', () => {
    const html = markup(true);
    expect(html).toContain('aria-label="Expand projects"');
    const labels = ['aria-label="Results"', 'aria-label="Skill library"'];
    for (const label of labels) expect(html).toContain(label);
    expect(labels.map(label => html.indexOf(label))).toEqual(labels.map(label => html.indexOf(label)).sort((a, b) => a - b));
    expect(html).not.toContain('id="daemon-search"');
  });
  it.each([false, true])('opens the learning feed from the sidebar entry (compact=%s)', (compact) => {
    const onOpen = vi.fn();
    let view: ReturnType<typeof create>;
    act(() => { view = create(<LearningLibraryEntry onOpen={onOpen} compact={compact} />); });
    try {
      expect(view!.root.findAllByType('button')).toHaveLength(1);
      act(() => view!.root.findByType('button').props.onClick());
      expect(onOpen).toHaveBeenCalledExactlyOnceWith('feed');
    } finally { act(() => view!.unmount()); }
  });
});
