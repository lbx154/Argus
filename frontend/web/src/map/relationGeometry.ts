export type Point = { x: number; y: number };
export type Box = Point & { width: number; height: number };
type Curve = [Point, Point, Point, Point];

export function pointOnCurve(c: Curve, t: number): Point {
  const u = 1 - t;
  return {
    x:
      u ** 3 * c[0].x +
      3 * u ** 2 * t * c[1].x +
      3 * u * t ** 2 * c[2].x +
      t ** 3 * c[3].x,
    y:
      u ** 3 * c[0].y +
      3 * u ** 2 * t * c[1].y +
      3 * u * t ** 2 * c[2].y +
      t ** 3 * c[3].y,
  };
}
/** Point a given arc distance along a sampled polyline, from either end.
 * Fan join dots use it to sit just off a card port (fanout) or clear of the
 * returning arrowhead (fanin) without re-deriving curve geometry. */
export function pointAlong(
  points: Point[],
  distance: number,
  fromEnd = false,
): Point | null {
  if (!points.length) return null;
  const path = fromEnd ? [...points].reverse() : points;
  let travelled = 0;
  for (let i = 1; i < path.length; i++) {
    const step = Math.hypot(
      path[i].x - path[i - 1].x,
      path[i].y - path[i - 1].y,
    );
    if (step > 0 && travelled + step >= distance) {
      const t = (distance - travelled) / step;
      return {
        x: path[i - 1].x + (path[i].x - path[i - 1].x) * t,
        y: path[i - 1].y + (path[i].y - path[i - 1].y) * t,
      };
    }
    travelled += step;
  }
  return path[path.length - 1];
}

export function inside(p: Point, b: Box, pad = 0) {
  return (
    p.x > b.x - pad &&
    p.x < b.x + b.width + pad &&
    p.y > b.y - pad &&
    p.y < b.y + b.height + pad
  );
}

/** Route a cubic Bézier curve through free space, preserving endpoint tangents. */
export function routeRelation(
  source: Point,
  target: Point,
  direction: boolean | "left" | "up" | "loop",
  lane: number,
  boxes: Box[],
) {
  const vertical = direction === true || direction === "up";
  const project = (p: Point) => ({
    x: direction === "left" ? -p.x : p.x,
    y: direction === "up" ? -p.y : p.y,
  });
  const s = project(source),
    t = project(target);
  boxes = boxes.map((b) => ({
    ...b,
    x: direction === "left" ? -b.x - b.width : b.x,
    y: direction === "up" ? -b.y - b.height : b.y,
  }));
  const span = Math.max(
    80,
    direction === "loop"
      ? Math.max(Math.abs(t.x - s.x), Math.abs(t.y - s.y)) * 2.8
      : 0,
    (vertical ? Math.abs(t.y - s.y) : Math.abs(t.x - s.x)) *
      (0.4 + lane * 0.06),
  );
  const bend = 0;
  const direct: Curve = [
    s,
    vertical ? { x: s.x + bend, y: s.y + span } : { x: s.x + span, y: s.y },
    direction === "loop"
      ? { x: t.x, y: t.y + span }
      : vertical
        ? { x: t.x - bend, y: t.y - span }
        : { x: t.x - span, y: t.y },
    t,
  ];
  const samples = (curves: Curve[]) =>
    curves.flatMap((c) =>
      Array.from({ length: 31 }, (_, i) => pointOnCurve(c, i / 30)),
    );
  const collisions = (curves: Curve[]) =>
    samples(curves).reduce(
      (n, p) => n + boxes.filter((b) => inside(p, b, 22)).length,
      0,
    );
  let curves = [direct],
    cost = collisions(curves) * 100000;
  if (cost) {
    const mid = { x: (s.x + t.x) / 2, y: (s.y + t.y) / 2 };
    const center = vertical ? mid.x : mid.y;
    const channels = [
      ...new Set(
        boxes.flatMap((b) =>
          vertical
            ? [b.x - 110 - lane * 30, b.x + b.width + 110 + lane * 30]
            : [b.y - 110 - lane * 30, b.y + b.height + 110 + lane * 30],
        ),
      ),
    ]
      .sort((a, b) => Math.abs(a - center) - Math.abs(b - center))
      .slice(0, 12);
    for (const channel of channels) {
      // Vary tangent lengths to find the shortest detour around nearby cards.
      const offset = ((channel - center) * 4) / 3;
      for (const factor of [0.25, 0.4, 0.55]) {
        const d = Math.max(
          60,
          (vertical ? Math.abs(t.y - s.y) : Math.abs(t.x - s.x)) * factor,
        );
        const trial: Curve[] = [
          vertical
            ? [
                s,
                { x: s.x + offset, y: s.y + d },
                { x: t.x + offset, y: t.y - d },
                t,
              ]
            : [
                s,
                { x: s.x + d, y: s.y + offset },
                { x: t.x - d, y: t.y + offset },
                t,
              ],
        ];
        const score =
          collisions(trial) * 100000 +
          Math.abs(channel - center) +
          Math.abs(factor - 0.4) * 100;
        if (score < cost) {
          curves = trial;
          cost = score;
        }
      }
    }
  }
  curves = curves.map((c) => c.map(project) as Curve);
  return {
    path:
      `M ${source.x} ${source.y}` +
      curves
        .map(
          (c) =>
            ` C ${c[1].x} ${c[1].y}, ${c[2].x} ${c[2].y}, ${c[3].x} ${c[3].y}`,
        )
        .join(""),
    points: samples(curves),
    labels: [
      0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.35, 0.45, 0.55, 0.65, 0.25, 0.75,
      0.15, 0.85,
    ].map((v) => {
      const index = Math.min(curves.length - 1, Math.floor(v * curves.length));
      return pointOnCurve(curves[index], v * curves.length - index);
    }),
  };
}
