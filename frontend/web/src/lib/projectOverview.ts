import type { Snapshot } from '../../../core/src/types';

/** Work replaced, skipped or cancelled: it is no task left to finish, so no count includes it. */
export const COUNTED_OUT = new Set(['skipped', 'cancelled', 'superseded']);

/** Cost is an API-price equivalent, not what the account was charged; say so.
 * Empty only when nothing was spent; calls without a price say so instead. */
export function spendEstimateLabel(overview: { spendUsd: number | null; spendStatus: Snapshot['spend_status'] }, zh: boolean): string {
  if (overview.spendStatus === 'not_billed') return zh ? '不计费' : 'Not billed';
  if (overview.spendStatus === 'unpriced' || (overview.spendStatus === 'partial' && overview.spendUsd == null)) return zh ? '未定价' : 'Unpriced';
  if (overview.spendUsd == null || overview.spendStatus === 'empty') return '';
  const amount = `$${overview.spendUsd.toFixed(overview.spendUsd < 10 ? 2 : 1)}`;
  const sign = overview.spendStatus === 'partial' ? '≥' : '≈';
  return zh ? `估算 ${sign}${amount}（按 API 价）` : `Est. ${sign}${amount} (API prices)`;
}
export const SPEND_ESTIMATE_HINT = {
  zh: '按模型 API 公开价格折算的等价花费，用来比较规模；不是账户实际被扣的钱（订阅或额度计费时可能完全不同）。',
  en: 'An API-price equivalent for comparing scale; not what your account was actually charged (subscriptions or quotas can differ entirely).',
};
