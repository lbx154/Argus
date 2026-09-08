import { describe, expect, it } from "vitest";
import {
  buildMap,
  promoteTeamBranches,
  type MapEvent,
  type MapTask,
} from "../map/model";

const task = (
  id: string,
  deps: string[] = [],
  status = "running",
  ts = 0,
): MapTask => ({ id, title: id, objective: "", status, deps, ts });

const team = (
  id: string,
  item_id: string,
  extra: Partial<MapEvent> = {},
): MapEvent => ({ id, item_id, type: "team.task", ts: 0, text: "", ...extra });

describe("team fan-out promotion", () => {
  const graph = () => buildMap([task("m", [], "failed", 10), task("later", [], "pending", 20)]);
  const events = [
    team("team:r1", "m", {
      ts: 11,
      team_role: "idea-route",
      team_task_id: "portfolio/route-1",
      status: "done",
      text: "SUMMARY=Route one findings\nDecision: go\nMILESTONE_STATUS=ok",
    }),
    team("team:r2", "m", {
      ts: 12,
      team_role: "idea-route",
      team_task_id: "portfolio/route-2",
      status: "failed",
      pending_question: "Pick a fallback source?",
    }),
    team("team:v1", "m", {
      ts: 13,
      team_role: "idea-review",
      team_task_id: "review-route-1",
      status: "done",
      deps: ["team:r1"],
    }),
    team("team:sel", "m", {
      ts: 14,
      team_role: "idea-selector",
      team_task_id: "selector",
      status: "pending",
      deps: ["team:v1"],
    }),
    { id: "e9", item_id: "m", type: "round.start", ts: 9, text: "" } as MapEvent,
  ];

  it("synthesizes one branch node per team task with fan-out and fan-in links", () => {
    const base = graph();
    const out = promoteTeamBranches(base, events, false);
    const branches = out.tasks.filter((t) => t.branch);
    expect(branches.map((t) => t.id)).toEqual([
      "team:r1",
      "team:r2",
      "team:v1",
      "team:sel",
    ]);
    const byId = Object.fromEntries(branches.map((t) => [t.id, t]));
    expect(byId["team:r1"]).toMatchObject({
      parent_id: "m",
      team_role: "idea-route",
      title: "Research route 1",
      status: "done",
      ts: 11,
    });
    expect(byId["team:r1"].objective).toContain("Route one findings");
    expect(byId["team:r1"].objective).not.toContain("Decision:");
    expect(byId["team:r1"].objective).not.toContain("MILESTONE_STATUS");
    expect(byId["team:r2"].pending_question).toBe("Pick a fallback source?");
    expect(byId["team:v1"].title).toBe("Independent review 1");
    expect(byId["team:sel"].title).toBe("Idea selection");
    // fan-out: parent → roots, and team dependencies in team-event-id space
    const fanout = out.links
      .filter((l) => l.kind === "fanout")
      .map((l) => [l.source, l.target]);
    expect(fanout).toEqual(
      expect.arrayContaining([
        ["m", "team:r1"],
        ["m", "team:r2"],
        ["team:r1", "team:v1"],
        ["team:v1", "team:sel"],
      ]),
    );
    expect(fanout).toHaveLength(4);
    // fan-in: only terminal team nodes return to the owning card
    const fanin = out.links
      .filter((l) => l.kind === "fanin")
      .map((l) => [l.source, l.target])
      .sort();
    expect(fanin).toEqual(
      [
        ["team:r2", "m"],
        ["team:sel", "m"],
      ].sort(),
    );
    // pure: the input graph is untouched and original links come first, intact
    expect(base.tasks.some((t) => t.branch)).toBe(false);
    expect(out.links.slice(0, base.links.length)).toEqual(base.links);
    expect(out.missing).toBe(base.missing);
  });

  it("localizes branch titles", () => {
    const out = promoteTeamBranches(graph(), events, true);
    expect(out.tasks.find((t) => t.id === "team:r1")!.title).toBe("研究路线 1");
    expect(out.tasks.find((t) => t.id === "team:v1")!.title).toBe("独立复核 1");
    expect(out.tasks.find((t) => t.id === "team:sel")!.title).toBe("方案选择");
  });

  it("caps a fan at 16 branches and adds a localized overflow pill", () => {
    const base = buildMap([task("m")]);
    const crowd = Array.from({ length: 25 }, (_, i) =>
      team(`team:t${String(i).padStart(2, "0")}`, "m", {
        ts: i + 1,
        team_role: "idea-route",
        team_task_id: `portfolio/route-${i + 1}`,
        status: "done",
        // one kept event depends on a dropped one: must not dangle
        deps: i === 10 ? ["team:t20"] : [],
      }),
    );
    const out = promoteTeamBranches(base, crowd, false);
    const branches = out.tasks.filter((t) => t.branch && !t.overflow_count);
    expect(branches).toHaveLength(16);
    expect(branches.map((t) => t.ts)).toEqual(crowd.slice(0, 16).map((e) => e.ts));
    const overflow = out.tasks.find((t) => t.overflow_count);
    expect(overflow).toMatchObject({
      id: "team-overflow:m",
      branch: true,
      parent_id: "m",
      overflow_count: 9,
      title: "+9 more",
    });
    expect(
      promoteTeamBranches(base, crowd, true).tasks.find((t) => t.overflow_count)!
        .title,
    ).toBe("还有 9 条");
    expect(
      out.links.some(
        (l) => l.kind === "fanout" && l.source === "m" && l.target === "team-overflow:m",
      ),
    ).toBe(true);
    expect(
      out.links.some(
        (l) => l.kind === "fanin" && l.source === "team-overflow:m" && l.target === "m",
      ),
    ).toBe(true);
    // a dependency on a dropped event falls back to the parent, never dangles
    const ids = new Set(out.tasks.map((t) => t.id));
    for (const l of out.links) {
      if (l.kind !== "fanout" && l.kind !== "fanin") continue;
      expect(ids.has(l.source)).toBe(true);
      expect(ids.has(l.target)).toBe(true);
    }
    expect(
      out.links.some(
        (l) => l.kind === "fanout" && l.source === "m" && l.target === "team:t10",
      ),
    ).toBe(true);
  });

  it("never collides with an existing task id", () => {
    const base = buildMap([task("m"), task("team:dup", [], "done", 5)]);
    const out = promoteTeamBranches(
      base,
      [
        team("team:dup", "m", { ts: 1, status: "done" }),
        team("team:ok", "m", { ts: 2, status: "done" }),
      ],
      false,
    );
    expect(out.tasks.filter((t) => t.id === "team:dup")).toHaveLength(1);
    expect(out.tasks.find((t) => t.id === "team:dup")!.branch).toBeUndefined();
    expect(out.tasks.find((t) => t.id === "team:ok")?.branch).toBe(true);
    expect(
      out.links.filter((l) => l.kind === "fanout").map((l) => l.target),
    ).toEqual(["team:ok"]);
  });

  it("keeps the latest revision when a team event arrives twice", () => {
    const base = buildMap([task("m")]);
    const out = promoteTeamBranches(
      base,
      [
        team("team:r1", "m", { ts: 1, status: "running", revision: "a" }),
        team("team:r1", "m", { ts: 1, status: "done", revision: "b" }),
      ],
      false,
    );
    expect(out.tasks.filter((t) => t.branch)).toHaveLength(1);
    expect(out.tasks.find((t) => t.id === "team:r1")!.status).toBe("done");
  });

  it("returns the graph unchanged without team events", () => {
    const base = buildMap([task("a"), task("b", ["a"])]);
    const out = promoteTeamBranches(
      base,
      [{ id: "x", item_id: "a", type: "round.start", ts: 1, text: "" } as MapEvent],
      false,
    );
    expect(out).toBe(base);
  });
});
