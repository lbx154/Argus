import { useQuery } from '@tanstack/react-query';
import { api, type KnowledgeEvent, type SkillLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { formatCalendarTime } from '../lib/format';

export const KNOWLEDGE_FEED_LIMIT = 50;
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

/** The team's changed skill files, newest first. */
export function KnowledgeFeedList({ events, items, query = '', onSelect, selected }: {
  events: KnowledgeEvent[];
  items: SkillLibraryItem[];
  query?: string;
  onSelect: (item: SkillLibraryItem) => void;
  selected?: SkillLibraryItem | null;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const needle = query.trim().toLocaleLowerCase();
  const rows = [...events].filter(event => !needle || `${event.title} ${event.path}`.toLocaleLowerCase().includes(needle))
    .sort((a, b) => b.ts - a.ts);
  if (rows.length === 0) {
    return <p className="p-3 text-sm leading-relaxed text-ink-faint">{needle
      ? (zh ? '没有匹配的学习动态。' : 'No feed lines match the search.')
      : (zh ? '还没有学习动态。' : 'Nothing learned yet.')}</p>;
  }
  return <ol className="space-y-1" aria-label={zh ? '学习动态' : 'Learning feed'}>
    {rows.map(event => {
      const item = items.find(item => item.source !== 'bundled' && item.path === event.path);
      const active = Boolean(item && selected && item.library === selected.library && item.path === selected.path);
      const label = event.title || event.path;
      return <li key={`${event.ts}/${event.path}`} className="rounded-md px-3 py-2.5" data-feed-row>
        <time className="text-[11px] text-ink-faint" dateTime={new Date(event.ts * 1000).toISOString()}>
          {formatCalendarTime(event.ts, zh ? 'zh-CN' : 'en')}
        </time>
        {item
          ? <button type="button" onClick={() => onSelect(item)} aria-pressed={active} data-feed-title
            className={`mt-1 block w-full break-words text-left text-sm font-medium hover:text-blue ${active ? 'text-blue' : 'text-ink'}`}>{label}</button>
          : <span data-feed-title className="mt-1 block break-words text-sm font-medium text-ink" title={event.path}>{label}</span>}
      </li>;
    })}
  </ol>;
}
