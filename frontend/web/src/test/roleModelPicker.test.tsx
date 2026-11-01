import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ConfigSnapshot } from '../api';
import { ModelPicker } from '../components/ModelPicker';

let renderer: ReactTestRenderer | undefined, client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const config: ConfigSnapshot = { revision: 3, schema_version: 1, generated_at_utc: '', how_to_change: [], operator_knobs: [],
  backends: { codex: 'Codex', pi: 'Pi', dsh: 'DeepSeek Harness' },
  roles: ['manager', 'planner', 'engineer', 'reviewer'].map(role => ({ role, backend: 'codex', backend_label: 'Codex', backend_source: 'persisted',
    model: 'old-model', model_source: 'persisted', reasoning_effort: null, reasoning_effort_source: 'default', description: '' })) };
async function mount(selectedConfig = config) {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8799' } });
  vi.spyOn(api, 'config').mockResolvedValue(selectedConfig);
  vi.spyOn(api, 'modelChoices').mockImplementation(async (_sid, _signal, selected) => ({ backend: selected?.backend ?? 'codex', installed: true,
    options: [{ model: `${selected?.backend ?? 'codex'}/sol`, name: 'Sol', default: false, efforts: ['low', 'high'], price: null },
      { model: `${selected?.backend ?? 'codex'}/astra`, name: 'Astra', default: true, price: null }] }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(['config', 'one'], selectedConfig);
  await act(async () => { renderer = create(<QueryClientProvider client={client!}><ModelPicker sid="one" open onClose={() => {}} /></QueryClientProvider>); });
}
const by = (name: string, value: unknown = true) => renderer!.root.findByProps({ [name]: value });
const contents = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : contents(child)).join('');
const catalogReady = async (backend: string) => {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer!.root.findAllByProps({ 'data-role-model': `${backend}/sol` })).toHaveLength(1);
  });
};

it('opens four independent roles and selects CLI then model without inference, saving only changed roles atomically', async () => {
  const probe = vi.spyOn(api, 'probeModel');
  const save = vi.spyOn(api, 'setRoleConfig').mockResolvedValue({ ok: true, revision: 4, effective: 'next_turn' });
  await mount();
  expect(renderer!.root.findAll(node => node.type === 'button' && node.props['data-role-tab'])).toHaveLength(4);
  expect(probe).not.toHaveBeenCalled();
  await act(async () => { by('data-role-cli').props.onChange({ target: { value: 'pi' } }); });
  expect(by('data-role-model-id').props.value).toBe('');
  await catalogReady('pi');
  await act(async () => { by('data-role-model', 'pi/sol').findByType('input').props.onChange(); });
  await act(async () => { by('data-role-tab', 'engineer').props.onClick(); });
  expect(by('data-role-cli').props.value).toBe('codex');
  expect(by('data-role-model-id').props.value).toBe('old-model');
  await act(async () => { by('data-role-cli').props.onChange({ target: { value: 'dsh' } }); });
  await catalogReady('dsh');
  await act(async () => { by('data-role-model', 'dsh/astra').findByType('input').props.onChange(); });
  await act(async () => { by('data-save-role-config').props.onClick(); });
  expect(save).toHaveBeenCalledWith('one', [{ role: 'manager', backend: 'pi', model: 'pi/sol', effort: '' },
    { role: 'engineer', backend: 'dsh', model: 'dsh/astra', effort: '' }], 3);
  expect(probe).not.toHaveBeenCalled();
});

it('remembers a role’s per-CLI draft, filters models and explicitly tests the selected role/CLI/model', async () => {
  const probe = vi.spyOn(api, 'probeModel').mockResolvedValue({ model: 'pi/sol', ok: true, error: '', duration_ms: 5 });
  await mount();
  await act(async () => { by('data-role-tab', 'engineer').props.onClick(); });
  await act(async () => { by('data-role-cli').props.onChange({ target: { value: 'pi' } }); });
  await catalogReady('pi');
  await act(async () => { by('data-role-model', 'pi/sol').findByType('input').props.onChange(); });
  await act(async () => { by('data-role-cli').props.onChange({ target: { value: 'dsh' } }); });
  await act(async () => { by('data-role-cli').props.onChange({ target: { value: 'pi' } }); });
  expect(by('data-role-model-id').props.value).toBe('pi/sol');
  await act(async () => { by('data-role-model-search').props.onChange({ target: { value: 'Sol' } }); });
  expect(renderer!.root.findAllByProps({ 'data-role-model': 'pi/astra' })).toHaveLength(0);
  await act(async () => { by('data-test-role-model').props.onClick(); });
  expect(probe).toHaveBeenCalledWith('one', 'pi/sol', { role: 'engineer', backend: 'pi', effort: '' });
});

it('never silently retries a revision conflict or overwrites another window’s configuration', async () => {
  const save = vi.spyOn(api, 'setRoleConfig').mockRejectedValue(new Error('Settings changed in another window'));
  await mount();
  await act(async () => { by('data-role-model-id').props.onChange({ target: { value: 'custom-model' } }); });
  await act(async () => { by('data-save-role-config').props.onClick(); });
  expect(save).toHaveBeenCalledOnce();
  expect(by('data-role-model-id').props.value).toBe('custom-model');
  expect(renderer!.root.findAll(node => node.props.role === 'alert').length).toBeGreaterThan(0);
});

it('filters executable choices without installed badges and never changes a missing saved route', async () => {
  await mount({ ...config, available_backends: { pi: 'Pi', dsh: 'DeepSeek Harness' } });
  const select = by('data-role-cli');
  expect(select.findAllByType('option').map(option => option.props.value)).toEqual(['', 'pi', 'dsh']);
  expect(select.props.value).toBe('');
  expect(by('data-role-model-id').props.value).toBe('old-model');
  expect(JSON.stringify(renderer!.toJSON())).not.toContain('Installed');
  expect(by('data-save-role-config').props.disabled).toBe(true);
});

it('renders each of four routes with its own quote and does not infer a price for unknown models', async () => {
  const backends = ['codex', 'claude', 'pi', 'dsh'];
  const custom = { ...config, backends: Object.fromEntries(backends.map(name => [name, name])),
    roles: config.roles.map((row, i) => ({ ...row, backend: backends[i], model: `${backends[i]}/sol` })) };
  await mount(custom);
  await vi.waitFor(() => expect(client!.isFetching()).toBe(0));
  for (const [i, role] of config.roles.entries()) {
    await act(async () => { client!.setQueryData(['role-model-choices', 'one', role.role, backends[i]], {
      backend: backends[i], options: [{ model: `${backends[i]}/sol`, default: false, price: null,
        pricing: { source: 'cli-catalog', rates: { input: i + 1, output: 10 + i }, checked_at: '2026-11-01T00:00:00Z', stale: false } }],
    }); });
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(contents(by('data-role-price', role.role))).toContain(`$${i + 1} / $${10 + i}`);
    });
  }
  await act(async () => { by('data-role-model-id').props.onChange({ target: { value: 'unknown-custom-model' } }); });
  expect(contents(by('data-role-price', 'manager'))).toContain('Unknown rates');
  expect(contents(by('data-role-price', 'engineer'))).toContain('$3 / $12');
});
