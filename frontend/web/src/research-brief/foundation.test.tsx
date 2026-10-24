import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { mapCopyKey } from '../map/copyMode';
import type { MapCopy } from '../map/presentation';
import { useMapCopy } from '../map/useMapCopy';
import { selectFoundation, useSelectedFoundation } from './foundation';
import { briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source } from './testFixtures';

vi.mock('../components/Modal', () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  ModalHeader: ({ title }: { title: string }) => <h2>{title}</h2>,
}));

let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
const preview = 'question-foundation' as const;
const props = inputs();
const empty: MapCopy = { ...completedCopy(source.tasks[0], []), cards: {} };
function liveCache() {
  client.setQueryData(briefLiveKey(props.sid, briefSelection(props.snapshot, props.view)), currentBriefData(source, props.sid, 'a'));
}
async function flush(ms = 25) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
beforeEach(() => {
  vi.useFakeTimers();
  const storage = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
  vi.stubGlobal('window', { location: { search: '?reader_preview=question-foundation' } });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  liveCache();
});
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('retains explicit choices across remounts while isolating projects and locales', async () => {
  let chosen!: { a: string | null; az: string | null; b: string | null };
  function Choices() {
    chosen = { a: useSelectedFoundation('project-a', 'en-US').id,
      az: useSelectedFoundation('project-a', 'zh-CN').id, b: useSelectedFoundation('project-b', 'en-US').id };
    return null;
  }
  const render = () => <QueryClientProvider client={client}><Choices /></QueryClientProvider>;
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  act(() => { renderer = create(render()); });
  act(() => { selectFoundation(client, 'project-a', 'en-US', 'a-en'); selectFoundation(client, 'project-a', 'zh-CN', 'a-zh'); selectFoundation(client, 'project-b', 'en-US', 'b-en'); });
  await flush();
  expect(chosen).toEqual({ a: 'a-en', az: 'a-zh', b: 'b-en' });
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  act(() => { renderer = create(render()); });
  expect(chosen).toEqual({ a: 'a-en', az: 'a-zh', b: 'b-en' });
  act(() => { selectFoundation(client, 'project-a', 'en-US', null); }); await flush();
  expect(chosen).toEqual({ a: null, az: 'a-zh', b: 'b-en' });
  expect(generate).not.toHaveBeenCalled();
});

it('never falls back to default or another preview’s explanations when no foundation is selected', async () => {
  const read = vi.spyOn(api, 'mapCopy').mockResolvedValue(empty);
  const generate = vi.spyOn(api, 'generateMapCopy');
  const foundation = vi.spyOn(api, 'generateReaderFoundation');
  function Readers() {
    useMapCopy(source, 'a', false, undefined, props.sid);
    return null;
  }
  await act(async () => { renderer = create(<QueryClientProvider client={client}><Readers /></QueryClientProvider>); });
  await flush(1500);
  expect(read.mock.calls.length).toBeGreaterThan(0);
  expect(read.mock.calls.every(call => call[5] === preview && call[6] === null)).toBe(true);
  expect(generate).not.toHaveBeenCalled();
  expect(foundation).not.toHaveBeenCalled();
});

it('reads map explanations saved for the reader’s pinned foundation until a new reading selection is made', async () => {
  for (const id of ['foundation-a', 'foundation-b']) {
    const saved = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
    saved.cards.a.title = `${id} application`;
    client.setQueryData(mapCopyKey('project', props.sid, 'en-US', props.sid, preview, id), saved);
  }
  selectFoundation(client, props.sid, 'en-US', 'foundation-a');
  let pinned: string | undefined = 'foundation-a';
  let latest!: ReturnType<typeof useMapCopy>;
  function Reader() { latest = useMapCopy(source, 'a', false, undefined, props.sid, 'a', pinned); return null; }
  const render = () => <QueryClientProvider client={client}><Reader /></QueryClientProvider>;
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(render()); }); await flush(750);
  expect(latest.copy?.cards.a.title).toBe('foundation-a application');
  act(() => { selectFoundation(client, props.sid, 'en-US', 'foundation-b'); }); await flush(750);
  expect(latest.copy?.cards.a.title).toBe('foundation-a application');
  expect(latest.questionContext.foundationId).toBe('foundation-a');
  pinned = undefined;
  act(() => renderer!.update(render())); await flush(750);
  expect(latest.copy?.cards.a.title).toBe('foundation-b application');
  expect(latest.questionContext.foundationId).toBe('foundation-b');
  expect(generate).not.toHaveBeenCalled();
});
