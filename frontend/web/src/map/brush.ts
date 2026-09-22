import type { Point } from "./relationGeometry";

/** An ink stroke along a sampled route, as a filled outline.
 *
 * A brush is set down lightly, drawn with growing weight, pressed once and
 * lifted to a point. Drawn that way a route shows where it is going along its
 * whole length, so it needs no separate arrowhead, and it reads as a line
 * someone made instead of a connector a tool placed.
 *
 * `weight` is the body's full width and `tip` the length of the pressed,
 * pointed end, both in the route's own units (the caller divides screen pixels
 * by the zoom). The outline is resampled by arc length, because the route's
 * samples are even in curve parameter and bunch up on tight bends. */
export function brushStroke(points: Point[], weight: number, tip: number): string {
  if (points.length < 2) return "";
  const lengths = [0];
  for (let i = 1; i < points.length; i++)
    lengths.push(lengths[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
  const total = lengths[lengths.length - 1];
  if (!(total > 0)) return "";
  // A route shorter than three tips is mostly tip; keep the head in proportion.
  const head = Math.min(tip, total / 3);
  // The press is short and the lift long, so the end reads as a brush tip
  // pointing where the route goes, not as a lozenge.
  const press = head * 1.22;

  let cursor = 1;
  const at = (distance: number): Point => {
    const d = Math.max(0, Math.min(total, distance));
    while (cursor < lengths.length - 1 && lengths[cursor] < d) cursor++;
    while (cursor > 1 && lengths[cursor - 1] > d) cursor--;
    const span = lengths[cursor] - lengths[cursor - 1] || 1;
    const t = (d - lengths[cursor - 1]) / span;
    const a = points[cursor - 1], b = points[cursor];
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  };
  const smooth = (t: number) => t * t * (3 - 2 * t);
  const width = (distance: number) => {
    const left = total - distance;
    if (left <= head) return weight * 2.5 * Math.pow(left / head, 0.85);
    const body = weight * (0.34 + 0.66 * smooth(Math.min(1, distance / Math.max(1, total - press))));
    if (left >= press) return body;
    // The press: the body swells into the head over a short run.
    return body + (weight * 2.5 - body) * smooth((press - left) / (press - head));
  };

  const count = Math.max(12, Math.min(72, Math.round(total / Math.max(head, 1) * 1.5)));
  const stops = Array.from({ length: count + 1 }, (_, i) => (total - press) * (i / count));
  stops.push(total - (press + head) / 2, total - head, total);
  const left: string[] = [], right: string[] = [];
  const round = (n: number) => Math.round(n * 100) / 100;
  for (const distance of stops) {
    const p = at(distance);
    const before = at(distance - total * 0.004 - 0.5), after = at(distance + total * 0.004 + 0.5);
    const dx = after.x - before.x, dy = after.y - before.y;
    const norm = Math.hypot(dx, dy) || 1;
    const half = width(distance) / 2;
    const nx = (-dy / norm) * half, ny = (dx / norm) * half;
    left.push(`${round(p.x + nx)} ${round(p.y + ny)}`);
    right.push(`${round(p.x - nx)} ${round(p.y - ny)}`);
  }
  return `M ${left.join(" L ")} L ${right.reverse().join(" L ")} Z`;
}
