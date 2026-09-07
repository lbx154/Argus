import { memo, useEffect, useState } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  GitBranch,
  Play,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { MapCopy, CardReference } from "./presentation";
import { ACTIVE, statusKey } from "./model";
import {
  STEP_KINDS,
  type StepKind,
  type SubmapLayout,
  type SubmapStep,
  type MapCard,
} from "./submap";
import { SubmapEdges } from "./SubmapEdges";

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
  seenCards?: Set<string>;
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
const KINDS: Record<StepKind, [string, string]> = {
  plan: ["规划", "Plan"],
  execution: ["执行尝试", "Execute"],
  review: ["审查", "Review"],
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
  step.source === "task"
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
  const [arrive] = useState(() => !data.seenCards?.has(id));
  useEffect(() => { data.seenCards?.add(id); }, [data.seenCards, id]);
  const [readingLayout, setReadingLayout] = useState<SubmapLayout | null>(null);
  const layout = readingLayout || currentLayout;
  const screenWidth = data.canvasSize?.width || window.innerWidth;
  const screenHeight = data.canvasSize?.height || window.innerHeight;
  useEffect(() => {
    if (!detailed) {
      setDetailId(null);
      setReadingLayout(null);
    }
  }, [detailed]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = layout.steps.find((s) => s.id === detailId);
  const isLastPart = data.part === data.partCount;
  const state = !isLastPart
    ? "recorded"
    : task.status === "missing"
      ? "missing"
      : data.paused && ACTIVE.has(task.status) ? "paused" : statusKey(task);
  const summaryScale = Math.min(
    data.frame.width / 288,
    data.frame.height / 218,
  );
  const copy = data.copy?.cards || {};
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
          .map((step) => copy[step.id]?.summary || step.detail)
          .filter(
            (value) =>
              value &&
              !["暂无详细记录", "Details are not available yet."].includes(
                value,
              ),
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
          .find((s) => !["plan", "result"].includes(s.kind))?.id
      : null;
  const activeStep = data.paused ? null : activityStep;
  const reference = (step?: SubmapStep): CardReference => ({
    source: data.source,
    task_id: task.id,
    task_title: title,
    part: data.partCount > 1 ? data.part : undefined,
    step_id: step?.id,
    step_title: step?.title,
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
    420,
    layout.height - 48,
    Math.max(230, (screenHeight - (screenWidth < 640 ? 330 : 210)) / 1.05),
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
      s.startsWith("paused_") ? "paused" : ACTIVE.has(s) ? "running" : s
    ] ?? STATES.unknown)[zh ? 0 : 1];
  return (
    <article
      className={`map-macro map-state-${state}`}
      data-testid="map-macro"
      data-task-id={task.id}
      data-card-id={id}
      data-part={data.part}
      data-arrive={arrive}
      data-focused={focused}
      data-detailed={detailed}
      aria-label={title}
      data-active={isLastPart && data.live && !data.paused && ACTIVE.has(task.status)}
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
          width: data.frame.width / summaryScale - 20,
          transform: `translate(-50%, -50%) scale(${summaryScale})`,
        }}
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
              ) : (
                <span className="map-state-dot" />
              )}
              {stateLabel(state)}
            </span>
          </div>
          <h3>{title}</h3>
          <p>
            {partSummary ||
              (copy[task.id]?.task_status === task.status
                ? copy[task.id]?.summary
                : "") ||
              task.pending_question ||
              task.summary ||
              task.objective ||
              (zh ? "放大查看任务内部" : "Zoom to explore")}
          </p>
          <div className="map-card-bottom">
            <span>
              {data.partCount > 1
                ? range
                : `${layout.steps.length} ${zh ? "个环节" : "steps"}`}
            </span>
            <span className="map-card-submap-hint">
              {zh ? "查看进展" : "View progress"}
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
            <h2>{title}</h2>
          </div>
          <span className="macro-state">{stateLabel(state)}</span>
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
        <SubmapEdges layout={layout} />
        {layout.columns.map((col) => (
          <div
            className="macro-column-label"
            key={col.id}
            style={{ left: col.x, top: col.y }}
          >
            {col.title}
          </div>
        ))}
        {layout.steps.map((step) => {
          const Icon = ICONS[step.kind];
          return (
            <button
              key={step.id}
              className={`submap-step submap-kind-${step.kind} nodrag nopan ${detailId === step.id ? "is-selected" : ""}`}
              data-testid="submap-step"
              data-step-id={step.id}
              data-active={activeStep === step.id}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                data.menu(reference(step), { x: e.clientX, y: e.clientY });
              }}
              style={{
                left: layout.positions[step.id].x,
                top: layout.positions[step.id].y,
              }}
              tabIndex={detailed ? 0 : -1}
              onClick={() => read(step)}
              aria-expanded={detailId === step.id}
            >
              <div className="submap-step-meta">
                <span>
                  <Icon size={16} />
                  {KINDS[step.kind][zh ? 0 : 1]}
                  {step.round != null && (
                    <em className="submap-round">
                      {zh ? `第 ${step.round} 轮` : `R${step.round}`}
                    </em>
                  )}
                </span>
                <small>
                  {stateLabel(activityStep === step.id ? data.paused ? "paused" : "running" : step.status)}
                </small>
              </div>
              <h4>{step.title}</h4>
              <p>
                {copy[step.id]?.summary ||
                  step.detail ||
                  (zh ? "暂无详细记录" : "Details are not available yet")}
              </p>
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
            <h3>{detail.title}</h3>
            <div className="macro-reader-body">
              <ReactMarkdown>
                {copy[detail.id]?.detail ||
                  detail.detail ||
                  (zh ? "暂无详细记录。" : "No details available yet.")}
              </ReactMarkdown>
            </div>
            <footer>
              <span title={sourceLabel(detail, zh)}>
                {detail.ts
                  ? new Date(detail.ts * 1000).toLocaleString(
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
