import type { BacklogItem } from './types.js';

const TERMINAL = new Set(['done', 'completed', 'failed', 'skipped']);

export function isTerminalBacklogItem(item: BacklogItem): boolean {
  return TERMINAL.has(item.status);
}

export function visibleBacklogItems(items: BacklogItem[], history: boolean): BacklogItem[] {
  return items.filter((item) => isTerminalBacklogItem(item) === history);
}
