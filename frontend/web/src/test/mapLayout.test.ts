import { expect, it } from "vitest";
import { buildMap, type MapTask } from "../map/model";
import { layoutGraph, relationPorts } from "../map/graphLayout";
import { inside, routeRelation, type Box } from "../map/relationGeometry";

function arrange(dependencies: number[][]) {
  const tasks: MapTask[] = dependencies.map((deps, i) => ({
    id: String(i),
    title: String(i),
    objective: "",
    status: "done",
    ts: i,
    deps: deps.map(String),
  }));
  const graph = buildMap(tasks);
  const links = graph.links;
  const sizes = Object.fromEntries(
    graph.tasks.map((t, i) => [
      t.id,
      { width: i % 4 ? 1100 : 1440, height: i % 4 ? 1000 : 745 },
    ]),
  );
  const positions = layoutGraph(
    graph.tasks.map((t) => t.id),
    links,
    sizes,
  );
  const boxes = Object.fromEntries(
    graph.tasks.map((t) => [t.id, { ...positions[t.id], ...sizes[t.id] }]),
  );
  return { graph, links, positions, sizes, boxes };
}

function bounds(boxes: Record<string, Box>) {
  const values = Object.values(boxes);
  for (let i = 0; i < values.length; i++) {
    const a = values[i];
    expect(Number.isFinite(a.x) && Number.isFinite(a.y)).toBe(true);
    for (const b of values.slice(i + 1))
      expect(
        a.x + a.width + 350 <= b.x ||
          b.x + b.width + 350 <= a.x ||
          a.y + a.height + 350 <= b.y ||
          b.y + b.height + 350 <= a.y,
      ).toBe(true);
  }
}

it.each([
  ["the six-task science session", [[], [0], [0], [], [3], []]],
  ["a long chain", Array.from({ length: 36 }, (_, i) => (i ? [i - 1] : []))],
  ["wide fan-out", Array.from({ length: 36 }, (_, i) => (i ? [0] : []))],
  [
    "wide fan-in",
    Array.from({ length: 36 }, (_, i) =>
      i === 35 ? Array.from({ length: 35 }, (_, j) => j) : [],
    ),
  ],
  ["independent tasks", Array.from({ length: 36 }, () => [])],
  [
    "branches that merge",
    [[], [0], [0], [0], [1, 2, 3], [4], [4], [5, 6], [7], [7], [8, 9]],
  ],
  ["cycles and a self-reference", [[2], [0], [1], [3], [2, 3]]],
] as [string, number[][]][])(
  "lays out %s without losing edges or crowding cards",
  (_, deps) => {
    const { graph, links, positions, sizes, boxes } = arrange(deps);
    expect(Object.keys(positions)).toHaveLength(deps.length);
    bounds(boxes);
    expect(
      layoutGraph(
        graph.tasks.map((t) => t.id),
        links,
        sizes,
      ),
    ).toEqual(positions);
    expect(graph.links.filter((l) => l.kind === "dependency")).toHaveLength(
      deps.flat().length,
    );
  },
);

it("keeps the first branching stage readable from left to right", () => {
  const { positions } = arrange([[], [0], [0], [1, 2]]);
  expect(positions[0].x).toBeLessThan(positions[1].x);
  expect(positions[1].x).toBe(positions[2].x);
  expect(positions[3].x).toBeGreaterThan(positions[2].x);
});

it("routes a dependency chain through facing ports without crossing cards", () => {
  const { links, boxes } = arrange(
    Array.from({ length: 24 }, (_, i) => (i ? [i - 1] : [])),
  );
  for (const edge of links) {
    const a = boxes[edge.source],
      b = boxes[edge.target];
    const ports = relationPorts(a, b);
    const point = (box: Box, side: string) => ({
      x:
        box.x +
        (side === "left" ? 0 : side === "right" ? box.width : box.width / 2),
      y:
        box.y +
        (side === "top" ? 0 : side === "bottom" ? box.height : box.height / 2),
    });
    const obstacles = Object.entries(boxes)
      .filter(([id]) => id !== edge.source && id !== edge.target)
      .map(([, b]) => b);
    const direction =
      ports.sourceHandle === "left"
        ? "left"
        : ports.sourceHandle === "top"
          ? "up"
          : ports.sourceHandle === "bottom";
    const route = routeRelation(
      point(a, ports.sourceHandle),
      point(b, ports.targetHandle),
      direction,
      0,
      obstacles,
    );
    expect(
      route.points.some((p) => obstacles.some((box) => inside(p, box))),
    ).toBe(false);
  }
});

it("supports empty and single-card layouts", () => {
  expect(layoutGraph([], [], {})).toEqual({});
  expect(arrange([[]]).positions).toEqual({ "0": { x: 0, y: 0 } });
});

it("keeps late independent tasks in the first column and merge inputs before their consumers", () => {
  const { positions, boxes } = arrange([[], [], [1], [], [], [4], [3, 4, 5], [4, 5, 6], [], []]);
  for (const id of [1, 3, 4, 8, 9]) expect(positions[id].x).toBe(positions[0].x);
  expect(positions[2].x).toBe(positions[5].x);
  expect(positions[6].x).toBeGreaterThan(positions[5].x);
  expect(positions[7].x).toBeGreaterThan(positions[6].x);
  bounds(boxes);
});

it("places dependencies to the left even when timestamps put consumers first", () => {
  const { positions, sizes } = arrange([[1], [2], [], [2]]);
  expect(positions[2].x + sizes[2].width).toBeLessThan(positions[1].x);
  expect(positions[1].x).toBe(positions[3].x);
  expect(positions[1].x + sizes[1].width).toBeLessThan(positions[0].x);
});

it("keeps a cycle together with its downstream work in later columns", () => {
  const { positions } = arrange([[2], [0], [1], [3], [2, 3]]);
  expect(positions[0].x).toBe(positions[1].x);
  expect(positions[1].x).toBe(positions[2].x);
  expect(positions[4].x).toBeGreaterThan(positions[2].x);
  expect(positions[4].x).toBeGreaterThan(positions[3].x);
});

it("lays the browser reproduction out by dependency depth, independent of creation order", async () => {
  const { default: tasks } = await import("./fixtures/map-deps.json");
  const graph = buildMap(tasks);
  const links = graph.links;
  expect(links.map(({ source, target }) => [source, target])).toEqual([
    ["t1", "t2"], ["t1", "t3"], ["t2", "t5"], ["t3", "t5"], ["t5", "t6"],
  ]);
  const sizes = Object.fromEntries(tasks.map(({ id }) => [id, { width: 1440, height: 900 }]));
  const positions = layoutGraph(tasks.map(({ id }) => id), links, sizes);
  expect(positions.t1.x).toBe(positions.t4.x);
  expect(positions.t2.x).toBe(positions.t3.x);
  for (const { source, target } of links)
    expect(positions[source].x + sizes[source].width).toBeLessThan(positions[target].x);
});
