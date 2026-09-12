import { createContext, memo, useContext, useEffect, useRef, useState, type CSSProperties } from "react";
import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  GitBranch,
  HelpCircle,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { MarkdownExcerpt } from "../components/MarkdownExcerpt";
import { Button } from "../components/primitives";
import { MapReaderContent, type MapReaderSelection } from "./MapReaderContent";
import type { ArtifactInfo } from "../api";
import type { MapCopy, CardReference } from "./presentation";
import { ACTIVE, statusKey } from "./model";
import {
  STEP_KINDS,
  humanizeHarnessNote,
  noDetails,
  type StepKind,
  type SubmapLayout,
  type SubmapStep,
  type MapCard,
} from "./submap";
import { SubmapEdges } from "./SubmapEdges";
import { LiveLine } from "./LiveLine";
import { arrivalDelay } from "./alive";

export type MacroData = MapCard & {
  zh: boolean;
  layout: SubmapLayout;
  frame: { width: number; height: number; scale: number };
  canvasSize?: { width: number; height: number };
  open: (id: string, history?: boolean) => void;
  toggleHistory?: (taskId: string) => void;
  focused: boolean;
  detailed: boolean;
  copy?: Pick<MapCopy, "cards" | "version">;
  readCopy?: (nodeId: string, key: string | null) => void;
  readerCopy?: MapReaderSelection;
  live: boolean;
  paused?: boolean;
  growthDelay?: number;
  dispatchState?: 'receiving' | 'landed';
  growingSteps?: Record<string, number>;
  growingLinks?: Record<string, number>;
  /** Fan width the planner declared for this task's formation, if any. */
  plannedWidth?: number;
  seenCards?: Set<string>;
  restoring?: boolean;
  /** The pointer is on this card, or on one it is related to. */
  lit?: boolean;
  hovered?: boolean;
  /** The map is composing itself after a first opening. */
  arriving?: boolean;
  /** A replay is revealing cards in order. */
  revealing?: boolean;
  /** The role at work on this card right now, when the session is live. */
  phase?: string;
  readOnly: boolean;
  source: string;
  quote: (ref: CardReference) => void;
  menu: (ref: CardReference, point: { x: number; y: number }) => void;
  readStep: (
    id: string,
    rect: {
      x: number;
      y: number;
      width: number;
      height: number;
      scale: number;
    },
  ) => void;
} & Record<string, unknown>;
export type MacroNode = Node<MacroData, "task">;
/** Artifact previews change often and are only read inside the open reader;
 * context keeps them out of every node's data so updates skip idle nodes. */
/** Operator margin notes per node id; provided once by MapPanel so note
 * updates never churn every node's data object. */
