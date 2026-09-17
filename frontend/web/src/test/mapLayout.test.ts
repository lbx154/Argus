import { expect, it } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";
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
  const links = connectMap(graph, [], true);
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
  const width = Math.max(...values.map((b) => b.x + b.width));
  const height = Math.max(...values.map((b) => b.y + b.height));
  return width / height;
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
  "balances %s without losing edges or crowding cards",
  (_, deps) => {
    const { graph, links, positions, sizes, boxes } = arrange(deps);
    expect(Object.keys(positions)).toHaveLength(deps.length);
    expect(bounds(boxes)).toBeGreaterThan(0.9);
    expect(bounds(boxes)).toBeLessThan(3.5);
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

it("routes a folded chain through facing ports without crossing cards", () => {
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

it("keeps later independent tasks after earlier research, including merge inputs", () => {
  const { positions, sizes } = arrange([
    [],
    [],
    [1],
    [],
    [],
    [4],
    [3, 4, 5],
    [4, 5, 6],
    [],
    [],
  ]);
  for (let i = 1; i < 10; i++) {
    const a = positions[String(i - 1)],
      b = positions[String(i)];
    const ax = a.x + sizes[String(i - 1)].width / 2,
      bx = b.x + sizes[String(i)].width / 2;
    expect(ax).toBeLessThanOrEqual(bx);
  }
});

it("follows relationships with staggered columns while retaining chronological order", () => {
  const { positions, sizes } = arrange([
    [],
    [],
    [1],
    [],
    [],
    [4],
    [3, 4, 5],
    [4, 5, 6],
    [],
    [],
  ]);
  const columns = new Map<number, string[]>();
  for (let i = 0; i < 10; i++) {
    const id = String(i),
      p = positions[id],
      x = p.x + sizes[id].width / 2;
    columns.set(x, [...(columns.get(x) || []), id]);
  }
  expect(
    new Set([...columns.values()].map((ids) => Math.round(positions[ids[0]].y)))
      .size,
  ).toBeGreaterThan(1);
  for (const ids of columns.values())
    for (let i = 1; i < ids.length; i++)
      expect(
        positions[ids[i]].y -
          positions[ids[i - 1]].y -
          sizes[ids[i - 1]].height,
      ).toBeGreaterThanOrEqual(439.99);
});
