import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  GitBranch,
  ListChecks,
  MoreHorizontal,
  Route,
  ShieldCheck,
} from "lucide-react";
import { statusKey, type MapTask } from "./model";

/** World-unit frame of a branch pill; the atlas layout reads the same numbers. */
export const BRANCH_FRAME = { width: 640, height: 190 };

export type BranchData = {
  task: MapTask;
  zh: boolean;
  /** The card the fan belongs to; a branch pill only navigates there. */
  parentCardId: string;
  open: (id: string) => void;
} & Record<string, unknown>;
export type BranchFlowNode = Node<BranchData, "branch">;

const GLYPHS: Record<string, typeof GitBranch> = {
  "idea-route": Route,
  "idea-review": ShieldCheck,
  "idea-selector": ListChecks,
};

const STATES: Record<string, [string, string]> = {
  done: ["已完成", "Completed"],
  running: ["进行中", "In progress"],
  pending: ["待开始", "Planned"],
  failed: ["未通过", "Failed"],
  question: ["待答复", "Needs input"],
  paused: ["已暂停", "Paused"],
  aborted: ["已取消", "Cancelled"],
  skipped: ["已跳过", "Skipped"],
  superseded: ["已替代", "Superseded"],
  unknown: ["已记录", "Recorded"],
};

/** A promoted team branch: display and navigation only. Clicking always opens
 * the owning card, where the full subtask record lives.
 */
export const BranchNode = memo(function BranchNode({
  data,
}: NodeProps<BranchFlowNode>) {
  const { task, zh } = data;
  const state = statusKey(task);
  const Glyph = task.overflow_count
    ? MoreHorizontal
    : GLYPHS[task.team_role ?? ""] ?? GitBranch;
  const stateLabel = (STATES[state] ?? STATES.unknown)[zh ? 0 : 1];
  return (
    <button
      type="button"
      className={`map-branch map-state-${state} nodrag nopan`}
      data-testid="map-branch"
      data-branch-id={task.id}
      data-status={state}
      data-overflow={!!task.overflow_count}
      title={task.excerpt || task.objective || task.title}
      aria-label={`${task.title} · ${stateLabel} · ${zh ? "打开所属任务" : "Open the owning task"}`}
      onClick={() => data.open(data.parentCardId)}
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
      <span className="map-branch-glyph" aria-hidden="true">
        <Glyph />
      </span>
      <span className="map-branch-title">{task.title}</span>
      <span className="map-branch-dot" aria-hidden="true" />
    </button>
  );
});
