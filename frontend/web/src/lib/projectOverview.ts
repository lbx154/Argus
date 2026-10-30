import type { ProjectRow, Snapshot } from '../../../core/src/types';

/** Work skipped or cancelled: it is no task left to finish, so no count includes it. */
export const COUNTED_OUT = new Set(['skipped', 'cancelled']);

export function workDurationLabel(ms: number, zh: boolean, incomplete = false): string {
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60;
  const duration = zh ? `${hours ? `${hours}小时 ` : ''}${minutes ? `${minutes}分 ` : ''}${rest}秒`
    : `${hours ? `${hours}h ` : ''}${minutes ? `${minutes}m ` : ''}${rest}s`;
  return `${zh ? '已工作' : 'Worked'} ${incomplete ? '≥' : ''}${duration}`;
}

/** Cost is an API-price equivalent, not what the account was charged; say so.
 * Empty only when nothing was spent; calls without a price say so instead. */
export function spendEstimateLabel(overview: { spendUsd: number | null; spendStatus: Snapshot['spend_status'] }, zh: boolean, compact = false): string {
  if (overview.spendStatus === 'not_billed') return zh ? '不计费' : 'Not billed';
  if ((overview.spendStatus === 'unpriced' || overview.spendStatus === 'partial') && overview.spendUsd == null) return zh ? '未定价' : 'Unpriced';
  if (overview.spendUsd == null || overview.spendStatus === 'empty') return '';
  const amount = `$${overview.spendUsd.toFixed(overview.spendUsd < 10 ? 2 : 1)}`;
  const sign = overview.spendStatus === 'partial' || overview.spendStatus === 'unpriced' ? '≥' : '≈';
  if (compact) return `${sign}${amount}`;
  return zh ? `估算 ${sign}${amount}（按 API 价）` : `Est. ${sign}${amount} (API prices)`;
}

/** Add the list's existing figures for display; unknown calls keep the total incomplete. */
export function projectSpendTotal(projects: ProjectRow[]): { spendUsd: number | null; spendStatus: Snapshot['spend_status'] } {
  let amount = 0;
  let known = false;
  let incomplete = false;
  for (const project of projects) {
    if (typeof project.spend_usd === 'number') { amount += project.spend_usd; known = true; }
    if (project.spend_status === 'partial' || project.spend_status === 'unpriced'
      || project.spend_status === undefined || project.spend_status === 'priced' && project.spend_usd == null) incomplete = true;
  }
  return { spendUsd: (incomplete && amount === 0) || !known ? null : amount,
    spendStatus: incomplete ? 'partial' : known ? 'priced' : 'empty' };
}
export const SPEND_ESTIMATE_HINT = {
  zh: '按模型 API 公开价格折算的等价花费，用来比较规模；不是账户实际被扣的钱（订阅或额度计费时可能完全不同）。',
  en: 'An API-price equivalent for comparing scale; not what your account was actually charged (subscriptions or quotas can differ entirely).',
};
