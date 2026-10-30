import { replaceEqualDeep } from "@tanstack/react-query";
import type { Dataset } from "./model";

/** Each response carries all cards and the letters after its line cursor. */
export function mergeMapProgress(previous: Dataset | undefined, next: Dataset): Dataset {
  if (!previous || previous.id !== next.id) return next;
  const tasks = new Set(next.tasks.map(task => task.id));
  const events = new Map(previous.events.map(event => [event.id, event]));
  for (const event of next.events) events.set(event.id, event);
  return replaceEqualDeep(previous, { ...next,
    events: [...events.values()].filter(event => tasks.has(event.item_id)).sort((a, b) => a.ts - b.ts),
  });
}
