import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type AdvisorSettings as Settings } from '../api';
import { AdvisorSettings } from '../components/AdvisorSettings';

const config = { schema_version: 1, enabled: false, backend: 'pi', model: '', effort: '', timeout_seconds: 120, max_calls_per_turn: 2, max_evidence_bytes: 65536 };
const initial: Settings = { saved: config, config, overridden_fields: [], supported_backends: ['pi', 'copilot'] };
let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); });

it('lets a new project choose a registered model before knowing its runner', async () => {
  const empty = { ...config, backend: '' };
  const settings: Settings = { ...initial, saved: empty, config: empty,
    model_options: [{ backend: 'pi', model: 'argus/gpt-5.4-mini' }, { backend: 'pi', model: 'argus/gpt-5.5' }] };
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['advisor-settings', 'one'], settings);
  const mainConfig = vi.spyOn(api, 'setConfig');
  const save = vi.spyOn(api, 'saveAdvisorSettings').mockImplementation(async (_sid, values) => ({
    ...settings, saved: { ...empty, ...values }, config: { ...empty, ...values },
  }));
  await act(async () => { renderer = create(<QueryClientProvider client={client!}><AdvisorSettings sid="one" primaryModel="argus/gpt-5.4-mini" /></QueryClientProvider>); });
  expect(renderer!.root.findAllByProps({ 'aria-label': 'Advisor runner' })).toHaveLength(0);
  act(() => {
    renderer!.root.findByProps({ type: 'checkbox' }).props.onChange({ target: { checked: true } });
    renderer!.root.findByProps({ 'aria-label': 'Choose advisor model' }).props.onChange({ target: { value: JSON.stringify(['pi', 'argus/gpt-5.5']) } });
  });
  const button = renderer!.root.findAllByType('button').find(node => node.children.includes('Save advisor settings'))!;
  expect(button.props.disabled).toBe(false);
  await act(async () => { await button.props.onClick(); });
  expect(save).toHaveBeenCalledWith('one', expect.objectContaining({ enabled: true, backend: 'pi', model: 'argus/gpt-5.5' }));
  expect(mainConfig).not.toHaveBeenCalled();
});

it('keeps an existing custom advisor when it is absent from the registered catalog', async () => {
  const saved = { ...config, enabled: true, backend: 'copilot', model: 'custom-advisor' };
  const settings: Settings = { ...initial, saved, config: saved, model_options: [{ backend: 'pi', model: 'argus/gpt-5.5' }] };
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['advisor-settings', 'one'], settings);
  await act(async () => { renderer = create(<QueryClientProvider client={client!}><AdvisorSettings sid="one" /></QueryClientProvider>); });
  expect(renderer!.root.findByProps({ 'aria-label': 'Choose advisor model' }).props.value).toBe('custom');
  expect(renderer!.root.findByProps({ 'aria-label': 'Advisor runner' }).props.value).toBe('copilot');
  expect(renderer!.root.findByProps({ placeholder: 'Model ID for this runner' }).props.value).toBe('custom-advisor');
});

it('saves an explicitly selected advisor without changing the team model', async () => {
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['advisor-settings', 'one'], initial);
  const mainConfig = vi.spyOn(api, 'setConfig');
  const save = vi.spyOn(api, 'saveAdvisorSettings').mockImplementation(async (_sid, values) => ({
    ...initial, saved: { ...config, ...values }, config: { ...config, ...values },
  }));
  await act(async () => { renderer = create(<QueryClientProvider client={client!}><AdvisorSettings sid="one" /></QueryClientProvider>); });
  act(() => {
    renderer!.root.findByProps({ type: 'checkbox' }).props.onChange({ target: { checked: true } });
    renderer!.root.findByProps({ placeholder: 'Model ID for this runner' }).props.onChange({ target: { value: 'provider/advisor' } });
  });
  const button = renderer!.root.findAllByType('button').find(node => node.children.includes('Save advisor settings'))!;
  expect(button.props.disabled).toBe(false);
  await act(async () => { await button.props.onClick(); });
  expect(save).toHaveBeenCalledWith('one', expect.objectContaining({ enabled: true, backend: 'pi', model: 'provider/advisor' }));
  expect(save.mock.calls[0][1]).not.toHaveProperty('runner_bin');
  expect(save.mock.calls[0][1]).not.toHaveProperty('schema_version');
  expect(mainConfig).not.toHaveBeenCalled();
  expect(JSON.stringify(renderer!.toJSON())).toContain('Saved for the next consultation');
});

it('keeps a late save attached to its original project after navigation', async () => {
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['advisor-settings', 'one'], initial);
  const second = { ...initial, saved: { ...config, model: 'provider/second' }, config: { ...config, model: 'provider/second' } };
  client.setQueryData(['advisor-settings', 'two'], second);
  let finish!: (settings: Settings) => void;
  vi.spyOn(api, 'saveAdvisorSettings').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const render = (sid: string) => <QueryClientProvider client={client!}><AdvisorSettings sid={sid} /></QueryClientProvider>;
  await act(async () => { renderer = create(render('one')); });
  act(() => { renderer!.root.findAllByType('button').find(node => node.children.includes('Save advisor settings'))!.props.onClick(); });
  await act(async () => { renderer!.update(render('two')); });
  await act(async () => { finish(initial); });
  expect(renderer!.root.findByProps({ placeholder: 'Model ID for this runner' }).props.value).toBe('provider/second');
  expect(JSON.stringify(renderer!.toJSON())).not.toContain('Saved for the next consultation');
  expect(client.getQueryData(['advisor-settings', 'two'])).toEqual(second);
});
