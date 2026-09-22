import { GrowthReveal } from './GrowthReveal';
import { useId } from "react";
import type { SubmapLayout } from "./submap";

export function SubmapEdges({ layout, growing = {}, activeStep, activeTeamSteps = [] }: { layout: SubmapLayout; growing?: Record<string, number>; activeStep?: string | null; activeTeamSteps?: string[] }) {
  const marker = `submap-arrow-${useId().replace(/:/g, "")}`;
  return (
    <svg
      className="submap-relations"
      width={layout.width}
      height={layout.height}
      aria-label="Task process relationships"
    >
      <defs>
        {/* Sized to stay clearly readable as a direction cue; the fill matches
          * the relation stroke exactly. */}
        <marker
          id={marker}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6.5"
          markerHeight="6.5"
          orient="auto"
        >
          <path d="M 0.6 0.9 L 9.6 5 L 0.6 9.1 L 3 5 z" fill="var(--map-edge-context, #6f8ca8)" />
        </marker>
      </defs>
      {layout.links.map((link) => {
        const a = layout.positions[link.source],
          b = layout.positions[link.target];
        const vertical = a.x === b.x && b.y > a.y;
        const sx = a.x + (vertical ? 116 : 232),
          sy = a.y + (vertical ? 180 : 90);
        const tx = b.x + (vertical ? 116 : 0),
          ty = b.y + (vertical ? 0 : 90);
        const mx = (sx + tx) / 2,
          my = (sy + ty) / 2;
        const d = vertical
          ? `M ${sx} ${sy} L ${tx} ${ty}`
          : `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}`;
        return (
          <g
            key={link.id}
            data-testid="submap-relation"
            data-relation={link.relation}
            data-source={link.source}
            data-target={link.target}
            aria-label={`${link.label}: ${link.explanation}`}
          >
            <title>{link.explanation}</title>
            <GrowthReveal path={d} delay={growing[link.id]}>
            <path
              className="submap-relation-path"
              d={d}
              fill="none"
              stroke="var(--map-edge-context, #6f8ca8)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeDasharray={link.contextual ? "5 6" : undefined}
              markerEnd={`url(#${marker})`}
            />
            </GrowthReveal>
            {(activeStep === link.target || activeTeamSteps.includes(link.target)) && <path className="submap-flow" d={d} pathLength={1} fill="none" stroke="#4b9cae" strokeWidth="3" strokeDasharray=".09 .91" strokeLinecap="round" aria-hidden="true" />}
            <g
              className="submap-relation-label"
              transform={`translate(${mx}, ${my})`}
            >
              <rect x="-38" y="-11" width="76" height="22" rx="6" />
              <text textAnchor="middle" dominantBaseline="central">
                {link.label}
              </text>
            </g>
          </g>
        );
      })}
    </svg>
  );
}
