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

let latest!: ReturnType<typeof useMapCopy>;
function Probe({ paused = false, allowGeneration = true, zh = false, source = data, readingKey = 'task' }: {
  paused?: boolean; allowGeneration?: boolean; zh?: boolean; source?: Dataset; readingKey?: string | null;
}) {
  latest = useMapCopy(source, "task", zh, allowGeneration, undefined, "session", paused, false, readingKey);
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

it("does not retry failed summary generation when the daemon resumes without new source records", async () => {
  const generate = vi.spyOn(api, "generateMapCopy").mockRejectedValue(new Error("Runner unavailable"));
  act(() => { renderer = create(tree(true)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);

  act(() => renderer!.update(tree(false)));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);
});

it('retains a failed semantic attempt across noise, reading changes and unmount/remount', async () => {
  const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Provider limit'));
  let source = data;
  let readingKey: string | null = 'task';
  const render = () => <QueryClientProvider client={client}><Probe source={source} readingKey={readingKey} /></QueryClientProvider>;
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(latest.generationError).toBeInstanceOf(Error);
  source = { ...data, cursor: 'new-poll-cursor', tasks: [{ ...data.tasks[0], revision: 'noise-only' }],
    events: [{ id: 'waiting', item_id: 'task', type: 'life.planner.waiting', ts: 999, text: 'Still waiting', revision: 'waiting-2' }] };
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  readingKey = null;
  act(() => renderer!.update(render()));
  readingKey = 'task';
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  act(() => renderer!.unmount());
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generationError).toBeInstanceOf(Error);
  expect(latest.generating).toBe(false);
});

it('allows new attempts for meaningful source, selected event, model, version and preview changes', async () => {
  vi.stubGlobal('window', { location: { search: '' } });
  const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Cannot prepare this input'));
  let source: Dataset = { ...data, events: [{ id: 'review', item_id: 'task', type: 'round.review.completed',
    text: 'A recorded result', ts: 20, revision: 'event-1' }] };
  const render = () => <QueryClientProvider client={client}><Probe source={source} /></QueryClientProvider>;
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  source = { ...source, tasks: [{ ...source.tasks[0], title: 'A changed research question' }] };
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
  source = { ...source, events: [{ ...source.events[0], text: 'A new independently checked result', revision: 'event-2' }] };
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(3);
  act(() => { client.setQueryData(key, { ...empty, model_revision: 'new-model', version: 24 }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(4);
  act(() => { client.setQueryData(key, { ...empty, model_revision: 'new-model', version: 25 }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(5);
  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  client.setQueryData([...key, 'learning-path'], { ...empty, model_revision: 'new-model', version: 25 });
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(6);
});

it('keeps historical failure attached to the original step when current task execution changes', async () => {
  const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Historical explanation failed'));
  const event = { id: 'old', item_id: 'task', type: 'round.review.completed', text: 'Original review', ts: 2, revision: 'old-event' };
  const step: SubmapStep = { id: 'old', kind: 'review', title: 'Review', detail: event.text, ts: 2, status: 'done', source: 'event', eventIds: ['old'] };
  let source: Dataset = { ...data, events: [event] };
  function Historical() { latest = useMapCopy(source, 'task', false, true, [step], 'session', false, false, 'old'); return null; }
  const render = () => <QueryClientProvider client={client}><Historical /></QueryClientProvider>;
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  source = { ...source, tasks: [{ ...source.tasks[0], status: 'running', revision: 'later-attempt', attempt: 2,
    started_ts: 100, summary: 'Later progress', pending_question: 'A later question' }] };
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  source = { ...source, tasks: [{ ...source.tasks[0], acceptance_check: 'A changed research requirement' }] };
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('retries only by the explicit control after failure and retains the previous explanation', async () => {
  const retained: MapCopy = { ...empty, version: 25, cards: { task: {
    title: 'Retained title', summary: 'Retained summary', detail: 'Retained evidence', generated_at: 10, version: 24,
    task_revision: '1', task_status: 'done',
  } } };
  client.setQueryData(key, retained);
  let finish!: (value: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValueOnce(new Error('First request failed'))
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.copy?.cards.task).toEqual(retained.cards.task);
  act(() => renderer!.update(tree(false, false)));
  await act(async () => { await latest.retry(); });
  expect(generate).toHaveBeenCalledTimes(1);
  act(() => renderer!.update(tree(false)));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  act(() => { void latest.retry(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(latest.generationError).toBeNull();
  expect(latest.generating).toBe(true);
  expect(latest.copy?.cards.task).toEqual(retained.cards.task);
  const updated = { ...retained, cards: { task: { ...retained.cards.task, version: 25, generated_at: 20 } } };
  await act(async () => { finish(updated); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationError).toBeNull();
  expect(latest.readingNeedsUpdate).toBe(false);
});

it('honors a successful server coalescing delay and eventually refreshes the new source', async () => {
  const updated: MapCopy = { ...empty, cards: { task: {
    title: 'Updated', summary: 'New result', detail: 'Complete conditions', generated_at: 10, task_revision: '1', task_status: 'done',
  } } };
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValueOnce({ ...empty, retry_after: 25 }).mockResolvedValue(updated);
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(24000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generationUnavailable).toBe(false);
  expect(latest.generationError).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(latest.copy?.cards.task.title).toBe('Updated');
  expect(latest.readingNeedsUpdate).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('does not loop when a response has no complete result or server-directed retry', async () => {
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(empty);
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generationUnavailable).toBe(true);
  await act(async () => { await latest.retry(); await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('does not duplicate an in-flight semantic attempt when the canvas remounts', async () => {
  let finish!: (value: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  act(() => renderer!.unmount());
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generating).toBe(true);
  await act(async () => { finish({ ...empty, cards: { task: {
    title: 'Completed once', summary: 'Result', detail: 'Source', generated_at: 1, task_revision: '1', task_status: 'done',
  } } }); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.copy?.cards.task.title).toBe('Completed once');
});

it('keeps a failed card visibly failed while another card in the same map is generating', async () => {
  const source: Dataset = { ...data, tasks: [...data.tasks, { ...data.tasks[0], id: 'other', title: 'Another task' }] };
  let focused = 'task';
  const render = () => <QueryClientProvider client={client}><Reader /></QueryClientProvider>;
  function Reader() { latest = useMapCopy(source, focused, false, true, undefined, 'session'); return null; }
  const failure = new Error('First card failed');
  const generate = vi.spyOn(api, 'generateMapCopy').mockRejectedValueOnce(failure).mockReturnValueOnce(new Promise(() => {}));
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(latest.generationError).toBe(failure);
  focused = 'other';
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
  focused = 'task';
  act(() => renderer!.update(render()));
  expect(latest.generating).toBe(true);
  expect(latest.generationError).toBe(failure);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
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
