import type { AccountQuota, MissionUsage } from '../../../core/src/types';

type Translate = (key: string, variables?: Record<string, string | number>) => string;

function count(value: number): string {
  if (!Number.isFinite(value)) return '0';
  return Math.abs(value) >= 100 ? Math.round(value).toLocaleString() : String(Math.round(value * 10) / 10);
}

/** "Request-billed · 37 of 300 requests left this month" / "按额度计费 · 本月额度还剩 57%". */
export function accountQuotaText(quota: AccountQuota, t: Translate): string {
  if (quota.error) return t('spend.account.error');
  if (quota.unlimited || quota.billing_mode === 'unlimited') return t('spend.account.unlimited');
  let text: string;
  if (quota.billing_mode === 'request') {
    text = t('spend.account.request', {
      left: count(quota.remaining ?? 0),
      total: count(quota.entitlement ?? 0),
    });
  } else if (quota.billing_mode === 'credit') {
    const percent = quota.percent_remaining == null ? '?' : `${Math.round(quota.percent_remaining)}%`;
    text = t('spend.account.credit', { percent });
  } else {
    return t('spend.account.unknown');
  }
  return quota.reset_date ? `${text} · ${t('spend.account.resets', { date: quota.reset_date })}` : text;
}

/** Short chip text: "37 left" / "57%". */
export function accountQuotaChip(quota: AccountQuota): string {
  if (quota.error || quota.unlimited || quota.billing_mode === 'unknown' || quota.billing_mode === 'unlimited') return '';
  if (quota.billing_mode === 'request') return `${count(quota.remaining ?? 0)}/${count(quota.entitlement ?? 0)}`;
  return quota.percent_remaining == null ? '' : `${Math.round(quota.percent_remaining)}%`;
}

/** Plain parts of what one task has spent; every non-zero unit is shown. */
export function missionSpendParts(
  usage: MissionUsage | undefined,
  t: Translate,
  budget?: { requests: number; usd: number },
): string[] {
  if (!usage || usage.calls <= 0) return [];
  const parts: string[] = [];
  const requests = usage.premium_requests || 0;
  if (requests > 0 || (budget?.requests ?? 0) > 0) {
    const used = t('spend.mission.requests', { count: count(requests) });
    parts.push(budget && budget.requests > 0
      ? t('spend.mission.ofBudget', { used, limit: count(budget.requests) })
      : used);
  }
  if (usage.credits > 0) parts.push(t('spend.mission.credits', { count: count(usage.credits) }));
  if (usage.known_cost_usd > 0 || (budget?.usd ?? 0) > 0) {
    const usd = `$${usage.known_cost_usd.toFixed(2)}`;
    parts.push(budget && budget.usd > 0
      ? t('spend.mission.ofBudget', { used: usd, limit: `$${budget.usd.toFixed(2)}` })
      : usd);
  }
  if (!parts.length) {
    const tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
    if (tokens > 0) parts.push(t('spend.mission.tokens', { count: tokens.toLocaleString() }));
  }
  parts.push(t('spend.mission.calls', { count: usage.calls }));
  return parts;
}
