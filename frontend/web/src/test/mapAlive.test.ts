import { describe, expect, it } from "vitest";
import { arrivalDelay, arrivalEdgeDelay, attention, elapsedLabel, moved, settle } from "../map/alive";

describe("settling positions", () => {
  const from = { a: { x: 0, y: 0 }, b: { x: 100, y: 50 } };
  const to = { a: { x: 200, y: 0 }, b: { x: 100, y: 50 }, c: { x: 40, y: 40 } };
  it("moves each card part of the way, and lands exactly", () => {
    const mid = settle(from, to, 0.5);
    expect(mid.a.x).toBeGreaterThan(0);
    expect(mid.a.x).toBeLessThan(200);
    expect(mid.a.y).toBe(0);
    expect(settle(from, to, 1)).toEqual(to);
    expect(settle(from, to, 0).a).toEqual(from.a);
  });
  it("eases out: the first half of the time covers more than half the distance", () => {
    expect(settle(from, to, 0.5).a.x).toBeGreaterThan(100);
  });
  it("places a card that had no previous position straight at its target", () => {
    expect(settle(from, to, 0.2).c).toEqual(to.c);
  });
  it("drops cards that left the scene", () => {
    expect(Object.keys(settle(to, from, 0.3))).toEqual(["a", "b"]);
  });
  it("knows when nothing that was already placed has moved", () => {
    expect(moved(from, { ...from, c: { x: 1, y: 1 } })).toBe(false);
    expect(moved(from, to)).toBe(true);
    expect(moved({}, to)).toBe(false);
  });
});

describe("attention under the pointer", () => {
  const links = [
    { id: "ab", source: "a", target: "b" },
    { id: "bc", source: "b", target: "c" },
    { id: "cd", source: "c", target: "d" },
  ];
  it("lights the hovered card, its relations, and the cards across them", () => {
    const lit = attention(links, "b");
    expect([...lit.edges].sort()).toEqual(["ab", "bc"]);
    expect([...lit.nodes].sort()).toEqual(["a", "b", "c"]);
  });
  it("lights nothing when the pointer is elsewhere", () => {
    const lit = attention(links, null);
    expect(lit.edges.size).toBe(0);
    expect(lit.nodes.size).toBe(0);
  });
});

describe("opening choreography", () => {
  it("staggers cards in reading order and caps the wait", () => {
    expect(arrivalDelay(1)).toBe(0);
    expect(arrivalDelay(2)).toBe(80);
    expect(arrivalDelay(40)).toBe(arrivalDelay(11));
    expect(arrivalEdgeDelay(3)).toBeGreaterThan(arrivalDelay(3));
  });
});

describe("elapsed time in words", () => {
  it("reads naturally in both languages", () => {
    expect(elapsedLabel(42, false)).toBe("42s");
    expect(elapsedLabel(42, true)).toBe("42 秒");
    expect(elapsedLabel(60 * 12 + 5, false)).toBe("12 min");
    expect(elapsedLabel(3600 * 2 + 60 * 7, false)).toBe("2 h 07 min");
    expect(elapsedLabel(3600 * 2 + 60 * 7, true)).toBe("2 小时 7 分");
    expect(elapsedLabel(-5, false)).toBe("0s");
  });
});
