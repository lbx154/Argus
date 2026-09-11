import { describe, expect, it } from "vitest";
import {
  branchGroupTitle,
  buildMap,
  foldTeamBranches,
  promoteTeamBranches,
  type MapEvent,
  type MapTask,
} from "../map/model";
import { layoutGraph } from "../map/graphLayout";
import { BRANCH_FRAME, GROUP_FRAME } from "../map/BranchNode";

const task = (id: string, status = "running", ts = 0): MapTask =>
  ({ id, title: id, objective: "", status, deps: [], ts });

const team = (id: string, item_id: string, extra: Partial<MapEvent> = {}): MapEvent =>
  ({ id, item_id, type: "team.task", ts: 0, text: "", ...extra });

/** Twelve research routes, twelve independent reviews that each depend on
 * one route, and one selection that depends on every review: the shape of
 * an idea portfolio, with every subtask still waiting to start. */
function portfolio(status: (i: number, review: boolean) => string = () => "pending"): MapEvent[] {
  const routes = Array.from({ length: 12 }, (_, i) =>
    team(`team:route${i + 1}`, "m", {
      ts: 10 + i, team_role: "idea-route", team_task_id: `portfolio/route-${i + 1}`, status: status(i, false),
    }));
  const reviews = Array.from({ length: 12 }, (_, i) =>
    team(`team:review${i + 1}`, "m", {
      ts: 30 + i, team_role: "idea-review", team_task_id: `review-route-${i + 1}`,
      deps: [`team:route${i + 1}`], status: status(i, true),
    }));
  const selector = team("team:sel", "m", {
    ts: 50, team_role: "idea-selector", team_task_id: "selector",
    deps: reviews.map((r) => r.id), status: "pending",
  });
  return [...routes, ...reviews, selector];
}

const promote = (events: MapEvent[], cap = 48) =>
  promoteTeamBranches(buildMap([task("before", "done", 1), task("m", "failed", 2), task("after", "pending", 3)]), events, true, cap);

