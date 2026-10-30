import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { CredentialsSettings } from '../components/CredentialsSettings';

let view: ReactTestRenderer | undefined;
afterEach(() => { act(() => view?.unmount()); view = undefined; vi.restoreAllMocks(); });
const field = (name: string) => view!.root.findByProps({ name });

it('reads names, saves the literal input, clears it, replaces and deletes registrations', async () => {
  vi.spyOn(api, 'credentials').mockResolvedValue({ names: ['HF_TOKEN'] });
  const save = vi.spyOn(api, 'saveCredential').mockResolvedValue({ names: ['HF_TOKEN'] });
  const remove = vi.spyOn(api, 'deleteCredential').mockResolvedValue({ names: [] });
  await act(async () => { view = create(<CredentialsSettings />); });
  expect(field('credential-value').props.value).toBe('');
  expect(view!.root.findByType('code').children).toEqual(['HF_TOKEN']);
  const literal = '  token\n"quoted" 中文\t ';
  act(() => {
    field('credential-name').props.onChange({ target: { value: 'HF_TOKEN' } });
    field('credential-value').props.onChange({ target: { value: literal } });
  });
  await act(async () => view!.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  expect(save).toHaveBeenCalledExactlyOnceWith('HF_TOKEN', literal);
  expect(field('credential-value').props.value).toBe('');
  expect(field('credential-name').props.value).toBe('');
  expect(view!.root.findAllByType('code')).toHaveLength(1);
  await act(async () => view!.root.findByProps({ 'aria-label': 'Delete HF_TOKEN' }).props.onClick());
  expect(remove).toHaveBeenCalledExactlyOnceWith('HF_TOKEN');
  expect(view!.root.findAllByType('code')).toHaveLength(0);
});

it('opening Settings again starts with empty inputs and reads the current names', async () => {
  const read = vi.spyOn(api, 'credentials').mockResolvedValue({ names: ['WANDB_API_KEY'] });
  await act(async () => { view = create(<CredentialsSettings />); });
  act(() => field('credential-value').props.onChange({ target: { value: 'unsaved' } }));
  act(() => view!.unmount());
  await act(async () => { view = create(<CredentialsSettings />); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(field('credential-value').props.value).toBe('');
  expect(field('credential-name').props.value).toBe('');
  expect(view!.root.findByType('code').children).toEqual(['WANDB_API_KEY']);
});

it('keeps unsaved input when saving fails and reports the failure', async () => {
  vi.spyOn(api, 'credentials').mockResolvedValue({ names: [] });
  vi.spyOn(api, 'saveCredential').mockRejectedValue(new Error('Could not write settings'));
  await act(async () => { view = create(<CredentialsSettings />); });
  act(() => {
    field('credential-name').props.onChange({ target: { value: 'HF_TOKEN' } });
    field('credential-value').props.onChange({ target: { value: 'unsaved' } });
  });
  await act(async () => view!.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  expect(field('credential-value').props.value).toBe('unsaved');
  expect(view!.root.findByProps({ role: 'alert' })).toBeDefined();
});
