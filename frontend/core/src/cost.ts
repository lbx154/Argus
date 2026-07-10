import type { EventMsg } from './types.js';

export interface Spend {
  /** Settled spend observed in the current event window. */
  total: number;
  /** Completed missions represented in the current event window. */
  missions: number;
  /** Most recent completed mission's cost. */
  last: number;
}

const isMissionDone = (type: string): boolean =>
  type === 'life.mission.completed' || type === 'mission.completed';

/**
 * Sum disjoint settled cost events. Planner costs and mission costs are emitted
 * separately, so counting only mission-complete rows under-reports spend.
 */
export function computeSpend(events: EventMsg[]): Spend {
  let total = 0;
  let missions = 0;
  let last = 0;
  for (const event of events) {
    const cost = event.cost_usd;
    if (typeof cost !== 'number' || !Number.isFinite(cost) || cost <= 0) continue;
    total += cost;
    if (isMissionDone(String(event.type ?? ''))) {
      missions += 1;
      last = cost;
    }
  }
  return { total, missions, last };
}

/** Prefer the server's full-history settled total over a replay-window sum. */
export function authoritativeSpend(observed: Spend, settledUsd?: number): number {
  return typeof settledUsd === 'number' && Number.isFinite(settledUsd) && settledUsd >= 0
    ? settledUsd
    : observed.total;
}

export function fraction(value: number, cap: number | null | undefined): number {
  if (!cap || cap <= 0) return 0;
  return Math.min(1, Math.max(0, value / cap));
}
