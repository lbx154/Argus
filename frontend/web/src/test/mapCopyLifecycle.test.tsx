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
  vi.useRealTimers();
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
