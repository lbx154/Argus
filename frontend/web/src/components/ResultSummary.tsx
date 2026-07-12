import type { ArtifactInfo, JournalEntry } from '../api';
import { money } from '../lib/format';

interface EvidenceFile {
  path?: string;
  why?: string;
}

/** The latest reviewed outcome, promoted out of the journal when work completes. */
export function ResultSummary({
  entries,
  artifacts,
  artifactError = false,
  onOpenArtifact,
}: {
  entries: JournalEntry[];
  artifacts?: ArtifactInfo[];
  artifactError?: boolean;
  onOpenArtifact?: (path: string) => void;
}) {
  const latest = [...entries].reverse().find((entry) =>
    ['mission_complete', 'win', 'milestone'].includes(entry.kind),
  );
  if (!latest) return null;

  const extra = latest.extra ?? {};
  const report = (extra.planner_report ?? {}) as Record<string, unknown>;
  const headline = String(report.headline ?? extra.completion_summary ?? latest.summary ?? latest.title).trim();
  const evidence = Array.isArray(report.evidence_files)
    ? (report.evidence_files as EvidenceFile[]).filter((item) => item?.path).slice(0, 4)
    : [];
  const reviewedArtifacts = artifacts?.filter((item) => item.source !== 'manager_live');
  const files: Array<EvidenceFile & Partial<ArtifactInfo>> = reviewedArtifacts ?? evidence;
  const certified = extra.final_submission_certified === true;
  const pricingStatus = String(extra.pricing_status ?? '');
  const rawCost = Object.prototype.hasOwnProperty.call(extra, 'cost_usd')
    ? extra.cost_usd
    : latest.cost_usd;
  const costLabel = typeof rawCost === 'number' && rawCost >= 0
    ? `${money(rawCost)}${pricingStatus === 'partial' || pricingStatus === 'unpriced' ? '+' : ''}`
    : pricingStatus === 'partial' || pricingStatus === 'unpriced'
    ? pricingStatus
    : '';

  return (
    <section className="card mb-3 shrink-0 border-ok/40 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ok">
          {certified ? 'Certified result' : 'Latest result'}
        </span>
        {costLabel ? <span className="ml-auto text-[10px] text-ink-faint">{costLabel}</span> : null}
      </div>
      <div className="mt-1 text-sm font-medium text-ink">{latest.title}</div>
      {headline && headline !== latest.title ? (
        <p
          className="mt-1 overflow-hidden text-xs leading-relaxed text-ink-dim"
          style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
        >
          {headline}
        </p>
      ) : null}
      {files.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {files.slice(0, 4).map((item) => {
            const interactive = Boolean(reviewedArtifacts && item.exists && item.path && onOpenArtifact);
            return (
              <button
                key={item.path}
                type="button"
                disabled={!interactive}
                title={item.why || (item.exists === false ? 'declared evidence is not present' : String(item.path))}
                onClick={() => item.path && interactive && onOpenArtifact?.(item.path)}
                className={`rounded border px-2 py-0.5 font-mono text-[10px] transition-colors ${
                  interactive
                    ? 'border-blue-deep/50 bg-blue-deep/10 text-blue-sky hover:border-blue-sky/60 hover:bg-blue-deep/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-sky/50'
                    : item.exists === false
                    ? 'cursor-not-allowed border-line/70 bg-bg/30 text-ink-faint line-through'
                    : 'cursor-default border-line bg-bg/50 text-blue-sky'
                }`}
              >
                {item.path}
                {interactive ? <span aria-hidden="true"> ↗</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
      {artifactError ? (
        <p className="mt-2 text-[10px] text-warn" role="status">
          Artifact preview unavailable · check the connection or Web token.
        </p>
      ) : null}
    </section>
  );
}
