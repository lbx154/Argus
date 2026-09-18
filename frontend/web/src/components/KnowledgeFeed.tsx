import { useQuery } from '@tanstack/react-query';
import { api, type KnowledgeEvent, type KnowledgeEventKind, type WikiLibraryItem, type WikiScope } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';

/**
 * The host's knowledge journal as a live feed: what Argus learned after a
 * mission, what it read back into a prompt, and what it promoted to a shared
 * level. Shared by the knowledge browser (its default tab) and the sidebar entry.
 */

export const KNOWLEDGE_FEED_LIMIT = 50;
/** Consecutive "recalled" lines from one role this close together fold into one row. */
export const RECALL_GROUP_WINDOW_S = 60;
/** A learned or promoted page younger than this shows as "just learned" in the sidebar. */
export const JUST_LEARNED_WINDOW_S = 24 * 3600;

export const knowledgeFeedQueryKey = (limit: number) => ['knowledge-feed', limit] as const;

export function useKnowledgeFeed(enabled = true, limit = KNOWLEDGE_FEED_LIMIT) {
  return useQuery({
    queryKey: knowledgeFeedQueryKey(limit),
    queryFn: ({ signal }) => api.knowledgeFeed(limit, signal),
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
}

export const knowledgeKindLabels: Record<'en' | 'zh', Record<KnowledgeEventKind, string>> = {
  en: { learned: 'Learned', recalled: 'Recalled', promoted: 'Promoted' },
  zh: { learned: '学到了', recalled: '读取了', promoted: '提升了' },
};
const scopeLabels: Record<'en' | 'zh', Record<WikiScope, string>> = {
  en: { private: 'About you', global: 'Global', vertical: 'Vertical', project: 'Project' },
  zh: { private: '关于你', global: '全局', vertical: '垂直领域', project: '项目' },
};

/** One feed row: a single journal line, or several recalls by one role folded together. */
export type KnowledgeFeedRow =
  | { kind: 'single'; event: KnowledgeEvent }
  | { kind: 'recalls'; ts: number; role: string; events: KnowledgeEvent[] };

/** Fold consecutive "recalled" lines from the same role within the window into one row; newest first. */
export function groupKnowledgeEvents(events: KnowledgeEvent[], window = RECALL_GROUP_WINDOW_S): KnowledgeFeedRow[] {
  const ordered = [...events].sort((a, b) => b.ts - a.ts);
  const rows: KnowledgeFeedRow[] = [];
  for (const event of ordered) {
    const previous = rows[rows.length - 1];
    if (event.kind === 'recalled' && previous?.kind === 'recalls' && previous.role === event.role && previous.ts - event.ts <= window) {
      previous.events.push(event);
      continue;
    }
    rows.push(event.kind === 'recalled' ? { kind: 'recalls', ts: event.ts, role: event.role, events: [event] } : { kind: 'single', event });
  }
  return rows;
}

/** The catalog page a journal line points at, when the browser can open it. */
export function resolveKnowledgeItem(event: Pick<KnowledgeEvent, 'scope' | 'vertical' | 'path' | 'source_project'>, items: WikiLibraryItem[], sid: string | null = null): WikiLibraryItem | null {
  if (!event.path) return null;
  if (event.scope === 'project') {
    // Project pages are only in the catalog for the project being viewed.
    if (sid && event.source_project && event.source_project !== sid) return null;
    return items.find(item => item.scope === 'project' && item.path === event.path) ?? null;
  }
  return items.find(item => item.scope === event.scope && item.vertical === event.vertical && item.path === event.path) ?? null;
}

/** The newest page learned or promoted within the window, for the sidebar's "just learned" row. */
export function latestLearned(events: KnowledgeEvent[], now = Date.now() / 1000, window = JUST_LEARNED_WINDOW_S): KnowledgeEvent | null {
  return [...events].sort((a, b) => b.ts - a.ts)
    .find(event => (event.kind === 'learned' || event.kind === 'promoted') && now - event.ts <= window && event.title) ?? null;
}

const badgeTone: Record<KnowledgeEventKind, string> = {
  learned: 'bg-blue/10 text-blue',
  recalled: 'bg-line/60 text-ink-dim',
  promoted: 'bg-ok/10 text-ok',
};

export function KnowledgeFeedList({ events, items, sid, query = '', onSelect, selected }: {
  events: KnowledgeEvent[];
  items: WikiLibraryItem[];
  sid: string | null;
  query?: string;
  onSelect: (item: WikiLibraryItem) => void;
  selected?: WikiLibraryItem | null;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const kinds = knowledgeKindLabels[zh ? 'zh' : 'en'];
  const scopes = scopeLabels[zh ? 'zh' : 'en'];
  const relative = (ts: number) => formatRelativeTime(ts, zh ? 'zh-CN' : 'en');
  const needle = query.trim().toLocaleLowerCase();
  const visible = needle
    ? events.filter(event => `${event.title} ${event.path} ${event.vertical} ${event.source_project} ${event.role} ${event.note}`.toLocaleLowerCase().includes(needle))
    : events;
  const rows = groupKnowledgeEvents(visible);
  const where = (event: KnowledgeEvent) => [
    scopes[event.scope] ?? event.scope,
    event.vertical,
    event.source_project ? `${zh ? '来自 ' : 'from '}${event.source_project}` : '',
    event.role,
  ].filter(Boolean).join(' · ');
  const isSelected = (item: WikiLibraryItem | null) => Boolean(item && selected && item.scope === selected.scope && item.vertical === selected.vertical && item.path === selected.path);
  const titleButton = (event: KnowledgeEvent, className: string) => {
    const item = resolveKnowledgeItem(event, items, sid);
    const label = event.title || event.path;
    return item
      ? <button type="button" onClick={() => onSelect(item)} aria-pressed={isSelected(item)} data-feed-title
        className={`${className} text-left hover:text-blue ${isSelected(item) ? 'text-blue' : 'text-ink'}`}>{label}</button>
      : <span data-feed-title className={`${className} text-ink`} title={event.path}>{label}</span>;
  };

  if (rows.length === 0) {
    return <p className="p-3 text-sm leading-relaxed text-ink-faint">{needle
      ? (zh ? '没有匹配的学习动态。' : 'No feed lines match the search.')
      : (zh ? '还没有学习动态。Argus 在任务后反思写下教训、把已有知识读进提示、或把评审通过的页面提升到共享层时，都会在这里留下一行。'
        : 'Nothing learned yet. A line appears here whenever Argus writes a lesson after a mission, reads existing knowledge into a prompt, or promotes a reviewed page to a shared level.')}</p>;
  }
  return <ol className="space-y-1" aria-label={zh ? '学习动态' : 'Learning feed'}>
    {rows.map(row => row.kind === 'single'
      ? <li key={`${row.event.ts}/${row.event.kind}/${row.event.path}`} className="rounded-md px-3 py-2.5" data-feed-row={row.event.kind}>
        <span className="flex items-baseline gap-2 text-[11px] text-ink-faint">
          <time dateTime={new Date(row.event.ts * 1000).toISOString()}>{relative(row.event.ts)}</time>
          <span className={`rounded px-1.5 py-px text-[10px] font-medium ${badgeTone[row.event.kind] ?? badgeTone.learned}`}>{kinds[row.event.kind] ?? row.event.kind}</span>
        </span>
        {titleButton(row.event, 'mt-1 block w-full break-words text-sm font-medium')}
        <span className="mt-1 block text-[11px] text-ink-faint">{where(row.event)}</span>
        {row.event.note && <span className="mt-1 block text-xs leading-relaxed text-ink-dim">{row.event.note}</span>}
      </li>
      : <li key={`${row.ts}/recalls/${row.role}`} className="rounded-md px-3 py-2.5" data-feed-row="recalls">
        <span className="flex items-baseline gap-2 text-[11px] text-ink-faint">
          <time dateTime={new Date(row.ts * 1000).toISOString()}>{relative(row.ts)}</time>
          <span className={`rounded px-1.5 py-px text-[10px] font-medium ${badgeTone.recalled}`}>{kinds.recalled}</span>
        </span>
        <span className="mt-1 block text-sm font-medium text-ink" data-feed-title>
          {row.events.length === 1
            ? (row.events[0].title || row.events[0].path)
            : (zh ? `读取了 ${row.events.length} 条已有知识` : `Recalled ${row.events.length} existing pages`)}
        </span>
        <span className="mt-1 block text-[11px] text-ink-faint">{where(row.events[0])}</span>
        {row.events.length > 1 && <span className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs">
          {row.events.map(event => <span key={`${event.ts}/${event.scope}/${event.vertical}/${event.path}`}>{titleButton(event, 'inline text-xs text-ink-dim')}</span>)}
        </span>}
      </li>)}
  </ol>;
}
