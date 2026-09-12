import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset, MapEvent, MapTask } from "../map/model";
import type { CardRequest, MapCopy } from "../map/presentation";
import { focusedCopyRequests, prewarmRequests, useMapCopy } from "../map/useMapCopy";
import { buildSubmap } from "../map/submap";

const task = (id: string, status: string): MapTask => ({
  id, title: id, objective: id, status, deps: [], revision: "1",
});
const events: MapEvent[] = ["a", "b"].flatMap((id) => [
  "life.mission.started",
  "round.main.completed",
  "round.review.completed",
  "life.mission.completed",
].map((type, i) => ({ id: `${id}${i}`, type, ts: i, item_id: id, text: "" })));
const data: Dataset = {
  id: "live:research",
  title: "Research",
  description: "",
  kind: "live",
  read_only: false,
  tasks: [task("a", "running"), task("b", "done"), task("c", "pending")],
  events,
};
const key = ["map-copy", "project", "research", "en-US", "session"];
const empty: MapCopy = { cards: {}, relations: [], available: true };
let client: QueryClient;
let renderer: ReactTestRenderer | undefined;

function Probe({ focused, prewarm, readingKey, dataset = data }: { focused: string | null; prewarm: boolean; readingKey?: string | null; dataset?: Dataset }) {
  useMapCopy(dataset, focused, false, true, undefined, "session", false, prewarm, readingKey);
  return null;
}
const tree = (focused: string | null, prewarm = true, readingKey?: string | null, dataset = data) => (
  <QueryClientProvider client={client}><Probe focused={focused} prewarm={prewarm} readingKey={readingKey} dataset={dataset} /></QueryClientProvider>
);
type Generate = { mock: { calls: unknown[][] } };
const requested = (generate: Generate): CardRequest[][] =>
  generate.mock.calls.map((call) => (call[2] as { cards: CardRequest[] }).cards);

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

it("warms finished tasks first and never the task that is open", () => {
  const keys = prewarmRequests(data, false, "a").map((r) => r.task_id);
  expect(keys[0]).toBe("b");
  expect(keys).not.toContain("a");
  expect(keys).toContain("c");
  expect(prewarmRequests(data, false, "a", 2)).toHaveLength(2);
});

it("writes the cards on screen before warming steps of other tasks", async () => {
  // Every request comes back written, as the server cache would hold it.
  const generate = vi.spyOn(api, "generateMapCopy").mockImplementation((_source, _name, body) =>
    Promise.resolve({
      ...empty,
      cards: Object.fromEntries(body.cards.map((c) => [c.key, {
        title: c.key, summary: c.key, detail: c.key, generated_at: 1,
        task_revision: "1", task_status: data.tasks.find((t) => t.id === c.task_id)?.status,
        event_ids: c.event_ids,
      }])),
    }));
  act(() => { renderer = create(tree("a")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  const [first] = requested(generate);
  expect(first).toHaveLength(8);
  // The open task's own card and its steps, then the other task cards.
  expect(first[0].key).toBe("a");
  expect(first.filter((c) => c.task_id === "a").length).toBeGreaterThan(1);
  expect(first.map((c) => c.key)).toContain("b");
  // Later batches reach the steps of the finished task before the pending one.
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  const later = requested(generate).slice(1).flat();
  expect(later.some((c) => c.task_id === "b" && c.key !== "b")).toBe(true);
  // Once everything is written the canvas goes quiet instead of asking again.
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  const calls = generate.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(generate.mock.calls.length).toBe(calls);
  expect(requested(generate).flat().map((c) => c.key)).toContain("c:brief");
});

it("does not generate unopened history or a merely focused task when warming is off", async () => {
  const generate = vi.spyOn(api, "generateMapCopy").mockResolvedValue(empty);
  act(() => { renderer = create(tree(null, false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => renderer!.update(tree('b', false, null)));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => renderer!.update(tree('b', false, 'b')));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(requested(generate)[0].map(card => card.key)).toEqual(['b']);
});

it("requests only the explicitly read step and cancels a closed reader's debounce", async () => {
  const steps = buildSubmap(data.tasks[1], data.events, false);
  const chosen = steps.find(step => step.kind === 'review')!;
  expect(focusedCopyRequests(data, steps, 'b', chosen.id).map(card => card.key)).toEqual([chosen.id]);
  expect(focusedCopyRequests(data, steps, 'a', chosen.id)).toEqual([]);
  const generate = vi.spyOn(api, "generateMapCopy").mockResolvedValue(empty);
  act(() => { renderer = create(tree('b', false, chosen.id)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  act(() => renderer!.update(tree('b', false, null)));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(generate).not.toHaveBeenCalled();
  act(() => renderer!.update(tree('b', false, chosen.id)));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(requested(generate)[0].map(card => card.key)).toEqual([chosen.id]);
});

it('keeps an open root explanation stable during tool/segment updates and refreshes it for a milestone', async () => {
  client.setQueryData(key, { ...empty, version: 15 });
  let revision = 0;
  const generate = vi.spyOn(api, 'generateMapCopy').mockImplementation(async (_source, _name, body) => ({
    cards: Object.fromEntries(body.cards.map(card => [card.key, {
      title: 'Explanation', summary: '', detail: '', version: 15, generated_at: ++revision, copy_revision: revision,
      task_revision: '1', task_status: 'running', event_ids: card.event_ids,
    }])), relations: [],
  }));
  act(() => { renderer = create(tree('a', false, 'a')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(1);
  const withTools = { ...data, events: [...data.events,
    { id: 'tool-update', item_id: 'a', type: 'engineer.progress', ts: 20, text: 'Read another file' },
    { id: 'new-segment', item_id: 'a', type: 'work.segment', ts: 21, text: 'More work' },
  ] };
  act(() => renderer!.update(tree('a', false, 'a', withTools)));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(generate).toHaveBeenCalledTimes(1);
  const reviewed = { ...withTools, events: [...withTools.events,
    { id: 'new-review', item_id: 'a', type: 'round.review.completed', ts: 30, text: 'A new review' },
  ] };
  act(() => renderer!.update(tree('a', false, 'a', reviewed)));
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(requested(generate)[1]).toHaveLength(1);
  expect(requested(generate)[1][0].event_ids).toContain('new-review');
  expect(requested(generate)[1][0].event_ids).not.toContain('new-segment');
});

it('uses the current attempt’s milestones and preserves real question/answer evidence for turn tasks', () => {
  const turn: MapTask = { ...task('turn', 'done'), kind: 'turn', started_ts: 100 };
  const value = { ...data, tasks: [turn], events: [
    { id: 'old-review', item_id: 'turn', type: 'round.review.completed', ts: 90, text: 'Older attempt' },
    { id: 'question', item_id: 'turn', type: 'turn.asked', ts: 100, text: 'Question' },
    { id: 'answer', item_id: 'turn', type: 'turn.replied', ts: 110, text: 'Answer' },
    { id: 'tool', item_id: 'turn', type: 'work.segment', ts: 115, text: 'Tool trace' },
    { id: 'other-answer', item_id: 'another', type: 'turn.replied', ts: 120, text: 'Other answer' },
  ] };
  expect(focusedCopyRequests(value, [], 'turn', 'turn')).toEqual([
    { key: 'turn', task_id: 'turn', kind: 'task', event_ids: ['question', 'answer'] },
  ]);
});
