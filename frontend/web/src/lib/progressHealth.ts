import type { BacklogItem, Snapshot } from '../../../core/src/types';

const FAILED = new Set(['failed', 'error']);
export const REPEATED_FAILURE_THRESHOLD = 3;
export const NO_ACCEPTED_RESULT_HOURS = 12;

export interface ProgressHealth {
  problem: 'repeated_failures' | 'no_accepted_result';
  /** Identifies this occurrence, so a dismissal does not hide the next one. */
  key: string;
  failures: number;
  hoursWithoutAccepted: number;
  latestFailure?: { title: string; reason: string };
}

function failed(item: BacklogItem): boolean {
  const outcome = (item as BacklogItem & { outcome?: Record<string, unknown> }).outcome;
  return FAILED.has(item.status) || outcome?.execution_status === 'failed';
}

/**
 * Whether work is going in circles: several tasks in a row ended without a
 * result, or a long active stretch has produced nothing that passed review.
 * Measured from task outcomes, not from event activity, so a busy but
 * unproductive run is still noticed.
 *
 * The stretch counts only time the team was working: it starts at the later
 * of the last reviewed result and the last start of work, so a project that
 * was stopped for days and started again an hour ago has been quiet for an
 * hour, not for days. While nothing runs there is no stretch at all.
 */
export function progressHealth(snapshot: Pick<Snapshot, 'backlog' | 'daemon'>, nowSeconds: number): ProgressHealth | null {
  const finished = snapshot.backlog
    .filter((item) => Number(item.finished_ts) > 0)
    .sort((left, right) => Number(right.finished_ts) - Number(left.finished_ts));
  let failures = 0;
  for (const item of finished) {
    if (item.status === 'done') break;
    if (failed(item)) failures += 1;
  }
  const latest = finished.find(failed);
  const latestFailure = latest ? { title: latest.title || latest.objective, reason: String(latest.last_error || '') } : undefined;
  if (failures >= REPEATED_FAILURE_THRESHOLD && latest) {
    return { problem: 'repeated_failures', key: `failures:${latest.id}`, failures, hoursWithoutAccepted: 0, latestFailure };
  }
  // A task left "running" by a stopped daemon is not work going on.
  if (!snapshot.daemon.alive) return null;
  const accepted = Math.max(0, ...snapshot.backlog.filter((item) => item.status === 'done').map((item) => Number(item.finished_ts) || 0));
  const starts = snapshot.backlog.map((item) => Number(item.started_ts) || Number(item.ts) || 0).filter((value) => value > 0);
  const resumed = Date.parse(snapshot.daemon.started_at_iso || '') / 1000;
  const since = Math.max(accepted || (starts.length ? Math.min(...starts) : 0), Number.isFinite(resumed) ? resumed : 0);
  if (!since) return null;
  const hours = (nowSeconds - since) / 3600;
  if (hours < NO_ACCEPTED_RESULT_HOURS) return null;
  return { problem: 'no_accepted_result', key: `quiet:${Math.round(since)}`, failures, hoursWithoutAccepted: Math.floor(hours), latestFailure };
}
