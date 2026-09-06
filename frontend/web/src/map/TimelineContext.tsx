import { ViewportPortal, type Node } from "@xyflow/react";

/** A shared chronological context, explicitly separate from recorded dependency edges. */
export function TimelineContext({
  nodes,
  originalPositions,
  zh,
}: {
  nodes: Node[];
  originalPositions: Record<string, { x: number; y: number }>;
  zh: boolean;
}) {
  const visible = nodes.filter((n) => !n.hidden);
  if (!visible.length) return null;
  const top = Math.min(...visible.map((n) => n.position.y)) - 140;
  const left = Math.min(...visible.map((n) => n.position.x)) - 110;
  const right = Math.max(...visible.map((n) => n.position.x)) + 500;
  const groups = new Map<number, Node[]>();
  for (const node of visible) {
    const key = originalPositions[node.id]?.x ?? node.position.x;
    groups.set(key, [...(groups.get(key) ?? []), node]);
  }
  return (
    <ViewportPortal>
      <svg
        className="map-timeline-context"
        overflow="visible"
        width="1"
        height="1"
        aria-label={
          zh
            ? "同一会话的时间关系，不是任务依赖"
            : "Chronology within this session, not task dependencies"
        }
      >
        <path data-testid="timeline-path" d={`M ${left} ${top} H ${right}`} />
        <text x={left} y={top - 35}>
          {zh ? "同一会话 · 时间向右 →" : "Same session · time →"}
        </text>
        {[...groups.entries()].map(([key, group]) => {
          const spine = Math.min(...group.map((n) => n.position.x)) - 110;
          const bottom = Math.max(
            ...group.map(
              (n) => n.position.y + (n.measured?.height ?? n.height ?? 824) / 2,
            ),
          );
          return (
            <g key={key}>
              <circle cx={spine} cy={top} r="9" />
              <path
                data-testid="timeline-path"
                d={`M ${spine} ${top} V ${bottom}`}
              />
              {group.map((n) => (
                <path
                  data-testid="timeline-path"
                  key={n.id}
                  d={`M ${spine} ${n.position.y + (n.measured?.height ?? n.height ?? 824) / 2} H ${n.position.x}`}
                />
              ))}
            </g>
          );
        })}
      </svg>
    </ViewportPortal>
  );
}
