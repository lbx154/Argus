import { replaceEqualDeep } from "@tanstack/react-query";
import type { Snapshot } from "../api";
import { ACTIVE, type Dataset } from "./model";

export interface MapHistoryInfo {
  task_count: number;
  event_bytes: number;
  requires_choice: boolean;
  current_task_id: string | null;
  current_task_ts: number;
  current_event_ts: number;
}

export interface MapSelection {
  mode: "full" | "current" | "off";
  since?: number;
  eventSince?: number;
  taskId?: string;
}

export function parseMapSelection(raw: string | null): MapSelection | null {
  try {
    const value = JSON.parse(raw || "null");
    if (!value || !["full", "current", "off"].includes(value.mode)) return null;
    if (value.mode === "current" && ![value.since, value.eventSince].every(
      (n) => typeof n === "number" && Number.isFinite(n) && n >= 0,
    )) return null;
    return value;
  } catch { return null; }
}

export function mergeMapProgress(previous: Dataset | undefined, next: Dataset): Dataset {
  if (!previous || previous.id !== next.id || next.reset_history) return next;
  const tasks = new Map(
    (!next.incremental || next.tasks_complete ? [] : previous.tasks).map((t) => [t.id, t]),
  );
  for (const task of next.tasks) tasks.set(task.id, task);
  for (const id of next.removed_task_ids || []) tasks.delete(id);
  // Team rows are mutable taskboard observations. A full refresh replaces
  // them, while ordinary historical events remain available across windows.
  const events = new Map(previous.events
    .filter((event) => (next.incremental && !next.team_events_complete) || event.type !== 'team.task')
    .map((event) => [event.id, event]));
  for (const event of next.events) events.set(event.id, event);
  for (const id of next.removed_event_ids || []) events.delete(id);
  return replaceEqualDeep(previous, {
    ...previous, ...next,
    tasks: [...tasks.values()].sort((a, b) => (a.ts || 0) - (b.ts || 0) || a.id.localeCompare(b.id)),
    events: [...events.values()].filter((e) => tasks.has(e.item_id)),
  });
}

export function livePollInterval(
  approved: boolean,
  data: Pick<Dataset, "history_loading" | "events"> | undefined,
  snapshot: Snapshot,
): number | false {
  if (!approved) return false;
  if (data?.history_loading) return 400;
  // SSE invalidation is the primary refresh trigger while work is active;
  // this interval is only a safety net for missed events.
  return !mapIsPaused(snapshot) || data?.events.some(
    (event) => event.type === "team.task" && ACTIVE.has(event.status || ""),
  ) ? 15_000 : false;
}

export function mapIsPaused(snapshot: Snapshot): boolean {
  if (!snapshot.daemon.alive) return true;
  if (snapshot.continuous?.enabled === false && /pause|stop/i.test(snapshot.continuous.done_reason || ""))
    return true;
  return snapshot.daemon.health?.state === "stopped";
}
