import type { BacklogSummary } from './types.js';

// The backlog's sinks (`_TERMINAL_STATUSES` in argus/life/memory.py).
const TERMINAL = new Set(['done', 'completed', 'failed', 'aborted', 'skipped', 'superseded']);

export function isTerminalBacklogItem(item: Pick<BacklogSummary, 'status'>): boolean {
  return TERMINAL.has(item.status);
}

export function visibleBacklogItems<T extends BacklogSummary>(items: T[], history: boolean): T[] {
  return items.filter((item) => isTerminalBacklogItem(item) === history);
}
