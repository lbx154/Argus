import { describe, expect, it } from "vitest";
import { attentionTasks, buildMap, currentTask, replayTasks, statusKey, supersededByLaterWork, taskDependencies, latestCertifiedTask, type MapTask, type MapEvent } from "../map/model";

const task = (
  id: string,
  deps: string[] = [],
  status = "pending",
  ts = 0,
): MapTask => ({ id, title: id, objective: "", deps, status, ts });
describe("progress map data semantics", () => {
  it("separates a recorded review error from execution failure without rewriting task status", () => {
    const failed = { ...task("failed", [], "failed"), outcome: { review_status: "unavailable" } };
    expect(statusKey(failed)).toBe("review_unavailable");
    expect(failed.status).toBe("failed");
    expect(statusKey({ ...failed, status: "running" })).toBe("running");
    expect(statusKey({ ...failed, pending_question: "Choose" })).toBe("question");
    expect(statusKey(task("plain-failure", [], "failed"))).toBe("failed");
    expect(statusKey({ ...failed, outcome: { review_status: "blocked" } })).toBe("failed");
  });
  it("links only explicit final acceptance, not an intermediate success or later conversation", () => {
    const accepted = { ...task("final", [], "done", 20), started_ts: 19, finished_ts: 20,
      outcome: { execution_status: "completed", review_status: "done", stage_certification: "certified" } };
    const completion: MapEvent = { id: "end", item_id: "final", type: "life.mission.completed",
      ts: 21, text: "", overall_complete: true };
    const rows = [task("failed", [], "failed", 10), accepted, { ...task("hello", [], "done", 30), kind: "turn" }];
    expect(latestCertifiedTask(rows, [completion])).toBe(accepted);
    expect(latestCertifiedTask(rows, [{ ...completion, overall_complete: false }])).toBeUndefined();
    expect(latestCertifiedTask(rows, [])).toBeUndefined();
    expect(latestCertifiedTask([{ ...accepted, status: "running" }], [completion])).toBeUndefined();
    expect(rows[0].status).toBe("failed");
  });
  it("orders actionable questions before failures without duplicating or mutating tasks", () => {
    const rows = [
      task("failed", [], "failed"),
      { ...task("question", [], "failed"), pending_question: "Choose a route" },
      { ...task("finished", [], "done"), pending_question: "Old question" },
      task("running", [], "running"),
    ];
    expect(attentionTasks(rows).map((row) => row.id)).toEqual(["question", "failed"]);
    expect(rows.map((row) => row.id)).toEqual(["failed", "question", "finished", "running"]);
    expect(currentTask(rows)?.id).toBe("running");
    expect(currentTask(rows.slice(0, 3))?.id).toBe("question");
    expect(currentTask([rows[0], task("planned")])?.id).toBe("failed");
    expect(currentTask([task("finished", [], "done"), task("planned")])?.id).toBe("planned");
    expect(currentTask([])).toBeUndefined();
  });
  it("drops a failed card from attention once later work has moved past it", () => {
    const stale = task("first", [], "failed", 100);
    const rows = [stale, task("selector", [], "done", 200), task("implement", [], "running", 300)];
    expect(supersededByLaterWork(stale, rows)).toBe(true);
    expect(attentionTasks(rows)).toEqual([]);
    expect(currentTask(rows)?.id).toBe("implement");
    // The frontier failure still needs the reader, and so does any open question.
    const frontier = task("latest", [], "failed", 400);
    expect(attentionTasks([...rows, frontier]).map((row) => row.id)).toEqual(["latest"]);
    const asked = { ...stale, pending_question: "Which venue?" };
    expect(attentionTasks([asked, rows[1], rows[2]]).map((row) => row.id)).toEqual(["first"]);
    // Without timestamps nothing is known to be later, so the failure stays visible.
    expect(attentionTasks([task("f", [], "failed"), task("d", [], "done")]).map((row) => row.id)).toEqual(["f"]);
  });
  it("traces only direct recorded dependencies, retaining missing references", () => {
    const graph = buildMap([
      task("ancestor"), task("parent", ["ancestor"]),
      task("selected", ["parent", "outside"]), task("child", ["selected"]),
      task("grandchild", ["child"]), task("unrelated"),
    ]);
    graph.links.push({ id: "semantic", source: "unrelated", target: "selected", kind: "semantic" });
    const dependencies = taskDependencies(graph, "selected");
    expect(dependencies.upstream.map((row) => row.id).sort()).toEqual(["outside", "parent"]);
    expect(dependencies.downstream.map((row) => row.id)).toEqual(["child"]);
    expect(dependencies.upstream.find((row) => row.id === "outside")?.status).toBe("missing");
    expect(taskDependencies(graph, "unrelated")).toEqual({ upstream: [], downstream: [] });
  });
  it("keeps both fan-in edges, independent of parent_branch_id simplification", () => {
    const graph = buildMap([task("a"), task("b"), task("join", ["a", "b"])]);
    expect(
      graph.links
        .filter((e) => e.kind === "dependency")
        .map((e) => [e.source, e.target]),
    ).toEqual([
      ["a", "join"],
      ["b", "join"],
    ]);
  });
  it("does not invent dependencies for legacy chronological rows", () => {
    const graph = buildMap([
      task("b", [], "failed", 2),
      task("a", [], "done", 1),
    ]);
    expect(graph.links).toHaveLength(0);
    expect(graph.tasks.map((t) => t.status)).toEqual(["done", "failed"]);
  });
  it("represents missing parents without dropping recorded edges", () => {
    const graph = buildMap([task("child", ["outside"])]);
    expect(graph.missing).toBe(1);
    expect(graph.tasks.find((t) => t.id === "outside")?.status).toBe("missing");
    expect(graph.links[0]).toMatchObject({
      source: "outside",
      target: "child",
      missing: true,
    });
  });
  it("retains and identifies cyclic dependencies", () => {
    const graph = buildMap([task("a", ["b"]), task("b", ["a"])]);
    expect(graph.cyclic).toBe(true);
    expect(graph.links.filter((e) => e.kind === "dependency")).toHaveLength(2);
    expect(graph.links.every((l) => l.cycle)).toBe(true);
  });
  it("does not label downstream work or a bridge between cycles as cyclic", () => {
    const graph = buildMap([
      task("a", ["b"]),
      task("b", ["a"]),
      task("c", ["b", "d"]),
      task("d", ["c"]),
      task("downstream", ["d"]),
      task("self", ["self"]),
    ]);
    expect(graph.cyclic).toBe(true);
    expect(graph.links.filter((link) => link.cycle).map((link) => [link.source, link.target]))
      .toEqual([["b", "a"], ["a", "b"], ["d", "c"], ["c", "d"], ["self", "self"]]);
    expect(graph.links).toHaveLength(7);
  });
  it("is idempotent when task states arrive twice", () => {
    const rows = [task("a"), task("b", ["a"])];
    expect(buildMap([...rows, ...rows])).toEqual(buildMap(rows));
  });
  it("preserves a replaced branch and highlights pending questions separately", () => {
    const old = task("old", [], "superseded");
    const question = {
      ...task("new", [], "failed"),
      pending_question: "Choose a route",
    };
    expect(buildMap([old, question]).tasks).toHaveLength(2);
    expect(statusKey(question)).toBe("question");
    expect(statusKey(old)).toBe("superseded");
  });
  it("supports empty and single-task maps", () => {
    expect(buildMap([]).tasks).toEqual([]);
    expect(buildMap([task("one")]).links).toEqual([]);
  });
  it("does not present an unrecognized state as planned work", () => {
    expect(statusKey(task("legacy", [], "unknown-legacy-state"))).toBe(
      "unknown",
    );
  });
  it("reveals history deterministically without pretending to reconstruct old states", () => {
    const tasks = [task("b", [], "done", 2), task("a", [], "failed", 1)];
    expect(replayTasks(tasks, 1)).toEqual([tasks[1]]);
    expect(tasks[0].status).toBe("done");
  });
  it("represents a plan replacement without inventing a one-to-one task dependency", () => {
    const old = {
      ...task("old", [], "superseded", 1),
      superseded_by_plan_id: "p2",
    };
    const next = { ...task("new", [], "pending", 2), plan_id: "p2" };
    const sibling = { ...task("sibling", [], "pending", 3), plan_id: "p2" };
    const graph = buildMap([old, next, sibling]);
    expect(graph.links).toEqual([
      expect.objectContaining({
        source: "old",
        target: "new",
        target_plan_id: "p2",
        target_count: 2,
        kind: "replacement",
      }),
    ]);
    expect(graph.tasks.map((t) => t.id)).toEqual(["old", "new", "sibling"]);
  });
  it("does not fabricate a replacement target outside the selected data", () => {
    expect(
      buildMap([{ ...task("old"), superseded_by_plan_id: "absent" }]).links,
    ).toEqual([]);
  });
});
