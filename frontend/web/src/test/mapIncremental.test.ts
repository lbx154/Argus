import { expect, it } from "vitest";
import { buildMap, latestMissionCompletion, type Dataset } from "../map/model";
import { layoutScene } from "../map/submap";
import { mapIsPaused, mergeMapProgress } from "../map/incremental";
import type { Snapshot } from "../api";

const first: Dataset = {
  id: "live:s-map", kind: "live", title: "Study", description: "", read_only: false,
  tasks: [{ id: "a", title: "Method", objective: "Compare", status: "running", deps: [], ts: 1, revision: "r1", content_revision: "c1" }],
  events: [{ id: "e1", item_id: "a", type: "round.review.completed", ts: 2, text: "Check", revision: "v1" }],
  cursor: "one", history_cursor: "epoch:1", history_loading: false,
};

it("retains loaded records and object identities while connecting new work", () => {
  const next = mergeMapProgress(first, { ...first, incremental: true,
    tasks: [{ id: "b", title: "Validate", objective: "Evaluate", status: "pending", deps: ["a"], ts: 3 }],
    events: [{ id: "e2", item_id: "b", type: "life.mission.started", ts: 4, text: "" }], cursor: "two" });
  expect(next.tasks.map((t) => t.id)).toEqual(["a", "b"]);
  expect(next.tasks[0]).toBe(first.tasks[0]);
  expect(next.events[0]).toBe(first.events[0]);
  expect(buildMap(next.tasks).links[0]).toMatchObject({ source: "a", target: "b", kind: "dependency" });
  const stable = mergeMapProgress(next, { ...next, incremental: true, tasks: [], events: [] });
  expect(stable).toBe(next);
});

it("keeps records in the order they happened when a page brings an earlier one", () => {
  // A work segment is delivered when its round closes, after records that followed its start.
  const closed = { id: "seg", item_id: "a", type: "work.segment", ts: 1.5, text: "Checking." };
  const next = mergeMapProgress(first, { ...first, incremental: true, tasks: [], events: [closed] });
  expect(next.events.map((e) => e.id)).toEqual(["seg", "e1"]);
});

it("merges paged history without duplicates and drops explicitly removed tasks", () => {
  const page = { ...first, tasks_complete: true, incremental: true };
  expect(mergeMapProgress(first, page).events).toHaveLength(1);
  const removed = mergeMapProgress(first, { ...first, incremental: true, tasks: [], events: [], removed_task_ids: ["a"] });
  expect(removed.tasks).toEqual([]);
  expect(removed.events).toEqual([]);
  expect(mergeMapProgress(first, { ...first, reset_history: true, events: [] }).events).toEqual([]);
});

it("keeps completion scope across event-only pages without leaking an earlier attempt into a new finish", () => {
  const done = { ...first, tasks: [{ ...first.tasks[0], status: "done", started_ts: 2, finished_ts: 4, attempt: 1 }], events: [] };
  const partial = { id: "partial", item_id: "a", type: "life.mission.completed", ts: 5, text: "",
    success: true, overall_complete: false, campaign_continues: true, attempt: 1 };
  const page = mergeMapProgress(done, { ...done, incremental: true, tasks: [], events: [partial] });
  const next = mergeMapProgress(page, { ...done, incremental: true, tasks: [], events: first.events });
  expect(layoutScene(buildMap(next.tasks), next.events, false).cards[0].completionScope).toContain("further work remained");
  expect(next.tasks[0].status).toBe("done");

  const retried = { ...next.tasks[0], started_ts: 10, finished_ts: 12, attempt: 2 };
  const retryPage = mergeMapProgress(next, { ...done, incremental: true, tasks: [retried], events: [] });
  expect(latestMissionCompletion(retried, retryPage.events)).toBeUndefined();
  expect(layoutScene(buildMap(retryPage.tasks), retryPage.events, false).cards[0].completionScope).toBeUndefined();
  const legacyFinish = { id: "later", item_id: "a", type: "life.mission.completed", ts: 13, text: "", success: true };
  const finished = mergeMapProgress(retryPage, { ...done, incremental: true, tasks: [], events: [legacyFinish] });
  const olderPage = mergeMapProgress(finished, { ...done, incremental: true, tasks: [], events: [partial] });
  expect(latestMissionCompletion(retried, olderPage.events)).toEqual(legacyFinish);
  expect(layoutScene(buildMap(olderPage.tasks), olderPage.events, false).cards[0].completionScope).toBeUndefined();
  expect(latestMissionCompletion(retried, [{ ...partial, ts: 15 }])).toBeUndefined();
  expect(latestMissionCompletion(retried, [legacyFinish, {
    id: "restart", item_id: "a", type: "life.mission.started", ts: 14, text: "",
  }])).toBeUndefined();
});

it("pauses from daemon liveness while a foreground request keeps the map active", () => {
  const snapshot = { daemon: { alive: false }, roles: [] } as unknown as Snapshot;
  expect(mapIsPaused(snapshot)).toBe(true);
  expect(mapIsPaused({ ...snapshot, daemon: { ...snapshot.daemon, alive: true } })).toBe(false);
  expect(mapIsPaused({ ...snapshot, manager_requests: [{ request_id: 'self', status: 'running' }] })).toBe(false);
});
