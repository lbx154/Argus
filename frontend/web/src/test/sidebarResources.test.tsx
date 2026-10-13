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
  onOpenLearning: vi.fn(), onOpenVerticals: vi.fn(), onNew: vi.fn(), loading: false,
  onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(),
};
function markup(collapsed = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><Sidebar {...props} collapsed={collapsed} /></QueryClientProvider>); }
  finally { client.clear(); }
}
describe('top resource navigation', () => {
  it('puts plugins, skill library and vertical store directly above project search in order', () => {
    const html = markup();
    expect(html.indexOf('data-sidebar-resources')).toBeGreaterThan(0);
    const labels = ['aria-label="Plugins"', 'aria-label="Knowledge &amp; skills"', 'aria-label="Domain store"'];
    for (const label of labels) {
      expect(html).toContain(label);
      expect(html.indexOf(label)).toBeLessThan(html.indexOf('id="daemon-search"'));
      expect(html.indexOf(label)).toBeLessThan(html.indexOf('Synthetic session A'));
      expect(html.split(label)).toHaveLength(2);
    }
    expect(labels.map(label => html.indexOf(label))).toEqual(labels.map(label => html.indexOf(label)).sort((a, b) => a - b));
    expect(html).not.toContain('More tools and resources');
    expect(html).not.toContain('sidebar-tools');
    expect(html).not.toContain('aria-label="Knowledge base"');
    expect(html).not.toContain('aria-label="Learning feed"');
    expect(html).not.toContain('aria-label="Across projects"');
    expect(html.indexOf('aria-label="Open settings"')).toBeGreaterThan(html.indexOf('Synthetic session A'));
  });
  it('keeps the existing compact entry rail and collapse controls', () => {
    const html = markup(true);
    expect(html).toContain('aria-label="Expand sessions"');
    const labels = ['aria-label="Plugins"', 'aria-label="Knowledge &amp; skills"', 'aria-label="Domain store"'];
    for (const label of labels) expect(html).toContain(label);
    expect(labels.map(label => html.indexOf(label))).toEqual(labels.map(label => html.indexOf(label)).sort((a, b) => a - b));
    expect(html).not.toContain('aria-label="Learning feed"');
    expect(html).not.toContain('aria-label="Knowledge base"');
    expect(html).not.toContain('id="daemon-search"');
  });
  it('opens reusable material rather than the learning feed from the sidebar entry', () => {
    const onOpen = vi.fn();
    let view: ReturnType<typeof create>;
    act(() => { view = create(<LearningLibraryEntry onOpen={onOpen} />); });
    try {
      expect(view!.root.findAllByType('button')).toHaveLength(1);
      act(() => view!.root.findByType('button').props.onClick());
      expect(onOpen).toHaveBeenCalledExactlyOnceWith('global');
    } finally { act(() => view!.unmount()); }
  });
});