describe("folding parallel subtasks that share a state", () => {
  it("names a group in one sentence, in both languages", () => {
    const counts = [{ role: "idea-route", count: 12 }, { role: "idea-review", count: 12 }];
    expect(branchGroupTitle(counts, "pending", true)).toBe("12 条研究路线与 12 次独立复核尚未开始");
    expect(branchGroupTitle(counts, "pending", false)).toBe("12 research routes and 12 independent reviews have not started");
    expect(branchGroupTitle([...counts, { role: "idea-selector", count: 1 }], "failed", true))
      .toBe("12 条研究路线、12 次独立复核与 1 次方案选择没有完成");
    expect(branchGroupTitle([{ role: "idea-route", count: 3 }], "done", false)).toBe("3 research routes are complete");
    expect(branchGroupTitle([{ role: "", count: 4 }], "running", false)).toBe("4 parallel subtasks are under way");
  });

  it("folds a whole waiting portfolio into one node that the fan enters and leaves", () => {
    const promoted = promote(portfolio());
    const folded = foldTeamBranches(promoted, new Set(), true);
    const branches = folded.tasks.filter((t) => t.branch);
    expect(branches).toHaveLength(1);
    const group = branches[0];
    expect(group.id).toBe("team-group:m:pending");
    expect(group.title).toBe("12 条研究路线、12 次独立复核与 1 次方案选择尚未开始");
    expect(group.group).toMatchObject({ status: "pending", expanded: false });
    expect(group.group!.members).toHaveLength(25);
    // Promotion lists the branches after every card; the group stands where
    // its first subtask stood in that list, so reading order holds.
    expect(folded.tasks.map((t) => t.id)).toEqual(["before", "m", "after", "team-group:m:pending"]);
    // The owning card fans out to the group and the group returns to it; nothing else.
    const fan = folded.links.filter((l) => l.kind === "fanout" || l.kind === "fanin").map((l) => [l.kind, l.source, l.target]);
    expect(fan).toEqual([
      ["fanout", "m", "team-group:m:pending"],
      ["fanin", "team-group:m:pending", "m"],
    ]);
    // Recorded dependencies between the cards are untouched.
    expect(folded.links.filter((l) => l.kind !== "fanout" && l.kind !== "fanin")).toEqual(
      promoted.links.filter((l) => l.kind !== "fanout" && l.kind !== "fanin"),
    );
    expect(folded.missing).toBe(promoted.missing);
  });

  it("lets a subtask leave its group the moment its state changes", () => {
    const events = portfolio((i, review) => (!review && i === 0 ? "running" : review && i === 1 ? "failed" : "pending"));
    const folded = foldTeamBranches(promote(events), new Set(), false);
    const ids = folded.tasks.filter((t) => t.branch).map((t) => t.id);
    expect(ids).toEqual(["team:route1", "team-group:m:pending", "team:review2"]);
    const group = folded.tasks.find((t) => t.id === "team-group:m:pending")!;
    expect(group.title).toBe("11 research routes, 11 independent reviews and 1 idea selection have not started");
    // The running route still hands its review to the group, and the failed review
    // still receives its route from the group and hands on to the selection inside it.
    const fan = folded.links.filter((l) => l.kind === "fanout").map((l) => [l.source, l.target]);
    expect(fan).toEqual(expect.arrayContaining([
      ["m", "team:route1"],
      ["team:route1", "team-group:m:pending"],
      ["m", "team-group:m:pending"],
      ["team-group:m:pending", "team:review2"],
      ["team:review2", "team-group:m:pending"],
    ]));
    // One link per pair, even though eleven reviews used to enter the selection.
    const pairs = fan.map((pair) => pair.join(">"));
    expect(new Set(pairs).size).toBe(pairs.length);
  });

  it("keeps small fans as individual pills", () => {
    const events = portfolio().slice(0, 2);
    const promoted = promote(events);
    expect(foldTeamBranches(promoted, new Set(), true)).toBe(promoted);
  });

  it("unfolds a group into a bracket around its members and folds it back", () => {
    const promoted = promote(portfolio());
    const open = foldTeamBranches(promoted, new Set(["team-group:m:pending"]), true);
    const ids = open.tasks.map((t) => t.id);
    expect(ids.slice(0, 5)).toEqual(["before", "m", "after", "team-group:m:pending", "team:route1"]);
    expect(ids).toContain("team:sel");
    expect(open.tasks.find((t) => t.group)!.group!.expanded).toBe(true);
    const fanout = open.links.filter((l) => l.kind === "fanout").map((l) => [l.source, l.target]);
    // The card fans out to the group, the group to every route; routes still
    // hand on to their reviews and the reviews to the selection.
    expect(fanout).toContainEqual(["m", "team-group:m:pending"]);
    for (let i = 1; i <= 12; i++) {
      expect(fanout).toContainEqual(["team-group:m:pending", `team:route${i}`]);
      expect(fanout).not.toContainEqual(["m", `team:route${i}`]);
      expect(fanout).toContainEqual([`team:route${i}`, `team:review${i}`]);
    }
    expect(open.links.some((l) => l.kind === "fanin" && l.source === "team:sel" && l.target === "m")).toBe(true);
    // Folding again gives back the single node.
    expect(foldTeamBranches(promoted, new Set(), true).tasks.filter((t) => t.branch)).toHaveLength(1);
  });

  it("never redefines an existing id and leaves the overflow pill alone", () => {
    const base = buildMap([task("m", "failed", 2), task("team-group:m:pending", "done", 3)]);
    const promoted = promoteTeamBranches(base, portfolio(), false, 4);
    const folded = foldTeamBranches(promoted, new Set(), false);
    expect(folded.tasks.filter((t) => t.id === "team-group:m:pending")).toHaveLength(1);
    expect(folded.tasks.find((t) => t.id === "team-group:m:pending")!.group).toBeUndefined();
    expect(folded.tasks.find((t) => t.overflow_count)).toBeDefined();
  });

  it("lays out the folded node in the fan's column with a frame of its own", () => {
    const folded = foldTeamBranches(promote(portfolio()), new Set(), true);
    const ids = folded.tasks.map((t) => t.id);
    const sizes = Object.fromEntries(ids.map((id) => [
      id,
      folded.tasks.find((t) => t.id === id)!.group ? GROUP_FRAME
        : folded.tasks.find((t) => t.id === id)!.branch ? BRANCH_FRAME
          : { width: 1440, height: 1080 },
    ]));
    const positions = layoutGraph(ids, folded.links, sizes);
    for (const id of ids) {
      expect(Number.isFinite(positions[id].x)).toBe(true);
      expect(Number.isFinite(positions[id].y)).toBe(true);
    }
    const x = (id: string) => positions[id].x + sizes[id].width / 2;
    expect(x("m")).toBeLessThan(x("team-group:m:pending"));
    expect(GROUP_FRAME.width).toBeGreaterThan(BRANCH_FRAME.width);
  });
});
