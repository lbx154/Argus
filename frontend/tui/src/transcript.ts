import type { EventMsg, Turn } from './api.js';

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
