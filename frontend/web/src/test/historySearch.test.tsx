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

it('moves through search results with arrows and opens the focused result', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { setTimeout, clearTimeout });
  const doc = { activeElement: null as unknown };
  vi.stubGlobal('document', doc);
  const results = [1, 2].map(n => ({ sid: 'project', title: 'Project', kind: 'conversation' as const, line: n, snippet: `Message ${n}`, text: `Message ${n}` }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api, 'searchHistory').mockResolvedValue({ results, partial: false, errors: [] });
  const choose = vi.fn(), close = vi.fn();
  const nodes: { focus: () => void }[] = [];
  act(() => { renderer = create(<QueryClientProvider client={client}><HistorySearch open onClose={close} onChoose={choose} /></QueryClientProvider>, {
    createNodeMock: element => {
      if (element.type !== 'button') return null;
      const node = { focus: () => { doc.activeElement = node; } }; nodes.push(node); return node;
    },
  }); });
  act(() => renderer.root.findByType('input').props.onChange({ target: { value: 'Message' } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(310); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const key = (key: string) => act(() => renderer.root.findByProps({ className: 'experience-panel search-body' }).props.onKeyDown({ key, nativeEvent: {}, preventDefault: vi.fn() }));
  key('ArrowDown'); expect(doc.activeElement).toBe(nodes[0]);
  key('ArrowDown'); expect(doc.activeElement).toBe(nodes[1]);
  key('ArrowUp'); expect(doc.activeElement).toBe(nodes[0]);
  key('ArrowUp'); expect(doc.activeElement).toBe(nodes[1]);
  act(() => renderer.root.findAllByType('button')[1].props.onClick());
  expect(choose).toHaveBeenCalledWith(results[1]); expect(close).toHaveBeenCalledOnce();
  act(() => renderer.unmount()); client.clear();
});
