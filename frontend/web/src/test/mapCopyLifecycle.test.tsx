import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dataset } from "../map/model";
import type { MapCopy } from "../map/presentation";
import { useMapCopy } from "../map/useMapCopy";

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
