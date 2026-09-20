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

type RelationEdge = Edge<{ lane: number; growthDelay?: number; active?: boolean; muted?: boolean; lit?: boolean }, "relation">;

/** MapPanel paints a cyclic reference directly on the stroke; that warning
 * outranks the kind hues resolved from edges.css. */
const CYCLE_STROKE = "#dc6648";

/** Curves follow their ports; label type stays readable at overview scale. */
export function MapRelationEdge({ id, label, style, data }: EdgeProps<RelationEdge>) {
  // Zoom only drives screen-constant sizing here. Bucketing it means a zoom
  // gesture re-renders every edge at step boundaries (≤4% size drift between
  // steps, absorbed by the canvas transform) instead of on every frame.
  const zoom = useStore((s) => Math.round(s.transform[2] * 24) / 24 || s.transform[2]);
  const uid = useId().replace(/:/g, "");
  const arrow = `relation-arrow-${uid}`;
  const flow = `relation-flow-${uid}`;
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
  // A route fades in from the card it leaves and is full strength where it
  // arrives, so direction reads along the whole line and not only at the
  // arrowhead. A cyclic reference keeps its flat warning colour.
  const from = route.points[0];
  const to = route.points[route.points.length - 1];
  const graded = !!from && !!to && style?.stroke !== CYCLE_STROKE
    && (Math.abs(from.x - to.x) > 1 || Math.abs(from.y - to.y) > 1);
  return (
    <>
      <defs>
        <marker
          id={arrow}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth={9 / zoom}
          markerHeight={9 / zoom}
          markerUnits="userSpaceOnUse"
          orient="auto"
        >
          {/* A swept head with a notched back sits on the line more lightly
            * than a solid triangle. */}
          <path d="M 0.6 0.9 L 9.6 5 L 0.6 9.1 L 3 5 z" style={{ fill: stroke }} />
        </marker>
        {graded && (
          <linearGradient id={flow} gradientUnits="userSpaceOnUse" x1={from.x} y1={from.y} x2={to.x} y2={to.y}>
            <stop offset="0" style={{ stopColor: stroke, stopOpacity: 0.28 }} />
            <stop offset="0.55" style={{ stopColor: stroke, stopOpacity: 0.78 }} />
            <stop offset="1" style={{ stopColor: stroke, stopOpacity: 1 }} />
          </linearGradient>
        )}
      </defs>
      <GrowthReveal path={route.path} delay={data?.growthDelay} padding={24 / zoom}>
      <BaseEdge
        id={id}
        path={route.path}
        markerEnd={`url(#${arrow})`}
        style={{
          ...style,
          stroke: graded ? `url(#${flow})` : stroke,
          strokeLinecap: "round",
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
      {/* Where the route leaves its card: a small anchor ties the line to it. */}
      {!fan && from && (
        <circle
          className="map-edge-anchor"
          cx={from.x}
          cy={from.y}
          r={2.6 / zoom}
          style={{ fill: "var(--map-paper)", stroke, strokeWidth: 1.4 / zoom, opacity: style?.opacity }}
          aria-hidden="true"
        />
      )}
      {joint && (
        <circle
          className="map-edge-joint"
          cx={joint.x}
          cy={joint.y}
          r={3.2 / zoom}
          style={{ fill: stroke, opacity: style?.opacity }}
          aria-hidden="true"
        />
      )}
      </GrowthReveal>
      {((data?.active && !data.muted) || data?.lit) && (
        <EdgeLabelRenderer>
          {/* A spark drifting along the route replaces the old dash-offset
           * stroke animation: offset-distance runs on the compositor, while
           * dash offsets recalculated style and repainted a filtered path on
           * the main thread every frame of every active edge, forever. */}
          <div
            className="map-edge-spark"
            data-lit={!data?.active && !!data?.lit}
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
            data-lit={!!data?.lit}
            title={text}
            style={{
              opacity: style?.opacity,
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
