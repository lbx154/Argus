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
  roles: ['manager', 'planner', 'engineer', 'reviewer'].map(name => role(name, 'gpt-5.6-luna')),
  backends: { codex: 'Codex', claude: 'Claude Code' },
});
const choices = (): ModelChoices => ({
  backend: 'codex',
  options: [
    { model: 'gpt-6.1-sol', default: true, price: { input: 2, output: 10 } },
    { model: 'gpt-5.6-luna', default: false, price: { input: 0.2, output: 1.2 } },
    { model: 'gpt-6-astra', default: false, price: null },
  ],
});

async function mount() {
  vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:8000' } });
  vi.spyOn(api, 'modelChoices').mockResolvedValue(choices());
  vi.spyOn(api, 'config').mockResolvedValue(config());
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['config', 'one'], config());
  client.setQueryData(['model-choices', 'one'], choices());
  await act(async () => {
    renderer = create(<QueryClientProvider client={client!}><ModelPicker sid="one" open onClose={() => {}} /></QueryClientProvider>);
  });
}

const row = (model: string) => renderer!.root.findByProps({ 'data-model-row': model });

it('lists Automatic first and each model with the price on record, and never calls a model on open', async () => {
  const probe = vi.spyOn(api, 'probeModel');
  await mount();
  expect(probe).not.toHaveBeenCalled();
  expect(renderer!.root.findAll(node => node.props['data-model-row'] !== undefined && node.type === 'div').map(node => node.props['data-model-row']))
    .toEqual(['', 'gpt-6.1-sol', 'gpt-5.6-luna', 'gpt-6-astra']);
  // Automatic names the backend's default; the model in use is marked.
  expect(textOf(row('').findByProps({ 'data-meta': true }) as never)).toBe("Codex's default model · gpt-6.1-sol");
  expect(row('gpt-5.6-luna').props['aria-checked']).toBe(true);
  expect(textOf(row('gpt-5.6-luna') as never)).toContain('in use');
  expect(textOf(row('gpt-6.1-sol').findByProps({ 'data-meta': true }) as never)).toBe('$2 in · $10 out per 1M tokens');
  // A price the table does not have is left out, not told as unknown.
  expect(row('gpt-6-astra').findAllByProps({ 'data-meta': true })).toHaveLength(0);
  // Nothing chosen yet: the confirm bar has nothing to save.
  expect(renderer!.root.findByProps({ 'data-use-model': true }).props.disabled).toBe(true);
});

it("saves the project's one model through the settings write", async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  await act(async () => { row('gpt-6-astra').props.onClick(); });
  expect(row('gpt-6-astra').props['aria-checked']).toBe(true);
  expect(textOf(renderer!.root.findByProps({ 'data-choice-summary': true }) as never)).toBe('gpt-6-astra · High');
  const use = renderer!.root.findByProps({ 'data-use-model': true });
  expect(textOf(use as never)).toBe('Use this model');
  await act(async () => { use.props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_MODEL', 'gpt-6-astra']]);
  expect(textOf(renderer!.root.findByProps({ 'data-apply-message': true }) as never)).toContain('Saved.');
});

it("goes back to the backend's default by choosing Automatic", async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  await act(async () => { row('').props.onClick(); });
  expect(textOf(renderer!.root.findByProps({ 'data-choice-summary': true }) as never)).toBe('Automatic · High');
  await act(async () => { renderer!.root.findByProps({ 'data-use-model': true }).props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_MODEL', '']]);
});

it('saves a model the list does not have, by the name typed', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  const name = () => renderer!.root.findByProps({ 'data-model-name': true });
  // The model in use is in the list, so the field is empty.
  expect(name().props.value).toBe('');
  await act(async () => { name().props.onChange({ target: { value: ' claude-opus-4-1 ' } }); });
  expect(name().props.value).toBe('claude-opus-4-1');
  expect(row('gpt-5.6-luna').props['aria-checked']).toBe(false);
  await act(async () => { renderer!.root.findByProps({ 'data-use-model': true }).props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_MODEL', 'claude-opus-4-1']]);
});

it('writes an effort once, for the project', async () => {
  const setConfig = vi.spyOn(api, 'setConfig').mockResolvedValue({} as never);
  await mount();
  await act(async () => { renderer!.root.findByProps({ 'data-effort': 'medium' }).props.onClick(); });
  expect(textOf(renderer!.root.findByProps({ 'data-choice-summary': true }) as never)).toBe('gpt-5.6-luna · Medium');
  await act(async () => { renderer!.root.findByProps({ 'data-use-model': true }).props.onClick(); });
  expect(setConfig.mock.calls).toEqual([['one', 'ARGUS_SKILL_REASONING_EFFORT', 'medium']]);
});

it('tests a model only when asked, without choosing it', async () => {
  const probe = vi.spyOn(api, 'probeModel').mockResolvedValue({ model: 'gpt-6-astra', ok: false, error: '503 quota exceeded', duration_ms: 900 });
  await mount();
  await act(async () => { row('gpt-6-astra').findByProps({ 'data-probe': 'gpt-6-astra' }).props.onClick({ stopPropagation() {} }); });
  expect(probe).toHaveBeenCalledWith('one', 'gpt-6-astra');
  expect(row('gpt-6-astra').props['aria-checked']).toBe(false);
  expect(textOf(row('gpt-6-astra').findByProps({ 'data-probe-result': true }) as never)).toBe('Test failed: 503 quota exceeded');
});
