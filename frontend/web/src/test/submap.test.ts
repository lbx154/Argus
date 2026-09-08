import { describe, expect, it } from "vitest";
import {
  buildSubmap,
  zoomTarget,
  layoutSubmap,
  layoutScene,
  frameForSubmap,
  readableRecord,
  submapLinks,
} from "../map/submap";
import { buildMap, type MapEvent, type MapTask } from "../map/model";

const task: MapTask = {
  id: "a",
  title: "A",
  objective: "Measured objective",
  status: "done",
  deps: [],
};
const event = (
  id: string,
  type: string,
  extra: Partial<MapEvent> = {},
): MapEvent => ({
  id,
  type,
  item_id: "a",
  ts: Number(id.replace(/\D/g, "")) || 0,
  text: id,
  ...extra,
});

describe("task submap evidence", () => {
  it("leaves missing execution and review stages absent", () => {
    const rows = buildSubmap(task, [], true);
    expect(rows.map((r) => r.kind)).toEqual(["plan", "result"]);
    expect(rows.every((r) => r.source === "task")).toBe(true);
  });
  it("excludes another task and deduplicates repeated events", () => {
    const own = event("e1", "round.start", { round_index: 1 });
    const rows = buildSubmap(
      task,
      [own, own, event("e2", "round.review.completed", { item_id: "b" })],
      true,
    );
    expect(rows.flatMap((r) => r.eventIds)).toEqual(["e1"]);
  });
  it("distinguishes requested revisions from executed work and preserves association strength", () => {
    const rows = buildSubmap(
      task,
      [
        event("e1", "round.review.completed", {
          round_index: 1,
          status: "continue",
          next_action: "Check the boundary",
          association: "single_active_window",
        }),
      ],
      true,
    );
    const revision = rows.find((r) => r.kind === "revision");
    expect(revision).toMatchObject({
      status: "requested",
      source: "interval",
      detail: "Check the boundary",
    });
    expect(rows.some((r) => r.kind === "execution")).toBe(false);
  });
  it("folds round observations but does not merge different mission episodes", () => {
    const rows = buildSubmap(
      task,
      [
        event("e1", "life.mission.started"),
        event("e2", "round.start", { round_index: 1 }),
        event("e3", "round.main.completed", { round_index: 1 }),
        event("e4", "life.mission.started"),
        event("e5", "round.start", { round_index: 1 }),
      ],
      true,
    );
    const rounds = rows.filter((r) => r.kind === "execution" && r.round === 1);
    expect(rounds).toHaveLength(2);
    expect(rounds[0].eventIds).toEqual(["e2", "e3"]);
    expect(rounds[1].eventIds).toEqual(["e5"]);
  });
  it("keeps skipped-review continuation instructions without inventing a rejection", () => {
    const rows = buildSubmap(task, [
      event("e1", "round.review.started", { round_index: 1 }),
      event("e2", "round.review.completed", {
        round_index: 1, status: "continue", review_skipped: true,
        text: "The session reached its turn allowance; no review ran.",
        next_action: "Resume from the saved checkpoint.",
      }),
    ], true);
    expect(rows.find((row) => row.kind === "review")).toMatchObject({
      title: "审查未执行", status: "skipped", eventIds: ["e1", "e2"],
    });
    expect(rows.find((row) => row.kind === "review")?.detail).toContain("Resume from the saved checkpoint.");
    expect(rows.some((row) => row.kind === "revision")).toBe(false);
  });
  it("does not turn an unsuccessful completed mission into success", () => {
    expect(
      buildSubmap(
        task,
        [event("e1", "life.mission.completed", { success: false })],
        false,
      ).find((r) => r.kind === "result")?.status,
    ).toBe("failed");
  });
});

describe("semantic zoom focus geometry", () => {
  const nodes = [
    { id: "a", position: { x: 344, y: 0 }, width: 272, height: 212 },
    { id: "hidden", hidden: true, position: { x: 0, y: 0 } },
  ];
  it("finds the task under the pointer after panning and zooming", () => {
    expect(
      zoomTarget(
        nodes,
        { x: -400, y: 80, zoom: 1.2 },
        { x: 70, y: 110 },
        { x: 800, y: 600 },
      ),
    ).toBe("a");
  });
  it("uses the viewport focal task when the pointer is outside a card", () => {
    expect(
      zoomTarget(
        nodes,
        { x: -400, y: 80, zoom: 1.2 },
        { x: 1000, y: 500 },
        { x: 70, y: 110 },
      ),
    ).toBe("a");
  });
  it("does not open a distant or hidden task while zooming empty space", () => {
    expect(
      zoomTarget(
        nodes,
        { x: 0, y: 0, zoom: 1 },
        { x: 10, y: 10 },
        { x: 800, y: 600 },
      ),
    ).toBeNull();
  });
});

