import { fraction, type Spend } from '../lib/cost';
import { money } from '../lib/format';
import type { Daemon } from '../api';

/**
 * Spend gauge. The authoritative total is the daemon's journaled settled spend
 * (``snapshot.spend_usd`` — full history), shown prominently. The event stream
 * gives the most-recent mission cost for the per-mission cap bar. Honest: the
 * per-mission cap fills against the last settled mission, not a partial sum.
 */
export function CostGauge({
  spend,
  settledUsd,
  daemon,
  backendLabel,
}: {
  spend: Spend;
  settledUsd: number | undefined;
  daemon: Daemon | undefined;
  backendLabel?: string;
}) {
  const cap = daemon?.per_mission_cap_usd ?? null;
  const daily = daemon?.daily_cap_usd ?? null;
  const total = settledUsd != null && settledUsd > 0 ? settledUsd : spend.total;
  const frac = fraction(spend.last || 0, cap);
  const pct = Math.round(frac * 100);
  const barColor = frac >= 0.9 ? '#c77b72' : frac >= 0.66 ? '#c1a363' : '#8fa7b8';
  // Copilot bills per PREMIUM REQUEST (flat $0.04/req), NOT per token — so a
  // copilot daemon's whole dollar cost is (#requests * $0.04). Surface the
  // request count so a low $ reads as "few requests", not "broken meter".
  const isCopilot = (backendLabel || '').toLowerCase().includes('copilot');
  const reqs = isCopilot ? Math.round(total / 0.04) : 0;

  return (
    <div
      className="flex items-center gap-2.5"
      title={
        isCopilot
          ? 'GitHub Copilot bills per premium request (flat $0.04/req), not per token — one request can do a lot of work'
          : 'settled spend for this daemon (journaled per-mission cost)'
      }
    >
      <div className="flex flex-col items-end leading-tight">
        <span className="text-sm font-semibold tabular-nums text-gold">{money(total)}</span>
        <span className="text-[10px] text-ink-faint">
          {isCopilot ? `${reqs} premium req${reqs === 1 ? '' : 's'}` : 'spent'}
          {daily ? ` · cap ${money(daily)}/d` : ''}
        </span>
      </div>
      {cap ? (
        <div className="flex flex-col gap-1">
          <div className="h-1.5 w-24 overflow-hidden rounded-sm bg-line" title="last mission vs per-mission cap">
            <div className="h-full transition-all" style={{ width: `${Math.max(2, pct)}%`, background: barColor }} />
          </div>
          <span className="text-[10px] tabular-nums text-ink-faint">
            last {money(spend.last)} / {money(cap)}
          </span>
        </div>
      ) : (
        <span className="text-[10px] text-ink-faint">no cap</span>
      )}
    </div>
  );
}
