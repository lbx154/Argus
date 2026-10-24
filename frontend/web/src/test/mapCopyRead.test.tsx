import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset, MapEvent, MapTask } from "../map/model";
import type { MapCopy } from "../map/presentation";
import { focusedCopyRequests, useMapCopy } from "../map/useMapCopy";
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
let latest!: ReturnType<typeof useMapCopy>;

function Probe({ focused, readingKey }: { focused: string | null; readingKey?: string | null }) {
  latest = useMapCopy(data, focused, false, undefined, "session", readingKey);
  return null;
}
const tree = (focused: string | null, readingKey?: string | null) => (
  <QueryClientProvider client={client}><Probe focused={focused} readingKey={readingKey} /></QueryClientProvider>
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

it("opens a task or one of its steps without asking for an explanation to be written", async () => {
  const steps = buildSubmap(data.tasks[1], data.events, false);
  const chosen = steps.find(step => step.kind === 'review')!;
  act(() => { renderer = create(tree('b')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(latest.readingRequest?.key).toBe('b');
  expect(latest.copy).toEqual(empty);
  act(() => renderer!.update(tree('b', chosen.id)));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(latest.readingRequest?.key).toBe(chosen.id);
});

it("keeps showing explanations that were saved earlier", async () => {
  const saved: MapCopy = { ...empty, cards: { b: { title: "Saved title", summary: "Saved summary", detail: "Saved detail", generated_at: 5 } } };
  const read = vi.spyOn(api, "mapCopy").mockResolvedValue(saved);
  client.removeQueries({ queryKey: key });
  act(() => { renderer = create(tree('b')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(read).toHaveBeenCalledOnce();
  expect(latest.copy?.cards.b.title).toBe("Saved title");
});

it("selects only the explicitly read step of the focused task", () => {
  const steps = buildSubmap(data.tasks[1], data.events, false);
  const chosen = steps.find(step => step.kind === 'review')!;
  expect(focusedCopyRequests(data, steps, 'b', chosen.id).map(card => card.key)).toEqual([chosen.id]);
  expect(focusedCopyRequests(data, steps, 'a', chosen.id)).toEqual([]);
  expect(focusedCopyRequests(data, steps, 'b', null)).toEqual([]);
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

it('asks for no explanation of question cards, as a task or as its answer', () => {
  const value: Dataset = { ...data, tasks: [{ ...task('qa', 'done'), kind: 'turn', turn_kind: 'qa' }],
    events: [{ id: 'answer', item_id: 'qa', type: 'turn.replied', ts: 1, text: 'Already answered.' }] };
  expect(focusedCopyRequests(value, [], 'qa', 'qa')).toEqual([]);
  expect(focusedCopyRequests(value, [], 'qa', 'answer')).toEqual([]);
});
