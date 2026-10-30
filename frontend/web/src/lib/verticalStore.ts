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

export interface StoreFilter {
  query: string;
  kind: KindFilter;
  tag: string | null;
  routingPath?: string;
}

export const EMPTY_FILTER: StoreFilter = { query: '', kind: 'all', tag: null };

// Customer-facing descriptions of the bundled verticals; the catalog's full
// description remains available in each card's technical details.
export const BUNDLED_GUIDES: Record<string, { title: string; purpose: string; input: string; output: string }> = {
  research: { title: '学术研究', purpose: '从研究问题出发，逐步完成分析、实验与论文写作。', input: '研究问题、已有资料和目标范围', output: '研究结论、实验记录或论文，按确认的范围完成' },
  software: { title: '软件开发', purpose: '修复项目问题、开发功能，并检查修改是否符合要求。', input: '代码项目、问题描述或功能要求', output: '代码修改、检查结果和使用说明' },
  argus_maintenance: { title: 'Argus 维护', purpose: '修复或改进 Argus 本身，适合维护服务的开发者。', input: 'Argus 问题、相关记录和修改范围', output: '修复、回归检查与发行检查结果' },
  learning: { title: '资料整理与学习', purpose: '把你提供的材料整理为可再次使用的知识和技能。', input: '需要整理的资料与保存范围', output: '新增、更新或归档的知识与技能内容' },
};

const VERTICAL_TITLES: Record<string, string> = {
  chip_design: '芯片设计', classical_poetry: '古典诗词', digital_circuit: '数字电路',
  digital_circuit_benchmark: '数字电路评测', fiction_writing: '小说创作', kernel_engineering: '计算性能优化',
  kernelbench: 'GPU 内核评测', literary_editor: '文学编辑', materials: '材料科学', math: '数学研究',
  math_synth: '数学推理数据', medical: '生物医药证据研究',
  modern_poetry: '现代诗创作', nanochat: '语言模型训练优化', nanogpt_speedrun: '语言模型训练加速',
};

export function verticalTitle(row: Pick<VerticalRow, 'name' | 'kind'>, locale: string): string {
  if (locale !== 'zh-CN') return row.name.replaceAll('_', ' ');
  return (row.kind === 'builtin' ? BUNDLED_GUIDES[row.name]?.title : undefined) ?? VERTICAL_TITLES[row.name] ?? row.name.replaceAll('_', ' ');
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
  const guide = row.kind === 'builtin' ? BUNDLED_GUIDES[row.name] : undefined;
  const haystack = [row.name, verticalTitle(row, 'zh-CN'), guide?.purpose ?? '', row.purpose, row.purpose_zh ?? '', ...(row.routing_path ?? []), ...row.tags, ...row.requires]
    .join('\n')
    .toLowerCase();
  return haystack.includes(needle);
}

export function filterRows(rows: VerticalRow[], filter: StoreFilter): VerticalRow[] {
  return rows.filter((row) =>
    matchesKind(row.kind, filter.kind)
    && (!filter.tag || row.tags.includes(filter.tag))
    && (!filter.routingPath || (row.routing_path ?? []).join('/') === filter.routingPath
      || (row.routing_path ?? []).join('/').startsWith(`${filter.routingPath}/`))
    && matchesQuery(row, filter.query));
}

export function deriveRoutingPaths(rows: VerticalRow[]): string[] {
  const paths = new Set<string>();
  for (const row of rows) {
    const path = row.routing_path ?? [];
    for (let depth = 1; depth <= path.length; depth++) paths.add(path.slice(0, depth).join('/'));
  }
  return [...paths].sort();
}

/** Every tag any row carries, once, in a stable alphabetical order. */
export function deriveTags(rows: VerticalRow[]): string[] {
  const tags = new Set<string>();
  for (const row of rows) for (const tag of row.tags) if (tag.trim()) tags.add(tag.trim());
  return [...tags].sort((a, b) => a.localeCompare(b));
}

/** The buttons a card shows: exactly the server's allowed actions. */
export function visibleActions(row: VerticalRow): VerticalAction[] {
  return row.actions.filter((action) => VERTICAL_ACTIONS.includes(action));
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