describe("connected maps with stable bounds", () => {
  const observations = [
    event("e1", "round.start", { round_index: 1 }),
    event("e2", "round.review.completed", {
      round_index: 1,
      status: "continue",
      next_action: "Adjust the experiment",
    }),
    event("e3", "round.start", { round_index: 2 }),
    event("e4", "life.mission.completed", { success: true }),
  ];
  it("connects every observed step, distinguishing review, revision and next round", () => {
    const layout = layoutSubmap(task, observations, true);
    expect(layout.links).toHaveLength(layout.steps.length - 1);
    expect(layout.links.map((e) => e.relation)).toEqual([
      "assignment",
      "review",
      "revision",
      "next_attempt",
      "outcome",
    ]);
    expect(
      new Set(layout.links.flatMap((e) => [e.source, e.target])).size,
    ).toBe(layout.steps.length);
    for (const p of Object.values(layout.positions)) {
      expect(p.x + 232).toBeLessThanOrEqual(layout.width);
      expect(p.y + 180).toBeLessThanOrEqual(layout.height - 48);
    }
  });
  it("does not describe unrelated observations as a causal review", () => {
    const steps = buildSubmap(
      task,
      [
        event("e1", "round.start", { round_index: 1 }),
        event("e2", "round.review.completed", { round_index: 2 }),
      ],
      true,
    );
    expect(submapLinks(steps, true)[1]).toMatchObject({
      relation: "record_order",
      contextual: true,
    });
  });
  it("fits variable-size task submaps without overlapping or changing with the locale", () => {
    const graph = buildMap(
      Array.from({ length: 9 }, (_, i) => ({ ...task, id: String(i), ts: i })),
    );
    const many = Array.from({ length: 18 }, (_, i) =>
      event(`e${i}`, "round.start", { item_id: "0", round_index: i + 1 }),
    );
    const scene = layoutScene(graph, many, true);
    expect(layoutScene(graph, many, false).positions).toEqual(scene.positions);
    for (let i = 0; i < graph.tasks.length; i++)
      for (let j = i + 1; j < graph.tasks.length; j++) {
        const a = graph.tasks[i].id,
          b = graph.tasks[j].id;
        const p = scene.positions[a],
          q = scene.positions[b],
          size = scene.frames[a];
        expect(
          q.x >= p.x + size.width ||
            q.y >= p.y + size.height ||
            p.x >= q.x + scene.frames[b].width ||
            p.y >= q.y + scene.frames[b].height,
        ).toBe(true);
      }
  });
  it("fits the frame around an ordered grid without dropping steps", () => {
    const many = Array.from({ length: 18 }, (_, i) =>
      event(`e${i + 1}`, "round.start", { round_index: i + 1 }),
    );
    const layout = layoutSubmap(task, many, true);
    expect(layout.steps).toHaveLength(20);
    expect(
      new Set(Object.values(layout.positions).map((p) => p.y)).size,
    ).toBeLessThanOrEqual(3);
    expect(layout.links).toHaveLength(layout.steps.length - 1);
    for (const p of Object.values(layout.positions)) {
      expect(p.x + 232).toBeLessThanOrEqual(layout.width);
      expect(p.y + 180).toBeLessThanOrEqual(layout.height - 48);
    }
    const frame = frameForSubmap(layout);
    expect(frame.width / frame.height).toBeCloseTo(
      layout.width / layout.height,
    );
    expect(
      frame.height -
        Math.max(
          ...Object.values(layout.positions).map(
            (p) => (p.y + 180) * frame.scale,
          ),
        ),
    ).toBeCloseTo(48 * frame.scale);
  });
  it("uses the true macro bounds for focus hit testing", () => {
    expect(
      zoomTarget(
        [{ id: "wide", position: { x: 0, y: 0 }, width: 2400, height: 900 }],
        { x: 0, y: 0, zoom: 0.5 },
        { x: 900, y: 200 },
        { x: 1400, y: 800 },
      ),
    ).toBe("wide");
  });
});

it("keeps late unnumbered observations moving right without crossing earlier rounds", () => {
  const layout = layoutSubmap(
    task,
    [
      event("e1", "round.start", { round_index: 1 }),
      event("e2", "round.review.completed", { round_index: 1 }),
      event("e3", "life.phase.started"),
      event("e4", "round.start", { round_index: 2 }),
    ],
    true,
  );
  const xs = layout.steps.map((step) => layout.positions[step.id].x);
  expect(xs).toEqual([...xs].sort((a, b) => a - b));
  expect(layout.steps.findIndex((s) => s.id === "e3")).toBeLessThan(
    layout.steps.findIndex((s) => s.id === "e4"),
  );
});

it("shows the result prose without runner-control fields", () => {
  expect(
    readableRecord(
      "Decision:\nMILESTONE_STATUS=done\nRESULT=完成 200 个独立种子。\nNEXT_OWNER=reviewer",
    ),
  ).toBe("完成 200 个独立种子。");
});

it("reuses geometry for streaming prose while recalculating changed content bounds", () => {
  const graph = buildMap([task]);
  const first = layoutScene(graph, [], true);
  const prose = layoutScene(
    buildMap([{ ...task, objective: "New public research detail" }]),
    [],
    true,
    first,
  );
  expect(prose.positions).toBe(first.positions);
  const rounds = Array.from({ length: 12 }, (_, i) =>
    event(`new-${i}`, "round.start", { round_index: i + 1 }),
  );
  const expanded = layoutScene(graph, rounds, true, prose);
  expect(expanded.positions).not.toBe(prose.positions);
  expect(expanded.frames[task.id]).not.toEqual(first.frames[task.id]);
});
