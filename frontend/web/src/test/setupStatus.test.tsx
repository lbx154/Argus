import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QRCodeSVG } from 'qrcode.react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type SetupStatus as Status } from '../api';
import { SetupStatus } from '../components/SetupStatus';

let view: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => view?.unmount()); view = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const status: Status = {
  backends: [
    { backend: 'codex', installed: true, version: 'codex 1.2.3', detail: '' },
    { backend: 'claude', installed: false, version: null, detail: 'not found on PATH' },
    { backend: 'pi', installed: true, version: null, detail: 'version probe failed' },
  ],
  sandbox: { bubblewrap: false, platform: 'linux' },
  mobile_origins: ['http://192.168.1.5:23456', 'http://10.1.2.3:23456'],
};
async function mount(data = status, open = true) {
  vi.stubGlobal('localStorage', { getItem: () => 'existing token & value' });
  vi.stubGlobal('window', { location: { search: '' } });
  vi.spyOn(api, 'setup').mockResolvedValue(data);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } } });
  if (open) await client.prefetchQuery({ queryKey: ['setup-status'], queryFn: () => api.setup() });
  await act(async () => { view = create(<QueryClientProvider client={client!}><SetupStatus open={open} /></QueryClientProvider>); });
}
const text = () => JSON.stringify(view!.toJSON());

it('does no setup request while closed and probes once when settings opens', async () => {
  await mount(status, false);
  expect(api.setup).not.toHaveBeenCalled();
  await act(async () => view!.update(<QueryClientProvider client={client!}><SetupStatus open /></QueryClientProvider>));
  expect(api.setup).toHaveBeenCalledTimes(1);
});

it('shows installation, version failures and the failed sandbox probe as facts', async () => {
  await mount();
  expect(text()).toContain('Installed');
  expect(text()).toContain('Not installed');
  expect(text()).toContain('codex 1.2.3');
  expect(text()).toContain('version probe failed');
  expect(text()).toContain('OS isolation is not confirmed');
});

it('encodes the existing token with a reachable host and lets the user choose an interface', async () => {
  await mount();
  const expected = new URL('/?token=existing+token+%26+value', status.mobile_origins[0]);
  expect(view!.root.findByType(QRCodeSVG).props.value).toBe(expected.href);
  expect(view!.root.findByProps({ 'data-mobile-url': true }).props.href).toBe(expected.href);
  await act(async () => view!.root.findByType('select').props.onChange({ target: { value: status.mobile_origins[1] } }));
  expect(new URL(view!.root.findByType(QRCodeSVG).props.value).host).toBe('10.1.2.3:23456');
  expect(api.setup).toHaveBeenCalledTimes(1);
});

it('offers no phone QR code for a loopback-only service', async () => {
  await mount({ ...status, sandbox: { bubblewrap: true, platform: 'linux' }, mobile_origins: [] });
  expect(view!.root.findAllByType(QRCodeSVG)).toHaveLength(0);
  expect(text()).toContain('launch probe passed');
});
