import { useI18n } from '../i18n';
import { money } from '../lib/format';
import type { CostControlSnapshot, Daemon, RequestUsage } from '../api';

/** Model/API-call spend only; GPU and infrastructure cost are excluded. */
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
  const { t } = useI18n();
  const cap = daemon?.global_daily_cap_usd ?? null;
  const total = settledUsd ?? 0;
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  const costText = settledUsd == null
    ? (incomplete ? spendStatus : money(0))
    : `${money(total)}${incomplete ? '+' : ''}`;
  // Copilot bills either per premium request or, on credit-billed accounts, by
  // tokens; the ledger carries both figures. Surface the request count so a low
  // dollar amount reads as "few requests", not "broken meter".
  const isCopilot = (backendLabel || '').toLowerCase().includes('copilot');
  const reqs = requestUsage?.copilot.premium_requests ?? 0;
  // A call whose cost the provider has not settled is counted toward the cap
  // at the day's costliest priced call until it settles.
  const unpriced = costControl?.unresolved_calls ?? 0;
  const counted = costControl?.counted_unpriced_usd ?? 0;

  return (
    <div
      className="flex items-center gap-2.5"
      title={
        isCopilot
          ? 'Model/API spend only. GitHub Copilot bills per premium request; GPU and infrastructure cost are excluded.'
          : 'Model/API spend from idempotent call-level usage records; GPU and infrastructure cost are excluded.'
      }
    >
      <div className="flex flex-col items-end leading-tight">
        <span className="text-sm font-semibold tabular-nums text-gold">{costText}</span>
        <span className="text-[10px] text-ink-faint">
          {isCopilot
            ? `model/API spend · ${reqs.toFixed(1)} premium req`
            : 'model/API spend'}
          {incomplete ? ` · ${spendStatus}` : ''}
          {cap ? ` · model cap ${money(cap)}/d` : ''}
        </span>
        {requestUsage ? (
          <span className="text-[10px] tabular-nums text-ink-faint">
            C {requestUsage.codex.daily_calls}/{requestUsage.codex.daily_cap || '∞'}
            {' · '}P {requestUsage.copilot.daily_calls}/{requestUsage.copilot.daily_cap || '∞'}
          </span>
        ) : null}
        {costControl && (costControl.active_reservations > 0 || unpriced > 0) ? (
          <span
            className="text-[10px] tabular-nums text-ink-faint"
            title={t('cost.unpricedCounted')}
          >
            in-flight {costControl.active_reservations}
            {unpriced > 0 ? ` · unpriced ${unpriced} · ~${money(counted)} counted` : ''}
          </span>
        ) : null}
      </div>
    </div>
  );
}
