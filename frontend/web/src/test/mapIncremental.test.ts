import { expect, it } from "vitest";
import { buildMap, type Dataset } from "../map/model";
import { mapIsPaused, mergeMapProgress, parseMapSelection } from "../map/incremental";
import { mergeMapCopy, needsCardCopy, type CardCopy } from "../map/presentation";
import type { Snapshot } from "../api";

const first: Dataset = {
  id: "live:s-map", kind: "live", title: "Study", description: "", read_only: false,
  tasks: [{ id: "a", title: "Method", objective: "Compare", status: "running", deps: [], ts: 1, revision: "r1", content_revision: "c1" }],
  events: [{ id: "e1", item_id: "a", type: "round.review.completed", ts: 2, text: "Check", revision: "v1" }],
  cursor: "one", history_cursor: "epoch:1", history_loading: false,
};
const saved: CardCopy = {
  title: "Method", summary: "Checked", detail: "Evidence", generated_at: 1,
  task_revision: "r1", task_content_revision: "c1", task_status: "running",
  event_ids: ["e1"], event_revisions: ["v1"], model_revision: "earlier",
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

it("merges paged history without duplicates and drops explicitly removed tasks", () => {
  const page = { ...first, tasks_complete: true, incremental: true };
  expect(mergeMapProgress(first, page).events).toHaveLength(1);
  const removed = mergeMapProgress(first, { ...first, incremental: true, tasks: [], events: [], removed_task_ids: ["a"] });
  expect(removed.tasks).toEqual([]);
  expect(removed.events).toEqual([]);
  expect(mergeMapProgress(first, { ...first, reset_history: true, events: [] }).events).toEqual([]);
});

it("keeps closed step copy when its task progresses or model selection changes", () => {
  const progressed = { ...first, tasks: [{ ...first.tasks[0], status: "done", revision: "r2", finished_ts: 9999 }] };
  const copy = { cards: { e1: saved }, relations: [], model_revision: "different" };
  const request = { key: "e1", task_id: "a", kind: "review", event_ids: ["e1"] };
  expect(needsCardCopy(request, progressed, copy)).toBe(false);
  expect(needsCardCopy(request, { ...progressed, events: [{ ...first.events[0], revision: "corrected" }] }, copy)).toBe(true);
  expect(needsCardCopy(request, { ...progressed, tasks: [{ ...progressed.tasks[0], content_revision: "edited-objective" }] }, copy)).toBe(true);
});

it("updates task summaries on real progress and reuses richer cached evidence", () => {
  const request = { key: "a", task_id: "a", kind: "task", event_ids: [] };
  const copy = { cards: { a: saved }, relations: [] };
  expect(needsCardCopy(request, first, copy)).toBe(false);
  expect(needsCardCopy(request, first, { ...copy, version: 99 })).toBe(false);
  expect(needsCardCopy(request, { ...first, tasks: [{ ...first.tasks[0], revision: "new" }] }, copy)).toBe(true);
});

it("late responses cannot erase other cards or overwrite newer model results", () => {
  const previous = { cards: { a: { ...saved, generated_at: 9, model_revision: "new" }, b: saved }, relations: [], model_revision: "new" };
  const stale = { cards: { a: { ...saved, generated_at: 10 } }, relations: [], model_revision: "earlier" };
  const merged = mergeMapCopy(previous, stale, "earlier");
  expect(merged.cards).toEqual(previous.cards);
  expect(merged.model_revision).toBe("new");
});

it("pauses stopped sessions and an explicit operator pause without stopping bounded work", () => {
  const snapshot = { daemon: { alive: false }, roles: [] } as unknown as Snapshot;
  expect(mapIsPaused(snapshot)).toBe(true);
  expect(mapIsPaused({ ...snapshot, daemon: { ...snapshot.daemon, alive: true }, continuous: { enabled: false, objective: "", done_reason: "operator pause" } })).toBe(true);
  expect(mapIsPaused({ ...snapshot, daemon: { ...snapshot.daemon, alive: true }, continuous: { enabled: false, objective: "" } })).toBe(false);
});

it("accepts only an explicit valid saved range, including keeping the map off", () => {
  expect(parseMapSelection(null)).toBeNull();
  expect(parseMapSelection('{"mode":"current"}')).toBeNull();
  expect(parseMapSelection('{"mode":"current","since":-1,"eventSince":0}')).toBeNull();
  expect(parseMapSelection('{"mode":"off"}')).toEqual({ mode: "off" });
  expect(parseMapSelection('{"mode":"full"}')).toEqual({ mode: "full" });
});

it("orders cache updates by their revision even if wall-clock time moves backward", () => {
  const previous = { cards: { a: { ...saved, generated_at: 99, copy_revision: 2 } }, relations: [], cache_revision: 2 };
  const next = { cards: { a: { ...saved, generated_at: 1, copy_revision: 3 } },
    relations: [{ source: "a", target: "b", label: "验证", evidence: "比较方法", kind: "semantic" as const }], cache_revision: 3 };
  const merged = mergeMapCopy(previous, next);
  expect(merged.cards.a.copy_revision).toBe(3);
  expect(mergeMapCopy(merged, previous).relations).toEqual(next.relations);
});
