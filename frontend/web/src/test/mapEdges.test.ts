import { expect, it } from "vitest";
import { pointAlong, routeRelation } from "../map/relationGeometry";

it("walks a fixed distance along a sampled polyline from either end", () => {
  const line = [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 20, y: 0 },
  ];
  expect(pointAlong(line, 0)).toEqual({ x: 0, y: 0 });
  expect(pointAlong(line, 15)).toEqual({ x: 15, y: 0 });
  expect(pointAlong(line, 5, true)).toEqual({ x: 15, y: 0 });
  // Past the end it clamps to the endpoint instead of extrapolating.
  expect(pointAlong(line, 99)).toEqual({ x: 20, y: 0 });
  expect(pointAlong([], 5)).toBeNull();
});

it("anchors fan join dots on the routed curve just off the ports", () => {
  const route = routeRelation({ x: 0, y: 0 }, { x: 1000, y: 0 }, false, 0, []);
  // Fanout: a couple of pixels out from the owning card's port.
  const out = pointAlong(route.points, 2);
  expect(out?.y).toBe(0);
  expect(out?.x).toBeCloseTo(2, 5);
  // Fanin: measured back from the target so the dot clears the arrowhead.
  const back = pointAlong(route.points, 15, true);
  expect(back?.y).toBe(0);
  expect(back?.x).toBeCloseTo(985, 5);
});

it("follows vertical routes the same way", () => {
  const route = routeRelation({ x: 0, y: 0 }, { x: 0, y: 1000 }, true, 0, []);
  const out = pointAlong(route.points, 2);
  expect(out?.x).toBe(0);
  expect(out?.y).toBeCloseTo(2, 5);
});
