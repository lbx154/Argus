import { renderToStaticMarkup } from "react-dom/server";
import { act, create } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import type { DeliveryReceipt, LaunchHold, Snapshot } from "../../../core/src/types";
import type { OperatorDecisionCard } from "../../../core/src/decisions";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { MapLaunchContext } from "../map/launchContext";
import { MapOverview } from "../map/MapOverview";
import { projectOverview } from "../map/overview";
import { layoutSubmap } from "../map/submap";
import type { MapEvent, MapTask } from "../map/model";
import { TeamPauseButton } from "../components/TeamControls";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const survey: MapTask = { id: "survey", title: "写中文综述", objective: "调研 reward hacking 并交付中文综述", status: "done", deps: [], ts: 1, finished_ts: 5 };
const later: MapTask = { id: "extra", title: "补充实验", objective: "", status: "failed", deps: ["survey"], ts: 6, finished_ts: 8 };
const review = (id: string, item: string, status: string, ts: number, text: string): MapEvent =>
  ({ id, item_id: item, type: "round.review.completed", ts, text, role: "reviewer", status });
const receipt = (patch: Partial<DeliveryReceipt> = {}): DeliveryReceipt => ({
  schema_version: 1, delivery_id: "d1", kind: "task_completed", item_id: "survey", title: "写中文综述",
  summary: "综述已交付", status: "done", review_status: "done", review_source: "reviewer", delivered_at: 5,
  primary_target: { path: "reward-hacking-survey-zh.md", label: "中文综述", source: "", why: "" }, targets: [], ...patch,
});
const hold: LaunchHold = { created_at: 1, item_id: "plan", title: "复现基线", request: "复现基线", continuous: false, settings: {} };
const question: OperatorDecisionCard = {
  id: "q1", item_id: "extra", revision: 1, status: "pending", title: "要不要扩大范围", reason: "", question: "要不要把 2024 年的工作也算进来？",
  evidence: [], options: [],
} as unknown as OperatorDecisionCard;

describe("projectOverview", () => {
  it("keeps a reviewed result under 主要成果 and 已验证 after a later item was aborted", () => {
    const view = projectOverview({
      goal: "", zh: true, tasks: [survey, later], receipts: [receipt()], decisions: [],
      events: [review("r1", "survey", "done", 4, "38 个链接均可访问，数字已逐条核对")],
    });
    expect(view.goal).toBe("调研 reward hacking 并交付中文综述");
    expect(view.results).toHaveLength(1);
    expect(view.results[0]).toMatchObject({ title: "中文综述", path: "reward-hacking-survey-zh.md", reviewed: true, taskId: "survey" });
    expect(view.checks).toEqual([expect.objectContaining({ taskId: "survey", title: "写中文综述", basis: "38 个链接均可访问，数字已逐条核对" })]);
    expect(view.decisions).toEqual([]);
  });

  it("lets the latest independent review decide, and ignores an engineer's self-check", () => {
    const view = projectOverview({
      goal: "目标", zh: true, tasks: [survey, later], receipts: [receipt({ review_source: "engineer_self_check" })], decisions: [],
      events: [
        review("r1", "survey", "done", 3, "通过"),
        review("r2", "survey", "rejected", 4, "数字对不上"),
        { ...review("r3", "extra", "done", 7, "自查通过"), role: "engineer" },
      ],
    });
    expect(view.checks).toEqual([]);
    expect(view.results[0].reviewed).toBe(false);
  });

  it("falls back to the receipt when the reviewer settled it outside the map window, newest delivery per task", () => {
    const view = projectOverview({
      goal: "目标", zh: true, tasks: [survey], events: [], decisions: [],
      receipts: [receipt({ delivery_id: "old", delivered_at: 2 }), receipt({ delivery_id: "new", delivered_at: 9, summary: "第二版" })],
    });
    expect(view.results.map((r) => r.key)).toEqual(["new"]);
    expect(view.checks).toEqual([expect.objectContaining({ taskId: "survey", basis: "第二版" })]);
  });

  it("shows a reviewed file as a result when its receipt was held back for later work that stopped", () => {
    const accepted: MapEvent = { id: "c1", item_id: "survey", type: "life.mission.completed", ts: 5, text: "已交付并通过独立复核",
      role: "reviewer", status: "done", success: true, delivery_candidates: ["reward-hacking-survey-zh.md"] };
    const view = projectOverview({ goal: "目标", zh: true, tasks: [survey, { ...later, status: "parked" }], receipts: [], decisions: [],
      events: [accepted, review("r1", "survey", "done", 4, "三大主题、出处均已核查")] });
    expect(view.results).toEqual([expect.objectContaining({ path: "reward-hacking-survey-zh.md", reviewed: true })]);
    expect(view.results[0].receipt).toBeUndefined();
    expect(view.checks.map((c) => c.taskId)).toEqual(["survey"]);
    // An engineer's own completion is not a reviewed result.
    const own = projectOverview({ goal: "目标", zh: true, tasks: [survey], receipts: [], decisions: [],
      events: [{ ...accepted, role: "engineer" }] });
    expect(own.results).toEqual([]);
  });

  it("lists a plan waiting to start and pending questions under 等你决定", () => {
    const view = projectOverview({
      goal: "目标", zh: true, tasks: [survey, { ...later, pending_question: "要不要把 2024 年的工作也算进来？" }],
      events: [], receipts: [], decisions: [question], launchHold: hold,
    });
    expect(view.decisions.map((d) => [d.kind, d.taskId])).toEqual([["launch", "plan"], ["question", "extra"]]);
  });
});

