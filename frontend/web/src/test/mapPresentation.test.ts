import { describe, it, expect } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";
import { mergeMapCopy, referenceText, requestsFor, splitDraft } from "../map/presentation";
import { buildSubmap } from "../map/submap";
import type { Dataset, MapEvent } from "../map/model";

it("keeps new model settings when an earlier generation finishes", () => {
  const previous = { cards: {}, relations: [], model_revision: "new-model", available: false };
  const result = { cards: {}, relations: [], model_revision: "old-model" };
  expect(mergeMapCopy(previous, result, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
  expect(mergeMapCopy(result, previous, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
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
