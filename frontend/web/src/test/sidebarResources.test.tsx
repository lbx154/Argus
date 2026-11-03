import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { act, create } from 'react-test-renderer';
import { Sidebar } from '../components/Sidebar';
import { LearningLibraryEntry } from '../components/LearningLibrary';

const props = {
  projects: [{ id: 's-A', label: 'Synthetic session A', display_name: 'Synthetic session A',
    last_active: 1, daemon_alive: false, daemon_pid: null, workdir: '/synthetic' }],
  activeId: 's-A', localCwd: '/synthetic', onSelect: vi.fn(), onManage: vi.fn(), onOpenPanel: vi.fn(),
  onOpenLearning: vi.fn(), onSearch: vi.fn(), onNew: vi.fn(), loading: false,
  onToggleCollapse: vi.fn(), themeMode: 'light' as const, onCycleTheme: vi.fn(),
};
function markup(collapsed = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  try { return renderToStaticMarkup(<QueryClientProvider client={client}><Sidebar {...props} collapsed={collapsed} /></QueryClientProvider>); }
  finally { client.clear(); }
}
describe('top resource navigation', () => {
  it('starts with the projects: search and new on one row, skills and settings at the bottom', () => {
    const html = markup();
    expect(html).not.toContain('data-sidebar-results');
    expect(html).not.toContain('data-sidebar-advanced');
    expect(html.indexOf('aria-label="Search history and results"')).toBeLessThan(html.indexOf('id="daemon-search"'));
    expect(html.split('aria-label="Skill library"')).toHaveLength(2);
    expect(html.indexOf('aria-label="Skill library"')).toBeGreaterThan(html.indexOf('Synthetic session A'));
    expect(html.indexOf('aria-label="Open settings"')).toBeGreaterThan(html.indexOf('Synthetic session A'));
    expect(html).not.toContain('aria-label="Plugins"');
  });
  it('keeps a compact entry rail with skills', () => {
    const html = markup(true);
    expect(html).toContain('aria-label="Expand projects"');
    expect(html).toContain('aria-label="Skill library"');
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
