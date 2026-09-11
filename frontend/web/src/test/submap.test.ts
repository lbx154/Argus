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
      title: "这一轮没有审阅", status: "skipped", eventIds: ["e1", "e2"],
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

describe('parallel Team task evidence', () => {
  const worker = (index: number, review = false, extra: Partial<MapEvent> = {}): MapEvent => {
    const route = `route-${String(index).padStart(2, '0')}`;
    return event(`team:${route}${review ? '-review' : ''}`, 'team.task', {
      ts: 10, team_id: 'ideas', team_task_id: `ideas-${route}${review ? '-review' : ''}`,
      team_role: review ? 'idea-review' : 'idea-route', role: review ? 'reviewer' : 'engineer',
      title: review ? 'Review route' : 'Investigate route', text: 'Source-grounded investigation',
      status: review ? 'pending' : 'running', deps: review ? [`team:${route}`] : [],
      ...extra,
    });
  };
  const activeTask = { ...task, status: 'running' };

  it('preserves every worker and its real failure while the parent is running', () => {
    const rows = buildSubmap(activeTask, [
      worker(1, false, { status: 'failed', reason: 'Required source report is missing.' }),
      worker(1, true), worker(2), worker(2, true),
      worker(3, false, { item_id: 'another-project-task' }),
    ], true);
    const workers = rows.filter((row) => row.source === 'team');
    expect(workers).toHaveLength(4);
    expect(workers[0]).toMatchObject({
      id: 'team:route-01', title: '研究路线 01', status: 'failed',
      teamId: 'ideas', teamTaskId: 'ideas-route-01', eventIds: ['team:route-01'],
    });
    expect(workers[0].detail).toContain('Required source report is missing.');
    expect(workers[1]).toMatchObject({ title: '独立复核 01', kind: 'review', status: 'pending' });
    expect(workers[1].detail).toContain('依赖: 研究路线 01');
    expect(rows.some((row) => row.teamRole === 'idea-selector')).toBe(false);
  });

  it('draws recorded route/review dependencies without serializing independent routes', () => {
    const rows = buildSubmap(activeTask, [worker(1), worker(1, true), worker(2), worker(2, true)], true);
    const links = submapLinks(rows, true);
    expect(links.filter((link) => link.relation === 'dependency').map((link) => [link.source, link.target])).toEqual([
      ['team:route-01', 'team:route-01-review'], ['team:route-02', 'team:route-02-review'],
    ]);
    expect(links.filter((link) => link.source === `${task.id}:brief`)).toHaveLength(2);
    expect(links.filter((link) => link.source === `${task.id}:brief`).every((link) => link.contextual)).toBe(true);
    expect(links.some((link) => link.source === 'team:route-01-review' && link.target === 'team:route-02')).toBe(false);
  });

  it('keeps twelve route/review pairs readable across cards and stable on status updates', () => {
    const observations = [event('e1', 'life.mission.started'), event('e2', 'round.start', { round_index: 1 }),
      ...Array.from({ length: 12 }, (_, index) => [worker(index + 1), worker(index + 1, true)]).flat()];
    const graph = buildMap([activeTask]);
    const scene = layoutScene(graph, observations, true);
    expect(scene.cards).toHaveLength(3);
    expect(Object.values(scene.layouts).flatMap((layout) => layout.steps).filter((row) => row.source === 'team')).toHaveLength(24);
    for (const layout of Object.values(scene.layouts)) {
      expect(layout.steps.length).toBeLessThanOrEqual(12);
      const ids = new Set(layout.steps.map((step) => step.id));
      for (const step of layout.steps.filter((row) => row.source === 'team'))
        expect((step.deps || []).every((id) => ids.has(id))).toBe(true);
    }
    expect(scene.cards[1].start).toBe(scene.cards[0].end + 1);
    expect(scene.cards[2].start).toBe(scene.cards[1].end + 1);
    const completed = layoutScene(graph, observations.map((row) => row.id === 'team:route-01'
      ? { ...row, status: 'done', revision: 'finished', updated_ts: 90 } : row), true, scene);
    expect(completed.positions).toBe(scene.positions);
    expect(Object.values(completed.layouts).flatMap((layout) => layout.steps).find((row) => row.id === 'team:route-01')?.status).toBe('done');
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

describe("work segments and single-agent turns", () => {
  it("turns a narration plus its tool calls into one plain execution step", () => {
    const steps = buildSubmap(task, [
      event("e1", "life.mission.started"),
      {
        ...event("e2", "work.segment", { role: "engineer" }),
        text: "先读取设计规范，再生成八页幻灯片。",
        steps: [
          { kind: "tool_use", label: 'view: {"path": "spec_lock.md"}', ts: 2, tool: "view" },
          { kind: "tool_use", label: 'rg: {"pattern": "case study"}', ts: 3, tool: "rg" },
          { kind: "command_execution", label: "python build.py", ts: 4, tool: "Build the deck" },
          { kind: "tool_use", label: 'web_fetch: {"url": "https://example.org/readme"}', ts: 5, tool: "web_fetch", status: "failed" },
        ],
      },
    ], true);
    const segment = steps.find((step) => step.id === "e2");
    expect(segment).toMatchObject({ kind: "execution", title: "先读取设计规范", status: "failed" });
    expect(segment?.summary).toBe("先读取设计规范，再生成八页幻灯片。");
    expect(segment?.detail).toBe([
      "先读取设计规范，再生成八页幻灯片。",
      ["· 查看 spec_lock.md", "· 查找 case study", "· Build the deck", "· 读取网页 https://example.org/readme（失败）"].join("\n"),
    ].join("\n\n"));
  });

  it("describes a segment with no narration by the shape of its work", () => {
    const steps = buildSubmap(task, [{
      ...event("e3", "work.segment"),
      text: "",
      overflow: 2,
      steps: [
        { kind: "tool_use", label: "view: a", ts: 1, tool: "view" },
        { kind: "tool_use", label: "view: b", ts: 2, tool: "view" },
        { kind: "command_execution", label: "make", ts: 3 },
      ],
    }], false);
    const segment = steps.find((step) => step.id === "e3");
    expect(segment?.title).toBe("3 steps of work");
    expect(segment?.summary).toBe("2 files read, 1 command run, 2 more not listed.");
  });

  it("lays out a single-agent turn as ask, work and answer", () => {
    const turn: MapTask = { id: "turn:web-1", kind: "turn", title: "README 有几行？", objective: "README 有几行？", status: "done", deps: [], role: "manager" };
    const steps = buildSubmap(turn, [
      { ...event("turn:web-1:work", "work.segment", { item_id: "turn:web-1", role: "manager" }), text: "",
        steps: [{ kind: "command_execution", label: "$ wc -l README.md", ts: 10, tool: "Count README lines", status: "completed" }] },
      { ...event("turn:web-1:reply", "turn.replied", { item_id: "turn:web-1", role: "manager", status: "done" }), text: "一行。" },
    ], true);
    expect(steps.map((step) => [step.kind, step.title])).toEqual([
      ["plan", "你提出的要求"],
      ["execution", "Argus 动手查证"],
      ["result", "Argus 的回答"],
    ]);
    expect(steps[1].detail).toBe("· Count README lines");
    expect(steps[2].summary).toBe("一行。");
  });
});

describe("work segments inside a round", () => {
  it("keeps the round's closing node after the segments recorded within it", () => {
    const steps = buildSubmap(task, [
      event("e1", "life.mission.started"),
      event("e2", "round.start", { round_index: 1 }),
      { ...event("e3", "work.segment"), text: "先读文档。", steps: [{ kind: "tool_use", label: "view: a", ts: 3, tool: "view" }] },
      { ...event("e4", "work.segment", { role: "reviewer" }), text: "核对结果。", steps: [{ kind: "tool_use", label: "view: b", ts: 4, tool: "view" }] },
      event("e5", "round.main.completed", { round_index: 1, text: "做完了。" }),
    ], true);
    expect(steps.map((step) => [step.kind, step.id])).toEqual([
      ["plan", "a:brief"],
      ["execution", "e1"],
      ["execution", "e3"],
      ["review", "e4"],
      ["execution", "e2"],
      ["result", "a:outcome"],
    ]);
    expect(steps[4].eventIds).toEqual(["e2", "e5"]);
  });
});

it("heads each step column with the round of work it holds", () => {
  const task = { id: "t", title: "T", objective: "T", status: "done", deps: [] } as MapTask;
  const rounds = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ id: `r${i + 1}`, type: "round.start", ts: i + 1, item_id: "t", round_index: i + 1, text: "" }) as MapEvent);
  const many = layoutSubmap(task, rounds(7), false);
  expect(many.columns.map((c) => c.title)).toEqual(["Setting out · Rounds 1–2", "Rounds 3–5", "Rounds 6–7 · Outcome"]);
  const few = layoutSubmap(task, rounds(1), true);
  expect(few.columns.map((c) => c.title)).toEqual(["起点", "第 1 轮", "结果"]);
  const none = layoutSubmap(task, [], false);
  expect(none.columns.map((c) => c.title)).toEqual(["Setting out", "Outcome"]);
  // A segment of work recorded without a round number belongs to the round under way.
  const segment = layoutSubmap(task, [
    ...rounds(1),
    { id: "p", type: "life.phase.started", ts: 5, item_id: "t", text: "", label: "implementation" } as MapEvent,
  ], false);
  for (const title of segment.columns.map((c) => c.title))
    expect(["Setting out", "Setting out · Round 1", "Round 1", "Round 1 · cont.", "Round 1 · Outcome", "Round 1 · cont. · Outcome", "Outcome"]).toContain(title);
  // Work recorded before the round's own record still belongs to that round.
  const early = layoutSubmap(task, [
    { id: "w", type: "life.mission.started", ts: 0, item_id: "t", text: "" } as MapEvent,
    { id: "p", type: "life.phase.started", ts: 0.5, item_id: "t", text: "", label: "implementation" } as MapEvent,
    ...rounds(1),
  ], false);
  for (const title of early.columns.map((c) => c.title))
    expect(["Setting out", "Setting out · Round 1", "Round 1", "Round 1 · cont.", "Round 1 · Outcome", "Round 1 · cont. · Outcome", "Outcome"]).toContain(title);
});
