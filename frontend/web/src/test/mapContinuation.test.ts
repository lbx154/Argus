import { expect, it } from "vitest";
import { buildMap, type MapTask, type MapEvent } from "../map/model";
import { buildSubmap, layoutScene, MAX_STEPS_PER_CARD } from "../map/submap";
import { referenceText, requestsFor, splitDraft } from "../map/presentation";

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
  "limits each card to twelve steps without losing observations: %s events",
  (count) => {
    const rows = events(count),
      graph = buildMap([task]);
    const original = buildSubmap(task, rows, true),
      scene = layoutScene(graph, rows, true);
    expect(scene.cards).toHaveLength(
      Math.ceil(original.length / MAX_STEPS_PER_CARD),
    );
    expect(scene.cards.flatMap((card) => scene.layouts[card.id].steps)).toEqual(
      original,
    );
    expect(
      scene.cards.every(
        (card) => scene.layouts[card.id].steps.length <= MAX_STEPS_PER_CARD,
      ),
    ).toBe(true);
    expect(scene.cards.every((card) => card.task.id === task.id)).toBe(true);
    const continuations = scene.links.filter(
      (link) => link.kind === "continuation",
    );
    expect(continuations).toHaveLength(scene.cards.length - 1);
    expect(
      Object.values(scene.layouts).reduce((sum, l) => sum + l.links.length, 0) +
        continuations.length,
    ).toBe(original.length - 1);
    for (const [i, card] of scene.cards.entries()) {
      expect(card.start).toBe(i * MAX_STEPS_PER_CARD + 1);
      expect(card.end - card.start + 1).toBe(
        scene.layouts[card.id].steps.length,
      );
      if (i)
        expect(continuations[i - 1]).toMatchObject({
          source: scene.cards[i - 1].id,
          target: card.id,
        });
    }
    expect(graph.tasks).toHaveLength(1);
    expect(task.deps).toEqual([]);
  },
);

it("appends a new part without renaming or redistributing earlier parts", () => {
  const graph = buildMap([task]);
  const before = layoutScene(graph, events(23), true),
    after = layoutScene(graph, events(24), true);
  expect(after.cards.length).toBe(before.cards.length + 1);
  for (const card of before.cards)
    expect(after.layouts[card.id].steps).toEqual(before.layouts[card.id].steps);
});

it("connects upstream input to the first part and downstream work to the final part", () => {
  const before = { ...task, id: "before", ts: 0 };
  const after = { ...task, id: "after", ts: 3, deps: [task.id] };
  const graph = buildMap([before, { ...task, deps: [before.id] }, after]);
  const scene = layoutScene(graph, events(35), true);
  const parts = scene.cards.filter((card) => card.task.id === task.id);
  expect(
    scene.links.find((l) => l.source === before.id && l.kind === "dependency")
      ?.target,
  ).toBe(parts[0].id);
  expect(
    scene.links.find((l) => l.target === after.id && l.kind === "dependency")
      ?.source,
  ).toBe(parts.at(-1)!.id);
  expect(graph.links.find((l) => l.target === after.id)?.source).toBe(task.id);
});

it("uses original task and event identities for continuation copy and references", () => {
  const rows = events(30),
    scene = layoutScene(buildMap([task]), rows, true),
    card = scene.cards[1];
  const steps = scene.layouts[card.id].steps;
  const requested = requestsFor(
    {
      id: "live:session",
      title: "Research",
      description: "",
      kind: "live",
      read_only: false,
      tasks: [task],
      events: rows,
    },
    steps,
    task.id,
  );
  expect(requested.filter((r) => r.kind !== "task").map((r) => r.key)).toEqual(
    steps.map((s) => s.id),
  );
  expect(requested.every((r) => r.task_id === task.id)).toBe(true);
  expect(requested.some((r) => r.key === card.id)).toBe(false);
  const ref = {
    source: "live:session",
    task_id: task.id,
    task_title: "Research · Continued 1",
    part: 2,
    event_ids: steps.flatMap((s) => s.eventIds),
  };
  expect(
    splitDraft(referenceText(ref) + "Continue with this evidence"),
  ).toEqual({ refs: [ref], text: "Continue with this evidence" });
});

it("lays out each bounded part on aligned rows with an unambiguous reading order", () => {
  const scene = layoutScene(buildMap([task]), events(49), true);
  for (const card of scene.cards) {
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
