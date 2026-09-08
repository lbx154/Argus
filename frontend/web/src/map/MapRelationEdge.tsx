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
import { pointAlong } from "./relationGeometry";
import { relationLabelText, relationLayout } from "./relationLabels";
import "./edges.css";

type RelationEdge = Edge<{ lane: number; growthDelay?: number; active?: boolean }, "relation">;

/** MapPanel paints a cyclic reference directly on the stroke; that warning
 * outranks the kind hues resolved from edges.css. */
const CYCLE_STROKE = "#dc6648";

/** Curves follow their ports; label type stays readable at overview scale. */
export function MapRelationEdge({ id, label, style, data }: EdgeProps<RelationEdge>) {
  // Zoom only drives screen-constant sizing here. Bucketing it means a zoom
  // gesture re-renders every edge at step boundaries (≤4% size drift between
  // steps, absorbed by the canvas transform) instead of on every frame.
  const zoom = useStore((s) => Math.round(s.transform[2] * 24) / 24 || s.transform[2]);
  const arrow = `relation-arrow-${useId().replace(/:/g, "")}`;
  const store = useStoreApi();
  const layout = relationLayout(store, zoom);
  const route = layout.routes.get(id);
  const point = layout.labels.get(id);
  if (!route) return null;
  const kind = layout.kinds.get(id);
  const fan = kind === "fanout" || kind === "fanin";
  // Kind hues come from edges.css variables so both themes tune in one place;
  // the per-kind dash patterns from MapPanel stay the primary encoding.
  const stroke =
    !kind || style?.stroke === CYCLE_STROKE
      ? style?.stroke || "#7594ad"
      : `var(--map-edge-${kind}, ${style?.stroke || "#7594ad"})`;
  // Team fans read slightly bolder than the quiet width MapPanel assigns them.
  const strokeWidth = (Number(style?.strokeWidth || 2) + (fan ? 0.4 : 0)) / zoom;
  // A small join dot marks where the fan leaves its card (fanout) or gathers
  // again just short of the returning arrowhead (fanin).
  const joint = fan
    ? pointAlong(route.points, (kind === "fanout" ? 2 : 15) / zoom, kind === "fanin")
    : null;
  const text = typeof label === "string" ? relationLabelText(label) : undefined;
  return (
    <>
      <defs>
        <marker
          id={arrow}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={10.5 / zoom}
          markerHeight={10.5 / zoom}
          markerUnits="userSpaceOnUse"
          orient="auto"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" style={{ fill: stroke }} />
        </marker>
      </defs>
      <GrowthReveal path={route.path} delay={data?.growthDelay} padding={24 / zoom}>
      <BaseEdge
        id={id}
        path={route.path}
        markerEnd={`url(#${arrow})`}
        style={{
          ...style,
          stroke,
          vectorEffect: "none",
          strokeWidth,
          // Keep the dash pattern chosen per edge kind, at screen scale.
          strokeDasharray: style?.strokeDasharray
            ? String(style.strokeDasharray)
                .split(/[\s,]+/)
                .map((value) => Number(value) / zoom)
                .join(" ")
            : undefined,
        }}
      />
      {joint && (
        <circle
          className="map-edge-joint"
          cx={joint.x}
          cy={joint.y}
          r={3.2 / zoom}
          style={{ fill: stroke }}
          aria-hidden="true"
        />
      )}
      </GrowthReveal>
      {data?.active && (
        <EdgeLabelRenderer>
          {/* A spark drifting along the route replaces the old dash-offset
           * stroke animation: offset-distance runs on the compositor, while
           * dash offsets recalculated style and repainted a filtered path on
           * the main thread every frame of every active edge, forever. */}
          <div
            className="map-edge-spark"
            aria-hidden="true"
            style={{
              offsetPath: `path("${route.path}")`,
              width: 8 / zoom,
              height: 8 / zoom,
              margin: `${-4 / zoom}px 0 0 ${-4 / zoom}px`,
              animationDelay: `${-((id.charCodeAt(0) * 131 + id.length * 47) % 2800)}ms`,
            }}
          />
        </EdgeLabelRenderer>
      )}
      {label && text !== "" && point && (
        <EdgeLabelRenderer>
          <div
            className="map-relation-label nodrag nopan"
            data-kind={kind}
            title={text}
            style={{
              transform: `translate(-50%, -50%) translate(${point.x}px, ${point.y}px) scale(${1 / zoom})`,
            }}
          >
            {text ?? label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
