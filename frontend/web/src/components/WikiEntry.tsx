import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookMarked } from 'lucide-react';
import { api, type WikiPageSummary } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';
import { MarkdownContent } from './MarkdownContent';
import { Modal, ModalHeader } from './Modal';

const RECENT_LIMIT = 5;
const labels = {
  en: {
    title: 'Wiki',
    empty: 'No wiki pages yet',
    unavailable: 'Wiki unavailable',
    index: 'Index',
    back: '← Back to pages',
    pages: 'Wiki pages',
    loading: 'Loading page…',
    truncated: 'Page shortened for display.',
    updated: 'Updated ',
    sub: 'Durable knowledge the project keeps about itself; newest pages first.',
    count: (n: number) => `${n} ${n === 1 ? 'page' : 'pages'}`,
  },
  zh: {
    title: '知识库',
    empty: '尚无知识库页面',
    unavailable: '知识库暂时无法加载',
    index: '索引',
    back: '← 返回页面列表',
    pages: '知识库页面',
    loading: '正在加载页面…',
    truncated: '页面过长，已截断显示。',
    updated: '更新于 ',
    sub: '项目沉淀的长期知识，最新页面在前。',
    count: (n: number) => `${n} 页`,
  },
};

type View = { kind: 'list' } | { kind: 'index' } | { kind: 'page'; page: WikiPageSummary };

export const wikiQueryKey = (sid: string | null) => ['wiki', sid] as const;

function useWiki(sid: string | null, enabled = true) {
  return useQuery({
    queryKey: wikiQueryKey(sid),
    queryFn: ({ signal }) => api.wiki(sid!, signal),
    enabled: enabled && Boolean(sid),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

/** Keep the project's Wiki visible beside the project while it runs. */
export function WikiEntry({ sid, compact = false, visible = true }: {
  sid: string | null;
  compact?: boolean;
  visible?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const [view, setView] = useState<View | null>(null);
  const wiki = useWiki(sid, visible || view !== null);
  const pages = wiki.data?.exists ? wiki.data.pages : [];
  const recent = pages.slice(0, RECENT_LIMIT);
  const when = (page: WikiPageSummary) => formatRelativeTime(page.updated_at, zh ? 'zh-CN' : 'en');
  if (!sid) return null;
  return <section className={`shrink-0 border-t border-line/60 ${compact ? 'py-3' : 'mx-3 py-2'}`} data-wiki-entry>
    <button type="button" onClick={() => setView({ kind: 'list' })} title={names.title} aria-label={names.title}
      className={`relative flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm text-ink-dim hover:bg-bg ${compact ? 'justify-center' : ''}`}>
      <BookMarked className="h-4 w-4 shrink-0" />
      {!compact && names.title}
      {pages.length > 0 && <span aria-label={names.count(pages.length)}
        className={`rounded-full bg-blue/10 px-1.5 text-[10px] leading-4 text-blue ${compact ? 'absolute right-1 top-1' : 'ml-auto'}`}>{pages.length}</span>}
    </button>
    {!compact && wiki.data && recent.length === 0 && <p className="px-2 pb-1 text-xs text-ink-faint">{names.empty}</p>}
    {!compact && recent.length > 0 && <div className="px-2 pb-1">
      {recent.map(page => <button type="button" key={page.path} onClick={() => setView({ kind: 'page', page })}
        className="block w-full rounded py-1.5 text-left hover:text-blue" title={page.description || page.path}>
        <span className="block truncate text-xs text-ink">{page.title}</span>
        <span className="block text-[10px] text-ink-faint">{when(page)}</span>
      </button>)}
    </div>}
    {!compact && wiki.isError && <p className="px-2 text-xs text-ink-faint">{names.unavailable}</p>}
    <Modal open={view !== null} onClose={() => setView(null)} label={names.title} width="max-w-3xl">
      {view && <WikiBrowser sid={sid} view={view} onView={setView} />}
    </Modal>
  </section>;
}

function WikiBrowser({ sid, view, onView }: { sid: string; view: View; onView: (view: View) => void }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const wiki = useWiki(sid);
  const pages = wiki.data?.exists ? wiki.data.pages : [];
  const page = view.kind === 'page' ? view.page : null;
  const document = useQuery({
    queryKey: ['wiki-page', sid, page?.path, page?.updated_at],
    queryFn: ({ signal }) => api.wikiPage(sid, page!.path, signal),
    enabled: Boolean(page),
  });
  const timestamp = (value: number) => new Date(value * 1000).toLocaleString(zh ? 'zh-CN' : 'en-US');
  const root = wiki.data?.exists ? wiki.data.root : '';
  return <div className="flex max-h-[min(780px,84dvh)] min-h-0 flex-col" data-wiki-browser>
    <ModalHeader title={names.title} sub={root ? `${root} · ${names.count(pages.length)}` : names.sub} />
    {view.kind === 'list' && <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4" aria-label={names.pages}>
      {wiki.isError && <p className="p-3 text-sm text-ink-faint">{names.unavailable}</p>}
      {wiki.data && pages.length === 0 && <p className="p-3 text-sm text-ink-faint">{names.empty}</p>}
      {wiki.data?.exists && wiki.data.index_markdown && <button type="button" onClick={() => onView({ kind: 'index' })}
        className="mx-1 mb-2 rounded-md border border-line/60 px-3 py-1.5 text-xs text-ink-dim hover:bg-bg">INDEX.md</button>}
      {pages.map(item => <button key={item.path} type="button" onClick={() => onView({ kind: 'page', page: item })}
        className="block w-full rounded-md px-3 py-3 text-left hover:bg-bg">
        <span className="block break-words text-sm font-medium text-ink">{item.title}</span>
        {item.description && <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink-dim">{item.description}</span>}
        <span className="mt-1.5 block break-all text-[11px] text-ink-faint">{item.path} · {formatRelativeTime(item.updated_at, zh ? 'zh-CN' : 'en')}</span>
      </button>)}
    </div>}
    {view.kind !== 'list' && <article className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6" aria-label={names.pages}>
      <button type="button" className="mb-4 text-sm text-blue" onClick={() => onView({ kind: 'list' })}>{names.back}</button>
      {view.kind === 'index' && wiki.data?.exists && <>
        <h3 className="break-words text-lg font-semibold text-ink">{names.index}</h3>
        <p className="mt-1 break-all text-xs text-ink-faint">{root}/INDEX.md</p>
        <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink"><MarkdownContent>{wiki.data.index_markdown}</MarkdownContent></div>
      </>}
      {page && <>
        <h3 className="break-words text-lg font-semibold text-ink">{document.data?.title ?? page.title}</h3>
        <p className="mt-1 break-all text-xs text-ink-faint">{page.path}</p>
        <p className="mt-1 text-xs text-ink-faint">{names.updated}{timestamp(document.data?.updated_at ?? page.updated_at)}</p>
        <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
          {document.isPending && <p>{names.loading}</p>}
          {document.isError && <p role="alert" className="text-err">{document.error.message}</p>}
          {document.data?.truncated && <p className="mb-3 text-xs text-ink-faint">{names.truncated}</p>}
          {document.data && <MarkdownContent>{document.data.markdown}</MarkdownContent>}
        </div>
      </>}
    </article>}
  </div>;
}
