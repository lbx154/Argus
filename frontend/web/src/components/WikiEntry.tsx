import { BookMarked, ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import type { WikiLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';
import { useSidebarFold } from '../lib/sidebarFold';
import { libraryVertical } from '../lib/libraryPresentation';
import { knowledgeKindLabels, latestLearned, resolveKnowledgeItem, useKnowledgeFeed } from './KnowledgeFeed';
import { recentWikiPages, useWikiLibrary, wikiScopeLabels } from './WikiLibrary';

const RECENT_LIMIT = 5;
const FEED_LIMIT = 20;
const labels = {
  en: {
    title: 'Knowledge base',
    empty: 'No knowledge pages yet',
    unavailable: 'Knowledge base unavailable',
    justLearned: 'Just learned: ',
    count: (n: number) => `${n} ${n === 1 ? 'page' : 'pages'}`,
  },
  zh: {
    title: '知识库',
    empty: '尚无知识页面',
    unavailable: '知识库暂时无法加载',
    justLearned: '刚学到：',
    count: (n: number) => `${n} 页`,
  },
};

/**
 * Keep the knowledge base visible beside the project: page count, what Argus
 * just learned, the newest pages across global, vertical and project levels,
 * and a way into the full browser.
 */
export function WikiEntry({ sid, onOpen, compact = false, visible = true, defaultExpanded = false }: {
  sid: string | null;
  onOpen?: (page?: WikiLibraryItem) => void;
  compact?: boolean;
  visible?: boolean;
  /** Whether the recent-pages list starts open; the operator's fold is remembered afterwards. */
  defaultExpanded?: boolean;
}) {
  const [expanded, toggle] = useSidebarFold('wiki', defaultExpanded);
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const scopeNames = wikiScopeLabels[zh ? 'zh' : 'en'];
  const kindNames = knowledgeKindLabels[zh ? 'zh' : 'en'];
  const enabled = visible && Boolean(onOpen);
  const wiki = useWikiLibrary(sid, enabled);
  const feed = useKnowledgeFeed(enabled && !compact, FEED_LIMIT);
  const pages = wiki.data?.items ?? [];
  const learned = latestLearned(feed.data?.events ?? []);
  const learnedPage = learned ? resolveKnowledgeItem(learned, pages, sid) : null;
  const recent = recentWikiPages(pages)
    .filter(page => !learnedPage || page.scope !== learnedPage.scope || page.vertical !== learnedPage.vertical || page.path !== learnedPage.path)
    .slice(0, learned ? RECENT_LIMIT - 1 : RECENT_LIMIT);
  const where = (page: WikiLibraryItem) => page.scope === 'vertical' && page.vertical ? libraryVertical(page.vertical, locale) : scopeNames[page.scope];
  const when = (ts: number) => formatRelativeTime(ts, zh ? 'zh-CN' : 'en');
  if (!onOpen) return null;
  const hasRows = learned !== null || recent.length > 0;
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return <section className={`shrink-0 border-t border-line/60 ${compact ? 'py-3' : 'mx-3 py-2'}`} data-wiki-entry>
    <div className={`flex items-center ${compact ? 'justify-center' : ''}`}>
      <button type="button" onClick={() => onOpen()} title={names.title} aria-label={names.title}
        className={`relative flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-2 text-sm text-ink-dim hover:bg-bg ${compact ? 'justify-center' : ''}`}>
        <BookMarked className="h-4 w-4 shrink-0" />
        {!compact && names.title}
        {pages.length > 0 && <span aria-label={names.count(pages.length)}
          className={`rounded-full bg-blue/10 px-1.5 text-[10px] leading-4 text-blue ${compact ? 'absolute right-1 top-1' : 'ml-auto'}`}>{pages.length}</span>}
      </button>
      {!compact && hasRows && <button type="button" onClick={toggle} aria-expanded={expanded} data-sidebar-fold="wiki"
        aria-label={zh ? (expanded ? '收起最近页面' : '展开最近页面') : (expanded ? 'Hide recent pages' : 'Show recent pages')}
        className="rounded p-1.5 text-ink-faint hover:bg-bg hover:text-ink"><Chevron className="h-3.5 w-3.5" /></button>}
    </div>
    {!compact && wiki.data && !learned && recent.length === 0 && <p className="px-2 pb-1 text-xs text-ink-faint">{names.empty}</p>}
    {!compact && expanded && hasRows && <div className="px-2 pb-1">
      {learned && <button type="button" onClick={() => learnedPage ? onOpen(learnedPage) : onOpen()} data-just-learned
        className="block w-full rounded py-1.5 text-left hover:text-blue" title={learned.path}>
        <span className="flex items-center gap-1 text-xs text-ink"><Sparkles className="h-3 w-3 shrink-0 text-blue" /><span className="truncate">{names.justLearned}{learned.title}</span></span>
        <span className="block text-[10px] text-ink-faint">{kindNames[learned.kind] ?? learned.kind}{learned.vertical ? ` · ${libraryVertical(learned.vertical, locale)}` : ''} · {when(learned.ts)}</span>
      </button>}
      {recent.map(page => <button type="button" key={`${page.scope}/${page.vertical}/${page.path}`} onClick={() => onOpen(page)}
        className="block w-full rounded py-1.5 text-left hover:text-blue" title={page.description || page.path}>
        <span className="block truncate text-xs text-ink">{page.title}</span>
        <span className="block text-[10px] text-ink-faint">{where(page)} · {when(page.updated_at)}</span>
      </button>)}
    </div>}
    {!compact && wiki.isError && <p className="px-2 text-xs text-ink-faint">{names.unavailable}</p>}
  </section>;
}
