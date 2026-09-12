import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset } from "../map/model";
import type { MapCopy } from "../map/presentation";
import { useMapCopy } from "../map/useMapCopy";
import { MapReaderContent } from "../map/MapReaderContent";
import type { SubmapStep } from "../map/submap";
import { ReaderExplanation } from "../research-brief/ReaderExplanation";

const data: Dataset = {
  id: "live:research",
  title: "Research",
  description: "",
  kind: "live",
  read_only: false,
  tasks: [{ id: "task", title: "Study", objective: "Study", status: "done", deps: [], revision: "1" }],
  events: [],
};
const key = ["map-copy", "project", "research", "en-US", "session"];
const empty: MapCopy = { cards: {}, relations: [], available: true };
let client: QueryClient;
let renderer: ReactTestRenderer | undefined;

function Probe({ paused = false, allowGeneration = true, zh = false }: { paused?: boolean; allowGeneration?: boolean; zh?: boolean }) {
  useMapCopy(data, "task", zh, allowGeneration, undefined, "session", paused);
  return null;
}

const tree = (paused: boolean, allowGeneration = true) => (
  <QueryClientProvider client={client}>
    <Probe paused={paused} allowGeneration={allowGeneration} />
  </QueryClientProvider>
);

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(key, empty);
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('refreshes changed related sources only after opening an editable reader, retaining the old explanation while pending', async () => {
  const neighbor = { id: 'neighbor', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
  const retained: MapCopy = { ...empty, cards: { task: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained conditions', generated_at: 1, copy_revision: 1,
    task_revision: '1', task_status: 'done', reader_brief: { why: 'Why', scope: 'Scope', next: neighbor.objective, concept: null },
    source_snapshot: { version: 2, card_key: 'task', task_id: 'task', captured_at: 1,
      task: {}, events: [], source_ids: [], related_tasks: [{ ...neighbor }] },
  } } };
  const before = structuredClone(retained);
  client.setQueryData(key, retained);
  let current = { ...data, tasks: [...data.tasks, neighbor] };
  let state!: ReturnType<typeof useMapCopy>;
  function Reader({ open, allowed }: { open: boolean; allowed: boolean }) {
    state = useMapCopy(current, 'task', false, allowed, undefined, 'session', false, false, open ? 'task' : null);
    return null;
  }
  const reading = (open: boolean, allowed = true) => <QueryClientProvider client={client}><Reader open={open} allowed={allowed} /></QueryClientProvider>;
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(reading(false)); });
  current = { ...current, tasks: [...data.tasks, { ...neighbor, objective: 'New neighboring goal', status: 'cancelled' }] };
  act(() => { renderer!.update(reading(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => { renderer!.update(reading(true, false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => { renderer!.update(reading(true)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(state.readingNeedsUpdate).toBe(true);
  expect(state.generating).toBe(true);
  expect(state.copy?.cards.task).toEqual(before.cards.task);
  const updated: MapCopy = { ...retained, cache_revision: 2, cards: { task: {
    ...retained.cards.task, copy_revision: 2, generated_at: 2,
    reader_brief: { ...retained.cards.task.reader_brief!, next: 'New neighboring goal' },
    source_snapshot: { ...retained.cards.task.source_snapshot!, captured_at: 2, related_tasks: [{ ...current.tasks[1] }] },
  } } };
  await act(async () => { finish(updated); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(state.readingNeedsUpdate).toBe(false);
  expect(state.copy?.cards.task.reader_brief?.next).toBe('New neighboring goal');
  expect(generate).toHaveBeenCalledTimes(1);
  expect(retained).toEqual(before);
});

it('verifies hidden related sources once per source cursor and keeps late responses bound to the cursor they checked', async () => {
  const neighbor = { id: 'neighbor', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
  const retained: MapCopy = { ...empty, cards: { task: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained conditions', generated_at: 1, copy_revision: 1,
    task_revision: '1', task_status: 'done',
    source_snapshot: { version: 2, card_key: 'task', task_id: 'task', captured_at: 1,
      task: {}, events: [], source_ids: [], related_tasks: [neighbor] },
  } } };
  client.setQueryData(key, retained);
  let current: Dataset = { ...data, cursor: 'full-source-v1', incremental: false };
  let state!: ReturnType<typeof useMapCopy>;
  function Reader({ open = true, allowed = true }: { open?: boolean; allowed?: boolean }) {
    state = useMapCopy(current, 'task', false, allowed, undefined, 'session', false, false, open ? 'task' : null);
    return null;
  }
  const reading = (open = true, allowed = true) => <QueryClientProvider client={client}><Reader open={open} allowed={allowed} /></QueryClientProvider>;
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(retained);
  act(() => { renderer = create(reading(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  act(() => { renderer!.update(reading(true, false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => { renderer!.update(reading()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(state.readingNeedsUpdate).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(generate).toHaveBeenCalledTimes(1); // An omitted neighbor still exists; no loop.

  current = { ...current, cursor: 'unrelated-change-v2' };
  act(() => { renderer!.update(reading()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(state.copy?.cards.task).toEqual(retained.cards.task);
  expect(state.readingNeedsUpdate).toBe(false); // Cached verification needs no new card version.

  let finish!: (copy: MapCopy) => void;
  generate.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  current = { ...current, cursor: 'hidden-goal-edit-v3' };
  act(() => { renderer!.update(reading()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(3);
  current = { ...current, cursor: 'hidden-task-deleted-v4' };
  act(() => { renderer!.update(reading()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(3);
  const edited: MapCopy = { ...retained, cache_revision: 2, cards: { task: { ...retained.cards.task, generated_at: 2, copy_revision: 2,
    source_snapshot: { ...retained.cards.task.source_snapshot!, captured_at: 2,
      related_tasks: [{ ...neighbor, objective: 'Changed hidden goal' }] },
  } } };
  // A coalesced response cannot certify the later deletion as checked.
  generate.mockResolvedValueOnce({ ...edited, retry_after: 25 });
  await act(async () => { finish(edited); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(4);
  const deleted: MapCopy = { ...edited, cache_revision: 3, cards: { task: { ...edited.cards.task, generated_at: 3, copy_revision: 3,
    source_snapshot: { ...edited.cards.task.source_snapshot!, captured_at: 3, related_tasks: [] },
  } } };
  generate.mockResolvedValue(deleted);
  await act(async () => { await vi.advanceTimersByTimeAsync(24000); });
  expect(generate).toHaveBeenCalledTimes(4);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(5);
  expect(state.readingNeedsUpdate).toBe(false);
  expect(state.copy?.cards.task.source_snapshot?.related_tasks).toEqual([]);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(generate).toHaveBeenCalledTimes(5);
});

it('does not treat another reader sharing the source request as verification of its own cached card', async () => {
  const shared: Dataset = { ...data, cursor: 'full-source-v1', tasks: [
    ...data.tasks, { ...data.tasks[0], id: 'other' },
  ] };
  const retained: MapCopy = { ...empty, cards: Object.fromEntries(shared.tasks.map(task => [task.id, {
    title: `Explanation for ${task.id}`, summary: 'Summary', detail: 'Conditions', generated_at: 1, copy_revision: 1,
    task_revision: task.revision, task_status: task.status,
    source_snapshot: { version: 2 as const, card_key: task.id, task_id: task.id, captured_at: 1,
      task: {}, events: [], source_ids: [], related_tasks: [{ id: 'hidden', title: 'Hidden', objective: 'Old neighboring goal', status: 'pending', deps: [] }] },
  }])) };
  client.setQueryData(key, retained);
  const states: Record<string, ReturnType<typeof useMapCopy>> = {};
  function Reader({ task }: { task: string }) {
    states[task] = useMapCopy(shared, task, false, true, undefined, 'session');
    return null;
  }
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue(retained);
  act(() => { renderer = create(<QueryClientProvider client={client}><Reader task="task" /><Reader task="other" /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0][2].cards.map(card => card.key)).toEqual(['task']);
  // The backend returns the entire cache, including an unrequested old card.
  await act(async () => { finish(retained); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][2].cards.map(card => card.key)).toEqual(['other']);
  expect(states.task.readingNeedsUpdate).toBe(false);
  expect(states.other.readingNeedsUpdate).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('starts the selected preview after an in-flight normal request finishes without mixing their text', async () => {
  vi.stubGlobal('window', { location: { search: '' } });
  client.setQueryData([...key, 'source-first'], empty);
  let state!: ReturnType<typeof useMapCopy>;
  function Reader() { state = useMapCopy(data, 'task', false, true, undefined, 'session'); return null; }
  const render = () => <QueryClientProvider client={client}><Reader /></QueryClientProvider>;
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }))
    .mockReturnValueOnce(new Promise(() => {}));
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  const normal: MapCopy = { ...empty, cards: { task: {
    title: 'Normal result', summary: 'Recorded summary', detail: 'Recorded detail', generated_at: 1,
  } } };
  await act(async () => { finish(normal); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls.map(call => call[5])).toEqual([null, 'source-first']);
  expect(client.getQueryData<MapCopy>(key)?.cards.task.title).toBe('Normal result');
  expect(state.copy?.cards.task).toBeUndefined();
  expect(state.generating).toBe(true);
});

it("naturally rechecks an open historical step after saving review settings while retaining its original text and evidence", async () => {
  const task = { id: 'historical', title: 'Recorded task', objective: 'Original objective', status: 'done', deps: [],
    revision: 'later-task-state', content_revision: 'same-goal', started_ts: 20, attempt: 2 };
  const original = { id: 'old-review', item_id: task.id, type: 'round.review.completed', ts: 3,
    text: 'The earlier attempt was not accepted.', status: 'failed', revision: 'original-event', attempt: 1 };
  const historical: Dataset = { ...data, id: 'live:s-history', tasks: [task], events: [original,
    { ...original, id: 'later-review', ts: 30, text: 'A later attempt passed.', status: 'done', revision: 'later-event', attempt: 2 },
  ] };
  const step: SubmapStep = { id: original.id, kind: 'review', title: 'Earlier review', detail: original.text,
    status: 'failed', ts: original.ts, source: 'event', eventIds: [original.id] };
  const retained: MapCopy = { version: 21, model_revision: 'review-medium', cache_revision: 1, available: true, relations: [], cards: {
    [step.id]: { title: 'Earlier explanation', summary: 'Earlier summary', detail: 'Earlier complete conditions',
      reader_brief: { why: 'Earlier purpose', scope: 'Earlier result only', next: 'Earlier next action', concept: null },
      generated_at: 10, copy_revision: 1, version: 21, model_revision: 'review-medium', task_revision: 'original-task-state',
      task_content_revision: 'same-goal', task_status: 'failed', event_ids: [original.id], event_revisions: [original.revision],
      source_snapshot: { version: 1, card_key: step.id, task_id: task.id, captured_at: 9,
        task: { title: task.title, objective: task.objective }, source_ids: [original.id], events: [{ ...original }] },
    },
  } };
  const before = structuredClone({ historical, retained });
  const historyKey = ['map-copy', 'project', 's-history', 'en-US', 's-history'];
  client.setQueryData(historyKey, retained);
  let stored = retained;
  vi.spyOn(api, 'mapCopy').mockImplementation(async () => stored);
  vi.spyOn(api, 'setConfig').mockImplementation(async () => {
    stored = { ...stored, model_revision: 'review-high' };
    return { restart_required: false };
  });
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let state!: ReturnType<typeof useMapCopy>;
  function HistoricalReader() {
    state = useMapCopy(historical, task.id, false, true, [step], 's-history', false, false, step.id);
    return <MapReaderContent cardKey={step.id} taskId={task.id} task={task} card={state.copy?.cards[step.id]} originalDetail={step.detail}
      selection={state.readingRequest ? { request: state.readingRequest, evidence: historical.events,
        pending: state.readingNeedsUpdate, generating: state.generating } : undefined} />;
  }
  act(() => { renderer = create(<QueryClientProvider client={client}><HistoricalReader /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).not.toHaveBeenCalled();

  await act(async () => {
    await api.setConfig('s-history', 'ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT', 'high');
    await client.invalidateQueries({ queryKey: ['map-copy'] });
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(state.copy?.model_revision).toBe('review-high');
  expect(state.readingNeedsUpdate).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0][2].cards).toEqual([{ key: step.id, task_id: task.id, kind: 'review', event_ids: [original.id] }]);
  expect(state.readingNeedsUpdate).toBe(true);
  expect(state.generating).toBe(true);
  expect(state.copy?.cards[step.id]).toEqual(before.retained.cards[step.id]);
  expect(renderer!.root.findByType(ReaderExplanation).props.brief).toEqual(retained.cards[step.id].reader_brief);
  expect(renderer!.root.findByType(ReaderExplanation).props.detail).toBe(retained.cards[step.id].detail);

  const updated: MapCopy = { ...retained, model_revision: 'review-high', cache_revision: 2, cards: {
    [step.id]: { ...retained.cards[step.id], model_revision: 'review-high', copy_revision: 2, generated_at: 40 },
  } };
  await act(async () => { stored = updated; finish(updated); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(state.readingNeedsUpdate).toBe(false);
  expect(state.copy?.cards[step.id].source_snapshot).toEqual(before.retained.cards[step.id].source_snapshot);
  expect(historical).toEqual(before.historical);
  expect(generate).toHaveBeenCalledTimes(1);
});

it("resumes failed summary generation when a paused session resumes without new records", async () => {
  const generate = vi.spyOn(api, "generateMapCopy").mockRejectedValue(new Error("Runner unavailable"));
  act(() => { renderer = create(tree(true)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);

  act(() => renderer!.update(tree(false)));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it("never schedules generation in read-only mode, including across pause changes", async () => {
  client.setQueryData(key, { ...empty, version: 15, cards: { task: {
    title: '旧中文标题', summary: 'Old summary', detail: 'Original detail', generated_at: 1, version: 14,
    task_revision: '1', task_status: 'done',
  } } });
  const generate = vi.spyOn(api, "generateMapCopy").mockResolvedValue(empty);
  act(() => { renderer = create(tree(true, false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  act(() => renderer!.update(tree(false, false)));
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).not.toHaveBeenCalled();
  expect(client.getQueryData<MapCopy>(key)?.cards.task.title).toBe('旧中文标题');
});

it("starts the new locale after the canvas remounts without losing an earlier in-flight result", async () => {
  const completed = (title: string): MapCopy => ({
    ...empty,
    cards: {
      task: { title, summary: title, detail: title, generated_at: 1, task_revision: "1", task_status: "done" },
    },
  });
  let finishEnglish!: (result: MapCopy) => void;
  const english = new Promise<MapCopy>((resolve) => { finishEnglish = resolve; });
  const generate = vi.spyOn(api, "generateMapCopy")
    .mockImplementation((_source, _name, body) => body.locale === "en-US" ? english : Promise.resolve(completed("研究摘要")));
  const chineseKey = ["map-copy", "project", "research", "zh-CN", "session"];
  client.setQueryData(chineseKey, empty);
  // MapPanel keys the provider/canvas by source, session, locale and history choice.
  const localizedTree = (zh: boolean) => (
    <QueryClientProvider client={client}><Probe key={String(zh)} zh={zh} /></QueryClientProvider>
  );
  act(() => { renderer = create(localizedTree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);

  act(() => renderer!.update(localizedTree(true)));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(client.getQueryData<MapCopy>(chineseKey)?.cards.task.title).toBe("研究摘要");

  await act(async () => { finishEnglish(completed("Research summary")); });
  expect(client.getQueryData<MapCopy>(key)?.cards.task.title).toBe("Research summary");
  expect(client.getQueryData<MapCopy>(chineseKey)?.cards.task.title).toBe("研究摘要");
});