export const MapNotesContext = createContext<{
  notes: Record<string, import("./notes").MapNote[]>;
}>({ notes: {} });
export const MapArtifactContext = createContext<{
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
}>({});
const KINDS: Record<StepKind, [string, string]> = {
  plan: ["Planner", "Planner"],
  execution: ["Engineer", "Engineer"],
  review: ["Reviewer", "Reviewer"],
  revision: ["修订", "Revise"],
  result: ["结果", "Result"],
};
const ICONS = {
  plan: GitBranch,
  execution: Play,
  review: ShieldCheck,
  revision: RotateCcw,
  result: FileText,
};
/* Status words a reader outside the team understands at a glance. */
const STATES: Record<string, [string, string]> = {
  done: ["已完成", "Completed"],
  running: ["进行中", "In progress"],
  pending: ["待开始", "Planned"],
  failed: ["未通过", "Did not pass"],
  aborted: ["已取消", "Cancelled"],
  skipped: ["已跳过", "Skipped"],
  superseded: ["已被新计划替代", "Replaced by a new plan"],
  question: ["等待答复", "Waiting for an answer"],
  paused: ["已暂停", "Paused"],
  paused_external_work: ["等待后台任务", "Waiting on background work"],
  missing: ["记录中缺失", "Not in this record"],
  unknown: ["状态未知", "Unknown"],
  continue: ["需再改一轮", "Another pass needed"],
  blocked: ["受阻", "Held up"],
  started: ["进行中", "Under way"],
  recorded: ["已记录", "On record"],
  requested: ["修改建议", "Requested"],
  replan: ["需要调整计划", "Plan needs adjusting"],
  replan_requested: ["需要调整计划", "Plan needs adjusting"],
};
/* One sentence per stage, so a newcomer learns who does what while reading. */
const KIND_NOTES: Record<StepKind, [string, string]> = {
  plan: ["规划者决定要做什么、为什么做", "The Planner decides what to do and why"],
  execution: ["工程师动手把事情做出来", "The Engineer does the work"],
  review: ["审阅者独立核查结果", "The Reviewer checks the result independently"],
  revision: ["审阅者要求修改的地方", "What the Reviewer asked to change"],
  result: ["这项任务最后得到了什么", "What the task produced in the end"],
};
/* A single-agent turn has a different cast: you asked, Argus worked, Argus answered. */
const TURN_KINDS: Record<StepKind, [string, string]> = {
  plan: ["提问", "Asked"],
  execution: ["Argus 查证", "Argus"],
  review: ["核对", "Check"],
  revision: ["修订", "Revise"],
  result: ["回答", "Answer"],
};
const TURN_KIND_NOTES: Record<StepKind, [string, string]> = {
  plan: ["你提出的要求", "What you asked for"],
  execution: ["Argus 自己动手查证、运行命令", "Argus did the work itself: read, searched, ran commands"],
  review: ["对结果的核对", "A check of the result"],
  revision: ["需要修改的地方", "What had to change"],
  result: ["Argus 给出的回答", "What Argus answered"],
};
const sourceLabel = (step: SubmapStep, zh: boolean) =>
  step.source === "team"
    ? zh ? "子任务工作记录" : "Subtask work record"
    : step.source === "task"
    ? zh
      ? "任务记录"
      : "Task record"
    : step.source === "interval"
      ? zh
        ? "根据同期记录关联"
        : "By execution window"
      : zh
        ? "来自任务记录"
        : "Linked event";

