import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ConfigSnapshot, type ModelChoices } from '../api';
import { ModelPicker } from '../components/ModelPicker';

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const textOf = (node: { children: unknown[] }): string =>
  node.children.map(child => (typeof child === 'string' ? child : textOf(child as { children: unknown[] }))).join('');

const role = (name: string, model: string) => ({
  role: name, backend: 'codex', backend_label: 'Codex', backend_source: 'env', model, model_source: 'persisted:ARGUS_SKILL_MODEL',
  reasoning_effort: 'high', reasoning_effort_source: 'default', description: '',
});
const config = (): ConfigSnapshot => ({
  schema_version: 1, generated_at_utc: '', how_to_change: [], operator_knobs: [],
  roles: ['manager', 'planner', 'engineer', 'reviewer'].map(name => role(name, 'gpt-6.1-sol')),
  backends: { codex: 'Codex', claude: 'Claude Code' },
});
const unknown = { state: 'unknown' as const, at: 0, reason: '' };
const choices = (): ModelChoices => ({
  backend: 'codex', billing: 'token', premium_request_usd: null, task_sample: 2,
  options: [
    { model: 'gpt-6.1-sol', source: 'configured', default: false, invalid: false, price: { input: 2, cached_input: 0.2, output: 10 },
      task_estimate_usd: 0.04, latency_ms: 20_000, latency_samples: 3, availability: { state: 'ok', at: 1, reason: '' } },
    { model: 'gpt-5.6-luna', source: 'configured', default: false, invalid: false, price: { input: 0.2, cached_input: 0.02, output: 1.2 },
      task_estimate_usd: 0.005, latency_ms: null, latency_samples: 0, availability: unknown },
    { model: 'gpt-5.6-sol', source: 'configured', default: false, invalid: false, price: { input: 5, cached_input: 0.5, output: 30 },
      task_estimate_usd: 0.12, latency_ms: null, latency_samples: 0, availability: unknown },
    { model: 'gpt-6-astra', source: 'seen', default: false, invalid: false, price: null,
      task_estimate_usd: null, latency_ms: null, latency_samples: 0, availability: { state: 'failed', at: 2, reason: '503 quota exceeded' } },
  ],
});

async function mount() {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' } });
  const modelChoices = vi.spyOn(api, 'modelChoices').mockResolvedValue(choices());
  vi.spyOn(api, 'config').mockResolvedValue(config());
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['config', 'one'], config());
  client.setQueryData(['model-choices', 'one'], choices());
  await act(async () => {
    renderer = create(<QueryClientProvider client={client!}><ModelPicker sid="one" open onClose={() => {}} /></QueryClientProvider>);
  });
  return { modelChoices };
}

const card = (model: string) => renderer!.root.findByProps({ 'data-model-card': model });

it('shows each model with the price, speed and availability on record, and never calls a model on open', async () => {
  const probe = vi.spyOn(api, 'probeModel');
  await mount();
  expect(probe).not.toHaveBeenCalled();
  expect(card('gpt-6.1-sol').props['aria-checked']).toBe(true);
  // No band guessed from the price table.
  expect(renderer!.root.findAll(node => node.props['data-tier'] !== undefined)).toHaveLength(0);
  expect(textOf(card('gpt-6.1-sol').findByProps({ 'data-price': true }) as never)).toBe('$2 in · $10 out per 1M tokens');
  expect(textOf(card('gpt-6.1-sol').findByProps({ 'data-task-estimate': true }) as never)).toBe('about $0.040');
  expect(textOf(card('gpt-6.1-sol').findByProps({ 'data-speed': true }) as never)).toBe('median call 20 s (3 calls)');
  // A figure with no data is left out, not told as unknown.
  expect(card('gpt-5.6-luna').findAllByProps({ 'data-speed': true })).toHaveLength(0);
  expect(card('gpt-6-astra').findAllByProps({ 'data-price': true })).toHaveLength(0);
  expect(card('gpt-5.6-luna').findAllByProps({ 'data-availability': 'unknown' })).toHaveLength(0);
  expect(card('gpt-6-astra').findAllByProps({ 'data-task-estimate': true })).toHaveLength(0);
  expect(textOf(card('gpt-6-astra').findByProps({ 'data-availability': 'failed' }) as never)).toContain('503 quota exceeded');
  // Nothing chosen yet: the confirm bar has nothing to save.
  expect(renderer!.root.findByProps({ 'data-use-model': true }).props.disabled).toBe(true);
});

it("saves the project's one model through the settings write", async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  await act(async () => { card('gpt-6-astra').props.onClick(); });
  expect(card('gpt-6-astra').props['aria-checked']).toBe(true);
  expect(textOf(renderer!.root.findByProps({ 'data-choice-summary': true }) as never)).toBe('gpt-6-astra · High');
  const use = renderer!.root.findByProps({ 'data-use-model': true });
  expect(textOf(use as never)).toBe('Use this model');
  await act(async () => { use.props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_MODEL', 'gpt-6-astra']]);
  expect(textOf(renderer!.root.findByProps({ 'data-apply-message': true }) as never)).toContain('Saved.');
});

it('saves a model the list does not have, by the name typed', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  const name = () => renderer!.root.findByProps({ 'data-model-name': true });
  // The model in use is a card, so the field is empty.
  expect(name().props.value).toBe('');
  await act(async () => { name().props.onChange({ target: { value: ' claude-opus-4-1 ' } }); });
  expect(name().props.value).toBe('claude-opus-4-1');
  expect(card('gpt-6.1-sol').props['aria-checked']).toBe(false);
  await act(async () => { renderer!.root.findByProps({ 'data-use-model': true }).props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_MODEL', 'claude-opus-4-1']]);
});

it('writes an effort once, for the project', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  await act(async () => { renderer!.root.findByProps({ 'data-effort': 'medium' }).props.onClick(); });
  expect(textOf(renderer!.root.findByProps({ 'data-choice-summary': true }) as never)).toBe('gpt-6.1-sol · Medium');
  await act(async () => { renderer!.root.findByProps({ 'data-use-model': true }).props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_REASONING_EFFORT', 'medium']]);
});

it('tests a model only when asked, then refreshes the recorded availability', async () => {
  const probe = vi.spyOn(api, 'probeModel').mockResolvedValue({ model: 'gpt-6-astra', ok: false, error: '503 quota exceeded', duration_ms: 900 });
  const { modelChoices } = await mount();
  const calls = modelChoices.mock.calls.length;
  await act(async () => { card('gpt-6-astra').findByProps({ 'data-probe': 'gpt-6-astra' }).props.onClick({ stopPropagation() {} }); });
  expect(probe).toHaveBeenCalledWith('one', 'gpt-6-astra');
  // Testing does not select the card.
  expect(card('gpt-6-astra').props['aria-checked']).toBe(false);
  expect(textOf(card('gpt-6-astra').findByProps({ 'data-probe-result': true }) as never)).toBe('Test failed: 503 quota exceeded');
  expect(modelChoices.mock.calls.length).toBeGreaterThan(calls);
});
