import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset } from "../map/model";
import { useMapWords, type CardWords } from "../map/useMapWords";

// React Query runs interval refetches only in a browser; give it one.
vi.hoisted(() => { Object.assign(globalThis, { window: globalThis }); });

const data: Dataset = {
  id: "live:s-words", title: "Words", description: "", kind: "live", read_only: false,
  tasks: [{ id: "wrap", title: "Add wrap", objective: "Add wrap", status: "done", deps: [] }],
  events: [],
};
let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
let latest: Record<string, CardWords> = {};

function Probe({ dataset = data }: { dataset?: Dataset }) {
  latest = useMapWords(dataset, true, true, "s-words");
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("asks again when the server says to, so a finished task gets its words without a reload", async () => {
  const words = { title: "按词折行", summary: "审阅通过。" };
  const call = vi.spyOn(api, "mapCardWords")
    .mockResolvedValueOnce({ cards: {}, available: true })
    .mockResolvedValueOnce({ cards: {}, available: true, retry_after: 39 })
    .mockResolvedValue({ cards: { wrap: words }, available: true });
  act(() => { renderer = create(<QueryClientProvider client={client}><Probe /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(call).toHaveBeenCalledTimes(2);
  expect(latest).toEqual({});
  await act(async () => { await vi.advanceTimersByTimeAsync(39_000); });
  expect(call).toHaveBeenCalledTimes(3);
  expect(call.mock.calls[2][2]).toMatchObject({ tasks: ["wrap"], write: true });
  expect(latest).toEqual({ wrap: words });
  // Written: nothing more is asked for.
  await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
  expect(call).toHaveBeenCalledTimes(3);
});

it("asks again when an answered question sets a task going", async () => {
  const waiting = { ...data, tasks: [{ ...data.tasks[0], status: "parked", pending_question: "A or B?" }] };
  const call = vi.spyOn(api, "mapCardWords").mockResolvedValue({ cards: { wrap: { title: "等你选择", summary: "" } }, available: true });
  const mount = (dataset: Dataset) => <QueryClientProvider client={client}><Probe dataset={dataset} /></QueryClientProvider>;
  act(() => { renderer = create(mount(waiting)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(call).toHaveBeenCalledTimes(1);
  act(() => renderer!.update(mount({ ...data, tasks: [{ ...data.tasks[0], status: "running" }] })));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(call).toHaveBeenCalledTimes(2);
});
