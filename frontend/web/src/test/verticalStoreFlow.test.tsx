import type { ReactNode } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../../core/src/http';
import type { VerticalRow, VerticalsPayload } from '../../../core/src/types';
import { api } from '../api';
import { VerticalStore } from '../components/VerticalStore';

// The store renders into document.body through a portal; the test renderer
// has no DOM, so the portal becomes an ordinary subtree and window/document
// are the few members the component touches.
vi.mock('react-dom', async (importOriginal) => ({ ...(await importOriginal<typeof import('react-dom')>()), createPortal: (node: ReactNode) => node }));
vi.mock('../i18n', () => ({
  useI18n: () => ({ locale: 'en', t: (key: string, vars?: Record<string, string | number>) => vars ? `${key} ${Object.values(vars).join(', ')}` : key }),
}));
vi.mock('../api', () => ({
  api: { meta: vi.fn(), verticals: vi.fn(), refreshVerticalCatalog: vi.fn(), manageVertical: vi.fn(), verticalOperation: vi.fn() },
  VERTICAL_STORE_CAPABILITY: 'verticals.store.v1',
}));

const row = (over: Partial<VerticalRow> & { name: string }): VerticalRow => ({
  purpose: 'Purpose', purpose_zh: null, kind: 'available', version: '1.0.0', installed_version: null, enabled: false,
  update_available: false, requires: [], shared: [], python_requirements: [], missing_python: [], tags: [], size_bytes: null,
  used_by: [], operation: null, managed_by_host: false, actions: ['install'], ...over,
});
const kernel = row({ name: 'kernel_engineering', kind: 'installed', enabled: true, installed_version: '1.2.0', actions: ['disable', 'uninstall'], used_by: ['s-1', 's-2'] });
const materials = row({ name: 'materials', kind: 'available', actions: ['install'] });
const payload: VerticalsPayload = {
  verticals: [kernel, materials],
  catalog: { source: 'bundled', fetched_at: null, release_tag: null, error: null },
  host: { managed_by_host: false, store_root: '/store' },
};
const runningPayload: VerticalsPayload = {
  ...payload,
  verticals: [kernel, { ...materials, actions: [], operation: { status: 'running', action: 'install', progress: 0.25, message: 'Fetching', started: 'now', finished: null } }],
};
const meta = (capabilities: string[]) => ({ capabilities } as never);
const conflict = new ApiError('POST /api/verticals/kernel_engineering/manage/uninstall → 409: used by projects s-1, s-2', 409, 'POST', '/api/verticals/kernel_engineering/manage/uninstall', '', 'used by projects s-1, s-2');

let renderer: ReactTestRenderer | undefined;
let keyListeners: Array<(event: { key: string }) => void>;
const onClose = vi.fn();
const text = (node: ReactTestInstance): string => node.children.map((child) => typeof child === 'string' ? child : text(child)).join('');
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(1); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const root = () => renderer!.root;
const byTestId = (id: string) => root().findAllByProps({ 'data-testid': id }).filter((node) => typeof node.type === 'string');
const action = (name: string) => root().findAllByProps({ 'data-action': name }).filter((node) => typeof node.type === 'string');
const click = (node: ReactTestInstance) => act(() => { node.props.onClick(); });

