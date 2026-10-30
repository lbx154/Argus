import { replaceEqualDeep } from "@tanstack/react-query";
import type { Dataset } from "./model";

export interface MapSelection {
  mode: "full" | "current" | "off";
  since?: number;
  eventSince?: number;
  taskId?: string;
}

export function mergeMapProgress(previous: Dataset | undefined, next: Dataset): Dataset {
  if (!previous || previous.id !== next.id || next.reset_history) return next;
  const tasks = new Map(
    (!next.incremental || next.tasks_complete ? [] : previous.tasks).map((t) => [t.id, t]),
  );
  for (const task of next.tasks) tasks.set(task.id, task);
  for (const id of next.removed_task_ids || []) tasks.delete(id);
  // Historical events remain available across windows.
  const events = new Map(previous.events.map((event) => [event.id, event]));
  for (const event of next.events) events.set(event.id, event);
  return replaceEqualDeep(previous, {
    ...previous, ...next,
    tasks: [...tasks.values()].sort((a, b) => (a.ts || 0) - (b.ts || 0) || a.id.localeCompare(b.id)),
    // A page brings what happened earlier as well as later; keep the order it happened in.
    events: [...events.values()].filter((e) => tasks.has(e.item_id)).sort((a, b) => a.ts - b.ts),
  });
}
