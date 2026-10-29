import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { ProjectTrash } from '../components/ExperiencePanels';

let view: ReactTestRenderer | undefined;
let client: QueryClient;
afterEach(() => { if (view) act(() => view!.unmount()); client?.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('shows the saved name and deletion time, then closes after the restored project is selected', async () => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const entry = { trash_id: 'p-one-20261029', sid: 'p-one', label: 'Deleted project', launch_cwd: '/projects/one', trashed_at: 1 };
  const read = vi.spyOn(api, 'trash').mockResolvedValue({ entries: [entry], total: 1, offset: 0, limit: 100 });
  const restore = vi.spyOn(api, 'restoreTrash').mockResolvedValue({ ok: true, sid: entry.sid });
  const onRestored = vi.fn(async () => {});
  const onClose = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><ProjectTrash open onClose={onClose} onRestored={onRestored} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  expect(JSON.stringify(view!.toJSON())).toContain(entry.label);
  expect(read).toHaveBeenCalledWith('', 0, expect.any(AbortSignal));
  await act(async () => { await view!.root.findAllByType('button').find(node => node.children.includes('Restore'))!.props.onClick(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  expect(restore).toHaveBeenCalledWith(entry.trash_id);
  expect(onRestored).toHaveBeenCalledWith(entry.sid);
  expect(view!.root.findByType('time').props.dateTime).toBe('1970-01-01T00:00:01.000Z');
  expect(JSON.stringify(view!.toJSON())).not.toContain(entry.launch_cwd);
  expect(onClose).toHaveBeenCalledOnce();
  expect(onRestored.mock.invocationCallOrder[0]).toBeLessThan(onClose.mock.invocationCallOrder[0]);
  expect(read).toHaveBeenCalledOnce();
});

it('keeps restore errors visible and lets the operator search persisted entries', async () => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api, 'trash').mockResolvedValue({ entries: [{ trash_id: 'entry', sid: 'p-one', label: 'One', launch_cwd: '/one', trashed_at: 1 }], total: 1, offset: 0, limit: 100 });
  vi.spyOn(api, 'restoreTrash').mockRejectedValue(new Error('Restore refused'));
  const restored = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><ProjectTrash open onClose={() => {}} onRestored={restored} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  act(() => view!.root.findByProps({ type: 'search' }).props.onChange({ target: { value: 'One' } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  expect(api.trash).toHaveBeenLastCalledWith('One', 0, expect.any(AbortSignal));
  await act(async () => { await view!.root.findAllByType('button').find(node => node.children.includes('Restore'))!.props.onClick(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
  expect(view!.root.findByProps({ role: 'alert' })).toBeDefined();
  expect(JSON.stringify(view!.toJSON())).toContain('Restore refused');
  expect(restored).not.toHaveBeenCalled();
});
