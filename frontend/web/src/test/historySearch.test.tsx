import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { HistorySearch } from '../components/ExperiencePanels';
import { api } from '../api';

vi.mock('../components/Modal', () => ({ Modal: ({ open, children }: { open: boolean; children: React.ReactNode }) => open ? <div>{children}</div> : null, ModalHeader: () => null }));
let renderer: ReactTestRenderer;
afterEach(() => { act(() => renderer?.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('clears both the input and debounced search on close, including closing before the debounce', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { setTimeout, clearTimeout });
  const search = vi.spyOn(api, 'searchHistory').mockResolvedValue({ results: [], partial: false, errors: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const show = (open: boolean) => <QueryClientProvider client={client}><HistorySearch open={open} onClose={() => {}} onChoose={() => {}} /></QueryClientProvider>;
  act(() => { renderer = create(show(true)); });
  act(() => renderer.root.findByType('input').props.onChange({ target: { value: 'weekly line' } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(310); });
  expect(search).toHaveBeenCalledWith('weekly line', expect.any(AbortSignal));
  act(() => renderer.update(show(false)));
  act(() => renderer.update(show(true)));
  expect(renderer.root.findByType('input').props.value).toBe('');
  act(() => renderer.root.findByType('input').props.onChange({ target: { value: 'unfinished' } }));
  act(() => renderer.update(show(false)));
  await act(async () => { await vi.advanceTimersByTimeAsync(310); });
  act(() => renderer.update(show(true)));
  expect(renderer.root.findByType('input').props.value).toBe('');
  expect(search).toHaveBeenCalledTimes(1);
  client.clear();
});
