import { renderToStaticMarkup } from "react-dom/server";
import { act, create } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import type { LaunchHold, Snapshot } from "../../../core/src/types";
import type { OperatorDecisionCard } from "../../../core/src/decisions";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { MapLaunchContext } from "../map/launchContext";
import { MapOverview } from "../map/MapOverview";
import { projectOverview } from "../map/overview";
import { layoutSubmap } from "../map/submap";
import { attentionTasks, type MapTask } from "../map/model";
import { emptyMissionView } from "../../../core/src/missionView";
import { currentWorkStatus, workStatusLabel } from "../lib/workStatus";
import { teamPhase, teamPhaseLabel } from "../components/TeamControls";
import { TeamPauseButton } from "../components/TeamControls";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const survey: MapTask = { id: "survey", title: "写中文综述", objective: "调研 reward hacking 并交付中文综述", status: "done", deps: [], ts: 1, finished_ts: 5 };
const later: MapTask = { id: "extra", title: "补充实验", objective: "", status: "failed", deps: ["survey"], ts: 6, finished_ts: 8 };
const hold: LaunchHold = { created_at: 1, item_id: "plan", title: "复现基线", request: "复现基线", continuous: false, settings: {} };
const question: OperatorDecisionCard = {
  id: "q1", item_id: "extra", revision: 1, status: "pending", title: "要不要扩大范围", reason: "", question: "要不要把 2024 年的工作也算进来？",
  evidence: [], options: [],
} as unknown as OperatorDecisionCard;

describe("projectOverview", () => {
  it("lists a plan waiting to start and pending questions under 等你决定", () => {
    const view = projectOverview({
      zh: true, tasks: [survey, { ...later, pending_question: "要不要把 2024 年的工作也算进来？" }], decisions: [question], launchHold: hold,
    });
    expect(view.decisions.map((d) => [d.kind, d.taskId])).toEqual([["launch", "plan"], ["question", "extra"]]);
  });
});

// s-7d7b7d22 (2026-10-24): the survey was delivered and passed independent
// review; three closing steps were then replaced by newer plans, one aborted by
// the operator, and the last, a repair of Argus's own records, failed on a
// supervisor error. The map said 已完成 · 这一步执行未完成 · 这次工作未完成 and
// "1 项需要你处理" beside a reviewed result.
describe("closing steps after a reviewed survey (s-7d7b7d22)", () => {
  const closing = (id: string, patch: Partial<MapTask>): MapTask =>
    ({ id, title: id, objective: "", status: "superseded", deps: ["survey"], ts: 6, finished_ts: 7,
      owns_paths: ["reward-hacking-survey-zh.md", ".argus/evidence/final-certification.md"], ...patch });
  const tasks = [survey,
    closing("9e5992dac968", { superseded_by_plan_id: "plan-2" }),
    closing("67c874f26b5e", { status: "failed", superseded_by_plan_id: "plan-3" }),
    closing("12dedb2805cb", { status: "aborted", outcome: { interruption_kind: "operator_abort" } }),
    closing("x-aborted-failed", { status: "failed", outcome: { interruption_kind: "operator_abort" } }),
    closing("681aae2979d7", { status: "failed", ts: 9, owns_paths: [".argus/maintenance/review-progress-repair"] })];

  it("does not ask the reader about aborted, replaced, or Argus-housekeeping steps", () => {
    expect(attentionTasks(tasks)).toEqual([]);
    // A failed step on the person's own files still needs them.
    expect(attentionTasks([...tasks, closing("real", { status: "failed", ts: 10, owns_paths: ["experiments/run.py"] })])
      .map((task) => task.id)).toEqual(["real"]);
  });

  it("keeps the header from saying 已完成 beside a step that did not finish", () => {
    const snapshot = (owns: string[]): Snapshot => ({
      session: { id: "s-7d7b7d22", display_name: "", objective: "", cwd: "/w", last_active: 1 },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null }, roles: [], recent_events: [],
      backlog: [{ id: "last", title: "收尾", objective: "", status: "failed", priority: 1, owns_paths: owns } as Snapshot["backlog"][number]],
    }) as Snapshot;
    const view = emptyMissionView();
    Object.assign(view.mission, { id: "last", status: "failed" });
    const housekeeping = currentWorkStatus(snapshot([".argus/maintenance/review-progress-repair"]), view, [], 10);
    expect(housekeeping.reason).toBe("");
    expect([teamPhaseLabel(teamPhase(housekeeping), "zh-CN"), workStatusLabel(housekeeping, "zh-CN")]).toEqual(["已完成", "这一步已结束"]);
    const failed = currentWorkStatus(snapshot(["report.md"]), view, [], 10);
    expect([teamPhaseLabel(teamPhase(failed), "zh-CN"), workStatusLabel(failed, "zh-CN")]).toEqual(["已停止", "这一步执行未完成"]);
  });
});

describe("MapOverview", () => {
  const base = { zh: true, readOnly: false, onFocusTask: vi.fn(() => true), onAnswer: vi.fn() };

  it("shows the team line and hides 等你决定 when nothing waits", () => {
    const overview = projectOverview({ zh: true, tasks: [survey, later], decisions: [] });
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(<MapOverview {...base} overview={overview} team={<div data-testid="team-line" />} />); });
    const root = renderer.root;
    expect(root.findAllByProps({ "data-testid": "team-line" })).toHaveLength(1);
    expect(root.findAll((n) => n.props["data-row"] === "decide")).toHaveLength(0);
    expect(root.findAll((n) => ["goal", "results", "checks"].includes(n.props["data-row"]))).toHaveLength(0);
  });

  it("starts a held plan from the overview with the chat card's action", async () => {
    const onStartLaunch = vi.fn(() => Promise.resolve());
    const overview = projectOverview({ zh: true, tasks: [], decisions: [], launchHold: hold });
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
