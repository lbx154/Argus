import { GrowthReveal } from './GrowthReveal';
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

type RelationEdge = Edge<{ lane: number; growthDelay?: number; active?: boolean }, "relation">;

/** Curves follow their ports; label type stays readable at overview scale. */
export function MapRelationEdge({ id, label, style, data }: EdgeProps<RelationEdge>) {
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
      <GrowthReveal path={route.path} delay={data?.growthDelay} padding={24 / zoom}>
      <BaseEdge
        id={id}
        path={route.path}
        markerEnd={`url(#${arrow})`}
        style={{
          ...style,
          vectorEffect: "none",
          strokeWidth: Number(style?.strokeWidth || 2) / zoom,
          // Keep the dash pattern chosen per edge kind, at screen scale.
          strokeDasharray: style?.strokeDasharray
            ? String(style.strokeDasharray)
                .split(/[\s,]+/)
                .map((value) => Number(value) / zoom)
                .join(" ")
            : undefined,
        }}
      />
      </GrowthReveal>
      {data?.active && <path className="map-edge-flow" d={route.path} pathLength={1} fill="none" stroke="var(--atlas-flow, #4b9cae)" strokeWidth={3 / zoom} strokeDasharray=".065 .935" strokeLinecap="round" aria-hidden="true" />}
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
