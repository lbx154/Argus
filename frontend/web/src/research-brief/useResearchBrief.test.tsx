import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
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
  vi.useRealTimers();
});

describe('semantic generation and shared cache lifecycle', () => {
  it('upgrades an unreviewed older explanation without presenting it as the current reading card', async () => {
    const old = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    old.cards.a.version = 11;
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

  it('keeps a coalesced older brief marked for update without an automatic request loop', async () => {
    const previous = completedCopy(source.tasks[0], ['start-a'], 7);
    client.setQueryData(briefCopyKey('s-research', 'en-US'), previous);
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue({ ...previous, retry_after: 25 });
    await mount();
    expect(result.brief).toEqual(previous.cards.a.reader_brief);
    expect(result.needsUpdate).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(generate).toHaveBeenCalledTimes(1);
  });

  it('stops on an older service without repeatedly requesting an unavailable upgrade', async () => {
    client.setQueryData(briefCopyKey('s-research', 'en-US'), { cards: {}, relations: [], available: true, version: 9 });
    const generate = vi.spyOn(api, 'generateMapCopy');
    await mount();
    expect(result.legacy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(generate).not.toHaveBeenCalled();
  });

  it('does not loop when a generation response itself contains legacy copy without a brief', async () => {
    const legacy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
    legacy.version = 9; legacy.cards.a.version = 9; delete legacy.cards.a.reader_brief;
    const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(legacy);
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(generate).toHaveBeenCalledTimes(1);
    expect(result.legacy).toBe(true);
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
