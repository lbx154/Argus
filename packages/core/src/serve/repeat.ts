// A role cannot wake itself, so the operator may have Argus send a project a message of theirs at
// the times they choose, as if they wrote it then: each day at a time, or every few hours. The
// chief may also be told when another project's work comes to rest, which Argus knows as it
// happens. Each is the operator's message and nothing more: Argus does not watch what comes of
// it. A time that passed while Argus was not running is not made up for.

import type { Every, Project, Repeat, Status, Store } from '../store.js';
import { HttpError } from './index.js';

/** The operator's daily message, checked: a time of day (HH:MM, UTC) and the words. */
export function repeatOf(body: Record<string, unknown>): Repeat {
  const at = String(body.at ?? '').trim();
  const text = String(body.text ?? '').trim();
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(at)) throw new HttpError(400, 'at is a time of day, HH:MM, in UTC', 'invalid_wake');
  if (!text) throw new HttpError(400, 'text is the message to send', 'invalid_wake');
  return { at, text };
}

/** The operator's message every few hours, checked: a whole number of hours, 1 to 24, and the words. */
export function everyOf(body: Record<string, unknown>): Every {
  const hours = Number(body.hours);
  const text = String(body.text ?? '').trim();
  if (!Number.isInteger(hours) || hours < 1 || hours > 24) throw new HttpError(400, 'hours is a whole number from 1 to 24', 'invalid_wake');
  if (!text) throw new HttpError(400, 'text is the message to send', 'invalid_wake');
  return { hours, text };
}

/** How long from `now` till the day's next `at` (HH:MM, UTC), in milliseconds; a whole day when it is now. */
export function untilNext(at: string, now: Date): number {
  const [hours, minutes] = at.split(':').map(Number) as [number, number];
  const next = new Date(now);
  next.setUTCHours(hours, minutes, 0, 0);
  if (next.getTime() <= now.getTime()) next.setUTCDate(next.getUTCDate() + 1);
  return next.getTime() - now.getTime();
}

/** How long from `now` till the next whole `hours` counted from midnight UTC, so that setting the
 * timers again never puts the time off. */
export function untilEvery(hours: number, now: Date): number {
  const period = hours * 3_600_000;
  return period - now.getTime() % period;
}

/** The words the chief is sent when another project's work rests: the operator's, then which project and how. */
export const restLetter = (words: string, project: Project, status: Status): string =>
  `${words}\n\n${project.name || project.request.split('\n')[0] || project.id} (${project.id}): ${status}${project.error ? `, ${project.error}` : ''}`;

/** The states a project's work comes to rest in. */
export const RESTING: readonly Status[] = ['idle', 'asking', 'failed'];

/** One timer for each project's daily message and each one's every few hours; `arm` sets them again from the records, after a change. */
export function repeating(store: Store, send: (id: string, text: string) => void): { arm: () => void; close: () => void } {
  let timers = new Set<NodeJS.Timeout>();
  const close = (): void => {
    for (const timer of timers) clearTimeout(timer);
    timers = new Set();
  };
  const set = (id: string, schedule: (project: Project, now: Date) => { wait: number; text: string } | undefined, wait: number): void => {
    const group = timers;
    const timer = setTimeout(() => {
      if (!group.delete(timer)) return;
      // Read again when it fires: the operator may have changed the words since.
      let due: { wait: number; text: string } | undefined;
      try { due = schedule(store.load(id), new Date()); } catch { due = undefined; }
      if (!due) return;
      if (due.text) send(id, due.text);
      if (timers === group) set(id, schedule, due.wait);
    }, wait);
    timer.unref?.();
    group.add(timer);
  };
  const byDay = (project: Project, now: Date) => project.repeat && { wait: untilNext(project.repeat.at, now), text: project.repeat.text };
  const byHours = (project: Project, now: Date) => project.every && { wait: untilEvery(project.every.hours, now), text: project.every.text };
  const arm = (): void => {
    close();
    const now = new Date();
    for (const { id, repeat, every } of store.list()) {
      if (repeat) set(id, byDay, untilNext(repeat.at, now));
      if (every) set(id, byHours, untilEvery(every.hours, now));
    }
  };
  return { arm, close };
}
