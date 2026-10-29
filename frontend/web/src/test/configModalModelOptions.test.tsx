import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ConfigSnapshot } from '../api';
import { AdvisorSettings } from '../components/AdvisorSettings';
import { ConfigModal } from '../components/InfoModals';
import { ModelPicker } from '../components/ModelPicker';
import { Capabilities } from '../lib/capabilities';

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const textOf = (node: { children: unknown[] }): string =>
  node.children.map(child => (typeof child === 'string' ? child : textOf(child as { children: unknown[] }))).join('');

const baseConfig = (): ConfigSnapshot => ({
  schema_version: 1, generated_at_utc: '', how_to_change: [], operator_knobs: [],
  roles: ['manager', 'planner', 'engineer', 'reviewer'].map(role => ({
    role, backend: 'pi', backend_label: 'Pi', backend_source: 'persisted', model: 'm-1', model_source: 'persisted',
    reasoning_effort: null, reasoning_effort_source: 'default', description: '',
  })),
  backends: { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' },
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

it('offers the backends the server lists, and one way to choose the model: the chooser', async () => {
  await mount(baseConfig());
  const select = renderer!.root.findByProps({ 'aria-label': 'Backend' });
  expect(select.props.value).toBe('pi');
  expect(select.findAllByType('option').map(option => [option.props.value, textOf(option as never)]))
    .toEqual([['codex', 'Codex'], ['claude', 'Claude Code'], ['copilot', 'Copilot CLI'], ['pi', 'Pi']]);
  expect(renderer!.root.findByProps({ 'data-apply-backend': true }).props.disabled).toBe(true);
  expect(renderer!.root.findAllByProps({ 'aria-label': 'Model' })).toHaveLength(0);
  expect(textOf(renderer!.root.findByProps({ 'data-current-model': true }) as never)).toBe('m-1');
  await act(async () => { renderer!.root.findByProps({ 'data-open-model-picker': true }).props.onClick(); });
  expect(renderer!.root.findByType(ModelPicker).props.open).toBe(true);
});

it('offers the advisor only where the server has it', async () => {
  vi.spyOn(api, 'advisorSettings').mockReturnValue(new Promise(() => {}));
  for (const [declared, shown] of [[['advisor.config.v1'], true], [['manager.sse.v1'], false]] as const) {
    const config = baseConfig();
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' } });
    vi.spyOn(api, 'config').mockResolvedValue(config);
    client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
    client.setQueryData(['config', 'one'], config);
    await act(async () => {
      renderer = create(<QueryClientProvider client={client!}><Capabilities.Provider value={declared}>
        <ConfigModal sid="one" open onClose={() => {}} />
      </Capabilities.Provider></QueryClientProvider>);
    });
    await act(async () => { renderer!.root.findByProps({ 'aria-controls': 'config-advanced-settings' }).props.onClick(); });
    expect(renderer!.root.findAllByType(AdvisorSettings)).toHaveLength(shown ? 1 : 0);
    act(() => renderer!.unmount());
  }
  renderer = undefined;
});
