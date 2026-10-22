import type { BacklogItem } from './types.js';

// The backlog's sinks (`_TERMINAL_STATUSES` in argus/life/memory.py).
const TERMINAL = new Set(['done', 'completed', 'failed', 'aborted', 'skipped', 'superseded']);
const UNDER_WAY = new Set(['pending', 'claimed', 'running', 'in_progress']);

export function isTerminalBacklogItem(item: Pick<BacklogItem, 'status'>): boolean {
  return TERMINAL.has(item.status);
}

/** What the task's last settled attempt came to. A task taken up again has no
 * result yet, and one a newer plan replaced has none of its own; the earlier
 * attempt is not shown as its current result. */
export function currentOutcome(item: BacklogItem): BacklogItem['outcome'] {
  return UNDER_WAY.has(item.status) || item.status === 'superseded' ? undefined : item.outcome;
}

/** A task repeats once it has been taken up again. `iterate` only lets its
 * domain re-arm it after a result falls short, which most domains never do. */
export function isRepeating(item: BacklogItem): boolean {
  return Boolean(item.iterate) && (item.iteration_cycles_done ?? 0) > 0;
}

export function visibleBacklogItems(items: BacklogItem[], history: boolean): BacklogItem[] {
  return items.filter((item) => isTerminalBacklogItem(item) === history);
}
