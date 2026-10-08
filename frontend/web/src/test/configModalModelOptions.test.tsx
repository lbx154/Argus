import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ConfigSnapshot } from '../api';
import { ConfigModal } from '../components/InfoModals';

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const textOf = (node: { children: unknown[] }): string =>
  node.children.map(child => (typeof child === 'string' ? child : textOf(child as { children: unknown[] }))).join('');

it('names what auto resolves to, marks a configured model the backend does not offer, and flags an offline list', async () => {
  const config: ConfigSnapshot = {
    schema_version: 1, generated_at_utc: '', roles: [], how_to_change: [],
    operator_knobs: [{ name: 'ARGUS_SKILL_MODEL', value: 'stale-id' } as ConfigSnapshot['operator_knobs'][number]],
    model_options: [
      { model: 'newest-9', source: 'catalog', offline: true },
      { model: 'stale-id', source: 'current', invalid: true },
    ],
    model_auto_resolves_to: 'newest-9',
  };
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' } });
  vi.spyOn(api, 'config').mockResolvedValue(config);
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['config', 'one'], config);
  await act(async () => {
    renderer = create(<QueryClientProvider client={client!}><ConfigModal sid="one" open onClose={() => {}} /></QueryClientProvider>);
  });
  const select = renderer!.root.findByProps({ 'aria-label': 'Model' });
  const labels = select.findAllByType('option').map(textOf);
  expect(labels[0]).toBe('auto → newest-9');
  expect(labels).toContain('stale-id (unavailable)');
  expect(labels).toContain('newest-9');
  expect(renderer!.root.findAllByProps({ 'data-model-offline': true })).toHaveLength(1);
});
