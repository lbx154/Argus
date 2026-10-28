import type { DeliveryReceipt, MissionView, Snapshot } from '../../../core/src/types';
import { stripLaunchNote } from './launch';

const ACTIVE = new Set(['pending', 'running', 'in_progress', 'claimed']);
/** Work replaced, skipped or cancelled: it is no task left to finish, so no count includes it. */
export const COUNTED_OUT = new Set(['skipped', 'cancelled', 'superseded']);

export interface ProjectOverview {
  goal: string;
  /** Whether `goal` is the committed objective or only the current task's title. */
  goalIsTask: boolean;
  done: number;
  total: number;
  active: number;
  /** Seconds the roles actually worked: the settled calls, plus the running
   * task's time since then; idle hours and days add nothing. Null when the
   * ledger records no call durations. */
  workedSeconds: number | null;
  /** When the project's first task was queued. */
  startedAt: number | null;
  spendUsd: number | null;
  spendStatus: Snapshot['spend_status'];
  latestDelivery: DeliveryReceipt | null;
}

/** What a returning operator needs first: the goal, how far it got, what it cost, the newest result. */
export function projectOverview(snapshot: Snapshot, view: MissionView | null | undefined, now: number, latest?: DeliveryReceipt | null): ProjectOverview | null {
  const items = snapshot.backlog.filter((item) => !COUNTED_OUT.has(item.status));
  if (!items.length) return null;
  const done = items.filter((item) => item.status === 'done').length;
  const active = items.filter((item) => ACTIVE.has(item.status)).length;
  const started = Math.min(...items.map((item) => Number(item.ts) || Number(item.started_ts) || Infinity));
  const settled = typeof snapshot.worked_seconds === 'number' && snapshot.worked_seconds > 0 ? snapshot.worked_seconds : null;
  // The call under way is not in the ledger until it ends: count the running
  // task from its start or the last settled call, whichever is later.
  const running = snapshot.daemon.alive ? Math.min(Infinity, ...items
    .filter((item) => ['running', 'in_progress', 'claimed'].includes(item.status) && Number(item.started_ts) > 0)
    .map((item) => Number(item.started_ts))) : Infinity;
  const since = Math.max(running, Number(snapshot.worked_until) || 0);
  const inFlight = Number.isFinite(running) && now > since ? now - since : 0;
  const committed = stripLaunchNote(String(snapshot.goal || snapshot.continuous?.objective || snapshot.session.objective || ''));
  const goal = committed || String(view?.mission.title || items[0].title || '').trim();
  return {
    goal,
    goalIsTask: !committed,
    done,
    total: items.length,
    active,
    workedSeconds: settled === null ? null : settled + inFlight,
    startedAt: Number.isFinite(started) ? started : null,
    spendUsd: typeof snapshot.spend_usd === 'number' ? snapshot.spend_usd : null,
    spendStatus: snapshot.spend_status,
    latestDelivery: latest ?? view?.delivery ?? null,
  };
}

export function durationLabel(seconds: number | null, zh: boolean): string {
  if (seconds == null) return '';
  if (seconds < 3600) return zh ? `${Math.max(1, Math.round(seconds / 60))} 分钟` : `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86400) return zh ? `${(seconds / 3600).toFixed(1).replace(/\.0$/, '')} 小时` : `${(seconds / 3600).toFixed(1).replace(/\.0$/, '')} h`;
  const days = Math.floor(seconds / 86400);
  const hours = Math.round((seconds % 86400) / 3600);
  return zh ? `${days} 天 ${hours} 小时` : `${days} d ${hours} h`;
}

/** Cost is an API-price equivalent, not what the account was charged; say so.
 * Empty only when nothing was spent; calls without a price say so instead. */
export function spendEstimateLabel(overview: Pick<ProjectOverview, 'spendUsd' | 'spendStatus'>, zh: boolean): string {
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

export const DISK_WARN_FRACTION = 0.8;

export function diskView(storage: Snapshot['storage'], zh: boolean, format: (bytes: number) => string): { text: string; warn: boolean } | null {
  if (!storage || storage.used_fraction == null) return null;
  const used = Math.round(storage.used_fraction * 100);
  const free = format(storage.free_bytes);
  const warn = storage.used_fraction >= DISK_WARN_FRACTION;
  return {
    warn,
    text: warn
      ? (zh ? `磁盘已用 ${used}%，只剩 ${free}，大文件可能写不下` : `Disk ${used}% full, ${free} left; large outputs may not fit`)
      : (zh ? `磁盘剩余 ${free}` : `${free} disk free`),
  };
}
