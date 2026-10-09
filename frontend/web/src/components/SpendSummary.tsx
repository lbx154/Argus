import type { AccountBudget, AccountQuota, MissionUsage } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { accountQuotaChip, accountQuotaText, missionSpendParts } from '../lib/spendText';

/** What this task has spent so far, in plain units; nothing when it spent nothing. */
export function MissionSpendLine({
  usage,
  budget,
  className = 'mt-3 text-xs tabular-nums text-ink-faint',
}: {
  usage?: MissionUsage;
  budget?: AccountBudget['mission_budget'];
  className?: string;
}) {
  const { t } = useI18n();
  const parts = missionSpendParts(usage, t, budget);
  if (!parts.length) return null;
  return <p className={className} data-mission-spend>
    {t('spend.mission.label')} · {parts.join(' · ')}
  </p>;
}

/** Compact header chip: what the account has left this month. */
export function AccountQuotaChip({ quota }: { quota?: AccountQuota | null }) {
  const { t } = useI18n();
  if (!quota) return null;
  const chip = accountQuotaChip(quota);
  if (!chip) return null;
  const title = accountQuotaText(quota, t);
  return <span
    data-account-quota
    data-low={quota.low ? 'true' : undefined}
    title={quota.low ? `${title}\n${t('spend.account.low', { percent: quota.warn_percent })}` : title}
    aria-label={title}
    className={`inline-flex h-6 shrink-0 items-center rounded-full border px-2 font-mono text-[10px] tabular-nums ${
      quota.low ? 'border-err/40 bg-err/10 text-err' : 'border-line/70 text-ink-faint'
    }`}
  >{chip}</span>;
}

/** Settings panel row: billing mode, what is left, and what Argus does about it. */
export function AccountQuotaPanel({ quota }: { quota?: AccountQuota | null }) {
  const { t } = useI18n();
  if (!quota) return null;
  const hint = quota.error ? '' : quota.billing_mode === 'request'
    ? t('spend.account.hintRequest')
    : quota.billing_mode === 'credit' ? t('spend.account.hintCredit') : '';
  return <div className="mt-2 text-xs text-ink-dim" data-account-quota-panel>
    <p>
      <span className="text-ink-faint">{t('spend.account.title')}{quota.login ? ` (${quota.login})` : ''}: </span>
      <span className="tabular-nums">{accountQuotaText(quota, t)}</span>
    </p>
    {hint ? <p className="mt-0.5 text-[10px] text-ink-faint">{hint}</p> : null}
    {quota.low ? <p role="status" className="mt-1 text-[11px] text-err" data-account-quota-low>
      {t('spend.account.low', { percent: quota.warn_percent })}
    </p> : null}
  </div>;
}
