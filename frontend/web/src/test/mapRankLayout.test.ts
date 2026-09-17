import { expect, it } from "vitest";
import {
  edgeLanes,
  foldLayout,
  layoutGraph,
  rankLayout,
} from "../map/graphLayout";
import {
  buildMap,
  connectMap,
  promoteTeamBranches,
  type MapEvent,
  type MapLink,
  type MapTask,
} from "../map/model";

const CARD = { width: 1440, height: 1080 };
const PILL = { width: 640, height: 190 };
const link = (
  kind: MapLink["kind"],
  source: string,
  target: string,
): MapLink => ({ id: `${kind}:${source}:${target}`, source, target, kind });

it("assigns ranks as columns on a fork-join dependency graph", () => {
  const ids = ["a", "b", "c", "d"];
  const links = [
    link("dependency", "a", "b"),
    link("dependency", "a", "c"),
    link("dependency", "b", "d"),
    link("dependency", "c", "d"),
  ];
  const sizes = Object.fromEntries(ids.map((id) => [id, CARD]));
  const positions = rankLayout(ids, links, sizes);
  const x = (id: string) => positions[id].x + sizes[id].width / 2;
  expect(x("a")).toBeLessThan(x("b"));
  expect(x("b")).toBe(x("c"));
  expect(x("c")).toBeLessThan(x("d"));
  // parallel siblings never overlap
  const [top, bottom] = [positions.b, positions.c].sort((p, q) => p.y - q.y);
  expect(bottom.y - top.y).toBeGreaterThanOrEqual(CARD.height);
});

it("gives a promoted 12-route fan real fork/join geometry", () => {
  const routes = Array.from({ length: 12 }, (_, i) => `team:r${i + 1}`);
  const ids = ["before", "m", ...routes, "team:sel", "after"];
  const links: MapLink[] = [
    link("context", "before", "m"),
    ...routes.map((r) => link("fanout", "m", r)),
    ...routes.map((r) => link("fanout", r, "team:sel")),
    link("fanin", "team:sel", "m"),
    link("context", "m", "after"),
  ];
  const sizes = Object.fromEntries(
    ids.map((id) => [id, id.startsWith("team:") ? PILL : CARD]),
  );
  const positions = layoutGraph(ids, links, sizes);
  for (const id of ids) {
    expect(Number.isFinite(positions[id].x)).toBe(true);
    expect(Number.isFinite(positions[id].y)).toBe(true);
  }
  const x = (id: string) => positions[id].x + sizes[id].width / 2;
  // the fan leaves the owning card: all 12 routes share one column to its right
  expect(new Set(routes.map(x)).size).toBe(1);
  expect(x("before")).toBeLessThanOrEqual(x("m"));
  expect(x("m")).toBeLessThan(x(routes[0]));
  expect(x(routes[0])).toBeLessThan(x("team:sel"));
  // ...and later chronological work resumes right of the fan
  expect(x("team:sel")).toBeLessThan(x("after"));
  // branch pills pack tighter: half the row gap between adjacent branches
  const ys = routes.map((r) => positions[r].y).sort((a, b) => a - b);
  for (let i = 1; i < ys.length; i++)
    expect(ys[i] - ys[i - 1] - PILL.height).toBeCloseTo(220, 4);
  // the fan-in return edge must not drag the parent out of its rank (no cycle)
  expect(x("m")).toBeLessThan(x("team:sel"));
  // gate is on: layoutGraph delegates to rankLayout for this graph
  expect(positions).toEqual(rankLayout(ids, links, sizes));
});

it("keeps the chronological fold when a session has no fan structure", () => {
  const ids = Array.from({ length: 12 }, (_, i) => `t${i}`);
  const contexts = ids.slice(1).map((id, i) => link("context", ids[i], id));
  const sizes = Object.fromEntries(ids.map((id) => [id, CARD]));
  expect(layoutGraph(ids, contexts, sizes)).toEqual(
    foldLayout(ids, contexts, sizes),
  );
  // dependency-only histories (legacy sessions) also keep the fold
  const deps = ids.slice(1).map((id, i) => link("dependency", ids[i], id));
  expect(layoutGraph(ids, deps, sizes)).toEqual(foldLayout(ids, deps, sizes));
});

