import type { EventMsg } from './types.js';

export interface Spend {
  /** Retained for API compatibility; event streams are not a cost ledger. */
  total: number;
  /** Completed missions represented in the current event window. */
  missions: number;
  /** Most recent completed mission's cost. */
  last: number;
}

const isMissionDone = (type: string): boolean =>
  type === 'life.mission.completed' || type === 'mission.completed';

/**
 * Derive only mission-local display metadata from events. Full-history spend is
 * authoritative only in snapshot.usage_summary / snapshot.spend_usd.
 */
export function computeSpend(events: EventMsg[]): Spend {
  let missions = 0;
  let last = 0;
  for (const event of events) {
    if (!isMissionDone(String(event.type ?? ''))) continue;
    const cost = event.cost_usd;
    if (typeof cost === 'number' && Number.isFinite(cost) && cost >= 0) {
      missions += 1;
      last = cost;
    }
  }
  return { total: 0, missions, last };
}

/** Lifecycle events are never a spend fallback; only the call ledger is. */
export function authoritativeSpend(observed: Spend, settledUsd?: number | null): number {
  void observed;
  return typeof settledUsd === 'number' && Number.isFinite(settledUsd) && settledUsd >= 0
    ? settledUsd
    : 0;
}

export function fraction(value: number, cap: number | null | undefined): number {
  if (!cap || cap <= 0) return 0;
  return Math.min(1, Math.max(0, value / cap));
}
