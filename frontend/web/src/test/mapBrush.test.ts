import { describe, expect, it } from "vitest";
import { brushStroke } from "../map/brush";

/** Half-widths of the outline at each stop, read back from the path: the left
 * side runs start to end, the right side end to start. */
function widths(d: string): number[] {
  const pts = d.replace(/^M |\s*Z$/g, "").split(" L ").map((p) => p.split(" ").map(Number));
  const n = pts.length / 2;
  return Array.from({ length: n }, (_, i) => Math.hypot(pts[i][0] - pts[2 * n - 1 - i][0], pts[i][1] - pts[2 * n - 1 - i][1]));
}

describe("brushStroke", () => {
  const line = Array.from({ length: 31 }, (_, i) => ({ x: i * 20, y: 0 }));

  it("is set down lightly, gains weight, is pressed once and lifted to a point", () => {
    const w = widths(brushStroke(line, 4, 40));
    expect(w[0]).toBeLessThan(2); // the start is a hair of the body's width
    const peak = Math.max(...w);
    expect(peak).toBeGreaterThan(4 * 2); // the press is wider than the body
    expect(w.indexOf(peak)).toBeGreaterThan(w.length * 0.8); // and sits at the far end
    expect(w[w.length - 1]).toBeCloseTo(0, 5); // the tip closes
    // Up to the press the stroke never thins: direction reads along its length.
    const body = w.slice(0, w.indexOf(peak) + 1);
    for (let i = 1; i < body.length; i++) expect(body[i]).toBeGreaterThanOrEqual(body[i - 1] - 1e-6);
  });

  it("keeps the head in proportion on a route shorter than its tip", () => {
    const short = [{ x: 0, y: 0 }, { x: 0, y: 30 }];
    const d = brushStroke(short, 4, 40);
    expect(d.startsWith("M ")).toBe(true);
    const ys = d.match(/-?\d+(\.\d+)? (-?\d+(\.\d+)?)/g)!.map((p) => Number(p.split(" ")[1]));
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(-0.01);
    expect(Math.max(...ys)).toBeLessThanOrEqual(30.01);
  });

  it("draws nothing for a route without length", () => {
    expect(brushStroke([], 4, 40)).toBe("");
    expect(brushStroke([{ x: 5, y: 5 }, { x: 5, y: 5 }], 4, 40)).toBe("");
  });
});
