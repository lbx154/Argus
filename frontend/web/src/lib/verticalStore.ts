import { ApiError } from '../../../core/src/http';
import type { VerticalAction, VerticalKind, VerticalOperation, VerticalRow } from '../../../core/src/types';

/**
 * Pure rules behind the vertical store page: which rows a filter keeps, which
 * tags exist, which buttons a card may show, how a size or an operation reads.
 * Nothing here touches React, fetch or the DOM.
 */

export type KindFilter = 'all' | 'installed' | 'available' | 'builtin';
export const KIND_FILTERS: readonly KindFilter[] = ['all', 'installed', 'available', 'builtin'];

export const VERTICAL_ACTIONS: readonly VerticalAction[] = ['install', 'update', 'enable', 'disable', 'uninstall'];

/** Actions that change what is on disk; a hosted workspace never offers them. */
const HOST_OWNED_ACTIONS: ReadonlySet<VerticalAction> = new Set(['install', 'update', 'uninstall']);

export interface StoreFilter {
  query: string;
  kind: KindFilter;
  tag: string | null;
}

export const EMPTY_FILTER: StoreFilter = { query: '', kind: 'all', tag: null };

/** The hosted trial build sets this flag; the host also reports it per payload. */
export function hostedTrialBuild(env: Record<string, string | boolean | undefined> = import.meta.env): boolean {
  return env.VITE_ARGUS_HOSTED_TRIAL === '1';
}

/** "Installed" means present on disk and usable, so bundled verticals count; "Built-in" stays its own group. */
export function matchesKind(kind: VerticalKind, filter: KindFilter): boolean {
  switch (filter) {
    case 'all': return true;
    case 'installed': return kind === 'installed' || kind === 'package';
    case 'available': return kind === 'available';
    case 'builtin': return kind === 'builtin';
  }
}

export function matchesQuery(row: VerticalRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [row.name, row.purpose, row.purpose_zh ?? '', ...row.tags, ...row.requires]
    .join('\n')
    .toLowerCase();
  return haystack.includes(needle);
}

export function filterRows(rows: VerticalRow[], filter: StoreFilter): VerticalRow[] {
  return rows.filter((row) =>
    matchesKind(row.kind, filter.kind)
    && (!filter.tag || row.tags.includes(filter.tag))
    && matchesQuery(row, filter.query));
}

/** Every tag any row carries, once, in a stable alphabetical order. */
export function deriveTags(rows: VerticalRow[]): string[] {
  const tags = new Set<string>();
  for (const row of rows) for (const tag of row.tags) if (tag.trim()) tags.add(tag.trim());
  return [...tags].sort((a, b) => a.localeCompare(b));
}

/** The buttons a card shows: exactly the server's list, minus what a host keeps for itself. */
export function visibleActions(row: VerticalRow, hosted: boolean): VerticalAction[] {
  const owned = hosted || row.managed_by_host;
  return row.actions.filter((action) => VERTICAL_ACTIONS.includes(action) && !(owned && HOST_OWNED_ACTIONS.has(action)));
}

export const actionLabelKey = (action: VerticalAction | string) => `verticals.action.${action}`;
export const kindLabelKey = (kind: VerticalKind) => `verticals.kind.${kind}`;
export const filterLabelKey = (filter: KindFilter) => `verticals.filter.${filter}`;
export const operationLabelKey = (action: string) => `verticals.operation.${action}`;

/** purpose_zh when the interface is Chinese and the catalog carries one, else purpose. */
export function purposeText(row: Pick<VerticalRow, 'purpose' | 'purpose_zh'>, locale: string): string {
  if (locale === 'zh-CN' && row.purpose_zh && row.purpose_zh.trim()) return row.purpose_zh;
  return row.purpose;
}

/** "1.2 MB" for a known size, an em dash for an unknown one. */
export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

/** The server's integer percent (0–100) for a determinate bar, or null while the job has not reported a step. */
export function progressPercent(operation: VerticalOperation | null | undefined): number | null {
  if (!operation || operation.status !== 'running') return null;
  const value = Number(operation.progress);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}

export const isRunning = (row: Pick<VerticalRow, 'operation'>) => row.operation?.status === 'running';

export function anyRunning(rows: VerticalRow[]): boolean {
  return rows.some(isRunning);
}

/** Follow a running job closely while the page is open, more slowly otherwise; stop entirely when there is nothing to follow. */
export function pollInterval(open: boolean, running: boolean): number {
  if (open) return running ? 1_500 : 4_000;
  return running ? 4_000 : 0;
}

/** The service's own sentence for a failed request, without the request line the ApiError message carries. */
export function failureDetail(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail;
    const match = /^[A-Z]+ \S+ → \S+: ([\s\S]+)$/.exec(error.message);
    return match ? match[1] : '';
  }
  return error instanceof Error ? error.message : String(error ?? '');
}

/** A 409 means the store refused for a reason the user can override with force (for example, projects still use it). */
export function isStoreConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/** 404 on the list means the backend predates the store entirely. */
export function isStoreMissing(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function hasStoreCapability(capabilities: readonly string[] | undefined, capability: string): boolean {
  return Array.isArray(capabilities) && capabilities.includes(capability);
}

/** The "requires" chips only jump to rows that exist in the payload. */
export function knownNames(rows: VerticalRow[]): Set<string> {
  return new Set(rows.map((row) => row.name));
}