/** Both levels occupy the same fixed node; only the camera changes their apparent size. */
export const MacroTaskNode = memo(function MacroTaskNode({
  id,
  data,
}: NodeProps<MacroNode>) {
  const { task, ordinal, zh, layout: currentLayout, focused, detailed } = data;
  const { artifacts, onOpenArtifact } = useContext(MapArtifactContext);
  const cardNotes = useContext(MapNotesContext).notes[task.id] ?? [];
  // Only density thresholds trigger React work; continuous zoom typography is CSS.
  const density = useStore((state) => {
    const width = state.transform[2] * data.frame.width;
    return width < 140 ? 'micro' : width < 230 ? 'compact' : 'full';
  });
  const [arrive] = useState(() => !!data.revealing || (!data.restoring && !data.seenCards?.has(id)));
  useEffect(() => { data.seenCards?.add(id); }, [data.seenCards, id]);
  const [readingLayout, setReadingLayout] = useState<SubmapLayout | null>(null);
  const layout = readingLayout || currentLayout;
  const currentStep = (step: SubmapStep) => step.source === 'team'
    ? currentLayout.steps.find((current) => current.id === step.id) || step : step;
  const screenWidth = data.canvasSize?.width || window.innerWidth;
  const screenHeight = data.canvasSize?.height || window.innerHeight;
  useEffect(() => {
    if (!detailed) {
      setDetailId(null);
      setReadingLayout(null);
      data.readCopy?.(id, null);
    }
  }, [detailed]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const previousStatus = useRef(task.status);
  const [completedNow, setCompletedNow] = useState(false);
  useEffect(() => {
    const changed = previousStatus.current !== task.status;
    previousStatus.current = task.status;
    if (!changed || task.status !== 'done' || data.completionScope) return;
    setCompletedNow(true);
    const timer = setTimeout(() => setCompletedNow(false), 1500);
    return () => clearTimeout(timer);
  }, [task.status, data.completionScope]);
  const selectedDetail = layout.steps.find((s) => s.id === detailId);
  const detail = selectedDetail ? currentStep(selectedDetail) : undefined;
  const detailTs = detail?.updatedAt ?? detail?.ts;
  const isLastPart = data.part === data.partCount;
  const state = !isLastPart || data.completionScope
    ? "recorded"
    : task.status === "missing"
      ? "missing"
      : data.paused && ACTIVE.has(task.status) ? "paused" : statusKey(task);
  const displayedState = state === 'paused' && task.status === 'paused_external_work'
    ? task.status : state;
  const summaryScale = Math.min(
    data.frame.width / 288,
    data.frame.height / 218,
  );
  const copy = data.copy?.cards || {};
  // Drifted status makes the generated summary dated, not wrong: keep showing
  // yesterday's prose and only hint that a refresh is on its way.
  // Only promise a refresh while the task is still moving: a terminal task
  // whose copy never regenerates would wear the hint forever.
  const staleSummary =
    Boolean(copy[task.id]?.summary) &&
    copy[task.id]?.task_status !== task.status &&
    (ACTIVE.has(task.status) || task.status === "pending");
  const stepCopy = (step: SubmapStep) => {
    if (step.completionScope) return undefined;
    const saved = copy[step.id];
    if (step.source !== 'team') return saved;
    const index = saved?.event_ids?.indexOf(step.id) ?? -1;
    return currentStep(step).revision && index >= 0 && saved?.event_revisions?.[index] === currentStep(step).revision ? saved : undefined;
  };
  const title = data.completionScope ? task.title : copy[task.id]?.title || task.title;
  const range = zh
    ? `环节 ${data.start}–${data.end} / ${data.totalSteps}`
    : `Steps ${data.start}–${data.end} / ${data.totalSteps}`;
  const partSummary =
    data.partCount > 1
      ? layout.steps
          .map((step) => stepCopy(step)?.summary || currentStep(step).summary || currentStep(step).detail)
          .filter(
            (value) => value && ![noDetails(true), noDetails(false)].includes(value),
          )
          .at(-1)
      : undefined;
  const scale = Math.min(
    data.frame.width / layout.width,
    data.frame.height / layout.height,
  );
  const activityStep =
    isLastPart && data.live && ACTIVE.has(task.status)
      ? [...layout.steps]
          .reverse()
          .find((s) => s.source !== 'team' && !["plan", "result"].includes(s.kind))?.id
      : null;
  const activeStep = data.paused ? null : activityStep;
  const teamSteps = currentLayout.steps.filter((step) => step.source === 'team');
  const activeTeamSteps = data.live ? teamSteps.filter((step) => ACTIVE.has(step.status)).map((step) => step.id) : [];
  const teamComplete = teamSteps.filter((step) => step.status === 'done').length;
  const teamRunning = teamSteps.filter((step) => ACTIVE.has(step.status)).length;
  const teamSummary = (zh ? `子任务 ${teamComplete}/${teamSteps.length} 完成 · ${teamRunning} 进行中` : `Subtasks ${teamComplete}/${teamSteps.length} done · ${teamRunning} running`)
    + ((data.plannedWidth ?? 0) > teamSteps.length
      ? zh ? ` · 计划并行 ×${data.plannedWidth}` : ` · planned ×${data.plannedWidth}`
      : "");
  const isStepActive = (step: SubmapStep) => step.source === 'team'
    ? activeTeamSteps.includes(step.id) : activeStep === step.id;
  const stepStatus = (step: SubmapStep) => step.source === 'team'
    ? currentStep(step).status
    : activityStep === step.id ? data.paused ? 'paused' : 'running' : step.status;
  const reference = (step?: SubmapStep): CardReference => ({
    source: data.source,
    task_id: task.id,
    task_title: title,
    lang: zh ? "zh" : "en",
    part: data.partCount > 1 ? data.part : undefined,
    step_id: step?.id,
    step_title: step?.title,
    team_id: step?.teamId,
    team_task_id: step?.teamTaskId,
    event_ids: step
      ? step.eventIds
      : data.partCount > 1
        ? [...new Set(layout.steps.flatMap((s) => s.eventIds))]
        : copy[task.id]?.event_ids || [],
  });
  const readerWidth = Math.min(
    640,
    layout.width - 48,
    Math.max(260, (screenWidth - 50) / 1.05),
  );
  const readerHeight = Math.min(
    600,
    layout.height - 48,
    Math.max(300, (screenHeight - (screenWidth < 640 ? 180 : 160)) / 1.05),
  );
  const rectFor = (step: SubmapStep) => ({
    x: Math.max(
      24,
      Math.min(
        layout.positions[step.id].x - 16,
        layout.width - readerWidth - 24,
      ),
    ),
    y: Math.max(
      24,
      Math.min(
        layout.positions[step.id].y - 16,
        layout.height - readerHeight - 24,
      ),
    ),
    width: readerWidth,
    height: readerHeight,
    scale,
  });
  const reader = detail ? rectFor(detail) : null;
  const read = (step: SubmapStep) => {
    setReadingLayout(layout);
    setDetailId(step.id);
    data.readCopy?.(id, step.id);
    data.readStep(id, rectFor(step));
  };
  useEffect(() => {
    if (detail) data.readStep(id, rectFor(detail));
    // Refit only when the available canvas changes, not on live text updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenWidth, screenHeight]);
  const stateLabel = (s: string) =>
    (STATES[
      s === 'paused_external_work' ? s : s.startsWith("paused_") ? "paused" : ACTIVE.has(s) ? "running" : s
    ] ?? STATES.unknown)[zh ? 0 : 1];
  const taskStateLabel = data.completionScope
    ? zh ? "执行结束 · 目标未完成" : "Execution ended · goal incomplete"
    : stateLabel(displayedState);
  return (
    <article
      className={`map-macro map-state-${state}`}
      data-testid="map-macro"
      data-task-id={task.id}
      data-card-id={id}
      data-part={data.part}
      data-history={!isLastPart}
      data-has-history={!!data.historyCount}
      // A first opening lets every card take the stage, whether or not the
      // viewport had already rendered it once; a replay only animates cards
      // that are being revealed.
      data-arrive={!!data.arriving || (arrive && !!data.revealing)}
      data-lit={!!data.lit}
      data-hovered={!!data.hovered}
      data-growing={data.growthDelay != null}
      data-dispatch={data.dispatchState}
      style={{
        animationDelay: `${data.growthDelay ?? (data.arriving ? arrivalDelay(ordinal) : 0)}ms`,
      } as CSSProperties}
      data-focused={focused}
      data-detailed={detailed}
      aria-label={title}
      data-overview-density={density}
      data-completed-now={completedNow}
      data-active={activeTeamSteps.length > 0 || isLastPart && data.live && !data.paused && ACTIVE.has(task.status)}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        data.menu(reference(), { x: e.clientX, y: e.clientY });
      }}
    >
      {(["source", "target"] as const).flatMap((type) =>
        [Position.Left, Position.Right, Position.Top, Position.Bottom].map(
          (position) => (
            <Handle
              key={`${type}-${position}`}
              id={position}
              type={type}
              position={position}
              isConnectable={false}
            />
          ),
        ),
      )}
      <div
        className="macro-summary"
        aria-hidden={detailed}
        style={{
          "--summary-scale": summaryScale,
          "--summary-height": `${data.frame.height / summaryScale - 20}px`,
          width: data.frame.width / summaryScale - 20,
          transform: `translate(-50%, -50%) scale(${summaryScale})`,
        } as import("react").CSSProperties}
      >
        <button
          className={`map-card map-state-${state} nodrag nopan`}
          data-testid="map-card"
          data-task-id={task.id}
          data-card-id={id}
          data-part={data.part}
          tabIndex={detailed ? -1 : 0}
          onClick={() => data.open(id)}
          aria-label={`${title} · ${zh ? "放大任务" : "Explore task"}`}
        >
          <div className="map-card-top">
            <span className="map-card-number">
              {isLastPart ? (zh ? "任务" : "MISSION") : (zh ? "历史" : "HISTORY")}
              {" "}{String(ordinal).padStart(2, "0")}
              {!isLastPart && ` · ${data.part}/${data.partCount}`}
            </span>
            {/* An earlier part of a long task has no state of its own; the
                task's state is read on its last part, so no chip here. An
                execution that ended before the goal was complete keeps its
                label even on a recorded part. */}
            {(state !== "recorded" || data.completionScope) && (
              <span className="map-status">
                {state === "done" ? (
                  <Check size={11} />
                ) : state === "failed" ? (
                  <X size={11} />
                ) : state === "question" ? (
                  <HelpCircle size={11} />
                ) : state === "paused" ? (
                  <Pause size={11} />
                ) : (
                  <span className="map-state-dot" />
                )}
                {taskStateLabel}
              </span>
            )}
          </div>
          <h3>
            <MarkdownExcerpt>{title}</MarkdownExcerpt>
            {cardNotes.length > 0 && (
              <span
                className="macro-note-badge"
                title={zh ? "操作员批注" : "Operator notes"}
              >
                {cardNotes.length}
              </span>
            )}
          </h3>
          {isLastPart && task.objective && task.objective.trim() !== task.title.trim() && (
            <p className="map-card-objective" title={task.objective}>{task.objective}</p>
          )}
          <div className="map-card-copy"><MarkdownExcerpt>
            {data.completionScope || (isLastPart ? task.pending_question : "") || partSummary ||
              copy[task.id]?.summary ||
              task.pending_question ||
              humanizeHarnessNote(task.summary || "", zh).summary ||
              task.summary ||
              task.objective ||
              (zh ? "放大查看任务内部" : "Zoom to explore")}
          </MarkdownExcerpt></div>
          <div className="map-card-stages">
            {isLastPart && data.live && !data.paused && ACTIVE.has(task.status)
              ? <LiveLine role={data.phase ?? task.role} since={task.started_ts} zh={zh} />
              : <span className="map-card-recorded">{isLastPart ? taskStateLabel : (zh ? "历史记录 · 非当前执行" : "History · not current execution")}</span>}
            {!!data.historyCount && teamSteps.length > 0 && <span className="map-card-team-summary">{teamSummary}</span>}
          </div>
          {isLastPart && teamSteps.length > 0 && (
            <span className="map-card-teambar" aria-hidden>
              <i
                style={{
                  width: `${Math.round((teamComplete / teamSteps.length) * 100)}%`,
                }}
              />
            </span>
          )}
          <div className="map-card-bottom">
            <span className={teamSteps.length ? 'map-card-team-summary' : undefined} title={range}>
              {data.historyCount ? "" : teamSteps.length
                ? teamSummary
                : data.plannedWidth && ACTIVE.has(task.status)
                ? zh ? `并行编队 ×${data.plannedWidth} 展开中` : `Fanning out ×${data.plannedWidth}`
                : data.partCount > 1
                ? range
                : `${layout.steps.length} ${zh ? "个环节" : "steps"}`}
            </span>
            <span className="map-card-submap-hint">
              {staleSummary
                ? zh ? "描述更新中" : "Summary updating"
                : zh ? "查看进展" : "View progress"}
              <ChevronRight size={12} />
            </span>
          </div>
        </button>
        {!!data.historyCount && data.toggleHistory && (
          <button type="button" className="map-history-toggle nodrag nopan"
            aria-expanded={data.historyExpanded}
            aria-label={`${zh ? "任务历史" : "Mission history"}: ${title}`}
            title={zh ? `保留全部 ${data.totalSteps} 个环节` : `All ${data.totalSteps} steps retained`}
            onClick={() => data.toggleHistory?.(task.id)}>
            {data.historyExpanded ? (zh ? "收起历史" : "Hide history") : `${zh ? "历史" : "History"} ${data.historyCount}`}
            <ChevronRight size={12} />
          </button>
        )}
      </div>
      {focused && <div
        className={`macro-detail ${detail ? "is-reading" : ""}`}
        aria-hidden={!detailed}
        style={{
          width: layout.width,
          height: layout.height,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
        <header className="macro-heading">
          <span className="macro-index">
            {String(ordinal).padStart(2, "0")}
          </span>
          <div>
            <small>
              {data.partCount > 1
                ? zh
                  ? `第 ${data.part} / ${data.partCount} 部分`
                  : `Part ${data.part} / ${data.partCount}`
                : zh
                  ? "任务内部"
                  : "INSIDE THIS TASK"}
            </small>
            <h2><MarkdownExcerpt>{title}</MarkdownExcerpt></h2>
          </div>
          {(state !== "recorded" || data.completionScope) && (
            <span className="macro-state" title={data.completionScope}>{taskStateLabel}</span>
          )}
          {detailed && isLastPart ? <Button data-testid="map-task-read" className="nodrag nopan text-xs"
            onClick={() => {
              setDetailId(null);
              setReadingLayout(null);
              data.readCopy?.(id, task.id);
            }}>{zh ? "阅读任务说明" : "Read task explanation"}</Button> : null}
        </header>
        <div className="macro-stage-key">
          {STEP_KINDS
            .filter((kind) => task.kind !== 'turn' || layout.steps.some((s) => s.kind === kind))
            .map((kind) => (
            <span
              key={kind}
              className={`submap-kind-${kind} ${layout.steps.some((s) => s.kind === kind) ? "" : "is-unrecorded"}`}
              title={task.kind === 'turn' ? TURN_KIND_NOTES[kind][zh ? 0 : 1] : KIND_NOTES[kind][zh ? 0 : 1]}
            >
              {task.kind === 'turn' ? TURN_KINDS[kind][zh ? 0 : 1] : KINDS[kind][zh ? 0 : 1]}
            </span>
          ))}
        </div>
        {cardNotes.length > 0 && (
          <aside className="macro-notes" aria-label={zh ? "操作员批注" : "Operator notes"}>
            <small>{zh ? "批注" : "Notes"}</small>
            {cardNotes.map((note) => (
              <p key={note.id}>{note.text}</p>
            ))}
          </aside>
        )}
        <SubmapEdges layout={layout} growing={data.growingLinks} activeStep={activeStep} activeTeamSteps={activeTeamSteps} />
        {layout.columns.map((col) => (
          <div
            className="macro-column-label"
            key={col.id}
            style={{ left: col.x, top: col.y }}
          >
            {col.title}
          </div>
        ))}
        {layout.steps.map((recorded, stepIndex) => {
          const step = currentStep(recorded);
          const Icon = ICONS[step.kind];
          return (
            <button
              key={step.id}
              className={`submap-step submap-kind-${step.kind} nodrag nopan ${detailId === step.id ? "is-selected" : ""}`}
              data-testid="submap-step"
              data-step-id={step.id}
              data-source={step.source}
              data-team-id={step.teamId}
              data-team-task-id={step.teamTaskId}
              data-status={stepStatus(step)}
              data-active={isStepActive(step)}
              data-growing={data.growingSteps?.[step.id] != null}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                data.menu(reference(step), { x: e.clientX, y: e.clientY });
              }}
              style={{
                animationDelay: `${data.growingSteps?.[step.id] ?? 0}ms`,
                "--step-index": stepIndex,
                left: layout.positions[step.id].x,
                top: layout.positions[step.id].y,
              } as CSSProperties}
              tabIndex={detailed ? 0 : -1}
              onClick={() => read(step)}
              aria-expanded={detailId === step.id}
            >
              <div className="submap-step-meta">
                <span>
                  <Icon size={16} />
                  {step.source === 'team' ? (zh ? '子任务 · ' : 'Subtask · ') : ''}{(task.kind === 'turn' ? TURN_KINDS : KINDS)[step.kind][zh ? 0 : 1]}
                  {step.round != null && (
                    <em className="submap-round">
                      {zh ? `第 ${step.round} 轮` : `Round ${step.round}`}
                    </em>
                  )}
                </span>
                <small>
                  {step.completionScope && step.status === 'recorded'
                    ? zh ? '执行结束' : 'Execution ended'
                    : step.source === 'team' && stepStatus(step) === 'failed' ? (zh ? '失败' : 'Failed') : stateLabel(stepStatus(step))}
                </small>
              </div>
              <h4><MarkdownExcerpt>{step.title}</MarkdownExcerpt></h4>
              <div className="submap-step-copy"><MarkdownExcerpt>
                {stepCopy(step)?.summary ||
                  currentStep(step).summary ||
                  step.detail ||
                  noDetails(zh)}
              </MarkdownExcerpt></div>
              <div className="submap-step-foot">
                <span title={sourceLabel(step, zh)}>
                  {zh ? "查看详情" : "Read more"}
                </span>
                <ChevronRight size={14} />
              </div>
            </button>
          );
        })}
        {data.partCount > 1 && !detail && (
          <nav
            className="macro-part-nav nodrag nopan"
            aria-label={zh ? "任务各部分" : "Task parts"}
          >
            <span>{range}</span>
            <div>
              <button
                disabled={!data.previousId}
                onClick={() => data.previousId && data.open(data.previousId, true)}
              >
                <ChevronLeft size={14} />
                {zh ? "上一部分" : "Previous part"}
              </button>
              <button
                disabled={!data.nextId}
                onClick={() => data.nextId && data.open(data.nextId, true)}
              >
                {zh ? "下一部分" : "Next part"}
                <ChevronRight size={14} />
              </button>
            </div>
          </nav>
        )}
        {detail && reader && (
          <section
            className="macro-reader nodrag nopan nowheel"
            data-testid="map-reader"
            data-kind={detail.kind}
            role="region"
            aria-label={zh ? "卡片详情" : "Card details"}
            style={{
              left: reader.x,
              top: reader.y,
              width: reader.width,
              height: reader.height,
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              data.menu(reference(detail), { x: e.clientX, y: e.clientY });
            }}
          >
            <header>
              <span>
                {(task.kind === 'turn' ? TURN_KINDS : KINDS)[detail.kind][zh ? 0 : 1]}
                <small className="macro-reader-note">{KIND_NOTES[detail.kind][zh ? 0 : 1]}</small>
              </span>
              <button
                aria-label="Close step details"
                onClick={() => {
                  setDetailId(null);
                  setReadingLayout(null);
                  data.readCopy?.(id, null);
                  data.open(id);
                }}
              >
                <X size={20} />
              </button>
            </header>
            <h3><MarkdownExcerpt>{stepCopy(detail)?.title || detail.title}</MarkdownExcerpt></h3>
            <div className="macro-reader-body">
              <MapReaderContent cardKey={detail.id} taskId={task.id} card={stepCopy(detail)} task={task}
                originalDetail={detail.detail || noDetails(zh)} selection={data.readerCopy}
                artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
            </div>
            <footer>
              <span title={sourceLabel(detail, zh)}>
                {detailTs
                  ? new Date(detailTs * 1000).toLocaleString(
                      zh ? "zh-CN" : "en-US",
                    )
                  : ""}
              </span>
              {!data.readOnly && (
                <button onClick={() => data.quote(reference(detail))}>
                  {zh ? "引用此项" : "Reference"}
                </button>
              )}
            </footer>
          </section>
        )}
      </div>}
    </article>
  );
});
