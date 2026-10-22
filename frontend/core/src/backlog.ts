import type { BacklogItem } from './types.js';

const TERMINAL = new Set(['done', 'completed', 'failed', 'skipped']);
const UNDER_WAY = new Set(['pending', 'claimed', 'running', 'in_progress']);

export function isTerminalBacklogItem(item: BacklogItem): boolean {
  return TERMINAL.has(item.status);
}

/** What the task's last settled attempt came to. A task taken up again has no
 * result yet; the earlier one is not shown as its current result. */
export function currentOutcome(item: BacklogItem): BacklogItem['outcome'] {
  return UNDER_WAY.has(item.status) ? undefined : item.outcome;
}

export function visibleBacklogItems(items: BacklogItem[], history: boolean): BacklogItem[] {
  return items.filter((item) => isTerminalBacklogItem(item) === history);
}
