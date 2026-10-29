import type { Snapshot } from '../api';
import { parkReason } from '../../../core/src/missionOutcome';

/** Parks Run does not end: a question waits for its answer, and the Planner retires its own. */
const NOT_ENDED_BY_RUN = new Set(['operator_question', 'research_open']);
const UNDER_WAY = new Set(['running', 'in_progress', 'claimed']);

/**
 * The tasks Run would take up again from their saved progress while nothing
 * runs them: parked on a stop Run ends (`OPERATOR_RESUMED_PARKS` in
 * argus/core/stop_kinds.py), or cut off mid-run when the team stopped.
 */
export function resumableTaskIds(snapshot: Snapshot | null | undefined): Set<string> {
  if (!snapshot || snapshot.daemon.alive) return new Set();
  return new Set(snapshot.backlog.filter((item) => {
    const reason = parkReason(item.status, item.outcome?.park?.reason);
    if (reason !== null) return item.outcome?.resumable !== false && !NOT_ENDED_BY_RUN.has(reason);
    return UNDER_WAY.has(item.status);
  }).map(item => item.id));
}