describe("MapOverview", () => {
  const base = { zh: true, readOnly: false, onOpenReceipt: vi.fn(), onOpenArtifact: vi.fn(), onFocusTask: vi.fn(() => true), onAnswer: vi.fn() };

  it("opens results, focuses checked cards, and hides 等你决定 when nothing waits", () => {
    const overview = projectOverview({ goal: "", zh: true, tasks: [survey, later], receipts: [receipt()], decisions: [],
      events: [review("r1", "survey", "done", 4, "数字已核对")] });
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(<MapOverview {...base} overview={overview} team={<div data-testid="team-line" />} />); });
    const root = renderer.root;
    expect(root.findAllByProps({ "data-testid": "team-line" })).toHaveLength(1);
    expect(root.findAll((n) => n.props["data-row"] === "decide")).toHaveLength(0);
    act(() => root.findByProps({ "data-testid": "overview-result" }).props.onClick());
    expect(base.onOpenReceipt).toHaveBeenCalledWith(expect.objectContaining({ delivery_id: "d1" }), "reward-hacking-survey-zh.md");
    act(() => root.findByProps({ "data-testid": "overview-check" }).props.onClick());
    expect(base.onFocusTask).toHaveBeenCalledWith("survey");
  });

  it("starts a held plan from the overview with the chat card's action", async () => {
    const onStartLaunch = vi.fn(() => Promise.resolve());
    const overview = projectOverview({ goal: "目标", zh: true, tasks: [], events: [], receipts: [], decisions: [], launchHold: hold });
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(<MapOverview {...base} overview={overview} onStartLaunch={onStartLaunch} />); });
    const start = renderer.root.findByProps({ "data-testid": "launch-start" });
    expect(start.children.join("")).toBe("开始执行");
    await act(async () => { await start.props.onClick({ stopPropagation: () => {} }); });
    expect(onStartLaunch).toHaveBeenCalledTimes(1);
    act(() => { renderer.update(<MapOverview {...base} readOnly overview={overview} onStartLaunch={onStartLaunch} />); });
    expect(renderer.root.findAllByProps({ "data-testid": "launch-start" })).toHaveLength(0);
  });
});

describe("canvas start and pause controls", () => {
  const task: MapTask = { id: "plan", title: "复现基线", objective: "", status: "pending", deps: [] };
  const node = (patch: Partial<MacroData> = {}) => ({
    id: "plan-card",
    data: {
      id: "plan-card", task, ordinal: 1, part: 1, partCount: 1, start: 1, end: 1, totalSteps: 0,
      layout: layoutSubmap(task, [], false, []), frame: { width: 900, height: 650, scale: 1 },
      canvasSize: { width: 1440, height: 960 }, zh: true, focused: false, detailed: false, live: true,
      source: "live:project", readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(), ...patch,
    } as unknown as MacroData,
  }) as unknown as NodeProps<MacroNode>;
  const render = (value: { itemId?: string; start?: () => Promise<void> }, patch: Partial<MacroData> = {}) =>
    renderToStaticMarkup(<MapLaunchContext.Provider value={value}><MacroTaskNode {...node(patch)} /></MapLaunchContext.Provider>);

  it("shows 开始执行 on the card of the plan waiting for confirmation only", () => {
    const start = () => Promise.resolve();
    expect(render({ itemId: "plan", start })).toContain("开始执行");
    expect(render({ itemId: "other", start })).not.toContain("开始执行");
    expect(render({ itemId: "plan" })).not.toContain("开始执行");
    expect(render({ itemId: "plan", start }, { readOnly: true })).not.toContain("开始执行");
  });

  it("offers 暂停团队 while the team runs and 继续 when it stopped with work queued", () => {
    const snapshot = (alive: boolean) => ({
      daemon: { alive, pid: null, uptime_seconds: null, backend: null },
      backlog: [{ id: "a", status: "pending" }],
    }) as unknown as Snapshot;
    const pause = renderToStaticMarkup(<TeamPauseButton snapshot={snapshot(true)} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(pause).toContain("暂停团队");
    const resume = renderToStaticMarkup(<TeamPauseButton snapshot={snapshot(false)} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(resume).toContain("继续");
  });
});
