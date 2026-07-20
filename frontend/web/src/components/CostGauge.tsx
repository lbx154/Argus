import { money } from '../lib/format';
import type { CostControlSnapshot, Daemon, RequestUsage } from '../api';

/**
 * Host-global spend backed by the call-level usage ledger.
 */
export function CostGauge({
  settledUsd,
  spendStatus,
  daemon,
  backendLabel,
  requestUsage,
  costControl,
}: {
  settledUsd: number | null | undefined;
  spendStatus?: string;
  daemon: Daemon | undefined;
  backendLabel?: string;
  requestUsage?: RequestUsage | null;
  costControl?: CostControlSnapshot | null;
}) {
  const cap = daemon?.global_daily_cap_usd ?? null;
  const total = settledUsd ?? 0;
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  const costText = settledUsd == null
    ? (incomplete ? spendStatus : money(0))
    : `${money(total)}${incomplete ? '+' : ''}`;
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
          {cap ? ` · global cap ${money(cap)}/d` : ''}
        </span>
        {requestUsage ? (
          <span className="text-[10px] tabular-nums text-ink-faint">
            C {requestUsage.codex.daily_calls}/{requestUsage.codex.daily_cap || '∞'}
            {' · '}P {requestUsage.copilot.daily_calls}/{requestUsage.copilot.daily_cap || '∞'}
          </span>
        ) : null}
        {costControl && (costControl.active_reservations > 0 || costControl.unresolved_calls > 0) ? (
          <span className={`text-[10px] tabular-nums ${costControl.unresolved_calls > 0 ? 'text-err' : 'text-ink-faint'}`}>
            in-flight {costControl.active_reservations} · unresolved {costControl.unresolved_calls}
          </span>
        ) : null}
      </div>
    </div>
  );
}
