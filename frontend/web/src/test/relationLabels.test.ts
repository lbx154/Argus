import { expect, it } from "vitest";
import type { useStoreApi } from "@xyflow/react";
import {
  LABEL_MAX_WIDTH,
  labelBox,
  labelZoom,
  relationLabelText,
  relationLayout,
} from "../map/relationLabels";

type Store = ReturnType<typeof useStoreApi>;

const node = (id: string, x: number, y: number, width = 300, height = 200) =>
  [
    id,
    {
      id,
      hidden: false,
      width,
      height,
      measured: { width, height },
      internals: { positionAbsolute: { x, y } },
    },
  ] as const;

const relation = (patch: Record<string, unknown> = {}) => ({
  id: "dep",
  source: "a",
  target: "b",
  sourceHandle: "right",
  targetHandle: "left",
  label: "motivates the follow-up",
  className: "map-edge-dependency",
  data: { lane: 0 },
  ...patch,
});

const storeWith = (edges: Record<string, unknown>[]) =>
  ({
    getState: () => ({
      nodeLookup: new Map([node("a", 0, 0), node("b", 900, 0)]),
      edges,
    }),
  }) as unknown as Store;

it("keeps the full phrase single-spaced; truncation belongs to the pill", () => {
  expect(relationLabelText("  motivates \n attribution   study ")).toBe(
    "motivates attribution study",
  );
  expect(relationLabelText("corrects comparator baseline")).toBe(
    "corrects comparator baseline",
  );
  expect(relationLabelText(undefined)).toBe("");
  expect(relationLabelText(null)).toBe("");
  // The helper never cuts words itself: ellipsis is purely a CSS concern.
  expect(relationLabelText("motivates attribution study")).not.toContain("…");
});

it("buckets zoom coarsely and keeps labels through the overview", () => {
  // The overview is where a map is read; what connects two tasks shows there.
  expect(labelZoom(0.2)).toBe(0.2);
  expect(labelZoom(0.11)).toBe(0.1);
  expect(labelZoom(0.49)).toBe(0.45);
  // Only where cards are down to a title do labels give way.
  expect(labelZoom(0.04)).toBe(0);
  expect(labelZoom(0.5)).toBe(0.5);
  expect(labelZoom(0.9)).toBe(0.875);
  expect(labelZoom(1)).toBe(1);
  // Floor semantics: reserved boxes (size / bucket) never undershoot reality.
  for (const zoom of [0.06, 0.11, 0.27, 0.49, 0.5, 0.62, 0.87, 1.01, 1.9])
    expect(labelZoom(zoom)).toBeLessThanOrEqual(zoom);
});

it("estimates pill boxes capped where the CSS ellipsis takes over", () => {
  expect(labelBox("aaaa")).toEqual({ width: 20 + 4 * 7, height: 26 });
  expect(labelBox("依赖依赖")).toEqual({ width: 20 + 4 * 10.5, height: 26 });
  expect(labelBox("x".repeat(60)).width).toBe(LABEL_MAX_WIDTH + 16);
});

it("places a label clear of both cards and reports each edge's kind", () => {
  const store = storeWith([relation()]);
  const layout = relationLayout(store, 1);
  expect(layout.routes.has("dep")).toBe(true);
  expect(layout.kinds.get("dep")).toBe("dependency");
  const point = layout.labels.get("dep");
  expect(point).toBeTruthy();
  const { width, height } = labelBox("motivates the follow-up");
  for (const box of [
    { x: 0, y: 0, width: 300, height: 200 },
    { x: 900, y: 0, width: 300, height: 200 },
  ]) {
    const clear =
      point!.x + width / 2 <= box.x ||
      point!.x - width / 2 >= box.x + box.width ||
      point!.y + height / 2 <= box.y ||
      point!.y - height / 2 >= box.y + box.height;
    expect(clear).toBe(true);
  }
});

it("places a relation's label at overview zoom, and drops it only where cards are titles", () => {
  const store = storeWith([relation()]);
  const layout = relationLayout(store, 0.4);
  expect(layout.routes.has("dep")).toBe(true);
  // The scale a map is read at: the line says what it is.
  expect(layout.labels.size).toBe(1);
  const far = relationLayout(store, 0.04);
  expect(far.routes.has("dep")).toBe(true);
  expect(far.labels.size).toBe(0);
});

it("reuses the placement pass while zoom stays inside one bucket", () => {
  const store = storeWith([relation()]);
  const first = relationLayout(store, 0.92).labels;
  expect(first.size).toBe(1);
  // 0.92 and 0.95 share the 0.875 bucket: the exact same Map comes back, so
  // continuous camera zoom does not recompute collision layout every frame.
  expect(relationLayout(store, 0.95).labels).toBe(first);
  expect(relationLayout(store, 1.2).labels).not.toBe(first);
});

it("derives fan kinds from the edge className and skips their labels", () => {
  const store = storeWith([
    relation({
      id: "fan",
      className: "react-flow__edge map-edge-fanout",
      label: undefined,
    }),
    relation({ id: "plain", className: undefined }),
  ]);
  const layout = relationLayout(store, 1);
  expect(layout.kinds.get("fan")).toBe("fanout");
  expect(layout.kinds.get("plain")).toBeUndefined();
  expect(layout.labels.has("fan")).toBe(false);
});
