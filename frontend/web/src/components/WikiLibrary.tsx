import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type WikiLibraryItem, type WikiScope } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';
import { MarkdownContent } from './MarkdownContent';
import { ModalHeader } from './Modal';

const scopes: WikiScope[] = ['global', 'vertical', 'project'];
export const wikiScopeLabels = {
  en: { global: 'Global', vertical: 'Vertical', project: 'Project', recent: 'Recent updates', index: 'Index' },
  zh: { global: '全局', vertical: '垂直领域', project: '项目', recent: '最近更新', index: '索引' },
};
const identity = (item: WikiLibraryItem) => `${item.scope}/${item.vertical}/${item.path}`;
const libraryKey = (library: { scope: WikiScope; vertical: string }) => `${library.scope}/${library.vertical}`;
/** Every page across libraries, newest file update first. */
export const recentWikiPages = (items: WikiLibraryItem[]) => [...items]
  .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || identity(a).localeCompare(identity(b)));
export const wikiQueryKey = (sid: string | null) => ['wiki-library', sid] as const;

export function useWikiLibrary(sid: string | null, enabled = true) {
  return useQuery({
    queryKey: wikiQueryKey(sid),
    queryFn: ({ signal }) => api.wikiLibrary(sid, signal),
    enabled,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

type View = { kind: 'page'; item: WikiLibraryItem } | { kind: 'index'; scope: WikiScope; vertical: string };

export function WikiLibrary({ sid, projectName, initialSelection = null, initialScope = 'recent' }: {
  sid: string | null;
  projectName?: string;
  initialSelection?: WikiLibraryItem | null;
  initialScope?: WikiScope | 'recent';
}) {
  // A project change remounts the browser, including its page selection.
  const initialSid = useRef(sid);
  return <LibraryBrowser key={sid ?? 'no-project'} sid={sid} projectName={projectName} initialSelection={initialSid.current === sid ? initialSelection : null} initialScope={initialScope} />;
}

function LibraryBrowser({ sid, projectName, initialSelection, initialScope }: {
  sid: string | null; projectName?: string; initialSelection: WikiLibraryItem | null; initialScope: WikiScope | 'recent';
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = wikiScopeLabels[zh ? 'zh' : 'en'];
  const [scope, setScope] = useState<WikiScope | 'recent'>(initialScope);
  const [search, setSearch] = useState('');
  const [vertical, setVertical] = useState('');
  const [view, setView] = useState<View | null>(initialSelection ? { kind: 'page', item: initialSelection } : null);
  const catalog = useWikiLibrary(sid);
  const all = catalog.data?.items ?? [];
  const libraries = catalog.data?.libraries ?? [];
  const selected = view?.kind === 'page' ? all.find(item => identity(item) === identity(view.item)) ?? null : null;
  const indexLibrary = view?.kind === 'index' ? libraries.find(library => libraryKey(library) === libraryKey(view)) ?? null : null;
  const reading = Boolean(selected || indexLibrary);
  const document = useQuery({
    queryKey: ['wiki-document', sid, selected?.scope, selected?.vertical, selected?.path, selected?.updated_at],
    queryFn: ({ signal }) => api.wikiDocument(sid, selected!.scope, selected!.vertical, selected!.path, signal),
    enabled: Boolean(selected),
  });
  const query = search.trim().toLocaleLowerCase();
  const inScope = (item: { scope: WikiScope; vertical: string }) => (scope === 'recent' || item.scope === scope)
    && (scope !== 'vertical' || !vertical || item.vertical === vertical);
  const candidates = scope === 'recent' ? recentWikiPages(all) : all;
  const items = candidates.filter(item => inScope(item)
    && (!query || `${item.title} ${item.description} ${item.path} ${item.vertical}`.toLocaleLowerCase().includes(query)));
  const indexes = query ? [] : libraries.filter(library => library.index_markdown && inScope(library));
  const pickScope = (value: WikiScope | 'recent') => { setScope(value); setView(null); };
  const timestamp = (value: number) => new Date(value * 1000).toLocaleString(zh ? 'zh-CN' : 'en-US');
  const relative = (value: number) => formatRelativeTime(value, zh ? 'zh-CN' : 'en');
  const where = (item: { scope: WikiScope; vertical: string }) => item.scope === 'project'
    ? `${names.project}${projectName ? ` · ${projectName}` : ''}`
    : `${names[item.scope]}${item.vertical ? ` · ${item.vertical}` : ''}`;
  const filtered = Boolean(query || (scope === 'vertical' && vertical));

  return <div className="flex h-[min(780px,84dvh)] min-h-0 flex-col" data-wiki-library>
    <ModalHeader title={zh ? '知识库' : 'Knowledge base'} sub={zh ? '项目、领域与全局层沉淀的长期知识；评审通过的页面由主机提升到领域层。' : 'Durable knowledge at project, vertical and global level; reviewed pages are promoted by the host.'} />
    <nav aria-label={zh ? '知识分类' : 'Knowledge levels'} className="flex shrink-0 overflow-x-auto border-b border-line/60 px-4">
      {(['recent', ...scopes] as const).map(value => <button key={value} type="button" aria-pressed={scope === value} onClick={() => pickScope(value)}
        className={`shrink-0 border-b-2 px-2 py-2.5 text-xs sm:px-3 sm:text-sm ${scope === value ? 'border-blue text-ink' : 'border-transparent text-ink-faint hover:text-ink'}`}>
        {value === 'recent' ? <>{zh ? '最近' : 'Recent'}<span className="hidden sm:inline">{zh ? '更新' : ' updates'}</span></> : names[value]}{value !== 'recent' && catalog.data && <span className="ml-1.5 text-xs text-ink-faint">{all.filter(item => item.scope === value).length}</span>}
      </button>)}
    </nav>
    <div className="flex min-h-0 flex-1">
      <div className={`${reading ? 'hidden md:flex' : 'flex'} min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r md:border-line/60`}>
        <div className="space-y-2 p-4">
          <input type="search" value={search} onChange={event => setSearch(event.target.value)} aria-label={zh ? '搜索页面' : 'Search pages'}
            placeholder={zh ? '搜索页面' : 'Search pages'} className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink" />
          {scope === 'vertical' && <select value={vertical} onChange={event => { setVertical(event.target.value); setView(null); }} aria-label={zh ? '垂直领域' : 'Vertical'}
            className="w-full rounded-md border border-line bg-bg px-2 py-2 text-sm text-ink">
            <option value="">{zh ? '所有领域' : 'All verticals'}</option>
            {catalog.data?.verticals.map(value => <option key={value} value={value}>{value}{value === catalog.data.active_vertical ? (zh ? ' · 当前项目' : ' · this project') : ''}</option>)}
          </select>}
          {scope === 'recent' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '按文件更新时间排列，涵盖所有层级。' : 'All levels, newest file updates first.'}</p>}
          {scope === 'global' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '所有项目和任务都能读到。' : 'Readable by every project and task.'}</p>}
          {scope === 'vertical' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '同一领域的项目共享；评审通过的页面由主机复制到这里。' : 'Shared by projects of one vertical; reviewed pages are copied here by the host.'}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4" aria-label={zh ? '知识页面' : 'Knowledge pages'}>
          {catalog.isPending && <p className="p-3 text-sm text-ink-faint">{zh ? '正在加载页面…' : 'Loading pages…'}</p>}
          {catalog.isError && <div role="alert" className="p-3 text-sm text-err">{zh ? '无法加载知识库。' : 'Could not load the knowledge base.'} <button type="button" className="underline" onClick={() => void catalog.refetch()}>{zh ? '重试' : 'Retry'}</button></div>}
          {indexes.length > 0 && <div className="mx-1 mb-2 flex flex-wrap gap-1.5" aria-label={zh ? '索引文件' : 'Index files'}>
            {indexes.map(library => <button key={libraryKey(library)} type="button" onClick={() => setView({ kind: 'index', scope: library.scope, vertical: library.vertical })}
              aria-pressed={indexLibrary ? libraryKey(indexLibrary) === libraryKey(library) : false}
              className={`rounded-md border border-line/60 px-2.5 py-1 text-[11px] hover:bg-bg ${indexLibrary && libraryKey(indexLibrary) === libraryKey(library) ? 'bg-blue/10 text-ink' : 'text-ink-dim'}`}>
              INDEX.md<span className="text-ink-faint"> · {where(library)}</span>
            </button>)}
          </div>}
          {!catalog.isPending && !catalog.isError && items.length === 0 && <p className="p-3 text-sm leading-relaxed text-ink-faint">{filtered ? <>{zh ? '当前筛选条件下没有页面。' : 'No pages match the current filters.'} <button type="button" className="underline" onClick={() => { setSearch(''); setVertical(''); setView(null); }}>{zh ? '清除筛选' : 'Clear filters'}</button></>
            : scope === 'project' && !sid ? (zh ? '选择一个项目以查看它的知识库。' : 'Select a project to see its knowledge base.')
              : scope === 'recent' ? (zh ? '尚无知识页面。工作中写下的页面会自动出现在这里。' : 'No knowledge pages yet. Pages written during work will appear here automatically.')
                : scope === 'project' ? (zh ? '该项目尚未写下知识页面。工作中沉淀的页面会出现在这里。' : 'This project has no knowledge pages yet. Pages written during work will appear here.')
                  : scope === 'vertical' ? (zh ? '该领域暂无页面。评审通过的项目页面会由主机复制到这里。' : 'No pages for this vertical yet. Reviewed project pages are copied here by the host.')
                    : (zh ? '全局层暂无页面。标记为全局并通过评审的页面会出现在这里。' : 'No global pages yet. Pages marked global that pass review will appear here.')}</p>}
          {items.map(item => <button key={identity(item)} type="button" onClick={() => setView({ kind: 'page', item })} aria-pressed={selected ? identity(selected) === identity(item) : false}
            className={`block w-full rounded-md px-3 py-3 text-left ${selected && identity(selected) === identity(item) ? 'bg-blue/10' : 'hover:bg-bg'}`}>
            <span className="block break-words text-sm font-medium text-ink">{item.title}</span>
            {item.description && <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink-dim">{item.description}</span>}
            <span className="mt-1.5 block text-[11px] text-ink-faint">{where(item)} · {relative(item.updated_at)}</span>
          </button>)}
          {catalog.data?.errors.length ? <p role="status" className="p-3 text-xs text-err">{zh ? '部分页面无法读取。' : 'Some pages could not be read.'}</p> : null}
        </div>
      </div>
      <article className={`${reading ? 'block' : 'hidden md:block'} min-w-0 flex-1 overflow-y-auto p-5 sm:p-6`} aria-label={zh ? '页面全文' : 'Knowledge page'}>
        {selected ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setView(null)}>{zh ? '← 返回页面列表' : '← Back to pages'}</button>
          <h3 className="break-words text-lg font-semibold text-ink">{document.data?.title || selected.title}</h3>
          <p className="mt-1 text-xs text-ink-faint">{where(selected)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{selected.path}</p>
          <p className="mt-1 text-xs text-ink-faint">{zh ? '更新于 ' : 'Updated '}{timestamp(document.data?.updated_at ?? selected.updated_at)}</p>
          {(document.data?.description || selected.description) && <p className="mt-4 text-sm leading-relaxed text-ink-dim">{document.data?.description || selected.description}</p>}
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
            {document.isPending && <p>{zh ? '正在加载全文…' : 'Loading page…'}</p>}
            {document.isError && <p role="alert" className="text-err">{document.error.message} <button type="button" className="underline" onClick={() => void document.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
            {document.data?.truncated && <p className="mb-3 text-xs text-ink-faint">{zh ? '页面过长，已截断显示。' : 'Page shortened for display.'}</p>}
            {document.data && <MarkdownContent>{document.data.content}</MarkdownContent>}
          </div>
        </> : indexLibrary ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setView(null)}>{zh ? '← 返回页面列表' : '← Back to pages'}</button>
          <h3 className="break-words text-lg font-semibold text-ink">{names.index}</h3>
          <p className="mt-1 text-xs text-ink-faint">{where(indexLibrary)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{indexLibrary.root}/INDEX.md</p>
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink"><MarkdownContent>{indexLibrary.index_markdown}</MarkdownContent></div>
        </> : <p className="text-sm text-ink-faint">{zh ? '选择页面，阅读完整内容。' : 'Select a page to read it in full.'}</p>}
      </article>
    </div>
  </div>;
}
