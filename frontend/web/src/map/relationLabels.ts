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
  zoom: number;
  labels: Map<string, Point>;
};
const cache = new WeakMap<Store, Cache>();

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
    ]),
  ]);
  let value = cache.get(store);
  if (!value || value.key !== key) {
    const routes = new Map<string, Route>();
    for (const e of edges)
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
    value = { key, routes, zoom: -1, labels: new Map() };
    cache.set(store, value);
  }
  if (value.zoom !== zoom) {
    value.zoom = zoom;
    value.labels = new Map();
    const occupied: Box[] = [...boxes];
    for (const e of edges) {
      const route = value.routes.get(e.id);
      if (!route || !e.label) continue;
      const width =
        [...String(e.label)].reduce(
          (n, c) => n + (/[^\x00-\x7F]/.test(c) ? 12 : 7),
          18,
        ) / zoom;
      const height = 30 / zoom;
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
  return value;
}
