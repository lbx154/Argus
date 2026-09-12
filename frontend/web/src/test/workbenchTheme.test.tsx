import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useWorkbenchTheme } from '../useWorkbenchTheme';

const storage = new Map<string, string>();
const listeners = new Map<string, Set<(event: unknown) => void>>();
let renderer: ReactTestRenderer | undefined;
let current: ReturnType<typeof useWorkbenchTheme>;
function Probe() {
  current = useWorkbenchTheme();
  return null;
}

beforeEach(() => {
  storage.clear(); listeners.clear();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  const browser = {
    location: { href: 'https://argus.test/admin/data', search: '' },
    history: { replaceState: vi.fn() },
    parent: null as unknown,
    matchMedia: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    addEventListener: (name: string, callback: (event: unknown) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name)!.add(callback);
    },
    removeEventListener: (name: string, callback: (event: unknown) => void) => listeners.get(name)?.delete(callback),
  };
  browser.parent = browser;
  vi.stubGlobal('window', browser);
  vi.stubGlobal('document', { documentElement: { dataset: {} } });
});
afterEach(() => {
  if (renderer) act(() => renderer!.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

it('uses the workbench preference and publishes changes for the other surface', () => {
  storage.set('argus.theme', 'dark');
  act(() => { renderer = create(<Probe />); });
  expect(current.themeMode).toBe('dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  act(() => current.cycleTheme());
  expect(current.themeMode).toBe('light');
  expect(storage.get('argus.theme')).toBe('light');
  expect(document.documentElement.dataset.theme).toBe('light');
});

it('updates an open administrator view when the user workbench changes theme', () => {
  act(() => { renderer = create(<Probe />); });
  expect(current.themeMode).toBe('light');
  storage.set('argus.theme', 'dark');
  act(() => { for (const callback of listeners.get('storage') ?? []) callback({ key: 'argus.theme' }); });
  expect(current.themeMode).toBe('dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  act(() => renderer!.unmount()); renderer = undefined;
  expect(listeners.get('storage')?.size).toBe(0);
});