it("leaves a tiny fan inside a large sparse session on the fold path", () => {
  const cards = Array.from({ length: 20 }, (_, i) => `t${i}`);
  const ids = [...cards.slice(0, 6), "team:a", "team:b", ...cards.slice(6)];
  const links: MapLink[] = [
    ...cards.slice(1).map((id, i) => link("context", cards[i], id)),
    link("fanout", "t5", "team:a"),
    link("fanout", "t5", "team:b"),
    link("fanin", "team:a", "t5"),
    link("fanin", "team:b", "t5"),
  ];
  const sizes = Object.fromEntries(
    ids.map((id) => [id, id.startsWith("team:") ? PILL : CARD]),
  );
  // 3 of 22 nodes are fan-connected (< 30%): appearance must not change
  expect(layoutGraph(ids, links, sizes)).toEqual(foldLayout(ids, links, sizes));
});

it("unfolds an s-d9c7aeb2-like session: 19 serial cards, one failed card hiding a 12-route portfolio", () => {
  // 19 macro cards, nearly all deps:[], the portfolio buried in card t6.
  const tasks: MapTask[] = Array.from({ length: 19 }, (_, i) => ({
    id: `t${i}`,
    title: `t${i}`,
    objective: "",
    status: i === 6 ? "failed" : "done",
    deps: [],
    ts: i,
  }));
  const events: MapEvent[] = [
    ...Array.from({ length: 12 }, (_, i) => ({
      id: `team:route${i + 1}`,
      item_id: "t6",
      type: "team.task",
      ts: 100 + i,
      text: "",
      team_role: "idea-route",
      team_task_id: `portfolio/route-${i + 1}`,
      status: i < 3 ? "done" : "failed",
    })),
    ...Array.from({ length: 12 }, (_, i) => ({
      id: `team:review${i + 1}`,
      item_id: "t6",
      type: "team.task",
      ts: 200 + i,
      text: "",
      team_role: "idea-review",
      team_task_id: `review-route-${i + 1}`,
      deps: [`team:route${i + 1}`],
      status: "failed",
    })),
    {
      id: "team:selector",
      item_id: "t6",
      type: "team.task",
      ts: 300,
      text: "",
      team_role: "idea-selector",
      team_task_id: "selector",
      deps: Array.from({ length: 12 }, (_, i) => `team:review${i + 1}`),
      status: "failed",
    },
  ];
  const graph = buildMap(tasks);
  const promoted = promoteTeamBranches(graph, events, false);
  const branches = promoted.tasks.filter((t) => t.branch);
  // 25 events → 16 kept (12 routes + 4 reviews by ts) + one overflow pill
  expect(branches).toHaveLength(17);
  expect(branches.find((t) => t.overflow_count)?.title).toBe("+9 more");
  // Compose exactly as MapPanel does: cards (single-part → card id = task id),
  // context links from connectMap, fan links, pills right after their card.
  const links = [
    ...connectMap(graph, [], false),
    ...promoted.links.filter((l) => l.kind === "fanout" || l.kind === "fanin"),
  ];
  const ids = tasks.flatMap((t) =>
    t.id === "t6" ? [t.id, ...branches.map((b) => b.id)] : [t.id],
  );
  const sizes = Object.fromEntries(
    ids.map((id) => [id, /^t\d/.test(id) ? CARD : PILL]),
  );
  const positions = layoutGraph(ids, links, sizes);
  const x = (id: string) => positions[id].x + sizes[id].width / 2;
  // the fan leaves t6: routes and the overflow pill share one column
  const routeColumn = new Set(
    [...Array.from({ length: 12 }, (_, i) => `team:route${i + 1}`), "team-overflow:t6"].map(x),
  );
  expect(routeColumn.size).toBe(1);
  expect(x("t6")).toBeLessThan(x("team:route1"));
  // kept reviews form the next column, and later cards resume right of the fan
  expect(x("team:route1")).toBeLessThan(x("team:review1"));
  expect(x("team:review1")).toBeLessThan(x("t7"));
  // earlier serial work still reads left to right
  for (let i = 1; i <= 6; i++)
    expect(x(`t${i - 1}`)).toBeLessThanOrEqual(x(`t${i}`));
  // no node lost, nothing NaN
  for (const id of ids) {
    expect(Number.isFinite(positions[id].x)).toBe(true);
    expect(Number.isFinite(positions[id].y)).toBe(true);
  }
});

it("keeps single-pass lane assignment equivalent for the new edge kinds", () => {
  const links = [
    link("fanout", "m", "r1"),
    link("fanout", "m", "r2"),
    link("dependency", "a", "m"),
    link("fanin", "r1", "m"),
    link("fanin", "r2", "m"),
    link("context", "m", "b"),
  ];
  const reference = links.map(
    (e, index) =>
      links
        .slice(0, index)
        .filter((l) => l.source === e.source || l.target === e.target).length,
  );
  expect(edgeLanes(links)).toEqual(reference);
});
