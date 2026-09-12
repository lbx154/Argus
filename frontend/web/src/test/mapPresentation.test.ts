import { describe, it, expect } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";
import { attentionReason, mergeMapCopy, needsCardCopy, referenceText, requestsFor, splitDraft } from "../map/presentation";
import { buildSubmap } from "../map/submap";
import type { Dataset, MapEvent } from "../map/model";

it("shows the recorded question or most recent explicit failure, never an unrelated summary", () => {
  const task: MapTask = { id: "a", title: "Routing", objective: "", status: "failed", deps: [] };
  const event: MapEvent = { id: "old", item_id: "a", ts: 1, type: "life.mission.failed", text: "Old failure" };
  const events = [
    { ...event, id: "new", ts: 3, reason: "Route cost differs from the baseline" },
    event,
    { ...event, id: "other", ts: 4, item_id: "b", reason: "Another task" },
    { ...event, id: "progress", ts: 5, type: "round.start", text: "Inspecting files" },
  ];
  expect(attentionReason(task, events, false)).toBe("Route cost differs from the baseline");
  expect(attentionReason({ ...task, pending_question: "Choose a route" }, events, false)).toBe("Choose a route");
  expect(attentionReason(task, [event], false)).toBe("Old failure");
  expect(attentionReason(task, [], false)).toBe("This task did not finish, and the record does not say why.");
});

it("keeps new model settings when an earlier generation finishes", () => {
  const previous = { cards: {}, relations: [], model_revision: "new-model", available: false };
  const result = { cards: {}, relations: [], model_revision: "old-model" };
  expect(mergeMapCopy(previous, result, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
  expect(mergeMapCopy(result, previous, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
});

it('refreshes stable old cards for a newer server version and keeps late responses from downgrading it', () => {
  const task: MapTask = { id: 'historical', title: 'Historical work', objective: '', status: 'done', revision: 'r1' };
  const data: Dataset = { id: 'live:project', kind: 'live', title: '', description: '', read_only: false, tasks: [task], events: [] };
  const request = { key: task.id, task_id: task.id, kind: 'task', event_ids: [] };
  const card = { title: '旧中文标题', summary: 'Old summary', detail: 'Old details', generated_at: 1,
    version: 14, copy_revision: 1, task_revision: 'r1', task_status: 'done', event_ids: [] };
  const known = { version: 15, cards: { [task.id]: card }, relations: [] };
  expect(needsCardCopy(request, data, known)).toBe(true);
  const updated = { ...card, version: 15, copy_revision: 2, generated_at: 2, title: '新版中文标题' };
  // POST results need not repeat the server's top-level version.
  const current = mergeMapCopy(known, { cards: { [task.id]: updated }, relations: [] });
  expect(current.version).toBe(15);
  expect(needsCardCopy(request, data, current)).toBe(false);
  const late = mergeMapCopy(current, { version: 14, cards: { [task.id]: { ...card, copy_revision: 3, generated_at: 3 } }, relations: [] });
  expect(late.version).toBe(15);
  expect(late.cards[task.id].title).toBe(updated.title);
});

describe("map presentation and references", () => {
  it("keeps source and exact node identity in the same composer draft", () => {
    const ref = {
      source: "live:s-science",
      task_id: "a",
      task_title: "覆盖率比较",
      step_id: "e1",
      step_title: "审查与反馈",
      event_ids: ["e1"],
      team_id: 'idea-portfolio',
      team_task_id: 'idea-portfolio-route-01',
    };
    const draft = referenceText(ref) + "增加极端偏移条件";
    expect(splitDraft(draft)).toEqual({
      refs: [ref],
      text: "增加极端偏移条件",
    });
    expect(splitDraft("[[Argus引用 broken]]").text).toBe(
      "[[Argus引用 broken]]",
    );
  });
  it("connects all components without changing real dependencies or inventing missing nodes", () => {
    const tasks: MapTask[] = ["a", "b", "c", "d"].map((id, i) => ({
      id,
      title: id,
      objective: id,
      status: "done",
      deps: id === "b" ? ["a"] : [],
      ts: i,
    }));
    const graph = buildMap(tasks);
    const links = connectMap(
      graph,
      [
        {
          source: "b",
          target: "c",
          label: "对比实验",
          evidence: "shared methods",
        },
        { source: "ghost", target: "d", label: "bad", evidence: "bad" },
      ],
      true,
    );
    expect(links).toHaveLength(3);
    expect(links.find((l) => l.kind === "semantic")?.source).toBe("b");
    expect(links.find((l) => l.kind === "context")?.target).toBe("d");
    expect(graph.links).toHaveLength(1);
    expect(tasks[2].deps).toEqual([]);
  });
});

it("leaves malformed reference fields as editable text", () => {
  const malformed =
    '[[Argus引用 {"source":"live:s","task_id":"a","task_title":{},"event_ids":[]}]]';
  expect(splitDraft(malformed)).toEqual({ refs: [], text: malformed });
});

it("does not use a later successful attempt as evidence for an earlier failed result", () => {
  const task: MapTask = {
    id: "a",
    title: "比较覆盖率",
    objective: "比较方法",
    status: "done",
    deps: [],
  };
  const events: MapEvent[] = [
    "life.mission.started",
    "round.main.completed",
    "round.review.completed",
    "life.mission.failed",
    "life.mission.started",
    "round.main.completed",
    "round.review.completed",
    "life.mission.completed",
  ].map((type, ts) => ({ id: `e${ts}`, type, ts, item_id: "a", text: "" }));
  const data = { id: "live:s-test", tasks: [task], events } as Dataset;
  const requests = requestsFor(data, buildSubmap(task, events, true), task.id);
  expect(requests.find((r) => r.key === "e3")?.event_ids).toEqual([
    "e1",
    "e2",
    "e3",
  ]);
  expect(requests.find((r) => r.key === "e7")?.event_ids).toEqual([
    "e5",
    "e6",
    "e7",
  ]);
});

it("carries the quote-time locale through the marker and rejects bad types", () => {
  const ref = {
    source: "live:s-test",
    task_id: "a",
    task_title: "Coverage",
    event_ids: [],
    lang: "en",
  };
  const parsed = splitDraft(referenceText(ref) + "question");
  expect(parsed.refs).toEqual([ref]);
  expect(parsed.text).toBe("question");
  const bad = referenceText(ref).replace('"en"', "3");
  expect(splitDraft(bad).refs).toEqual([]);
  expect(splitDraft(bad).text).toBe(bad);
});
