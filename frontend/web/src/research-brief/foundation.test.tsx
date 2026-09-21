import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { MapReaderContent } from '../map/MapReaderContent';
import { mapCopyKey } from '../map/copyMode';
import type { MapCopy } from '../map/presentation';
import { useMapCopy } from '../map/useMapCopy';
import ResearchBrief from './ResearchBrief';
import { ReaderExplanation } from './ReaderExplanation';
import { selectFoundation, useSelectedFoundation } from './foundation';
import { briefCopyKey, briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source } from './testFixtures';
import { useResearchBrief } from './useResearchBrief';

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

it('never falls back to default or another preview generation when no foundation is selected', async () => {
  const read = vi.spyOn(api, 'mapCopy').mockResolvedValue(empty);
  const generate = vi.spyOn(api, 'generateMapCopy');
  const foundation = vi.spyOn(api, 'generateReaderFoundation');
  let current!: ReturnType<typeof useResearchBrief>, history!: ReturnType<typeof useMapCopy>;
  function Readers() {
    current = useResearchBrief({ ...props, locale: 'en-US', preview });
    history = useMapCopy(source, 'a', false, true, undefined, props.sid);
    return null;
  }
  await act(async () => { renderer = create(<QueryClientProvider client={client}><Readers /></QueryClientProvider>); });
  await flush(1500);
  expect(current.foundationRequired).toBe(true);
  expect(history.foundationRequired).toBe(true);
  await act(async () => { await current.retry(); await history.retry(); });
  expect(read.mock.calls.length).toBeGreaterThan(0);
  expect(read.mock.calls.every(call => call[5] === preview && call[6] === null)).toBe(true);
  expect(generate).not.toHaveBeenCalled();
  expect(foundation).not.toHaveBeenCalled();
});

it('keeps a pinned current reader on foundation A while the main reader generates against selected foundation B', async () => {
  const a = completedCopy(source.tasks[0], ['start-a']);
  const b = completedCopy(source.tasks[0], ['start-a']);
  a.cards.a.reader_brief = { ...a.cards.a.reader_brief!, why: 'Application using foundation A.' };
  b.cards.a.reader_brief = { ...b.cards.a.reader_brief!, why: 'Application using foundation B.' };
  client.setQueryData(briefCopyKey(props.sid, 'en-US', preview, 'foundation-a'), a);
  client.setQueryData(briefCopyKey(props.sid, 'en-US', preview, 'foundation-b'), b);
  selectFoundation(client, props.sid, 'en-US', 'foundation-a');
  let finishA!: (copy: MapCopy) => void, finishB!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finishA = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishB = resolve; }));
  const foundation = vi.spyOn(api, 'generateReaderFoundation');
  await act(async () => { renderer = create(<QueryClientProvider client={client}><ResearchBrief {...props} /></QueryClientProvider>); });
  await flush();
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());
  act(() => { selectFoundation(client, props.sid, 'en-US', 'foundation-b'); });
  await flush();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls.map(call => ({ foundation: call[2].foundation_id, cards: call[2].cards, preview: call[5] }))).toEqual([
    { foundation: 'foundation-a', cards: [{ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] }], preview },
    { foundation: 'foundation-b', cards: [{ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] }], preview },
  ]);
  await act(async () => { generate.mock.calls[1][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  const body = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-body' });
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Application using foundation A.');
  expect(reading().findByProps({ 'data-explanation-phase': 'planning' })).toBeTruthy();
  expect(body().findByType(ReaderExplanation).props.brief.why).toBe('Application using foundation B.');
  expect(reading().findByType(MapReaderContent).props.selection.foundationRequired).toBe(false);
  const doneA = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
  doneA.cards.a.reader_brief = { ...doneA.cards.a.reader_brief!, why: 'Finished application A.' };
  await act(async () => { finishA(doneA); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Finished application A.');
  expect(body().findByType(ReaderExplanation).props.brief.why).toBe('Application using foundation B.');
  const doneB = completedCopy(source.tasks[0], ['start-a', 'main-a'], 40);
  doneB.cards.a.reader_brief = { ...doneB.cards.a.reader_brief!, why: 'Finished application B.' };
  await act(async () => { finishB(doneB); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Finished application A.');
  expect(body().findByType(ReaderExplanation).props.brief.why).toBe('Finished application B.');
  expect(generate).toHaveBeenCalledTimes(2);
  expect(foundation).not.toHaveBeenCalled();
});

it('binds map application requests and caches to the reader’s pinned foundation until a new reading selection is made', async () => {
  for (const id of ['foundation-a', 'foundation-b']) client.setQueryData(mapCopyKey('project', props.sid, 'en-US', props.sid, preview, id), empty);
  selectFoundation(client, props.sid, 'en-US', 'foundation-a');
  let pinned: string | undefined = 'foundation-a';
  let latest!: ReturnType<typeof useMapCopy>;
  function Reader() { latest = useMapCopy(source, 'a', false, true, undefined, props.sid, false, 'a', pinned); return null; }
  const render = () => <QueryClientProvider client={client}><Reader /></QueryClientProvider>;
  let finishA!: (copy: MapCopy) => void, finishB!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finishA = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishB = resolve; }));
  act(() => { renderer = create(render()); }); await flush(750);
  act(() => { selectFoundation(client, props.sid, 'en-US', 'foundation-b'); }); await flush(750);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0][2].foundation_id).toBe('foundation-a');
  const doneA = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
  doneA.cards.a.title = 'Foundation A application';
  await act(async () => { finishA(doneA); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.copy?.cards.a.title).toBe('Foundation A application');
  pinned = undefined;
  act(() => renderer!.update(render())); await flush(750);
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][2].foundation_id).toBe('foundation-b');
  const doneB = completedCopy(source.tasks[0], ['start-a', 'main-a'], 40);
  doneB.cards.a.title = 'Foundation B application';
  await act(async () => { finishB(doneB); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.copy?.cards.a.title).toBe('Foundation B application');
  expect(client.getQueryData<MapCopy>(briefCopyKey(props.sid, 'en-US', preview, 'foundation-a'))?.cards.a.title).toBe('Foundation A application');
  expect(generate.mock.calls.every(call => call[5] === preview)).toBe(true);
});
