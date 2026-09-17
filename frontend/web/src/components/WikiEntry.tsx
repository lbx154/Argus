import { BookMarked } from 'lucide-react';
import type { WikiLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';
import { recentWikiPages, useWikiLibrary, wikiScopeLabels } from './WikiLibrary';

const RECENT_LIMIT = 5;
const labels = {
  en: {
    title: 'Knowledge base',
    empty: 'No knowledge pages yet',
    unavailable: 'Knowledge base unavailable',
    count: (n: number) => `${n} ${n === 1 ? 'page' : 'pages'}`,
  },
  zh: {
    title: '知识库',
    empty: '尚无知识页面',
    unavailable: '知识库暂时无法加载',
    count: (n: number) => `${n} 页`,
  },
};

/**
 * Keep the knowledge base visible beside the project: page count, the newest
 * pages across global, vertical and project levels, and a way into the full browser.
 */
export function WikiEntry({ sid, onOpen, compact = false, visible = true }: {
  sid: string | null;
  onOpen?: (page?: WikiLibraryItem) => void;
  compact?: boolean;
  visible?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const scopeNames = wikiScopeLabels[zh ? 'zh' : 'en'];
  const wiki = useWikiLibrary(sid, visible && Boolean(onOpen));
  const pages = wiki.data?.items ?? [];
  const recent = recentWikiPages(pages).slice(0, RECENT_LIMIT);
  const where = (page: WikiLibraryItem) => page.scope === 'vertical' && page.vertical ? page.vertical : scopeNames[page.scope];
  const when = (page: WikiLibraryItem) => formatRelativeTime(page.updated_at, zh ? 'zh-CN' : 'en');
  if (!onOpen) return null;
  return <section className={`shrink-0 border-t border-line/60 ${compact ? 'py-3' : 'mx-3 py-2'}`} data-wiki-entry>
    <button type="button" onClick={() => onOpen()} title={names.title} aria-label={names.title}
      className={`relative flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm text-ink-dim hover:bg-bg ${compact ? 'justify-center' : ''}`}>
      <BookMarked className="h-4 w-4 shrink-0" />
      {!compact && names.title}
      {pages.length > 0 && <span aria-label={names.count(pages.length)}
        className={`rounded-full bg-blue/10 px-1.5 text-[10px] leading-4 text-blue ${compact ? 'absolute right-1 top-1' : 'ml-auto'}`}>{pages.length}</span>}
    </button>
    {!compact && wiki.data && recent.length === 0 && <p className="px-2 pb-1 text-xs text-ink-faint">{names.empty}</p>}
    {!compact && recent.length > 0 && <div className="px-2 pb-1">
      {recent.map(page => <button type="button" key={`${page.scope}/${page.vertical}/${page.path}`} onClick={() => onOpen(page)}
        className="block w-full rounded py-1.5 text-left hover:text-blue" title={page.description || page.path}>
        <span className="block truncate text-xs text-ink">{page.title}</span>
        <span className="block text-[10px] text-ink-faint">{where(page)} · {when(page)}</span>
      </button>)}
    </div>}
    {!compact && wiki.isError && <p className="px-2 text-xs text-ink-faint">{names.unavailable}</p>}
  </section>;
}
