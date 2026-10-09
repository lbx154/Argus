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

const baseConfig = (): ConfigSnapshot => ({
  schema_version: 1, generated_at_utc: '', roles: [], how_to_change: [],
  operator_knobs: [
    { name: 'ARGUS_SKILL_RUNNER_BACKEND', value: 'pi' } as ConfigSnapshot['operator_knobs'][number],
    { name: 'ARGUS_SKILL_MODEL', value: 'auto' } as ConfigSnapshot['operator_knobs'][number],
  ],
  model_options: [{ model: 'm-1', source: 'catalog' }],
});

async function mount(config: ConfigSnapshot, snapshot?: unknown) {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' } });
  vi.spyOn(api, 'config').mockResolvedValue(config);
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['config', 'one'], config);
  if (snapshot) client.setQueryData(['snapshot', 'one'], snapshot);
  await act(async () => {
    renderer = create(<QueryClientProvider client={client!}><ConfigModal sid="one" open onClose={() => {}} /></QueryClientProvider>);
  });
}

it('saves a backend switch only on Apply, never on the dropdown change', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount(baseConfig());
  const select = renderer!.root.findByProps({ 'aria-label': 'Backend' });
  await act(async () => { select.props.onChange({ target: { value: 'copilot' } }); });
  expect(setConfig).not.toHaveBeenCalled();
  const apply = renderer!.root.findByProps({ 'data-apply-backend': true });
  expect(apply.props.disabled).toBe(false);
  await act(async () => { apply.props.onClick(); });
  expect(setConfig).toHaveBeenCalledWith('one', 'ARGUS_SKILL_RUNNER_BACKEND', 'copilot');
});

it('changing only the model sends only the model, and the backend shows where it comes from', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  const config = baseConfig();
  config.operator_knobs[0] = { name: 'ARGUS_SKILL_RUNNER_BACKEND', value: 'copilot', source: 'env' } as ConfigSnapshot['operator_knobs'][number];
  await mount(config);
  expect(renderer!.root.findByProps({ 'aria-label': 'Backend' }).props.value).toBe('copilot');
  const source = renderer!.root.findByProps({ 'data-backend-source': 'env' });
  expect(textOf(source as unknown as { children: unknown[] })).toBe('set by the server environment');
  const model = renderer!.root.findByProps({ 'aria-label': 'Model' });
  await act(async () => { model.props.onChange({ target: { value: 'm-1' } }); });
  const applyModel = renderer!.root.findAll(node => node.type === 'button' && node.props.onClick && !node.props['data-apply-backend'] && textOf(node as unknown as { children: unknown[] }) === 'Apply')[0];
  await act(async () => { applyModel.props.onClick(); });
  expect(setConfig).toHaveBeenCalledTimes(1);
  expect(setConfig).toHaveBeenCalledWith('one', 'ARGUS_SKILL_MODEL', 'm-1', true);
  const applyBackend = renderer!.root.findByProps({ 'data-apply-backend': true });
  expect(applyBackend.props.disabled).toBe(true);
  await act(async () => { applyBackend.props.onClick(); });
  expect(setConfig).toHaveBeenCalledTimes(1);
});

it('replaces a stale saved backend hidden by the environment and says what changes', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  const config = baseConfig();
  config.operator_knobs[0] = { name: 'ARGUS_SKILL_RUNNER_BACKEND', value: 'copilot', source: 'env', saved: 'codex' } as ConfigSnapshot['operator_knobs'][number];
  await mount(config);
  expect(renderer!.root.findByProps({ 'aria-label': 'Backend' }).props.value).toBe('copilot');
  const change = renderer!.root.findByProps({ 'data-backend-change': true });
  expect(textOf(change as unknown as { children: unknown[] })).toContain('replaces the saved');
  const applyBackend = renderer!.root.findByProps({ 'data-apply-backend': true });
  expect(applyBackend.props.disabled).toBe(false);
  await act(async () => { applyBackend.props.onClick(); });
  expect(setConfig).toHaveBeenCalledWith('one', 'ARGUS_SKILL_RUNNER_BACKEND', 'copilot');
});
