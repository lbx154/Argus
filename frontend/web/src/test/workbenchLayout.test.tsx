import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useWorkbenchLayout } from '../useWorkbenchLayout';

vi.mock('../useWorkbenchTheme', () => ({
  useWorkbenchTheme: () => ({ themeMode: 'light', themeStyle: 'standard', cycleTheme: vi.fn() }),
}));

let tree: ReactTestRenderer;
let layout: ReturnType<typeof useWorkbenchLayout>;
let storage: Map<string, string>;
let reads: string[];
let writes: string[];
function Harness() {
  layout = useWorkbenchLayout();
  return <div data-view={layout.workspaceView} />;
}

beforeEach(() => {
  storage = new Map();
  reads = []; writes = [];
  vi.stubGlobal('window', { location: { search: '' },
    innerWidth: 1440, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  });
  vi.stubGlobal('localStorage', { getItem: (key: string) => { reads.push(key); return storage.get(key) ?? null; },
    setItem: (key: string, value: string) => { writes.push(key); storage.set(key, value); } });
});
afterEach(() => { act(() => tree?.unmount()); vi.unstubAllGlobals(); });
const mount = () => act(() => { tree = create(<Harness />); });

describe('conversation as the starting view', () => {
  it('shows the conversation and results without requiring the user to find a drawer', () => {
    mount();
    expect(layout.workspaceView).toBe('activity');
  });

  it('ignores a saved view and keeps choices only in this project', () => {
    storage.set('argus.workspace.view.v3', 'map');
    mount();
    expect(layout.workspaceView).toBe('activity');
    act(() => layout.setWorkspaceView('map'));
    expect(storage.get('argus.workspace.view.v3')).toBe('map');
    act(() => { window.location.search = '?project=other'; tree.update(<Harness />); });
    expect(layout.workspaceView).toBe('activity');
  });

  it('respects an explicit map link and the map presentation mode', () => {
    window.location.search = '?view=map';
    mount();
    expect(layout.workspaceView).toBe('map');
    act(() => tree.unmount());
    window.location.search = '?kiosk=1&view=map';
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
    window.location.search = '?view=map';
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
    window.location.search = '?view=map';
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
    window.location.search = '?kiosk=1&view=map';
    mount();
    expect(layout.workspaceView).toBe('map');
    expect(layout.mapImmersive).toBe(false);
    expect(layout.setMapFull).toBeUndefined();
  });
});

describe('results visibility belongs to the open project', () => {
  it.each([1440, 1024, 390])('starts closed at %s px and never reads or writes the old global switch', width => {
    window.innerWidth = width;
    storage.set('argus.preview.expanded.v6', 'true');
    mount();
    expect(layout.mobileView).toBe('activity');
    expect(layout.rightPanelVisible).toBe(false);
    act(() => layout.openPreview());
    expect(layout.rightPanelVisible).toBe(true);
    expect(layout.mobileView).toBe('preview');
    act(() => { layout.setRightPanelOpen(false); layout.setMobileView('activity'); });
    expect(layout.rightPanelVisible).toBe(false);
    expect(reads).not.toContain('argus.preview.expanded.v6');
    expect(writes).not.toContain('argus.preview.expanded.v6');
  });

  it.each([1440, 1024, 390])('closes on project switch and return at %s px, while retaining width', width => {
    window.innerWidth = width;
    window.location.search = '?project=first';
    storage.set('argus.preview.width.v2', '560');
    mount();
    expect(layout.rightWidth).toBe(560);
    act(() => layout.setRightWidth(600));
    expect(storage.get('argus.preview.width.v2')).toBe('600');
    act(() => layout.openPreview());
    expect(layout.rightPanelVisible).toBe(true);
    act(() => { window.location.search = '?project=second'; tree.update(<Harness />); });
    expect(layout.mobileView).toBe('activity');
    expect(layout.rightPanelVisible).toBe(false);
    act(() => layout.openPreview());
    act(() => { window.location.search = '?project=first'; tree.update(<Harness />); });
    expect(layout.rightPanelVisible).toBe(false);
    act(() => tree.unmount());
    mount();
    expect(layout.rightPanelVisible).toBe(false);
    expect(layout.rightWidth).toBe(Number(storage.get('argus.preview.width.v2')));
  });

  it('keeps the results panel out of an unopened map', () => {
    mount();
    act(() => layout.setWorkspaceView('map'));
    expect(layout.rightPanelVisible).toBe(false);
    act(() => layout.openPreview());
    expect(layout.rightPanelVisible).toBe(true);
  });
});
