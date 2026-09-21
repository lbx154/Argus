import { readFileSync } from "node:fs";
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
function Probe({ allowGeneration = true, zh = false, source = data, readingKey = 'task' }: {
  allowGeneration?: boolean; zh?: boolean; source?: Dataset; readingKey?: string | null;
}) {
  latest = useMapCopy(source, "task", zh, allowGeneration, undefined, "session", false, readingKey);
  return null;
}

const tree = (allowGeneration = true) => (
  <QueryClientProvider client={client}>
    <Probe allowGeneration={allowGeneration} />
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

it('does not rewrite a saved explanation for neighboring changes, but refreshes changed selected input only when opened', async () => {
  const neighbor = { id: 'neighbor', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
  const retained: MapCopy = { ...empty, cards: { task: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained conditions', generated_at: 1, copy_revision: 1,
    task_revision: '1', task_status: 'done', reader_brief: { why: 'Why', scope: 'Scope', next: neighbor.objective, concept: null },
  } } };
  const before = structuredClone(retained);
  client.setQueryData(key, retained);
  let current = { ...data, tasks: [...data.tasks, neighbor] };
  let state!: ReturnType<typeof useMapCopy>;
  function Reader({ open, allowed }: { open: boolean; allowed: boolean }) {
    state = useMapCopy(current, 'task', false, allowed, undefined, 'session', false, open ? 'task' : null);
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
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(generate).not.toHaveBeenCalled();
  expect(state.readingNeedsUpdate).toBe(false);
  current = { ...current, tasks: [{ ...data.tasks[0], revision: '2', objective: 'Changed selected goal' }, current.tasks[1]] };
  act(() => { renderer!.update(reading(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => { renderer!.update(reading(true)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(state.readingNeedsUpdate).toBe(true);
  expect(state.generating).toBe(true);
  expect(state.copy?.cards.task).toEqual(before.cards.task);
  const updated: MapCopy = { ...retained, cache_revision: 2, cards: { task: {
    ...retained.cards.task, copy_revision: 2, generated_at: 2, task_revision: '2',
    reader_brief: { ...retained.cards.task.reader_brief!, next: 'New neighboring goal' },
  } } };
  await act(async () => { finish(updated); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(state.readingNeedsUpdate).toBe(false);
  expect(state.copy?.cards.task.reader_brief?.next).toBe('New neighboring goal');
  expect(generate).toHaveBeenCalledTimes(1);
  expect(retained).toEqual(before);
});

it('does not ask the server to verify or rewrite a saved explanation for source cursor changes', async () => {
  const retained: MapCopy = { ...empty, cards: { task: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained conditions', generated_at: 1, copy_revision: 1,
    task_revision: '1', task_status: 'done',
  } } };
  client.setQueryData(key, retained);
  let current: Dataset = { ...data, cursor: 'full-source-v1', incremental: false };
  let state!: ReturnType<typeof useMapCopy>;
  function Reader({ open = true, allowed = true }: { open?: boolean; allowed?: boolean }) {
    state = useMapCopy(current, 'task', false, allowed, undefined, 'session', false, open ? 'task' : null);
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
  expect(generate).not.toHaveBeenCalled();
  expect(state.readingNeedsUpdate).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(generate).not.toHaveBeenCalled();

  current = { ...current, cursor: 'unrelated-change-v2' };
  act(() => { renderer!.update(reading()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).not.toHaveBeenCalled();
  expect(state.copy?.cards.task).toEqual(retained.cards.task);
  expect(state.readingNeedsUpdate).toBe(false);
});

it('does not treat another reader sharing the source request as generation of its own missing card', async () => {
  const shared: Dataset = { ...data, cursor: 'full-source-v1', tasks: [
    ...data.tasks, { ...data.tasks[0], id: 'other' },
  ] };
  const retained: MapCopy = { ...empty, cards: Object.fromEntries(shared.tasks.map(task => [task.id, {
    title: `Explanation for ${task.id}`, summary: 'Summary', detail: 'Conditions', generated_at: 1, copy_revision: 1,
    task_revision: task.revision, task_status: task.status,
  }])) };
  client.setQueryData(key, empty);
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
  await act(async () => { finish({ ...retained, cards: { task: retained.cards.task } }); });
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
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  expect(state.readingGenerating).toBe(true);
  expect(state.generationPhase).toBe('planning');
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(state.generating).toBe(true);
  expect(state.readingGenerating).toBe(false);
  expect(state.generationPhase).toBeUndefined();
  await act(async () => { generate.mock.calls[0][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(state.generationPhase).toBeUndefined();
  const normal: MapCopy = { ...empty, cards: { task: {
    title: 'Normal result', summary: 'Recorded summary', detail: 'Recorded detail', generated_at: 1,
    task_revision: '1', task_status: 'done',
  } } };
  await act(async () => { finish(normal); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls.map(call => call[5])).toEqual([null, 'source-first']);
  expect(client.getQueryData<MapCopy>(key)?.cards.task.title).toBe('Normal result');
  expect(state.copy?.cards.task).toBeUndefined();
  expect(state.generating).toBe(true);
  expect(state.readingGenerating).toBe(true);
  await act(async () => { generate.mock.calls[1][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  await act(async () => { generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(state.generationPhase).toBe('writing');
  vi.stubGlobal('window', { location: { search: '' } });
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(state.readingGenerating).toBe(false);
  expect(state.generationPhase).toBeUndefined();
  expect(state.copy?.cards.task.title).toBe('Normal result');
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  act(() => renderer!.update(render()));
  expect(state.generationPhase).toBe('writing');
  expect(generate).toHaveBeenCalledTimes(2);
});

it("naturally rechecks an open historical step after saving review settings while retaining its original explanation", async () => {
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
    state = useMapCopy(historical, task.id, false, true, [step], 's-history', false, step.id);
    return <MapReaderContent cardKey={step.id} taskId={task.id} task={task} card={state.copy?.cards[step.id]} originalDetail={step.detail}
      selection={state.readingRequest ? { request: state.readingRequest,
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
  expect(state.copy?.cards[step.id].event_ids).toEqual([original.id]);
  expect(state.copy?.cards[step.id].detail).toEqual(before.retained.cards[step.id].detail);
  expect(historical).toEqual(before.historical);
  expect(generate).toHaveBeenCalledTimes(1);
});

it("does not retry a failed explanation on its own, however long the reader stays", async () => {
  const generate = vi.spyOn(api, "generateMapCopy").mockRejectedValue(new Error("Runner unavailable"));
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(120700); });
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
  function Historical() { latest = useMapCopy(source, 'task', false, true, [step], 'session', false, 'old'); return null; }
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
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.copy?.cards.task).toEqual(retained.cards.task);
  act(() => renderer!.update(tree(false)));
  await act(async () => { await latest.retry(); });
  expect(generate).toHaveBeenCalledTimes(1);
  act(() => renderer!.update(tree()));
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
  act(() => { renderer = create(tree()); });
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

it('refreshes completion immediately after an active-task coalescing response', async () => {
  const running = { ...data, tasks: [{ ...data.tasks[0], status: 'running' }] };
  const finished: MapCopy = { ...empty, retry_after: 0, cards: { task: {
    title: 'Done', summary: 'Reviewed result', detail: 'Conditions', generated_at: 10, task_revision: '1', task_status: 'done',
  } } };
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockResolvedValueOnce({ ...empty, retry_after: 600 }).mockResolvedValue(finished);
  act(() => { renderer = create(<QueryClientProvider client={client}><Probe source={running} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(1);
  act(() => { renderer!.update(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(latest.copy?.cards.task.title).toBe('Done');
  expect(latest.readingNeedsUpdate).toBe(false);
});

it('does not loop when a response has no complete result or server-directed retry', async () => {
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(empty);
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generationUnavailable).toBe(true);
  await act(async () => { await latest.retry(); await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('does not duplicate an in-flight semantic attempt when the canvas remounts', async () => {
  let finish!: (value: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('planning');
  act(() => renderer!.unmount());
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(latest.generating).toBe(true);
  expect(latest.readingGenerating).toBe(true);
  expect(latest.generationPhase).toBe('planning');
  await act(async () => { generate.mock.calls[0][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('writing');
  await act(async () => { finish({ ...empty, cards: { task: {
    title: 'Completed once', summary: 'Result', detail: 'Source', generated_at: 1, task_revision: '1', task_status: 'done',
  } } }); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.copy?.cards.task.title).toBe('Completed once');
  expect(latest.readingGenerating).toBe(false);
  expect(latest.generationPhase).toBeUndefined();
  await act(async () => { generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBeUndefined();
  expect(generate).toHaveBeenCalledTimes(1);
});

it('keeps a failed card visibly failed while another card in the same map is generating', async () => {
  const source: Dataset = { ...data, tasks: [...data.tasks, { ...data.tasks[0], id: 'other', title: 'Another task' }] };
  let focused = 'task';
  const render = () => <QueryClientProvider client={client}><Reader /></QueryClientProvider>;
  function Reader() {
    latest = useMapCopy(source, focused, false, true, undefined, 'session');
    const task = source.tasks.find(item => item.id === focused)!;
    return <MapReaderContent cardKey={focused} taskId={focused} task={task} card={latest.copy?.cards[focused]}
      originalDetail={task.objective || ''} selection={latest.readingRequest ? {
        request: latest.readingRequest,
        pending: latest.readingNeedsUpdate, generating: latest.readingGenerating, phase: latest.generationPhase,
        error: latest.generationError, retry: latest.retry, retryDisabled: latest.generating,
      } : undefined} />;
  }
  const failure = new Error('First card failed');
  let failFirst!: (reason: Error) => void, finishOther!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise((_resolve, reject) => { failFirst = reject; }))
    .mockReturnValueOnce(new Promise(resolve => { finishOther = resolve; }));
  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('planning');
  await act(async () => { failFirst(failure); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationError).toBe(failure);
  expect(latest.readingGenerating).toBe(false);
  expect(latest.generationPhase).toBeUndefined();
  focused = 'other';
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(2);
  await act(async () => { generate.mock.calls[1][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(renderer!.root.findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  focused = 'task';
  act(() => renderer!.update(render()));
  expect(latest.generating).toBe(true);
  expect(latest.readingGenerating).toBe(false);
  expect(latest.generationPhase).toBeUndefined();
  expect(latest.generationError).toBe(failure);
  expect(JSON.stringify(renderer!.toJSON())).toContain('The explanation could not be prepared. This request will not be repeated automatically.');
  expect(renderer!.root.findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  const retry = () => renderer!.root.findAllByType('button').find(node => node.children.includes('Retry'))!;
  expect(retry().props.disabled).toBe(true);
  await act(async () => { await latest.retry(); generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationError).toBe(failure);
  expect(latest.generationPhase).toBeUndefined();
  expect(generate).toHaveBeenCalledTimes(2);
  await act(async () => { finishOther({ ...empty, cards: { other: {
    title: 'Other task completed', summary: 'Result', detail: 'Sources', generated_at: 10, task_revision: '1', task_status: 'done',
  } } }); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generating).toBe(false);
  expect(latest.generationError).toBe(failure);
  expect(latest.generationPhase).toBeUndefined();
  expect(retry().props.disabled).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('shares an in-flight phase on return to its source without leaking it into another source with the same card id', async () => {
  const other: Dataset = { ...data, id: 'live:another-project', title: 'Another project' };
  const otherKey = ['map-copy', 'project', 'another-project', 'en-US', 'session'];
  client.setQueryData(otherKey, empty);
  let selected = data;
  const render = () => <QueryClientProvider client={client}><Probe key={selected.id} source={selected} /></QueryClientProvider>;
  let finishFirst!: (copy: MapCopy) => void, finishOther!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finishFirst = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishOther = resolve; }));
  const completed = (title: string): MapCopy => ({ ...empty, cards: { task: {
    title, summary: title, detail: title, generated_at: 1, task_revision: '1', task_status: 'done',
  } } });

  act(() => { renderer = create(render()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  selected = other;
  act(() => renderer!.update(render()));
  expect(latest.generationPhase).toBeUndefined();
  expect(latest.readingGenerating).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  await act(async () => { generate.mock.calls[1][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('writing');
  await act(async () => { generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('writing');

  selected = data;
  act(() => renderer!.update(render()));
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(latest.readingGenerating).toBe(true);
  expect(latest.generationPhase).toBe('reviewing');
  expect(generate).toHaveBeenCalledTimes(2);
  await act(async () => { finishFirst(completed('First source result')); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBeUndefined();
  expect(latest.readingGenerating).toBe(false);
  expect(latest.copy?.cards.task.title).toBe('First source result');

  selected = other;
  act(() => renderer!.update(render()));
  expect(latest.generationPhase).toBe('writing');
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBe('writing');
  await act(async () => { finishOther(completed('Other source result')); await vi.advanceTimersByTimeAsync(25); });
  expect(latest.generationPhase).toBeUndefined();
  expect(latest.readingGenerating).toBe(false);
  expect(client.getQueryData<MapCopy>(key)?.cards.task.title).toBe('First source result');
  expect(client.getQueryData<MapCopy>(otherKey)?.cards.task.title).toBe('Other source result');
  expect(generate.mock.calls.map(call => call[1])).toEqual(['research', 'another-project']);
  expect(generate).toHaveBeenCalledTimes(2);
});

it("explains what the reader opened without asking whether the project's daemon is running", async () => {
  // A map is read most when the work is over. Whether the daemon is alive is
  // the panel's concern (it stops the live dot); the explanation is written by
  // a separate read-only turn and must not be held back by it again.
  const hook = readFileSync(new URL("../map/useMapCopy.ts", import.meta.url), "utf8");
  expect(hook).not.toMatch(/\bpaused\b\s*(?:\|\||=|,)/);
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue(empty);
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(750); });
  expect(generate).toHaveBeenCalledTimes(1);
});

it('honors a persisted failure cooldown even when the cache has no cards', async () => {
  client.setQueryData(key, { ...empty, retry_after: 300,
    generation_error: { code: 'map_timeout', message: 'Cached fallback' } });
  const generate = vi.spyOn(api, 'generateMapCopy').mockResolvedValue({ ...empty,
    generation_error: null, retry_after: 0 });
  act(() => { renderer = create(tree()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(299999); });
  expect(generate).not.toHaveBeenCalled();
  expect(latest.generationError?.message).toBe('Cached fallback');
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(generate).toHaveBeenCalledTimes(1);
});

it("never schedules generation in read-only mode", async () => {
  client.setQueryData(key, { ...empty, version: 15, cards: { task: {
    title: '旧中文标题', summary: 'Old summary', detail: 'Original detail', generated_at: 1, version: 14,
    task_revision: '1', task_status: 'done',
  } } });
  const generate = vi.spyOn(api, "generateMapCopy").mockResolvedValue(empty);
  act(() => { renderer = create(tree(false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(121000); });
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
