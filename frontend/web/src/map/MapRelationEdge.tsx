import {
  BaseEdge,
  EdgeLabelRenderer,
  useStore,
  useStoreApi,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { useId } from "react";
import { relationLayout } from "./relationLabels";

type RelationEdge = Edge<{ lane: number }, "relation">;

/** Curves follow their ports; label type stays readable at overview scale. */
export function MapRelationEdge({ id, label, style }: EdgeProps<RelationEdge>) {
  const zoom = useStore((s) => s.transform[2]);
  const arrow = `relation-arrow-${useId().replace(/:/g, "")}`;
  const store = useStoreApi();
  const layout = relationLayout(store, zoom);
  const route = layout.routes.get(id);
  const point = layout.labels.get(id);
  if (!route) return null;
  return (
    <>
      <defs>
        <marker
          id={arrow}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={8 / zoom}
          markerHeight={8 / zoom}
          markerUnits="userSpaceOnUse"
          orient="auto"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill={style?.stroke || "#7594ad"} />
        </marker>
      </defs>
      <BaseEdge
        id={id}
        path={route.path}
        markerEnd={`url(#${arrow})`}
        style={{
          ...style,
          vectorEffect: "none",
          strokeWidth: Number(style?.strokeWidth || 2) / zoom,
          strokeDasharray: style?.strokeDasharray
            ? `${4 / zoom} ${5 / zoom}`
            : undefined,
        }}
      />
      {label && point && (
        <EdgeLabelRenderer>
          <div
            className="map-relation-label nodrag nopan"
            style={{
              transform: `translate(-50%, -50%) translate(${point.x}px, ${point.y}px) scale(${1 / zoom})`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
