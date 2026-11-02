// A role cannot wake itself, so the operator may have Argus send a project the same message every
// day at a time they choose, as if they wrote it then: the chief's daily letter starts this way.
// It is the operator's message and nothing more: Argus does not watch what comes of it. A time
// that passed while Argus was not running is not made up for.

import type { Repeat, Store } from '../store.js';
import { HttpError } from './index.js';

/** The operator's daily message, checked: a time of day (HH:MM, UTC) and the words. */
export function repeatOf(body: Record<string, unknown>): Repeat {
  const at = String(body.at ?? '').trim();
  const text = String(body.text ?? '').trim();
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(at)) throw new HttpError(400, 'at is a time of day, HH:MM, in UTC', 'invalid_repeat');
  if (!text) throw new HttpError(400, 'text is the message to send each day', 'invalid_repeat');
  return { at, text };
}

/** How long from `now` till the day's next `at` (HH:MM, UTC), in milliseconds; a whole day when it is now. */
export function untilNext(at: string, now: Date): number {
  const [hours, minutes] = at.split(':').map(Number) as [number, number];
  const next = new Date(now);
  next.setUTCHours(hours, minutes, 0, 0);
  if (next.getTime() <= now.getTime()) next.setUTCDate(next.getUTCDate() + 1);
  return next.getTime() - now.getTime();
}

/** One timer for each project with a daily message; `arm` sets them again from the records, after a change. */
export function repeating(store: Store, send: (id: string, text: string) => void): { arm: () => void; close: () => void } {
  const timers = new Map<string, NodeJS.Timeout>();
  const close = (): void => {
    for (const timer of timers.values()) clearTimeout(timer);
    timers.clear();
  };
  const arm = (): void => {
    close();
    for (const { id, repeat } of store.list()) {
      if (!repeat) continue;
      const timer = setTimeout(() => {
        // Read again when it fires: the operator may have changed the words since.
        let words: Repeat | undefined;
        try { words = store.load(id).repeat; } catch { words = undefined; }
        if (words) send(id, words.text);
        arm();
      }, untilNext(repeat.at, new Date()));
      timer.unref?.();
      timers.set(id, timer);
    }
  };
  return { arm, close };
}
