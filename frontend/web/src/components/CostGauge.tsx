import { fraction, type Spend } from '../lib/cost';
import { money } from '../lib/format';
import type { Daemon, RequestUsage } from '../api';

/**
 * Spend gauge backed by the call-level usage ledger. The event stream contributes
 * only the most recent mission's cap bar, never the cumulative total.
 */
export function CostGauge({
  spend,
  settledUsd,
  spendStatus,
  daemon,
  backendLabel,
  requestUsage,
}: {
  spend: Spend;
  settledUsd: number | null | undefined;
  spendStatus?: string;
  daemon: Daemon | undefined;
  backendLabel?: string;
  requestUsage?: RequestUsage | null;
}) {
  const cap = daemon?.per_mission_cap_usd ?? null;
  const daily = daemon?.daily_cap_usd ?? null;
  const total = settledUsd ?? 0;
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  const costText = settledUsd == null
    ? (incomplete ? spendStatus : money(0))
    : `${money(total)}${incomplete ? '+' : ''}`;
  const frac = fraction(spend.last || 0, cap);
  const pct = Math.round(frac * 100);
  const barColor = frac >= 0.9 ? '#c77b72' : frac >= 0.66 ? '#c1a363' : '#8fa7b8';
  // Copilot bills per PREMIUM REQUEST (flat $0.04/req), NOT per token — so a
  // copilot daemon's whole dollar cost is (#requests * $0.04). Surface the
  // request count so a low $ reads as "few requests", not "broken meter".
  const isCopilot = (backendLabel || '').toLowerCase().includes('copilot');
  const reqs = requestUsage?.copilot.premium_requests ?? 0;

  return (
    <div
      className="flex items-center gap-2.5"
      title={
        isCopilot
          ? 'GitHub Copilot bills per premium request (flat $0.04/req), not per token — one request can do a lot of work'
          : 'cumulative cost from idempotent call-level usage records'
      }
    >
      <div className="flex flex-col items-end leading-tight">
        <span className="text-sm font-semibold tabular-nums text-gold">{costText}</span>
        <span className="text-[10px] text-ink-faint">
          {isCopilot ? `${reqs.toFixed(1)} premium req` : 'cumulative cost'}
          {incomplete ? ` · ${spendStatus}` : ''}
          {daily ? ` · cap ${money(daily)}/d` : ''}
        </span>
        {requestUsage ? (
          <span className="text-[10px] tabular-nums text-ink-faint">
            C {requestUsage.codex.daily_calls}/{requestUsage.codex.daily_cap || '∞'}
            {' · '}P {requestUsage.copilot.daily_calls}/{requestUsage.copilot.daily_cap || '∞'}
          </span>
        ) : null}
      </div>
      {cap ? (
        <div className="flex flex-col gap-1">
          <div className="h-1.5 w-24 overflow-hidden rounded-sm bg-line" title="last mission vs per-mission cap">
            <div className="h-full transition-all" style={{ width: `${Math.max(2, pct)}%`, background: barColor }} />
          </div>
          <span className="text-[10px] tabular-nums text-ink-faint">
            last {money(spend.last)}{incomplete ? '+' : ''} / {money(cap)}
          </span>
        </div>
      ) : (
        <span className="text-[10px] text-ink-faint">no cap</span>
      )}
    </div>
  );
}
