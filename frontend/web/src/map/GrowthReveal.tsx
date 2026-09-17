import { useId, type ReactNode } from 'react';

/** A drawing mask preserves dashed relationship semantics and arrow geometry. */
export function GrowthReveal({ path, delay, padding = 24, children }: {
  path: string; delay?: number; padding?: number; children: ReactNode;
}) {
  const id = `map-growth-${useId().replace(/:/g, '')}`;
  if (delay == null) return <>{children}</>;
  // Map routes contain absolute M/L/C coordinates; their control-point bounds
  // conservatively contain the entire curve, including vertical straight paths.
  const values = (path.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi) ?? []).map(Number);
  const xs = values.filter((_, i) => i % 2 === 0), ys = values.filter((_, i) => i % 2 === 1);
  if (!xs.length || !ys.length) return <>{children}</>;
  const x = Math.min(...xs) - padding, y = Math.min(...ys) - padding;
  return <g data-map-growing-edge="true">
    <defs><mask id={id} maskUnits="userSpaceOnUse" x={x} y={y}
      width={Math.max(...xs) - x + padding} height={Math.max(...ys) - y + padding}>
      <path className="map-growth-mask" d={path} pathLength={1} fill="none" stroke="white"
        strokeWidth={padding * 2} strokeLinecap="round" style={{ animationDelay: `${delay}ms` }} />
    </mask></defs>
    <g mask={`url(#${id})`}>{children}</g>
  </g>;
}
