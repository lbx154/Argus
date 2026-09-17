import { expect, it } from "vitest";
import { inside, routeRelation } from "../map/relationGeometry";

it.each([false, true])(
  "routes around an intervening card, vertical=%s",
  (vertical) => {
    const source = { x: 0, y: 0 },
      target = vertical ? { x: 0, y: 1000 } : { x: 1000, y: 0 };
    const obstacle = vertical
      ? { x: -100, y: 350, width: 200, height: 300 }
      : { x: 350, y: -100, width: 300, height: 200 };
    const route = routeRelation(source, target, vertical, 0, [obstacle]);
    expect(route.points[0]).toEqual(source);
    expect(route.points.at(-1)).toEqual(target);
    expect(route.points.some((p) => inside(p, obstacle))).toBe(false);
    expect(route.path).not.toContain(" L ");
  },
);

it.each(["left", "up"] as const)(
  "routes a return connection facing %s",
  (direction) => {
    const source = { x: 1000, y: 1000 };
    const target = direction === "left" ? { x: 0, y: 1000 } : { x: 1000, y: 0 };
    const obstacle =
      direction === "left"
        ? { x: 350, y: 900, width: 300, height: 200 }
        : { x: 900, y: 350, width: 200, height: 300 };
    const route = routeRelation(source, target, direction, 0, [obstacle]);
    expect(route.points[0]).toEqual(source);
    expect(route.points.at(-1)).toEqual(target);
    expect(route.points.some((p) => inside(p, obstacle))).toBe(false);
  },
);

it("keeps a self-reference outside its own card", () => {
  const box = { x: 0, y: 0, width: 1100, height: 1000 };
  const route = routeRelation(
    { x: 1100, y: 500 },
    { x: 550, y: 1000 },
    "loop",
    0,
    [],
  );
  expect(route.points.some((p) => inside(p, box))).toBe(false);
});
