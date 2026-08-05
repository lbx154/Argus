import type { EventMsg, Turn } from './api.js';
import { reduceOperatorEvent } from '../../core/src/activity.js';

/** Convert persisted operator/Manager turns into the local events the TUI renders. */
export function transcriptEvents(turns: Turn[]): EventMsg[] {
  const events: EventMsg[] = [];
  for (const turn of turns) {
    const text = String(turn.text ?? '').trim();
    if (!text) continue;
    const type = turn.role === 'operator'
      ? 'ui.operator'
      : turn.role === 'argus'
        ? 'ui.argus'
        : '';
    if (!type) continue;
    events.push({
      type,
      text,
      ...(typeof turn.ts === 'number' ? { ts: turn.ts } : {}),
    } as EventMsg);
  }
  return events;
}

/**
 * Merge a late transcript replay without replacing optimistic rows that Ink
 * may already have committed to terminal scrollback. Live rows go through the
 * reducer first, so a nearby durable duplicate is discarded while the live
 * message id/key survives; sorting happens only after identity reconciliation.
 */
export function mergeTranscriptReplay(
  liveEvents: EventMsg[],
  turns: Turn[],
  maxEvents = 400,
): EventMsg[] {
  const merged = [
    ...liveEvents,
    ...transcriptEvents(turns),
  ].reduce(
    (current, event) => reduceOperatorEvent(
      current,
      event,
      Number.MAX_SAFE_INTEGER,
    ),
    [] as EventMsg[],
  ).sort(
    (left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0),
  );
  return merged.length > maxEvents
    ? merged.slice(merged.length - maxEvents)
    : merged;
}
