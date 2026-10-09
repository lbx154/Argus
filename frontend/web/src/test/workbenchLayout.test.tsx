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
});
