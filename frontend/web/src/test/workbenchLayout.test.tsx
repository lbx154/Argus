import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useWorkbenchLayout } from '../useWorkbenchLayout';

vi.mock('../useWorkbenchTheme', () => ({
  useWorkbenchTheme: () => ({ themeMode: 'light', themeStyle: 'standard', cycleTheme: vi.fn() }),
}));

let tree: ReactTestRenderer;
let layout: ReturnType<typeof useWorkbenchLayout>;
let storage: Map<string, string>;
function Harness() {
  layout = useWorkbenchLayout();
  return <div data-view={layout.workspaceView} />;
}

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal('window', { location: { search: '' },
    innerWidth: 1440, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  });
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value) });
});
afterEach(() => { act(() => tree?.unmount()); vi.unstubAllGlobals(); });
const mount = () => act(() => { tree = create(<Harness />); });

describe('conversation as the starting view', () => {
  it('shows the conversation and results without requiring the user to find a drawer', () => {
    mount();
    expect(layout.workspaceView).toBe('activity');
  });

  it('keeps an explicitly chosen map and remembers a new choice', () => {
    storage.set('argus.workspace.view.v3', 'map');
    mount();
    expect(layout.workspaceView).toBe('map');
    act(() => layout.setWorkspaceView('activity'));
    expect(storage.get('argus.workspace.view.v3')).toBe('activity');
    expect(layout.workspaceView).toBe('activity');
  });

  it('respects an explicit map link and the map presentation mode', () => {
    window.location.search = '?view=map';
    mount();
    expect(layout.workspaceView).toBe('map');
    act(() => tree.unmount());
    window.location.search = '?kiosk=1';
    mount();
    expect(layout.workspaceView).toBe('map');
  });
  it('routes removed mission links and saved choices to the conversation', () => {
    storage.set('argus.workspace.view.v3', 'map');
    window.location.search = '?view=mission';
    mount();
    expect(layout.workspaceView).toBe('activity');
    act(() => tree.unmount());
    window.location.search = '';
    storage.set('argus.workspace.view.v3', 'mission');
    mount();
    expect(layout.workspaceView).toBe('activity');
  });
});

describe('full canvas map', () => {
  it('makes Ctrl/Cmd+B on the full canvas map unfold the project list instead of silently flipping the saved choice', () => {
    storage.set('argus.workspace.view.v3', 'map');
    mount();
    expect(layout.mapImmersive).toBe(true);
    expect(layout.sidebarShown).toBe(false);
    act(() => layout.toggleSidebar());
    expect(layout.mapImmersive).toBe(false);
    expect(layout.sidebarShown).toBe(true);
    expect(storage.get('argus.map.fullscreen.v1')).toBe('0');
    expect(storage.get('argus.sidebar.expanded.v4')).toBe('true');
    act(() => layout.toggleSidebar());
    expect(layout.sidebarShown).toBe(false);
    expect(storage.get('argus.sidebar.expanded.v4')).toBe('false');
  });

  it('sizes the results panel against the 56px rail the full canvas map shows', () => {
    storage.set('argus.workspace.view.v3', 'map');
    storage.set('argus.sidebar.width.v2', '400');
    window.innerWidth = 1300;
    mount();
    const shown = layout.rightWidth;
    act(() => layout.openPreview());
    const onRail = layout.rightWidth;
    act(() => tree.unmount());
    storage.set('argus.map.fullscreen.v1', '0');
    storage.set('argus.preview.width.v2', String(shown));
    mount();
    act(() => layout.openPreview());
    expect(onRail).toBeGreaterThan(layout.rightWidth);
  });

  it('offers no full canvas toggle in presentation mode, where it could not take effect', () => {
    window.location.search = '?kiosk=1';
    mount();
    expect(layout.workspaceView).toBe('map');
    expect(layout.mapImmersive).toBe(false);
    expect(layout.setMapFull).toBeUndefined();
  });
});

describe('results visibility with a saved desktop preference', () => {
  it.each([1024, 390])('starts the %s px reading view closed without losing the saved preference', width => {
    window.innerWidth = width;
    storage.set('argus.preview.expanded.v6', 'true');
    mount();
    expect(layout.mobileView).toBe('activity');
    expect(layout.rightPanelVisible).toBe(false);
    expect(storage.get('argus.preview.expanded.v6')).toBe('true');
    act(() => layout.openPreview());
    expect(layout.rightPanelVisible).toBe(true);
    expect(layout.mobileView).toBe('preview');
    act(() => layout.setMobileView('activity'));
    expect(layout.rightPanelVisible).toBe(false);
    expect(storage.get('argus.preview.expanded.v6')).toBe('true');
  });

  it('restores a wide results panel and keeps it out of an unopened map', () => {
    storage.set('argus.preview.expanded.v6', 'true');
    mount();
    expect(layout.rightPanelVisible).toBe(true);
    act(() => layout.setWorkspaceView('map'));
    expect(layout.rightPanelVisible).toBe(false);
    act(() => layout.openPreview());
    expect(layout.rightPanelVisible).toBe(true);
  });
});
