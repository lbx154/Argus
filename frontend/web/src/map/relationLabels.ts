import type { useStoreApi } from "@xyflow/react";
import {
  inside,
  routeRelation,
  type Box,
  type Point,
} from "./relationGeometry";

type Store = ReturnType<typeof useStoreApi>;
type Route = ReturnType<typeof routeRelation>;
type Cache = {
  key: string;
  routes: Map<string, Route>;
  kinds: Map<string, string | undefined>;
  zoom: number;
  labels: Map<string, Point>;
};
const cache = new WeakMap<Store, Cache>();

/** Pills cap at this width on screen; longer phrases ellipsize (edges.css)
 * while the title attribute keeps the full wording. */
export const LABEL_MAX_WIDTH = 150;
const LABEL_MIN_ZOOM = 0.5;

/** Coarse zoom for label placement: 0 hides every pill at overview scale;
 * above the threshold an eighth-step floor keeps reserved boxes conservative
 * (bucket ≤ zoom, so estimates never shrink below true pill size) and stops
 * continuous camera zoom from recomputing the shared collision pass per frame. */
export function labelZoom(zoom: number) {
  return zoom < LABEL_MIN_ZOOM
    ? 0
    : Math.max(LABEL_MIN_ZOOM, Math.floor(zoom * 8) / 8);
}

/** The full phrase, single-spaced. Truncation is the pill's job (CSS ellipsis),
 * never the text's: a mid-word cut reads as broken copy. */
export function relationLabelText(label: unknown) {
  return label == null ? "" : String(label).replace(/\s+/g, " ").trim();
}

/** Estimated on-screen pill box for collision layout: 10px metadata type plus
 * padding, capped where the CSS ellipsis takes over. */
export function labelBox(label: unknown) {
  const width = [...relationLabelText(label)].reduce(
    (n, c) => n + (/[^\x00-\x7F]/.test(c) ? 10.5 : 6),
    20,
  );
  return { width: Math.min(width, LABEL_MAX_WIDTH + 16), height: 26 };
}

/** Shared by all edges in a canvas: labels reserve space in a stable order. */
export function relationLayout(store: Store, zoom: number) {
  const state = store.getState();
  const boxes = [...state.nodeLookup.values()]
    .filter((n) => !n.hidden)
    .map((n) => ({
      id: n.id,
      ...n.internals.positionAbsolute,
      width: n.width || n.measured.width || 0,
      height: n.height || n.measured.height || 0,
    }));
  function port(nodeId: string, handleId?: string | null) {
    const n = state.nodeLookup.get(nodeId);
    if (!n) return null;
    const { x, y } = n.internals.positionAbsolute;
    const width = n.width || n.measured.width || 0;
    const height = n.height || n.measured.height || 0;
    // Macro ports are fixed at each side's midpoint. Derive them from world
    // bounds so zoom-dependent DOM measurements cannot move a curve.
    return {
      x:
        x +
        (handleId === "left" ? 0 : handleId === "right" ? width : width / 2),
      y:
        y +
        (handleId === "top" ? 0 : handleId === "bottom" ? height : height / 2),
    };
  }
  const edges = state.edges
    .filter((e) => !e.hidden)
    .map((e) => ({
      ...e,
      s: port(e.source, e.sourceHandle),
      t: port(e.target, e.targetHandle),
    }));
  const key = JSON.stringify([
    boxes,
    edges.map((e) => [
      e.id,
      e.s,
      e.t,
      e.label,
      e.data?.lane,
      e.sourceHandle,
      e.targetHandle,
      e.className,
    ]),
  ]);
  let value = cache.get(store);
  if (!value || value.key !== key) {
    const routes = new Map<string, Route>();
    const kinds = new Map<string, string | undefined>();
    for (const e of edges) {
      // MapPanel names each edge's relation kind on its className; strokes and
      // pill tints derive their hue family from it (edges.css variables).
      kinds.set(e.id, /(?:^|\s)map-edge-(\w+)/.exec(e.className ?? "")?.[1]);
      if (e.s && e.t)
        routes.set(
          e.id,
          routeRelation(
            e.s,
            e.t,
            e.source === e.target
              ? "loop"
              : e.sourceHandle === "left"
                ? "left"
                : e.sourceHandle === "top"
                  ? "up"
                  : e.sourceHandle === "bottom",
            Number(e.data?.lane || 0),
            boxes.filter((b) => b.id !== e.source && b.id !== e.target),
          ),
        );
    }
    value = { key, routes, kinds, zoom: -1, labels: new Map() };
    cache.set(store, value);
  }
  const bucket = labelZoom(zoom);
  if (value.zoom !== bucket) {
    value.zoom = bucket;
    value.labels = new Map();
    if (bucket)
      placeLabels(value, edges, boxes, bucket);
  }
  return value;
}

/** Labels reserve card-free space in a stable order; a pill that fits nowhere
 * is dropped rather than laid over a card or another pill. */
function placeLabels(
  value: Cache,
  edges: Array<{ id: string; label?: unknown; data?: Record<string, unknown> }>,
  boxes: Box[],
  zoom: number,
) {
  const occupied: Box[] = [...boxes];
  for (const e of edges) {
    const route = value.routes.get(e.id);
    if (!route || !e.label) continue;
    const size = labelBox(e.label);
    const width = size.width / zoom;
    const height = size.height / zoom;
    const preferred = [0, 3, 4, 5, 6][Number(e.data?.lane || 0) % 5];
    const candidates = [
      route.labels[preferred],
      ...route.labels.filter((_, i) => i !== preferred),
    ];
    const available = candidates.filter((p) =>
      occupied.every(
        (b) =>
          p.x + width / 2 <= b.x ||
          p.x - width / 2 >= b.x + b.width ||
          p.y + height / 2 <= b.y ||
          p.y - height / 2 >= b.y + b.height,
      ),
    );
    let p: Point | undefined,
      best = Infinity;
    for (const candidate of available) {
      const box = {
        x: candidate.x - width / 2,
        y: candidate.y - height / 2,
        width,
        height,
      };
      let crossings = 0;
      for (const [otherId, other] of value.routes) {
        if (
          otherId !== e.id &&
          other.points.some((point) => inside(point, box))
        )
          crossings++;
      }
      if (crossings < best) {
        p = candidate;
        best = crossings;
      }
      if (crossings === 0) break;
    }
    if (p) {
      value.labels.set(e.id, p);
      occupied.push({
        x: p.x - width / 2,
        y: p.y - height / 2,
        width,
        height,
      });
    }
  }
}
