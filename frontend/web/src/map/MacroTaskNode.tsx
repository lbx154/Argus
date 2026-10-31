import { createContext, memo, useContext, useEffect, useRef, useState, type CSSProperties } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  GitBranch,
  Pause,
  Play,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import { isPauseStatus } from "../../../core/src/missionOutcome";
import { MarkdownExcerpt } from "../components/MarkdownExcerpt";
import { Button } from "../components/primitives";
import { MapReaderContent } from "./MapReaderContent";
import type { ArtifactInfo, Snapshot } from "../api";
import { ACTIVE, plainTitle, statusKey } from "./model";
import {
  STEP_KINDS,
  noDetails,
  type StepKind,
  type SubmapLayout,
  type SubmapStep,
  type MapCard,
} from "./submap";
import { SubmapEdges } from "./SubmapEdges";
import { CardNow, LiveLine } from "./LiveLine";
import { MapCardFigureContext } from "./cardContext";
import { WorkspaceImage } from "../components/MarkdownContent";
import { arrivalDelay } from "./alive";

export type MacroData = MapCard & {
  zh: boolean;
  layout: SubmapLayout;
  frame: { width: number; height: number; scale: number };
  canvasSize?: { width: number; height: number };
  open: (id: string, keyboard?: boolean) => void;
  focused: boolean;
  detailed: boolean;
  readCopy?: (nodeId: string, key: string | null) => void;
  liveCall?: NonNullable<Snapshot['daemon']['calls']>[number];
  workStatus?: Pick<NonNullable<Snapshot['work_status']>, 'state' | 'reason' | 'label'>;
  growthDelay?: number;
  growingSteps?: Record<string, number>;
  growingLinks?: Record<string, number>;
  seenCards?: Set<string>;
  restoring?: boolean;
  /** The pointer is on this card, or on one it is related to. */
  lit?: boolean;
  hovered?: boolean;
  /** The map is composing itself after a first opening. */
  arriving?: boolean;
  /** A replay is revealing cards in order. */
  revealing?: boolean;
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
export const MapArtifactContext = createContext<{
  sessionId?: string;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  workspace?: string;
}>({});
const KINDS: Record<StepKind, [string, string]> = {
  plan: ["计划", "Plan"],
  execution: ["动手处理", "Work"],
  review: ["检查结果", "Check"],
  result: ["结果", "Result"],
};
const ICONS = {
  plan: GitBranch,
  execution: Play,
  review: ShieldCheck,
  result: FileText,
};
/* Status words a reader outside the team understands at a glance. */
const STATES: Record<string, [string, string]> = {
  done: ["已结束", "Ended"],
  running: ["进行中", "In progress"],
  pending: ["待开始", "Planned"],
  failed: ["执行失败", "Execution failed"],
  review_unavailable: ["这次结果检查没有完成", "Result check did not finish"],
  held: ["工作暂停，后续尚未安排", "Work paused; no next step scheduled"],
  aborted: ["已结束", "Ended"],
  cancelled: ["已结束", "Ended"],
  skipped: ["已跳过", "Skipped"],
  question: ["等你回答", "Waiting for your answer"],
  paused: ["已停止", "Stopped"],
  missing: ["记录中缺失", "Not in this record"],
  unknown: ["状态未知", "Unknown"],
  engineer: ["需再改一轮", "Another pass needed"],
  operator: ["等你回答", "Waiting for your answer"],
  manager: ["等经理定夺", "With the Manager to decide"],
  blocked: ["受阻", "Held up"],
  started: ["进行中", "Under way"],
  recorded: ["已记录", "On record"],
  requested: ["修改建议", "Requested"],
  planner: ["需要调整计划", "Plan needs adjusting"],
};
/* One sentence per stage, so a newcomer learns who does what while reading. */
const KIND_NOTES: Record<StepKind, [string, string]> = {
  plan: ["接下来要做什么，为什么这样做", "What to do next and why"],
  execution: ["实际做了什么，遇到了什么问题", "What was done and what happened"],
  review: ["结果是否正确，有什么依据", "Whether the result is correct and what supports it"],
  result: ["这项任务最后得到了什么", "What the task produced in the end"],
};
/* A single-agent turn has a different cast: you asked, Argus worked, Argus answered. */
const TURN_KINDS: Record<StepKind, [string, string]> = {
  plan: ["提问", "Asked"],
  execution: ["Argus 查证", "Argus"],
  review: ["核对", "Check"],
  result: ["回答", "Answer"],
};
const TURN_KIND_NOTES: Record<StepKind, [string, string]> = {
  plan: ["你提出的要求", "What you asked for"],
  execution: ["Argus 自己动手查证、运行命令", "Argus did the work itself: read, searched, ran commands"],
  review: ["对结果的核对", "A check of the result"],
  result: ["Argus 给出的回答", "What Argus answered"],
};
const sourceLabel = (step: SubmapStep, zh: boolean) =>
  step.source === "task"
    ? zh
      ? "任务记录"
      : "Task record"
    : zh
        ? "来自任务记录"
        : "Linked event";

/** The task's figure on its card, when it has one: the result shows itself.
 * A component of its own, so a poll that moves nothing else leaves the image
 * (and its loaded preview) where it is. */
export function CardFigure({ taskId, title }: { taskId: string; title: string }) {
  const { sid, figures } = useContext(MapCardFigureContext);
  const path = figures.get(taskId);
  if (!sid || !path) return null;
  return (
    <div className="map-card-figure" data-testid="map-card-figure" data-path={path}>
      <WorkspaceImage sid={sid} path={path} src={path} alt={`${title} · ${path.split("/").pop()}`} className="map-card-figure-image" />
    </div>
  );
}

/** Both levels occupy the same fixed node; only the camera changes their apparent size. */
export const MacroTaskNode = memo(function MacroTaskNode({
  id,
  data,
}: NodeProps<MacroNode>) {
  const { task, ordinal, zh, layout: currentLayout, focused, detailed } = data;
  const paused = data.workStatus?.state === 'paused';
  const { artifacts, onOpenArtifact, workspace, sessionId } = useContext(MapArtifactContext);
  const [arrive] = useState(() => !!data.revealing || (!data.restoring && !data.seenCards?.has(id)));
  useEffect(() => { data.seenCards?.add(id); }, [data.seenCards, id]);
  const [readingLayout, setReadingLayout] = useState<SubmapLayout | null>(null);
  const layout = readingLayout || currentLayout;
  const screenWidth = data.canvasSize?.width || window.innerWidth;
  const screenHeight = data.canvasSize?.height || window.innerHeight;
  useEffect(() => {
    setDetailId(null);
    setReadingLayout(null);
    data.readCopy?.(id, null);
  }, [detailed, data.part]);
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
  const detail = layout.steps.find((s) => s.id === detailId);
  const detailTs = detail?.ts;
  const isLastPart = data.part === data.partCount;
  const state = (detailed && !isLastPart)
    ? "recorded"
    : task.status === "missing"
      ? "missing"
      : (task.status === 'blocked') ? 'question'
      : paused && ACTIVE.has(task.status) ? data.workStatus?.reason === 'operator_input' ? 'question' : 'paused' : statusKey(task);
  const summaryScale = Math.min(
    data.frame.width / 288,
    data.frame.height / 280,
  );
  const title = plainTitle(task.title);
  const range = zh
    ? `环节 ${data.start}–${data.end} / ${data.totalSteps}`
    : `Steps ${data.start}–${data.end} / ${data.totalSteps}`;
  const scale = Math.min(
    data.frame.width / layout.width,
    data.frame.height / layout.height,
  );
  const activityStep =
    isLastPart && ACTIVE.has(task.status)
      ? [...layout.steps]
          .reverse()
          .find((s) => !["plan", "result"].includes(s.kind))?.id
      : null;
  const activeStep = paused || (task.status === 'blocked') ? null : activityStep;
  const stepStatus = (step: SubmapStep) =>
    activityStep === step.id ? paused || (task.status === 'blocked') ? state : 'running' : step.status;
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
    if (step.source === 'task' && data.readCopy) {
      data.readCopy(id, task.id);
      return;
    }
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
      isPauseStatus(s) ? "paused" : ACTIVE.has(s) ? "running" : s
    ] ?? STATES.unknown)[zh ? 0 : 1];
  const answered = task.turn_kind === 'qa' && layout.steps.some(step => step.kind === 'result' && step.source === 'event');
  const requestAction = answered ? (zh ? '查看回答' : 'Read answer') : (zh ? '阅读请求' : 'Read request');
  const taskStateLabel = task.turn_kind === 'qa' && state === 'paused' && data.workStatus ? data.workStatus.label[zh ? 'zh-CN' : 'en']
    : task.turn_kind === 'qa' && state === 'running' && !data.liveCall ? stateLabel(state)
    : task.turn_kind === 'qa' && (state !== 'done' || answered) && ['done', 'running', 'cancelled', 'failed'].includes(state)
    ? ({ done: ['已回答', 'Answered'], running: ['正在回答', 'Answering'], cancelled: ['已中断', 'Interrupted'], failed: ['回答失败', 'Answer failed'] } as Record<string, string[]>)[state][zh ? 0 : 1]
    : stateLabel(state);
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
      style={{
        animationDelay: `${data.growthDelay ?? (data.arriving ? arrivalDelay(ordinal) : 0)}ms`,
      } as CSSProperties}
      data-focused={focused}
      data-detailed={detailed}
      aria-label={title}
      data-completed-now={completedNow}
      data-active={!paused && task.status !== 'blocked' && ACTIVE.has(task.status)}
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
        {...{ inert: detailed ? '' : undefined }}
        style={{
          "--summary-height": `${data.frame.height / summaryScale - 20}px`,
          // design.css sizes the far-view title from this and the zoom step.
          "--summary-scale": summaryScale,
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
          onClick={(event) => task.turn_kind === 'qa' && data.readCopy ? data.readCopy(id, task.id) : data.open(id, event.detail === 0)}
        >
          <div className="map-card-top">
            <span className="map-card-kind">
              {task.turn_kind === 'qa' ? (zh ? "请求" : "Request") : (zh ? "任务" : "MISSION")}
            </span>
            {state !== "recorded" && (
              <span className="map-status">
                {state === "done" ? (
                  <Check size={11} />
                ) : state === "failed" ? (
                  <X size={11} />
                ) : state === "review_unavailable" ? (
                  <TriangleAlert size={11} />
                ) : state === "question" || state === "paused" || state === "held" ? (
                  <Pause size={11} />
                ) : (
                  <span className="map-state-dot" />
                )}
                {taskStateLabel}
              </span>
            )}
          </div>
          <h2 title={title}>
            <MarkdownExcerpt>{title}</MarkdownExcerpt>
          </h2>
          {/* The overview shows the current holder and latest word; past steps
              stay in the detail pages. */}
          <div className="map-card-state" data-question={task.status === 'blocked'}>
            <CardNow task={task} zh={zh} />
            <CardFigure taskId={task.id} title={title} />
          </div>
          {/* The course a task ran: planned, carried out, reviewed, delivered.
              Four marks, inked where the record holds a step of that kind, so a
              whole map shows at a glance which tasks were reviewed and which
              stopped short. A chat turn has no such course. */}
          {task.turn_kind !== 'qa' && (
            <div className="map-card-course" aria-label={zh ? '任务经过' : 'Course of the task'}>
              {(['plan', 'execution', 'review', 'result'] as const).map((kind) => {
                const StageIcon = ICONS[kind];
                const present = layout.steps.some((step) => step.kind === kind);
                const active = layout.steps.some((step) => step.kind === kind && activeStep === step.id);
                const label = zh ? { plan: '规划', execution: '执行', review: '审查', result: '交付' }[kind] : KINDS[kind][1];
                return (
                  <span key={kind} className={`submap-kind-${kind}`} data-present={present} data-active={active} title={label}>
                    <StageIcon size={12} aria-hidden />
                    <b>{label}</b>
                  </span>
                );
              })}
            </div>
          )}
          {(() => {
            const live = !paused && task.status !== 'blocked' && ACTIVE.has(task.status) && !!data.liveCall;
            // The state is on the chip at the top of the card; it is not said
            // again down here unless the card has no chip.
            const chip = state !== "recorded";
            if (!live && chip) return null;
            return (
              <div className="map-card-stages">
                {live
                  ? <LiveLine replying={Boolean(data.liveCall!.answers)} role={data.liveCall!.role} since={data.liveCall!.started_ts} zh={zh} />
                  : chip ? null
                  : <span className="map-card-recorded">{taskStateLabel}</span>}
              </div>
            );
          })()}
          <div className="map-card-bottom">
            <span title={range}>
              {task.turn_kind === 'qa' ? (zh ? '请求记录' : 'Request record') : `${data.totalSteps} ${zh ? "个环节" : "steps"}`}
            </span>
            <span className="map-card-submap-hint">
              {task.turn_kind === 'qa' ? requestAction : zh ? "查看进展" : "View progress"}
              <ChevronRight size={12} />
            </span>
          </div>
        </button>
        {task.status === 'done' && !!task.result_paths?.length && onOpenArtifact && (
          <div className="map-card-results nodrag nopan">
            {task.result_paths.slice(0, 1).map((path) => (
              <button key={path} type="button" title={path} onClick={() => onOpenArtifact(path)}>
                {path.split('/').pop()}
              </button>
            ))}
          </div>
        )}
      </div>
      {focused && <div
        className={`macro-detail ${detail ? "is-reading" : ""}`}
        data-stacked={layout.stacked || undefined}
        aria-hidden={!detailed}
        style={{
          width: layout.width,
          height: layout.height,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
        <header className="macro-heading">
          <div>
            <small>
              {data.partCount > 1
                ? zh
                  ? `第 ${data.part} / ${data.partCount} 部分`
                  : `Part ${data.part} / ${data.partCount}`
                : task.turn_kind === 'qa' ? (zh ? '请求内容' : 'Request') : zh
                  ? "任务内部"
                  : "INSIDE THIS TASK"}
            </small>
            <h2><MarkdownExcerpt>{title}</MarkdownExcerpt></h2>

          </div>
          {state !== "recorded" && (
            <span className="macro-state">{taskStateLabel}</span>
          )}
          {detailed && isLastPart ? <Button data-testid="map-task-read" className="nodrag nopan text-xs"
            onClick={() => {
              if (task.turn_kind === 'qa') {
                data.readCopy?.(id, task.id);
                return;
              }
              setDetailId(null);
              setReadingLayout(null);
              data.readCopy?.(id, task.id);
            }}>{task.turn_kind === 'qa' ? requestAction : zh ? "阅读任务说明" : "Read task explanation"}</Button> : null}
        </header>
        <div className="macro-stage-key">
          {STEP_KINDS
            .filter((kind) => task.turn_kind !== 'qa' || layout.steps.some((s) => s.kind === kind))
            .map((kind) => (
            <span
              key={kind}
              className={`submap-kind-${kind} ${layout.steps.some((s) => s.kind === kind) ? "" : "is-unrecorded"}`}
              title={task.turn_kind === 'qa' ? TURN_KIND_NOTES[kind][zh ? 0 : 1] : KIND_NOTES[kind][zh ? 0 : 1]}
            >
              {task.turn_kind === 'qa' ? TURN_KINDS[kind][zh ? 0 : 1] : KINDS[kind][zh ? 0 : 1]}
            </span>
          ))}
        </div>
        <SubmapEdges layout={layout} growing={data.growingLinks} activeStep={activeStep} />
        {layout.columns.map((col) => (
          <div
            className="macro-column-label"
            key={col.id}
            style={{ left: col.x, top: col.y }}
          >
            {col.title}
          </div>
        ))}
        {layout.steps.map((step, stepIndex) => {
          const Icon = ICONS[step.kind];
          return (
            <button
              key={step.id}
              className={`submap-step submap-kind-${step.kind} nodrag nopan ${detailId === step.id ? "is-selected" : ""}`}
              data-testid="submap-step"
              data-step-id={step.id}
              data-source={step.source}
              data-status={stepStatus(step)}
              data-active={activeStep === step.id}
              data-growing={data.growingSteps?.[step.id] != null}
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
                  {(task.turn_kind === 'qa' ? TURN_KINDS : KINDS)[step.kind][zh ? 0 : 1]}

                </span>
                <small>
                  {stateLabel(stepStatus(step))}
                </small>
              </div>
              <h3><MarkdownExcerpt>{step.title}</MarkdownExcerpt></h3>
              <div className="submap-step-copy"><MarkdownExcerpt>
                {step.summary ||
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
          >
            <header>
              <span>
                {(task.turn_kind === 'qa' ? TURN_KINDS : KINDS)[detail.kind][zh ? 0 : 1]}
                {task.turn_kind !== 'qa' && <small className="macro-reader-note">{KIND_NOTES[detail.kind][zh ? 0 : 1]}</small>}
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
            <h3><MarkdownExcerpt>{detail.title}</MarkdownExcerpt></h3>
            <div className="macro-reader-body">
              <MapReaderContent sessionId={sessionId} eventId={detail.source === 'event' ? detail.id : undefined} cardKey={detail.id} taskId={task.id} task={task}
                workspace={workspace}
                originalDetail={detail.detail || noDetails(zh)} zh={zh}
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
            </footer>
          </section>
        )}
      </div>}
    </article>
  );
});
