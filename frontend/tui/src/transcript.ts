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
  // The live list may legitimately contain two identical rapid-fire turns.
  // Match the newest durable transcript rows to live rows by occurrence count,
  // not by timestamp: WebAPI cold start can delay the first durable row well
  // beyond the old two-second heuristic. Older unmatched history is retained.
  const replay = transcriptEvents(turns);
  const liveCounts = new Map<string, number>();
  for (const event of liveEvents) {
    const type = String(event.type ?? '');
    if (type !== 'ui.operator' && type !== 'ui.argus') continue;
    const key = `${type}\u0000${String(event.text ?? '')}`;
    liveCounts.set(key, (liveCounts.get(key) ?? 0) + 1);
  }
  const keepReplay = new Array(replay.length).fill(true);
  for (let index = replay.length - 1; index >= 0; index -= 1) {
    const event = replay[index];
    const key = `${String(event.type ?? '')}\u0000${String(event.text ?? '')}`;
    const count = liveCounts.get(key) ?? 0;
    if (count > 0) {
      keepReplay[index] = false;
      liveCounts.set(key, count - 1);
    }
  }
  const merged = replay
    .filter((_event, index) => keepReplay[index])
    .reduce(
      (current, event) => reduceOperatorEvent(
        current,
        event,
        Number.MAX_SAFE_INTEGER,
      ),
      [...liveEvents],
    ).sort(
    (left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0),
  );
  return merged.length > maxEvents
    ? merged.slice(merged.length - maxEvents)
    : merged;
}