async function mount(open = true) {
  await act(async () => { renderer = create(<VerticalStore open={open} onClose={onClose} />); });
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  keyListeners = [];
  vi.stubGlobal('window', {
    setInterval: (fn: () => void, ms: number) => setInterval(fn, ms),
    clearInterval: (id: ReturnType<typeof setInterval>) => clearInterval(id),
    setTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
    addEventListener: (_type: string, listener: (event: { key: string }) => void) => { keyListeners.push(listener); },
    removeEventListener: (_type: string, listener: (event: { key: string }) => void) => { keyListeners = keyListeners.filter((l) => l !== listener); },
  });
  vi.stubGlobal('document', { body: {}, getElementById: () => null });
  vi.mocked(api.meta).mockResolvedValue(meta(['verticals.store.v1']));
  vi.mocked(api.verticals).mockResolvedValue(structuredClone(payload));
  vi.mocked(api.manageVertical).mockResolvedValue({ name: 'x', action: 'enable', operation: null });
  vi.mocked(api.refreshVerticalCatalog).mockResolvedValue(structuredClone(payload));
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('vertical store lifecycle', () => {
  it('probes the capability, reads the list once open and renders a card per row', async () => {
    await mount();
    expect(api.meta).toHaveBeenCalledTimes(1);
    expect(api.verticals).toHaveBeenCalledTimes(1);
    expect(byTestId('vertical-kernel_engineering')).toHaveLength(1);
    expect(byTestId('vertical-materials')).toHaveLength(1);
    expect(byTestId('vertical-store-too-old')).toHaveLength(0);
    expect(root().findByProps({ role: 'dialog' }).props['aria-modal']).toBe('true');
  });

  it('renders nothing and requests nothing while closed', async () => {
    await mount(false);
    expect(root().children).toHaveLength(0);
    expect(api.meta).not.toHaveBeenCalled();
    expect(api.verticals).not.toHaveBeenCalled();
  });

  it('polls every 4 s while idle and every 1.5 s while a job runs', async () => {
    await mount();
    expect(api.verticals).toHaveBeenCalledTimes(1);
    await advance(3_900);
    expect(api.verticals).toHaveBeenCalledTimes(1);
    await advance(200);
    expect(api.verticals).toHaveBeenCalledTimes(2);
    vi.mocked(api.verticals).mockResolvedValue(structuredClone(runningPayload));
    await advance(4_000);
    // The poll picks up the running job; switching rhythm reads once more right away.
    expect(api.verticals).toHaveBeenCalledTimes(4);
    expect(byTestId('vertical-progress')).toHaveLength(1);
    const before = vi.mocked(api.verticals).mock.calls.length;
    await advance(1_300);
    expect(api.verticals).toHaveBeenCalledTimes(before);
    await advance(1_500);
    expect(api.verticals).toHaveBeenCalledTimes(before + 1);
    await advance(1_500);
    expect(api.verticals).toHaveBeenCalledTimes(before + 2);
  });

  it('keeps following a running job slowly after closing so reopening shows the result', async () => {
    vi.mocked(api.verticals).mockResolvedValue(structuredClone(runningPayload));
    await mount();
    await act(async () => { renderer!.update(<VerticalStore open={false} onClose={onClose} />); });
    const before = vi.mocked(api.verticals).mock.calls.length;
    await advance(1_500);
    expect(api.verticals).toHaveBeenCalledTimes(before);
    await advance(2_500);
    expect(api.verticals).toHaveBeenCalledTimes(before + 1);
    vi.mocked(api.verticals).mockResolvedValue(structuredClone(payload));
    await advance(4_000);
    const settled = vi.mocked(api.verticals).mock.calls.length;
    await advance(10_000);
    expect(api.verticals).toHaveBeenCalledTimes(settled);
  });

  it('shows the too-old notice without requesting the list when the capability is missing', async () => {
    vi.mocked(api.meta).mockResolvedValue(meta(['daemon.status.protocol.v1']));
    await mount();
    expect(byTestId('vertical-store-too-old')).toHaveLength(1);
    expect(api.verticals).not.toHaveBeenCalled();
    await advance(10_000);
    expect(api.verticals).not.toHaveBeenCalled();
  });

  it('also treats a 404 on the list as a backend without the store', async () => {
    vi.mocked(api.verticals).mockRejectedValue(new ApiError('GET /api/verticals → 404', 404, 'GET', '/api/verticals'));
    await mount();
    expect(byTestId('vertical-store-too-old')).toHaveLength(1);
    expect(byTestId('vertical-store-error')).toHaveLength(0);
  });

  it('reports any other list failure at the top and keeps trying', async () => {
    vi.mocked(api.verticals).mockRejectedValue(new Error('boom'));
    await mount();
    expect(byTestId('vertical-store-error')).toHaveLength(1);
    expect(text(byTestId('vertical-store-error')[0])).toBe('verticals.loadFailed');
    vi.mocked(api.verticals).mockResolvedValue(structuredClone(payload));
    await advance(4_000);
    expect(byTestId('vertical-store-error')).toHaveLength(0);
    expect(byTestId('vertical-materials')).toHaveLength(1);
  });

  it('closes on Escape and from the close button', async () => {
    await mount();
    expect(keyListeners).toHaveLength(1);
    act(() => keyListeners[0]({ key: 'a' }));
    expect(onClose).not.toHaveBeenCalled();
    act(() => keyListeners[0]({ key: 'Escape' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await click(root().findByProps({ 'aria-label': 'verticals.close' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe('vertical store actions', () => {
  it('sends the action the button names and reloads the list', async () => {
    await mount();
    await click(action('install')[0]);
    await settle();
    expect(api.manageVertical).toHaveBeenCalledWith('materials', 'install', {});
    expect(api.verticals).toHaveBeenCalledTimes(2);
    expect(byTestId('vertical-failure')).toHaveLength(0);
  });

  it('uninstalls only after a second click', async () => {
    await mount();
    expect(byTestId('vertical-confirm-uninstall')).toHaveLength(0);
    await click(action('uninstall')[0]);
    expect(api.manageVertical).not.toHaveBeenCalled();
    expect(byTestId('vertical-confirm-uninstall')).toHaveLength(1);
    await click(byTestId('vertical-confirm-uninstall')[0]);
    await settle();
    expect(api.manageVertical).toHaveBeenCalledWith('kernel_engineering', 'uninstall', {});
  });

  it('shows the 409 detail on the card and re-sends with force from Remove anyway', async () => {
    vi.mocked(api.manageVertical).mockRejectedValueOnce(conflict);
    await mount();
    await click(action('uninstall')[0]);
    await click(byTestId('vertical-confirm-uninstall')[0]);
    await settle();
    expect(byTestId('vertical-failure')).toHaveLength(1);
    expect(text(byTestId('vertical-failure')[0])).toContain('used by projects s-1, s-2');
    expect(byTestId('vertical-store-error')).toHaveLength(0);
    const force = byTestId('vertical-failure')[0].findByType('button');
    expect(text(force)).toBe('verticals.removeAnyway');
    await click(force);
    await settle();
    expect(api.manageVertical).toHaveBeenLastCalledWith('kernel_engineering', 'uninstall', { force: true });
    expect(byTestId('vertical-failure')).toHaveLength(0);
  });

  it('does not offer force for a 409 on another action or for a plain failure', async () => {
    vi.mocked(api.manageVertical).mockRejectedValueOnce(new ApiError('POST … → 409: dependency research is disabled', 409, 'POST', '/p', '', 'dependency research is disabled'));
    await mount();
    await click(action('install')[0]);
    await settle();
    expect(text(byTestId('vertical-failure')[0])).toBe('dependency research is disabled');
    expect(byTestId('vertical-failure')[0].findAllByType('button')).toHaveLength(0);
    vi.mocked(api.manageVertical).mockRejectedValueOnce(new Error('network lost'));
    await click(action('install')[0]);
    await settle();
    expect(text(byTestId('vertical-failure')[0])).toBe('network lost');
  });

  it('refreshes the catalog on request and shows the refreshed payload', async () => {
    vi.mocked(api.refreshVerticalCatalog).mockResolvedValue({ ...structuredClone(payload), catalog: { source: 'https://fresh', fetched_at: '2026-09-14T09:00:00Z', release_tag: 'v0.4.0', error: null } });
    await mount();
    await click(byTestId('vertical-refresh-catalog')[0]);
    await settle();
    expect(api.refreshVerticalCatalog).toHaveBeenCalledTimes(1);
    expect(text(byTestId('vertical-catalog')[0])).toContain('verticals.catalogRelease v0.4.0');
  });

  it('shows a catalog refresh failure at the top with the service sentence', async () => {
    vi.mocked(api.refreshVerticalCatalog).mockRejectedValue(new ApiError('POST … → 502: upstream unreachable', 502, 'POST', '/p', '', 'upstream unreachable'));
    await mount();
    await click(byTestId('vertical-refresh-catalog')[0]);
    await settle();
    expect(text(byTestId('vertical-store-error')[0])).toBe('upstream unreachable');
  });
});
