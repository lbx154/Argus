import { renderToStaticMarkup } from "react-dom/server";
import { act, create } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import type { Snapshot } from "../../../core/src/types";
import type { OperatorDecisionCard } from "../../../core/src/decisions";
import { MapOverview } from "../map/MapOverview";
import { projectOverview } from "../map/overview";
import { attentionTasks, type MapTask } from "../map/model";
import { emptyMissionView } from "../../../core/src/missionView";
import { currentWorkStatus, workStatusLabel } from "../lib/workStatus";
import { teamPhase } from "../components/TeamControls";
import { TeamPauseButton } from "../components/TeamControls";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const survey: MapTask = { id: "survey", title: "写中文综述", objective: "调研 reward hacking 并交付中文综述", status: "done", deps: [], ts: 1, finished_ts: 5 };
const later: MapTask = { id: "extra", title: "补充实验", objective: "", status: "failed", deps: ["survey"], ts: 6, finished_ts: 8 };
const question: OperatorDecisionCard = {
  id: "q1", item_id: "extra", revision: 1, status: "pending", title: "要不要扩大范围", reason: "", question: "要不要把 2024 年的工作也算进来？",
  evidence: [], options: [],
} as unknown as OperatorDecisionCard;

describe("projectOverview", () => {
  it("lists pending questions once under 等你决定", () => {
    const view = projectOverview({
      tasks: [survey, { ...later, pending_question: "要不要把 2024 年的工作也算进来？" }], decisions: [question],
    });
    expect(view.decisions.map((d) => d.taskId)).toEqual(["extra"]);
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

  it("keeps the header from saying 已完成 beside a step that did not finish, whatever files it owned", () => {
    const snapshot = (owns: string[]): Snapshot => ({
      session: { id: "s-7d7b7d22", display_name: "", objective: "", cwd: "/w", last_active: 1 },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, resumable: true }, roles: [], recent_events: [],
      backlog: [{ id: "last", title: "收尾", objective: "", status: "failed", priority: 1, owns_paths: owns } as Snapshot["backlog"][number]],
    }) as Snapshot;
    const view = emptyMissionView();
    Object.assign(view.mission, { id: "last", status: "failed" });
    for (const owns of [[".argus/maintenance/review-progress-repair"], ["report.md"]]) {
      const failed = currentWorkStatus(snapshot(owns), view, [], 10);
      expect([teamPhase(failed), workStatusLabel(failed, "zh-CN")]).toEqual(["paused", "当前任务未在运行"]);
    }
  });
});

describe("MapOverview", () => {
  const base = { zh: true, readOnly: false, onFocusTask: vi.fn(() => true), onAnswer: vi.fn() };

  it("shows the team line and hides 等你决定 when nothing waits", () => {
    const overview = projectOverview({ tasks: [survey, later], decisions: [] });
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(<MapOverview {...base} overview={overview} team={<div data-testid="team-line" />} />); });
    const root = renderer.root;
    expect(root.findAllByProps({ "data-testid": "team-line" })).toHaveLength(1);
    expect(root.findAll((n) => n.props["data-row"] === "decide")).toHaveLength(0);
    expect(root.findAll((n) => ["goal", "results", "checks"].includes(n.props["data-row"]))).toHaveLength(0);
  });


});

describe("canvas pause controls", () => {
  it("offers 暂停团队 while the team runs and 继续 when it stopped with work to take up", () => {
    const snapshot = (alive: boolean) => ({
      daemon: { alive, pid: null, uptime_seconds: null, backend: null, resumable: !alive },
      backlog: [{ id: "a", status: "in_progress" }],
    }) as unknown as Snapshot;
    const pause = renderToStaticMarkup(<TeamPauseButton snapshot={snapshot(true)} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(pause).toContain("暂停团队");
    const resume = renderToStaticMarkup(<TeamPauseButton snapshot={snapshot(false)} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(resume).toContain("继续");
  });
});
