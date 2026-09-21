import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type ExplanationPhase } from '../api';
import type { Dataset } from '../map/model';
import type { MapCopy } from '../map/presentation';
import { briefCopyKey, briefLiveKey, briefSelection, READER_BRIEF_VERSION } from './model';
import { useResearchBrief, type ResearchBriefOptions } from './useResearchBrief';
import { completedCopy, inputs, source } from './testFixtures';

let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
let result: ReturnType<typeof useResearchBrief>;

function Probe(props: ResearchBriefOptions) { result = useResearchBrief(props); return null; }
function tree(props: ResearchBriefOptions) { return <QueryClientProvider client={client}><Probe {...props} /></QueryClientProvider>; }
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(25); }); }
async function mount(props: ResearchBriefOptions = inputs()) {
  await act(async () => { renderer = create(tree(props)); });
  await flush();
  await flush();
}

const neighbor = { id: 'b', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
function relatedCopy(related: typeof neighbor | null = neighbor, generatedAt = 10): MapCopy {
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a'], generatedAt);
  copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, next: related ? `${related.objective}: ${related.status}` : 'No neighboring assignment remains.' };
  return copy;
}

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(briefCopyKey('s-research', 'en-US'), { cards: {}, relations: [], available: true, version: READER_BRIEF_VERSION });
  vi.spyOn(api, 'mapCopy').mockResolvedValue({ cards: {}, relations: [], available: true, version: READER_BRIEF_VERSION });
  vi.spyOn(api, 'liveMap').mockResolvedValue(source);
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('semantic generation and shared cache lifecycle', () => {
  it('does not count a pending preview request as normal generation after switching back', async () => {
    vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
    const previewKey = briefCopyKey('s-research', 'en-US');
    client.setQueryData(previewKey, { cards: {}, relations: [], available: true, version: 24 });
    let finishPreview!: (copy: MapCopy) => void;
    const normal = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    normal.cards.a.title = 'Normal explanation';
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockReturnValueOnce(new Promise(resolve => { finishPreview = resolve; }))
      .mockResolvedValueOnce(normal);
    await mount();
    expect(generate).toHaveBeenCalledTimes(1);
    vi.stubGlobal('window', { location: { search: '' } });
    act(() => renderer!.update(tree(inputs())));
    await flush(); await flush();
    expect(generate.mock.calls.map(call => call[5])).toEqual(['source-first', null]);
    expect(result.card?.title).toBe('Normal explanation');
    expect(result.generating).toBe(false);
    const preview = completedCopy(source.tasks[0], ['start-a', 'main-a'], 20);
    preview.version = 24; preview.cards.a.version = 24; preview.cards.a.title = 'Preview explanation';
    await act(async () => { finishPreview(preview); });
    await flush();
    expect(client.getQueryData<MapCopy>(previewKey)?.cards.a.title).toBe('Preview explanation');
    expect(result.card?.title).toBe('Normal explanation');
    expect(result.generating).toBe(false);
  });

  it.each([null, 'source-first'] as const)('does not rewrite saved copy for hidden neighbors or cursor changes in %s mode', async preview => {
    vi.stubGlobal('window', { location: { search: preview ? '?reader_preview=source-first' : '' } });
    const copyKey = briefCopyKey('s-research', 'en-US');
    const props = inputs();
    const liveKey = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
    const retained = relatedCopy();
    const before = structuredClone(retained);
    client.setQueryData(copyKey, retained);
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(retained);
    await mount({ ...props, readOnly: true });
    expect(generate).not.toHaveBeenCalled();
    act(() => renderer!.update(tree({ ...props, active: false })));
    await flush();
    expect(generate).not.toHaveBeenCalled();
    act(() => renderer!.update(tree(props)));
    await flush(); await flush();
    expect(generate).not.toHaveBeenCalled();
    expect(result.needsUpdate).toBe(false);
    act(() => { renderer!.unmount(); renderer = undefined; });
    await mount(props);
    expect(generate).not.toHaveBeenCalled();

    const publish = async (cursor: string, related: typeof neighbor | null) => {
      vi.mocked(api.liveMap).mockResolvedValue({ ...source, cursor, incremental: true,
        tasks: related ? [{ ...source.tasks[1], ...related }] : [],
        events: [{ id: 'neighbor-progress', item_id: 'b', type: 'round.main.completed', ts: 30, text: 'Other task record' }],
        removed_task_ids: related ? [] : ['b'],
      });
      await act(async () => { await client.refetchQueries({ queryKey: liveKey }); });
      await flush(); await flush();
    };
    await publish('unrelated-change', neighbor);
    expect(generate).not.toHaveBeenCalled();
    expect(result.card).toEqual(before.cards.a);
    expect(result.needsUpdate).toBe(false);

    const edited = { ...neighbor, objective: 'New neighboring goal' };
    await publish('neighbor-goal-changed', edited);
    expect(generate).not.toHaveBeenCalled();
    expect(result.needsUpdate).toBe(false);
    expect(result.generating).toBe(false);
    expect(result.card).toEqual(before.cards.a);

    const cancelled = { ...edited, status: 'cancelled' };
    await publish('neighbor-cancelled', cancelled);
    expect(generate).not.toHaveBeenCalled();
    await publish('neighbor-deleted', null);
    expect(generate).not.toHaveBeenCalled();
    expect(result.card).toEqual(before.cards.a);
    expect(result.needsUpdate).toBe(false);
    expect(client.getQueryData<Dataset>(liveKey)?.tasks.map(task => task.id)).toEqual(['a']);
    expect(result.loadedEvents?.every(event => event.item_id === 'a')).toBe(true);
    expect(retained).toEqual(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).not.toHaveBeenCalled();
  });

  it.each(['explicit', 'server-delay'] as const)('keeps coalesced stale copy pending until %s retry on the same input', async retryMode => {
    const retained = relatedCopy();
    retained.cards.a.event_ids = ['start-a'];
    client.setQueryData(briefCopyKey('s-research', 'en-US'), retained);
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue({ ...retained, retry_after: 25 });
    await mount();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.brief).toEqual(retained.cards.a.reader_brief);
    expect(result.needsUpdate).toBe(true);
    expect(result.generationUnavailable).toBe(true); // The existing retry button remains available.
    await act(async () => { await vi.advanceTimersByTimeAsync(24_000); });
    expect(generate).toHaveBeenCalledTimes(1);
    const updated = relatedCopy({ ...neighbor, objective: 'A revised hidden assignment' }, 11);
    generate.mockResolvedValue(updated);
    await act(async () => {
      if (retryMode === 'explicit') await result.retry();
      else await vi.advanceTimersByTimeAsync(1_000);
    });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.brief?.next).toContain('A revised hidden assignment');
    expect(result.needsUpdate).toBe(false);
    expect(result.generationUnavailable).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it('shares an in-flight generation without buying another explanation for intervening cursor changes', async () => {
    const retained = relatedCopy();
    retained.cards.a.event_ids = ['start-a'];
    client.setQueryData(briefCopyKey('s-research', 'en-US'), retained);
    const props = inputs();
    const liveKey = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
    let finish!: (copy: MapCopy) => void;
    const latest = relatedCopy({ ...neighbor, objective: 'Latest hidden goal' }, 12);
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce(latest);
    await act(async () => { renderer = create(<QueryClientProvider client={client}><Probe {...props} /><Probe {...props} /></QueryClientProvider>); });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(1);
    for (const cursor of ['intermediate-cursor', 'latest-cursor']) {
      act(() => { client.setQueryData<Dataset>(liveKey, previous => ({ ...previous!, cursor })); });
      await flush();
    }
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.generating).toBe(true);
    await act(async () => { finish(relatedCopy(neighbor, 11)); });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.brief?.next).toContain(neighbor.objective);
    expect(result.needsUpdate).toBe(false);
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it('does not let an old generation receipt confirm the new review configuration', async () => {
    const key = briefCopyKey('s-research', 'en-US');
    const retained = relatedCopy();
    retained.cards.a.event_ids = ['start-a'];
    retained.model_revision = 'review-old'; retained.cards.a.model_revision = 'review-old';
    client.setQueryData(key, retained);
    let finishOld!: (copy: MapCopy) => void;
    let finishNew!: (copy: MapCopy) => void;
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
      .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve; }));
    await mount();
    act(() => { client.setQueryData(key, { ...retained, model_revision: 'review-new' }); });
    await flush();
    expect(generate).toHaveBeenCalledTimes(1);
    const old = relatedCopy(neighbor, 11);
    old.model_revision = 'review-old'; old.cards.a.model_revision = 'review-old';
    await act(async () => { finishOld(old); });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.needsUpdate).toBe(true);
    expect(result.generating).toBe(true);
    expect(client.getQueryData<MapCopy>(key)?.model_revision).toBe('review-new');
    const current = relatedCopy({ ...neighbor, objective: 'Checked with current review settings' }, 12);
    current.model_revision = 'review-new'; current.cards.a.model_revision = 'review-new';
    await act(async () => { finishNew(current); });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.needsUpdate).toBe(false);
    expect(result.card?.model_revision).toBe('review-new');
  });

  it('retains reported progress across a read-only remount and clears it after failure without a new POST', async () => {
    let report!: (phase: ExplanationPhase) => void;
    let reject!: (error: Error) => void;
    const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation((_source, _name, _body, _signal, _sid, _preview, onProgress) => {
      report = onProgress!;
      report('planning');
      return new Promise((_resolve, fail) => { reject = fail; });
    });
    await mount();
    expect(result.generationPhase).toBe('planning');
    act(() => renderer!.unmount());
    await mount({ ...inputs(), active: false, readOnly: true });
    report('writing');
    await flush();
    expect(result.generationPhase).toBe('writing');
    expect(result.generating).toBe(true);
    const failure = new Error('The observed generation timed out');
    await act(async () => { reject(failure); });
    await flush();
    expect(result.generationPhase).toBeUndefined();
    expect(result.generating).toBe(false);
    expect(result.generationError).toBe(failure);
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it.each(['source-first', 'learning-path'] as const)('keeps normal and %s text separate when switching mode without a page reload', async mode => {
    vi.stubGlobal('window', { location: { search: '' } });
    const normal = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    normal.cards.a.title = 'Normal retained explanation';
    client.setQueryData(briefCopyKey('s-research', 'en-US'), normal);
    const preview = structuredClone(normal);
    preview.version = mode === 'source-first' ? 24 : 25;
    preview.cards.a.version = preview.version;
    preview.cards.a.title = 'Separate preview explanation';
    vi.mocked(api.mapCopy).mockResolvedValue(preview);
    const generate = vi.spyOn(api, 'generateMapCopy');
    await mount({ ...inputs(), readOnly: true });
    expect(result.card?.title).toBe(normal.cards.a.title);
    vi.stubGlobal('window', { location: { search: `?reader_preview=${mode}` } });
    await act(async () => { renderer!.update(tree({ ...inputs(), readOnly: true })); });
    await flush(); await flush();
    expect(result.card?.title).toBe(preview.cards.a.title);
    expect(api.mapCopy).toHaveBeenLastCalledWith('project', 's-research', 'en-US', expect.any(AbortSignal), 's-research', mode);
    vi.stubGlobal('window', { location: { search: '' } });
    await act(async () => { renderer!.update(tree({ ...inputs(), readOnly: true })); });
    await flush();
    expect(result.card?.title).toBe(normal.cards.a.title);
    expect(generate).not.toHaveBeenCalled();
  });

  it.each(['completed', 'failed'])('rechecks after saving review settings despite a %s attempt for the previous pipeline', async (previousAttempt) => {
    const key = briefCopyKey('s-research', 'en-US');
    const beforeSource = structuredClone(source);
    const retained = completedCopy(source.tasks[0], ['start-a', 'main-a'], 8);
    retained.model_revision = previousAttempt === 'completed' ? 'review-medium' : 'earlier-pipeline';
    retained.cards.a.model_revision = retained.model_revision;
    const beforeCard = structuredClone(retained.cards.a);
    let stored: MapCopy = previousAttempt === 'completed'
      ? { cards: {}, relations: [], available: true, version: READER_BRIEF_VERSION, model_revision: 'review-medium' }
      : { ...retained, model_revision: 'review-medium' };
    client.setQueryData(key, stored);
    vi.mocked(api.mapCopy).mockImplementation(async () => stored);
    let finish!: (copy: MapCopy) => void;
    const pending = new Promise<MapCopy>(resolve => { finish = resolve; });
    const generate = vi.spyOn(api, 'generateMapCopy').mockImplementationOnce(async () => {
      if (previousAttempt === 'failed') throw new Error('Previous review unavailable');
      stored = retained;
      return retained;
    }).mockReturnValueOnce(pending);
    const save = vi.spyOn(api, 'setConfig').mockImplementation(async () => {
      stored = { ...stored, model_revision: 'review-high' };
      return { restart_required: false };
    });
    await mount();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.card).toEqual(beforeCard);
    expect(result.generationError instanceof Error).toBe(previousAttempt === 'failed');

    // Same save and map-copy invalidation used by the existing Settings callback.
    await act(async () => {
      await api.setConfig('s-research', 'ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT', 'high');
      await client.invalidateQueries({ queryKey: ['map-copy'] });
    });
    await flush(); await flush();
    expect(save).toHaveBeenCalledWith('s-research', 'ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT', 'high');
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.needsUpdate).toBe(true);
    expect(result.generating).toBe(true);
    expect(result.card).toEqual(beforeCard);
    expect(result.brief).toEqual(beforeCard.reader_brief);
    expect(generate.mock.calls[1][2].cards).toEqual([{ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] }]);

    const updated = completedCopy(source.tasks[0], ['start-a', 'main-a'], 12);
    updated.model_revision = 'review-high';
    updated.cards.a.model_revision = 'review-high';
    await act(async () => { stored = updated; finish(updated); });
    await flush();
    expect(result.needsUpdate).toBe(false);
    expect(result.card?.model_revision).toBe('review-high');
    expect(result.card?.event_ids).toEqual(beforeCard.event_ids);
    expect(source).toEqual(beforeSource);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it('retains compatible Chinese copy while a newer server version refreshes the same input', async () => {
    const previous = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    previous.cards.a.title = '已有中文说明';
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(previous);
    await mount();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    act(() => { client.setQueryData(briefCopyKey('s-research', 'en-US'), { ...previous, version: 15 }); });
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.card?.title).toBe('已有中文说明');
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    expect(result.needsUpdate).toBe(true);
    // A coalesced v14 card, even without a top-level version, is not a v15 completion.
    generate.mockResolvedValue({ cards: previous.cards, relations: [] });
    await act(async () => { await result.retry(); }); await flush();
    expect(result.needsUpdate).toBe(true);
    const next = { ...previous.cards.a, version: 15, copy_revision: 20, generated_at: 20 };
    generate.mockResolvedValue({ cards: { a: next }, relations: [] });
    await act(async () => { await result.retry(); }); await flush();
    expect(result.needsUpdate).toBe(false);
    expect(client.getQueryData<MapCopy>(briefCopyKey('s-research', 'en-US'))?.version).toBe(15);
  });

  it('upgrades the previous explanation version without presenting it as the current reading card', async () => {
    const old = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    old.cards.a.version = READER_BRIEF_VERSION - 1;
    client.setQueryData(briefCopyKey('s-research', 'en-US'), old);
    let finish!: (copy: MapCopy) => void;
    const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await mount();
    expect(result.task?.objective).toBe(source.tasks[0].objective);
    expect(result.brief).toBeUndefined();
    expect(result.generating).toBe(true);
    expect(generate).toHaveBeenCalledTimes(1);
    await act(async () => finish(completedCopy(source.tasks[0], ['start-a', 'main-a'])));
    await flush();
    expect(result.brief).toBeDefined();
  });

  it('keeps task facts when teaching cannot be checked and does not buy an automatic retry loop', async () => {
    const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, concept: null };
    copy.cards.a.teaching_review = { status: 'unavailable', kind: 'model_teaching_review',
      reason: 'The example could not be established.', reviewed_at: null, review_version: 1 };
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(copy);
    await mount();
    expect(result.teachingUnavailable).toBe(true);
    expect(result.brief?.concept).toBeNull();
    expect(result.brief?.scope).toBe(copy.cards.a.reader_brief.scope);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it('generates one current card, shares the map cache and does not regenerate for tool/text changes', async () => {
    const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation(async (_source, _sid, body) => completedCopy(source.tasks[0], body.cards[0].event_ids));
    const props = inputs();
    await mount(props);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(generate.mock.calls[0][2]).toEqual({ cards: [{ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] }], locale: 'en-US' });
    expect(api.mapCopy).not.toHaveBeenCalled();
    expect(client.getQueryData<MapCopy>(briefCopyKey(props.sid, 'en-US'))?.cards.a.reader_brief).toBeDefined();
    const liveKey = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
    act(() => { client.setQueryData<Dataset>(liveKey, previous => ({ ...previous!,
      cursor: 'tool-only-cursor',
      tasks: previous!.tasks.map(task => ({ ...task, revision: 'tool-noise' })),
      events: [...previous!.events, { id: 'delta', item_id: 'a', type: 'work.segment', ts: 8, text: 'More public text' }],
    })); });
    await flush();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.needsUpdate).toBe(false);

    act(() => { client.setQueryData<Dataset>(liveKey, previous => ({ ...previous!, events: [...previous!.events,
      { id: 'review-a', item_id: 'a', type: 'round.review.completed', ts: 9, text: 'New review', status: 'continue' },
    ] })); });
    await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(generate.mock.calls[1][2].cards).toHaveLength(1);
    expect(generate.mock.calls[1][2].cards[0].event_ids).toContain('review-a');
  });

  it('uses the last cursor for a refresh and merges a semantic event-only page', async () => {
    vi.spyOn(api, 'generateMapCopy').mockResolvedValue(completedCopy(source.tasks[0], ['start-a', 'main-a']));
    const props = inputs();
    await mount(props);
    vi.mocked(api.liveMap).mockResolvedValue({ ...source, incremental: true, tasks: [],
      events: [{ id: 'review-a', item_id: 'a', type: 'round.review.completed', ts: 9, text: 'Review' }], cursor: 'cursor-2' });
    const key = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
    await act(async () => { await client.refetchQueries({ queryKey: key }); });
    await flush();
    expect(api.liveMap).toHaveBeenLastCalledWith('s-research', expect.any(AbortSignal), 'cursor-1', { mode: 'current', since: 2, eventSince: 2, taskId: 'a' });
    expect(result.task?.id).toBe('a');
    expect(result.evidence.map(event => event.id)).toEqual(['start-a', 'main-a', 'review-a']);
    expect(client.getQueryData<Dataset>(key)?.cursor).toBe('cursor-2');
  });

  it('waits for an in-flight explanation and skips intermediate task states', async () => {
    let finish!: (copy: MapCopy) => void;
    let latestTask = source.tasks[0];
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }))
      .mockImplementation(async (_source, _sid, body) => completedCopy(latestTask, body.cards[0].event_ids));
    const props = inputs();
    await mount(props);
    const key = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
    const changeSummary = (summary: string) => act(() => {
      latestTask = { ...latestTask, summary, revision: summary, content_revision: summary };
      client.setQueryData<Dataset>(key, previous => ({ ...previous!,
        tasks: previous!.tasks.map(task => task.id === 'a' ? latestTask : task),
      }));
    });
    changeSummary('Review recorded; certification pending.');
    await flush();
    changeSummary('Review recorded; stage remains uncertified.');
    await flush();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.generating).toBe(true);
    await act(async () => finish(completedCopy(source.tasks[0], ['start-a', 'main-a'])));
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.task?.summary).toBe('Review recorded; stage remains uncertified.');
    await flush();
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it('does not retry failed generation on polling, hide/show or remount; the button retries it', async () => {
    const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Narration unavailable'));
    const props = inputs();
    await mount(props);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.generationError).toBeInstanceOf(Error);
    act(() => renderer!.update(tree({ ...props, active: false })));
    await flush();
    act(() => renderer!.update(tree(props)));
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(1);
    act(() => { renderer!.unmount(); renderer = undefined; });
    await mount(props);
    expect(generate).toHaveBeenCalledTimes(1);
    generate.mockResolvedValue(completedCopy(source.tasks[0], ['start-a', 'main-a']));
    await act(async () => { await result.retry(); });
    await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.brief).toBeDefined();
  });

  it('keeps existing explanations visible while a new semantic input is being generated', async () => {
    const previous = completedCopy(source.tasks[0], ['start-a'], 7);
    client.setQueryData(briefCopyKey('s-research', 'en-US'), previous);
    let finish!: (copy: MapCopy) => void;
    vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await mount();
    expect(result.generating).toBe(true);
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    expect(result.card?.generated_at).toBe(7);
    await act(async () => finish(completedCopy(source.tasks[0], ['start-a', 'main-a'], 12)));
    await flush();
    expect(result.generating).toBe(false);
    expect(result.card?.generated_at).toBe(12);
  });

  it('keeps a coalesced older brief until the server delay then applies the new material', async () => {
    const previous = completedCopy(source.tasks[0], ['start-a'], 7);
    client.setQueryData(briefCopyKey('s-research', 'en-US'), previous);
    const updated = completedCopy(source.tasks[0], ['start-a', 'main-a'], 12);
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockResolvedValueOnce({ ...previous, retry_after: 25 }).mockResolvedValueOnce(updated);
    await mount();
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    expect(result.needsUpdate).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(24_000); });
    expect(generate).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.needsUpdate).toBe(false);
    expect(result.card?.generated_at).toBe(12);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it('stops on an older service without repeatedly requesting an unavailable upgrade', async () => {
    client.setQueryData(briefCopyKey('s-research', 'en-US'), { cards: {}, relations: [], available: true, version: 9 });
    const generate = vi.spyOn(api, 'generateMapCopy');
    await mount();
    expect(result.legacy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(generate).not.toHaveBeenCalled();
  });

  it('stops after the delayed refresh fails even though the earlier response had retry_after', async () => {
    const previous = completedCopy(source.tasks[0], ['start-a'], 7);
    client.setQueryData(briefCopyKey('s-research', 'en-US'), previous);
    const generate = vi.spyOn(api, 'generateMapCopy')
      .mockResolvedValueOnce({ ...previous, retry_after: 25 })
      .mockRejectedValueOnce(new Error('Provider unavailable'));
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(26_000); });
    await flush();
    expect(result.generationError).toBeInstanceOf(Error);
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it('retains the known server version without looping when a response contains legacy copy without a brief', async () => {
    const legacy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    legacy.version = 9; legacy.cards.a.version = 9; delete legacy.cards.a.reader_brief;
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(legacy);
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.legacy).toBe(false);
    expect(result.generationUnavailable).toBe(true);
    expect(client.getQueryData<MapCopy>(briefCopyKey('s-research', 'en-US'))?.version).toBe(READER_BRIEF_VERSION);
    expect(result.brief).toBeUndefined();
  });

  it('keeps read-only access to original records without calling the generator', async () => {
    const generate = vi.spyOn(api, 'generateMapCopy');
    await mount({ ...inputs(), readOnly: true });
    expect(result.task?.objective).toBe(source.tasks[0].objective);
    expect(result.evidence).toHaveLength(2);
    expect(generate).not.toHaveBeenCalled();
  });

  it('isolates task switches and routes late text to its original shared cache entry', async () => {
    let finishA!: (copy: MapCopy) => void;
    const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation((_source, _sid, body) => body.cards[0].task_id === 'a'
      ? new Promise(resolve => { finishA = resolve; }) : Promise.resolve(completedCopy(source.tasks[1], body.cards[0].event_ids, 20)));
    await mount(inputs('a'));
    act(() => renderer!.update(tree(inputs('b'))));
    await flush(); await flush();
    expect(generate).toHaveBeenCalledTimes(2);
    expect(result.task?.id).toBe('b');
    expect(result.card?.title).toBe('Different task');
    await act(async () => finishA(completedCopy(source.tasks[0], ['start-a', 'main-a'], 10)));
    await flush();
    expect(result.task?.id).toBe('b');
    expect(result.card?.title).toBe('Different task');
    const cached = client.getQueryData<MapCopy>(briefCopyKey('s-research', 'en-US'));
    expect(cached?.cards.a.reader_brief).toBeDefined();
    expect(cached?.cards.b.reader_brief).toBeDefined();
  });
});
