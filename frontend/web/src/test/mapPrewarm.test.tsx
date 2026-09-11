import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset, MapEvent, MapTask } from "../map/model";
import type { CardRequest, MapCopy } from "../map/presentation";
import { prewarmRequests, useMapCopy } from "../map/useMapCopy";

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

function Probe({ focused, prewarm }: { focused: string | null; prewarm: boolean }) {
  useMapCopy(data, focused, false, true, undefined, "session", false, prewarm);
  return null;
}
const tree = (focused: string | null, prewarm = true) => (
  <QueryClientProvider client={client}><Probe focused={focused} prewarm={prewarm} /></QueryClientProvider>
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

it("stops at the visible cards when warming is turned off", async () => {
  const generate = vi.spyOn(api, "generateMapCopy").mockResolvedValue(empty);
  act(() => { renderer = create(tree(null, false)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(700); });
  const [first] = requested(generate);
  expect(first.map((c) => c.key)).toEqual(["a", "b", "c"]);
});
