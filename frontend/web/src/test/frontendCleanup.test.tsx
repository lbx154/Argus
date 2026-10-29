import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { api, adoptTokenFromUrl, PairingRequiredError } from '../api';
import { ActionNotice, type UiNotice } from '../components/ActionNotice';
import { ConnectionProblemBanner } from '../components/ConnectionProblemBanner';
import { DaemonManageModal } from '../components/DaemonManageModal';
import { useArtifacts } from '../hooks';
import { ResourceStatusView } from '../components/ResourceStatus';
import * as i18n from '../i18n';

vi.mock('../components/Modal', () => ({ Modal: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
let view: ReactTestRenderer | undefined;
afterEach(() => { act(() => view?.unmount()); view = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([false, true])('closes the rename dialog only when saving succeeds (%s)', async saved => {
  const close = vi.fn();
  const rename = vi.fn(async () => saved);
  act(() => { view = create(<DaemonManageModal open sid="p-fixture" name="Before" alive={false} resumable={false} busy={false}
    onClose={close} onRename={rename} onStart={async () => false} onDelete={async () => false} />); });
  act(() => view!.root.findByType('input').props.onChange({ target: { value: 'After' } }));
  await act(async () => view!.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  expect(rename).toHaveBeenCalledExactlyOnceWith('After');
  expect(close).toHaveBeenCalledTimes(saved ? 1 : 0);
});

it('keeps Undo available until it is chosen or dismissed, and disables duplicate clicks while restoring', () => {
  vi.useFakeTimers(); vi.stubGlobal('window', { setTimeout, clearTimeout });
  const close = vi.fn();
  const undo = vi.fn(() => new Promise<void>(() => {}));
  const notice: UiNotice = { id: 1, tone: 'success', message: 'Project moved to the trash.', action: { label: 'Undo', onClick: undo } };
  act(() => { view = create(<ActionNotice notice={notice} onClose={close} />); });
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(close).not.toHaveBeenCalled();
  const button = () => view!.root.findAllByType('button').find(node => node.children.includes('Undo'))!;
  act(() => button().props.onClick());
  expect(button().props.disabled).toBe(true);
  expect(undo).toHaveBeenCalledOnce();
});

it('reads an idle project’s artifacts once and refreshes only when invalidated', async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, 'artifacts').mockResolvedValue([]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  function Probe() { useArtifacts('p-fixture'); return null; }
  try {
    await act(async () => { view = create(<QueryClientProvider client={client}><Probe /></QueryClientProvider>); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => { await client.invalidateQueries({ queryKey: ['artifacts', 'p-fixture'] }); });
    expect(read).toHaveBeenCalledTimes(2);
  } finally { act(() => view?.unmount()); view = undefined; client.clear(); }
});

it.each(['en', 'zh-CN'] as const)('formats numeric CPU telemetry in %s', locale => {
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale, setLocale: vi.fn(), t: (key, vars) => i18n.translate(key, vars, locale) });
  const html = renderToStaticMarkup(<ResourceStatusView status={{ schema_version: 1, enforcement: 'advisory',
    accelerators: [{ kind: 'cpu', status: 'available', device_count: 128, load_average: 22.65, used_memory_gib: 192.4, total_memory_gib: 995.5 }] }} error="" />);
  expect(html).toContain(locale === 'zh-CN' ? '负载 22.65 · 内存已用 192.4 / 995.5 GiB' : 'Load 22.65 · Memory 192.4 of 995.5 GiB in use');
});

it('distinguishes a missing credential from an invalid token link even after the token leaves the address bar', () => {
  const location = new URL('http://argus.test/');
  vi.stubGlobal('window', { location, history: { replaceState: vi.fn(() => { location.search = ''; }) } });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: 'zh-CN', setLocale: vi.fn(), t: key => i18n.translate(key, {}, 'zh-CN') });
  expect(renderToStaticMarkup(<ConnectionProblemBanner error={new PairingRequiredError()} onRetry={() => {}} />)).toContain('这个浏览器还没有访问凭证');
  location.search = '?token=wrong';
  adoptTokenFromUrl();
  expect(location.search).toBe('');
  expect(renderToStaticMarkup(<ConnectionProblemBanner error={{ status: 401 }} onRetry={() => {}} />)).toContain('这个链接无效或已过期');
});
