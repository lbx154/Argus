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
import { MarkdownContent } from "../components/MarkdownContent";
import { MarkdownExcerpt } from "../components/MarkdownExcerpt";
import { cleanDeliverySummary } from "../components/deliveryPresentation";
import type { ArtifactInfo } from "../api";
import type { MapCopy, CardReference } from "./presentation";
import { ACTIVE, statusKey } from "./model";
import {
  STEP_KINDS,
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
  open: (id: string) => void;
  focused: boolean;
  detailed: boolean;
  copy?: Pick<MapCopy, "cards">;
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
const STATES: Record<string, [string, string]> = {
  done: ["已完成", "Completed"],
  running: ["进行中", "In progress"],
  pending: ["待开始", "Planned"],
  failed: ["未通过", "Failed"],
  aborted: ["已取消", "Cancelled"],
  skipped: ["已跳过", "Skipped"],
  superseded: ["已替代", "Superseded"],
  question: ["待答复", "Needs input"],
  paused: ["已暂停", "Paused"],
  paused_external_work: ["等待后台任务", "Waiting on background work"],
  missing: ["引用缺失", "Missing"],
  unknown: ["状态未知", "Unknown"],
  continue: ["需修订", "Revise"],
  blocked: ["受阻", "Blocked"],
  started: ["开始记录", "Started"],
  recorded: ["已记录", "Recorded"],
  requested: ["修订建议", "Suggested"],
  replan: ["调整计划", "Revise plan"],
  replan_requested: ["调整计划", "Revise plan"],
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
    }
  }, [detailed]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const previousStatus = useRef(task.status);
  const [completedNow, setCompletedNow] = useState(false);
  useEffect(() => {
    const changed = previousStatus.current !== task.status;
    previousStatus.current = task.status;
    if (!changed || task.status !== 'done') return;
    setCompletedNow(true);
    const timer = setTimeout(() => setCompletedNow(false), 1500);
    return () => clearTimeout(timer);
  }, [task.status]);
  const selectedDetail = layout.steps.find((s) => s.id === detailId);
  const detail = selectedDetail ? currentStep(selectedDetail) : undefined;
  const detailTs = detail?.updatedAt ?? detail?.ts;
  const isLastPart = data.part === data.partCount;
  const state = !isLastPart
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
    const saved = copy[step.id];
    if (step.source !== 'team') return saved;
    const index = saved?.event_ids?.indexOf(step.id) ?? -1;
    return currentStep(step).revision && index >= 0 && saved?.event_revisions?.[index] === currentStep(step).revision ? saved : undefined;
  };
  const title =
    (copy[task.id]?.title || task.title) +
    (data.part > 1
      ? zh
        ? ` · 续篇 ${data.part - 1}`
        : ` · Continued ${data.part - 1}`
      : "");
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
  return (
    <article
      className={`map-macro map-state-${state}`}
      data-testid="map-macro"
      data-task-id={task.id}
      data-card-id={id}
      data-part={data.part}
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
              {String(ordinal).padStart(2, "0")}
              {data.partCount > 1 && ` · ${data.part}/${data.partCount}`}
            </span>
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
              {stateLabel(displayedState)}
            </span>
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
          <div className="map-card-copy"><MarkdownExcerpt>
            {partSummary ||
              copy[task.id]?.summary ||
              task.pending_question ||
              task.summary ||
              task.objective ||
              (zh ? "放大查看任务内部" : "Zoom to explore")}
          </MarkdownExcerpt></div>
          <div className="map-card-stages" aria-label={zh ? '任务阶段' : 'Task stages'}>
            {(['plan', 'execution', 'review', 'result'] as const).map((kind) => {
              const StageIcon = ICONS[kind];
              const present = layout.steps.some((step) => step.kind === kind);
              const active = layout.steps.some((step) => step.kind === kind && isStepActive(step));
              return <span key={kind} className={`submap-kind-${kind}`} data-present={present} data-active={active} title={KINDS[kind][zh ? 0 : 1]}><StageIcon size={12} /><span>{zh ? ({ plan: '规划', execution: '执行', review: '审查', result: '交付' })[kind] : KINDS[kind][1]}</span></span>;
            })}
          </div>
          {teamSteps.length > 0 && (
            <span className="map-card-teambar" aria-hidden>
              <i
                style={{
                  width: `${Math.round((teamComplete / teamSteps.length) * 100)}%`,
                }}
              />
            </span>
          )}
          {isLastPart && data.live && !data.paused && ACTIVE.has(task.status) && (
            <LiveLine role={data.phase ?? task.role} since={task.started_ts} zh={zh} />
          )}
          <div className="map-card-bottom">
            <span className={teamSteps.length ? 'map-card-team-summary' : undefined} title={range}>
              {teamSteps.length
                ? (zh ? `子任务 ${teamComplete}/${teamSteps.length} 完成 · ${teamRunning} 进行中` : `Subtasks ${teamComplete}/${teamSteps.length} done · ${teamRunning} running`)
                  + ((data.plannedWidth ?? 0) > teamSteps.length
                    ? zh ? ` · 计划并行 ×${data.plannedWidth}` : ` · planned ×${data.plannedWidth}`
                    : "")
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
      </div>
      <div
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
          <span className="macro-state">{stateLabel(displayedState)}</span>
        </header>
        <div className="macro-stage-key">
          {STEP_KINDS.map((kind) => (
            <span
              key={kind}
              className={`submap-kind-${kind} ${layout.steps.some((s) => s.kind === kind) ? "" : "is-unrecorded"}`}
            >
              {KINDS[kind][zh ? 0 : 1]}
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
                  {step.source === 'team' ? (zh ? '子任务 · ' : 'Subtask · ') : ''}{KINDS[step.kind][zh ? 0 : 1]}
                  {step.round != null && (
                    <em className="submap-round">
                      {zh ? `第 ${step.round} 轮` : `R${step.round}`}
                    </em>
                  )}
                </span>
                <small>
                  {step.source === 'team' && stepStatus(step) === 'failed' ? (zh ? '失败' : 'Failed') : stateLabel(stepStatus(step))}
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
                onClick={() => data.previousId && data.open(data.previousId)}
              >
                <ChevronLeft size={14} />
                {zh ? "上一部分" : "Previous part"}
              </button>
              <button
                disabled={!data.nextId}
                onClick={() => data.nextId && data.open(data.nextId)}
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
              <span>{KINDS[detail.kind][zh ? 0 : 1]}</span>
              <button
                aria-label="Close step details"
                onClick={() => {
                  setDetailId(null);
                  setReadingLayout(null);
                  data.open(id);
                }}
              >
                <X size={20} />
              </button>
            </header>
            <h3><MarkdownExcerpt>{detail.title}</MarkdownExcerpt></h3>
            <div className="macro-reader-body">
              <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>
                {cleanDeliverySummary(stepCopy(detail)?.detail || detail.detail || noDetails(zh))}
              </MarkdownContent>
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
      </div>
    </article>
  );
});
