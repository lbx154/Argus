import { expect, it } from "vitest";
import { buildMap, type MapTask, type MapEvent } from "../map/model";
import { buildSubmap, layoutScene, MAX_STEPS_PER_CARD } from "../map/submap";

const task: MapTask = {
  id: "research",
  title: "Research",
  objective: "Study",
  status: "running",
  deps: [],
  ts: 1,
};
const events = (count: number): MapEvent[] =>
  Array.from({ length: count }, (_, i) => ({
    id: `event-${i}`,
    item_id: task.id,
    type: "life.mission.started",
    text: `Observed attempt ${i + 1}`,
    ts: i + 2,
  }));

it.each([10, 11, 12, 23, 24, 49, 70])(
  "keeps all %s events in bounded detail pages and a single overview card",
  count => {
    const rows = events(count), graph = buildMap([task]);
    const original = buildSubmap(task, rows, true), scene = layoutScene(graph, rows, true);
    expect(scene.cards).toHaveLength(1);
    expect(scene.allCards).toHaveLength(Math.ceil(original.length / MAX_STEPS_PER_CARD));
    expect(scene.allCards.flatMap(card => scene.layouts[card.id].steps)).toEqual(original);
    expect(scene.links).toEqual([]);
    for (const [index, page] of scene.allCards.entries()) {
      expect(page.previousId).toBe(scene.allCards[index - 1]?.id);
      expect(page.nextId).toBe(scene.allCards[index + 1]?.id);
      expect(scene.layouts[page.id].steps.length).toBeLessThanOrEqual(MAX_STEPS_PER_CARD);
    }
  },
);

it("keeps dependency endpoints on the task while its detail page changes", () => {
  const before = { ...task, id: "before", ts: 0 };
  const after = { ...task, id: "after", ts: 3, deps: [task.id] };
  const graph = buildMap([before, { ...task, deps: [before.id] }, after]);
  const scene = layoutScene(graph, events(35), true);
  const earlier = layoutScene(graph, events(35), true, scene, graph.links, new Map([[task.id, 1]]));
  expect(earlier.cards.map(card => card.id)).toEqual(scene.cards.map(card => card.id));
  expect(earlier.links).toEqual(scene.links);
  expect(scene.links).toContainEqual(expect.objectContaining({ source: before.id, target: task.id }));
  expect(scene.links).toContainEqual(expect.objectContaining({ source: task.id, target: after.id }));
});

it("lays out each bounded part on aligned rows with an unambiguous reading order", () => {
  const scene = layoutScene(buildMap([task]), events(49), true);
  for (const card of scene.allCards) {
    const layout = scene.layouts[card.id];
    const points = layout.steps.map((step) => layout.positions[step.id]);
    const xs = [...new Set(points.map((p) => p.x))].sort((a, b) => a - b);
    const ys = [...new Set(points.map((p) => p.y))].sort((a, b) => a - b);
    for (const x of xs)
      expect(points.filter((p) => p.x === x).map((p) => p.y)).toEqual(
        ys.slice(0, points.filter((p) => p.x === x).length),
      );
    for (let i = 1; i < points.length; i++)
      expect(
        points[i].x > points[i - 1].x ||
          (points[i].x === points[i - 1].x && points[i].y > points[i - 1].y),
      ).toBe(true);
    for (let i = 0; i < points.length; i++)
      for (const b of points.slice(i + 1)) {
        const a = points[i];
        expect(
          a.x + 232 <= b.x ||
            b.x + 232 <= a.x ||
            a.y + 180 <= b.y ||
            b.y + 180 <= a.y,
        ).toBe(true);
      }
  }
});
