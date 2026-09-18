import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type WikiLibraryItem, type WikiLibrary as WikiLibraryShape, type WikiPageKind, type WikiScope } from '../api';
import { useI18n } from '../i18n';
import { formatRelativeTime } from '../lib/format';
import { KnowledgeFeedList, useKnowledgeFeed } from './KnowledgeFeed';
import { MarkdownContent } from './MarkdownContent';
import { ModalHeader } from './Modal';
import { KnowledgeReadingGuide, LibraryIntroduction } from './LibraryGuide';
import { libraryVertical } from '../lib/libraryPresentation';

const scopes: WikiScope[] = ['private', 'global', 'vertical', 'project'];
/** Browser tabs: the live feed, every page by recency, lessons, principles, then one tab per level. */
export type WikiTab = WikiScope | 'feed' | 'recent' | 'lessons' | 'principles';
const tabs: WikiTab[] = ['feed', 'recent', 'lessons', 'principles', ...scopes];
export const wikiScopeLabels = {
  en: { private: 'About you', global: 'Global', vertical: 'Vertical', project: 'Project', feed: 'Learning feed', recent: 'Recent', lessons: 'Lessons', principles: 'Principles', index: 'Index' },
  zh: { private: '关于你', global: '全局', vertical: '垂直领域', project: '项目', feed: '学习动态', recent: '最近更新', lessons: '经验教训', principles: '原则', index: '索引' },
};
export const wikiKindLabels: Record<'en' | 'zh', Record<WikiPageKind, string>> = {
  en: { fact: 'Fact', lesson: 'Lesson', survey: 'Survey', principles: 'Principles', note: 'Note', profile: 'Profile', page: 'Page' },
  zh: { fact: '事实', lesson: '经验教训', survey: '调研摘要', principles: '原则', note: '个人记录', profile: '个人概况', page: '参考页面' },
};
/** The page kind the host reported, folded onto the five the browser knows how to label. */
export const pageKind = (item: Pick<WikiLibraryItem, 'kind'>): WikiPageKind =>
  (item.kind in wikiKindLabels.en ? item.kind : 'page') as WikiPageKind;
const identity = (item: WikiLibraryItem) => `${item.scope}/${item.vertical}/${item.path}`;
const libraryKey = (library: { scope: WikiScope; vertical: string }) => `${library.scope}/${library.vertical}`;
/** Every page across libraries, newest file update first. */
export const recentWikiPages = (items: WikiLibraryItem[]) => [...items]
  .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || identity(a).localeCompare(identity(b)));
/** Libraries that have compiled principles, in catalog order. */
export const librariesWithPrinciples = (libraries: WikiLibraryShape[]) => libraries.filter(library => Boolean(library.principles?.trim()));
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

type View =
  | { kind: 'page'; item: WikiLibraryItem }
  | { kind: 'index'; scope: WikiScope; vertical: string }
  | { kind: 'principles'; scope: WikiScope; vertical: string };

export function WikiLibrary({ sid, projectName, initialSelection = null, initialScope }: {
  sid: string | null;
  projectName?: string;
  initialSelection?: WikiLibraryItem | null;
  /** Defaults to the learning feed; a page handed over from the sidebar opens beside the recent list instead. */
  initialScope?: WikiTab;
}) {
  // A project change remounts the browser, including its page selection.
  const initialSid = useRef(sid);
  const selection = initialSid.current === sid ? initialSelection : null;
  return <LibraryBrowser key={sid ?? 'no-project'} sid={sid} projectName={projectName} initialSelection={selection} initialScope={initialScope ?? (selection ? 'recent' : 'feed')} />;
}

function LibraryBrowser({ sid, projectName, initialSelection, initialScope }: {
  sid: string | null; projectName?: string; initialSelection: WikiLibraryItem | null; initialScope: WikiTab;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = wikiScopeLabels[zh ? 'zh' : 'en'];
  const kinds = wikiKindLabels[zh ? 'zh' : 'en'];
  const [scope, setScope] = useState<WikiTab>(initialScope);
  const [search, setSearch] = useState('');
  const [vertical, setVertical] = useState('');
  const [view, setView] = useState<View | null>(initialSelection ? { kind: 'page', item: initialSelection } : null);
  const catalog = useWikiLibrary(sid);
  const feed = useKnowledgeFeed();
  const all = catalog.data?.items ?? [];
  const libraries = catalog.data?.libraries ?? [];
  const events = feed.data?.events ?? [];
  const principled = librariesWithPrinciples(libraries);
  const selected = view?.kind === 'page' ? all.find(item => identity(item) === identity(view.item)) ?? null : null;
  const indexLibrary = view?.kind === 'index' ? libraries.find(library => libraryKey(library) === libraryKey(view)) ?? null : null;
  const principlesLibrary = view?.kind === 'principles' ? principled.find(library => libraryKey(library) === libraryKey(view)) ?? null : null;
  const reading = Boolean(selected || indexLibrary || principlesLibrary);
  const document = useQuery({
    queryKey: ['wiki-document', sid, selected?.scope, selected?.vertical, selected?.path, selected?.updated_at],
    queryFn: ({ signal }) => api.wikiDocument(sid, selected!.scope, selected!.vertical, selected!.path, signal),
    enabled: Boolean(selected),
  });
  const query = search.trim().toLocaleLowerCase();
  const listsPages = scope !== 'feed' && scope !== 'principles';
  const inScope = (item: { scope: WikiScope; vertical: string }) => (scope === 'recent' || scope === 'lessons' || item.scope === scope)
    && (scope !== 'vertical' || !vertical || item.vertical === vertical);
  const candidates = scope === 'recent' ? recentWikiPages(all) : scope === 'lessons' ? recentWikiPages(all.filter(item => pageKind(item) === 'lesson')) : all;
  const items = listsPages ? candidates.filter(item => inScope(item)
    && (!query || `${item.title} ${item.description} ${item.path} ${item.vertical}`.toLocaleLowerCase().includes(query))) : [];
  const indexes = query || scope === 'lessons' || !listsPages ? [] : libraries.filter(library => library.index_markdown && inScope(library));
  const pickScope = (value: WikiTab) => { setScope(value); setView(null); };
  const timestamp = (value: number) => new Date(value * 1000).toLocaleString(zh ? 'zh-CN' : 'en-US');
  const relative = (value: number) => formatRelativeTime(value, zh ? 'zh-CN' : 'en');
  const where = (item: { scope: WikiScope; vertical: string }) => item.scope === 'project'
    ? `${names.project}${projectName ? ` · ${projectName}` : ''}`
    : `${names[item.scope]}${item.vertical ? ` · ${libraryVertical(item.vertical, locale)}` : ''}`;
  const reused = (count: number) => zh ? `复用 ${count} 次` : count === 1 ? 'Reused once' : `Reused ${count} times`;
  const origin = (item: WikiLibraryItem) => pageKind(item) === 'lesson' ? (zh ? '由 Argus 反思写下' : 'Written by Argus in reflection')
    : pageKind(item) === 'survey' ? (zh ? '问答后沉淀' : 'Distilled after an answer') : '';
  const tabCount = (value: WikiTab) => value === 'lessons' ? all.filter(item => pageKind(item) === 'lesson').length
    : value === 'principles' ? principled.length
      : value === 'feed' || value === 'recent' ? null : all.filter(item => item.scope === value).length;
  const filtered = Boolean(query || (scope === 'vertical' && vertical));
  const showPrinciples = (library: WikiLibraryShape) => <section key={libraryKey(library)} className="mb-8" aria-label={`${names.principles} · ${where(library)}`}>
    <h3 className="break-words text-lg font-semibold text-ink">{names.principles}<span className="ml-2 text-sm font-normal text-ink-faint">{where(library)}</span></h3>
    <p className="mt-1 break-all text-xs text-ink-faint">{library.root}/principles.md</p>
    <div className="mt-4 border-t border-line/60 pt-4 text-sm text-ink"><MarkdownContent>{library.principles ?? ''}</MarkdownContent></div>
  </section>;
  const noPrinciples = zh ? '还没有沉淀出原则：至少需要 3 条教训。' : 'No principles yet: at least 3 lessons are needed.';

  return <div className="flex h-[min(780px,84dvh)] min-h-0 flex-col" data-wiki-library>
    <ModalHeader title={zh ? '知识库' : 'Knowledge base'} sub={zh ? 'Argus 一边工作一边学：任务后写下教训，读取已有知识，把评审通过的页面提升到共享层。' : 'Argus learns as it works: lessons after each mission, knowledge read back into prompts, reviewed pages promoted to shared levels.'} />
    <LibraryIntroduction kind="knowledge" />
    <nav aria-label={zh ? '知识分类' : 'Knowledge levels'} className="flex shrink-0 overflow-x-auto border-b border-line/60 px-4">
      {tabs.map(value => <button key={value} type="button" aria-pressed={scope === value} onClick={() => pickScope(value)}
        className={`shrink-0 border-b-2 px-2 py-2.5 text-xs sm:px-3 sm:text-sm ${scope === value ? 'border-blue text-ink' : 'border-transparent text-ink-faint hover:text-ink'}`}>
        {names[value]}{catalog.data && tabCount(value) != null && <span className="ml-1.5 text-xs text-ink-faint">{tabCount(value)}</span>}
      </button>)}
    </nav>
    <div className="flex min-h-0 flex-1">
      <div className={`${reading ? 'hidden md:flex' : 'flex'} min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r md:border-line/60`}>
        <div className="space-y-2 p-4">
          {scope !== 'principles' && <input type="search" value={search} onChange={event => setSearch(event.target.value)} aria-label={zh ? '搜索页面' : 'Search pages'}
            placeholder={scope === 'feed' ? (zh ? '搜索动态' : 'Search the feed') : (zh ? '搜索页面' : 'Search pages')} className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink" />}
          {scope === 'vertical' && <select value={vertical} onChange={event => { setVertical(event.target.value); setView(null); }} aria-label={zh ? '垂直领域' : 'Vertical'}
            className="w-full rounded-md border border-line bg-bg px-2 py-2 text-sm text-ink">
            <option value="">{zh ? '所有领域' : 'All verticals'}</option>
            {catalog.data?.verticals.map(value => <option key={value} value={value}>{libraryVertical(value, locale)}{value === catalog.data.active_vertical ? (zh ? ' · 当前项目' : ' · this project') : ''}</option>)}
          </select>}
          {scope === 'feed' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? 'Argus 学到、读取和提升知识的记录，最新在前，每 10 秒刷新。' : 'What Argus learned, recalled and promoted, newest first; refreshes every 10 seconds.'}</p>}
          {scope === 'recent' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '按文件更新时间排列，涵盖所有层级。' : 'All levels, newest file updates first.'}</p>}
          {scope === 'lessons' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '任务结束后 Argus 反思写下的教训，涵盖所有层级。反复出现的教训会被整理成原则。' : 'Lessons Argus wrote down while reflecting after a mission, across all levels. Lessons that keep recurring are compiled into principles.'}</p>}
          {scope === 'principles' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '每个领域从反复出现的教训中整理出的原则，每条都引用它的证据。' : 'Per vertical, the principles compiled from recurring lessons; each cites the lessons behind it.'}</p>}
          {scope === 'private' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '只有你和为你工作的 Argus 能看到：你的情况、偏好、计划。不会进入共享层，也不会写进任务。' : 'Seen only by you and the Argus working for you: your situation, preferences and plans. Never promoted to a shared level or written into a task.'}</p>}
          {scope === 'global' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '所有项目和任务都能读到。' : 'Readable by every project and task.'}</p>}
          {scope === 'vertical' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '同一领域的项目共享；评审通过的页面由主机复制到这里。' : 'Shared by projects of one vertical; reviewed pages are copied here by the host.'}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4" aria-label={zh ? '知识页面' : 'Knowledge pages'}>
          {scope === 'feed' && <>
            {feed.isPending && <p className="p-3 text-sm text-ink-faint">{zh ? '正在加载动态…' : 'Loading the feed…'}</p>}
            {feed.isError && <div role="alert" className="p-3 text-sm text-err">{zh ? '无法加载学习动态。' : 'Could not load the learning feed.'} <button type="button" className="underline" onClick={() => void feed.refetch()}>{zh ? '重试' : 'Retry'}</button></div>}
            {feed.data && <KnowledgeFeedList events={events} items={all} sid={sid} query={search} selected={selected} onSelect={item => setView({ kind: 'page', item })} />}
          </>}
          {scope === 'principles' && <>
            {catalog.isPending && <p className="p-3 text-sm text-ink-faint">{zh ? '正在加载页面…' : 'Loading pages…'}</p>}
            {catalog.data && principled.length === 0 && <p className="p-3 text-sm leading-relaxed text-ink-faint">{noPrinciples}</p>}
            {principled.map(library => <button key={libraryKey(library)} type="button" onClick={() => setView({ kind: 'principles', scope: library.scope, vertical: library.vertical })}
              aria-pressed={principlesLibrary ? libraryKey(principlesLibrary) === libraryKey(library) : false}
              className={`block w-full rounded-md px-3 py-3 text-left ${principlesLibrary && libraryKey(principlesLibrary) === libraryKey(library) ? 'bg-blue/10' : 'hover:bg-bg'}`}>
              <span className="block break-words text-sm font-medium text-ink" data-wiki-title>{libraryVertical(library.vertical, locale) || names[library.scope]}</span>
              <span className="mt-1.5 block text-[11px] text-ink-faint">{where(library)} · principles.md</span>
            </button>)}
          </>}
          {listsPages && <>
            {catalog.isPending && <p className="p-3 text-sm text-ink-faint">{zh ? '正在加载页面…' : 'Loading pages…'}</p>}
            {catalog.isError && <div role="alert" className="p-3 text-sm text-err">{zh ? '无法加载知识库。' : 'Could not load the knowledge base.'} <button type="button" className="underline" onClick={() => void catalog.refetch()}>{zh ? '重试' : 'Retry'}</button></div>}
            {scope === 'private' && !query && libraries.filter(library => library.scope === 'private' && library.profile?.trim()).map(library => <section key={libraryKey(library)} className="mx-1 mb-3 rounded-md border border-line/60 p-3" aria-label={zh ? '你的个人概况' : 'Your profile'} data-wiki-profile>
              <h3 className="text-sm font-semibold text-ink">{zh ? 'Argus 对你的了解' : 'What Argus knows about you'}</h3>
              <p className="mt-0.5 break-all text-[11px] text-ink-faint">{library.root}/profile.md</p>
              <div className="mt-2 text-sm text-ink"><MarkdownContent>{library.profile ?? ''}</MarkdownContent></div>
            </section>)}
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
                  : scope === 'lessons' ? (zh ? '还没有教训。任务结束后 Argus 会反思，把值得记住的教训写在这里。' : 'No lessons yet. After each mission Argus reflects and writes down what is worth remembering here.')
                    : scope === 'project' ? (zh ? '该项目尚未写下知识页面。工作中沉淀的页面会出现在这里。' : 'This project has no knowledge pages yet. Pages written during work will appear here.')
                      : scope === 'vertical' ? (zh ? '该领域暂无页面。评审通过的项目页面会由主机复制到这里。' : 'No pages for this vertical yet. Reviewed project pages are copied here by the host.')
                        : scope === 'private' ? (zh ? 'Argus 还没有记下关于你的信息。你在对话和任务里提到的自己的情况、偏好、计划会记在这里，只有你能看到。' : 'Argus has not noted anything about you yet. What you tell it about your situation, preferences and plans is kept here, for your eyes only.')
                        : (zh ? '全局层暂无页面。标记为全局并通过评审的页面会出现在这里。' : 'No global pages yet. Pages marked global that pass review will appear here.')}</p>}
            {items.map(item => <button key={identity(item)} type="button" onClick={() => setView({ kind: 'page', item })} aria-pressed={selected ? identity(selected) === identity(item) : false}
              className={`block w-full rounded-md px-3 py-3 text-left ${selected && identity(selected) === identity(item) ? 'bg-blue/10' : 'hover:bg-bg'}`}>
              <span className="flex items-baseline gap-1.5">
                <span className="shrink-0 rounded bg-line/60 px-1.5 py-px text-[10px] font-medium text-ink-dim" data-wiki-kind={pageKind(item)}>{kinds[pageKind(item)]}</span>
                <span className="min-w-0 break-words text-sm font-medium text-ink" data-wiki-title>{item.title}</span>
                {item.reuse_count > 0 && <span className="ml-auto shrink-0 rounded-full bg-blue/10 px-1.5 text-[10px] leading-4 text-blue" data-wiki-reuse={item.reuse_count}>{reused(item.reuse_count)}</span>}
              </span>
              {item.description && <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink-dim">{item.description}</span>}
              <span className="mt-1.5 block text-[11px] text-ink-faint">{where(item)} · {relative(item.updated_at)}</span>
            </button>)}
            {catalog.data?.errors.length ? <p role="status" className="p-3 text-xs text-err">{zh ? '部分页面无法读取。' : 'Some pages could not be read.'}</p> : null}
          </>}
        </div>
      </div>
      <article className={`${reading ? 'block' : 'hidden md:block'} min-w-0 flex-1 overflow-y-auto p-5 sm:p-6`} aria-label={zh ? '页面全文' : 'Knowledge page'}>
        {selected ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setView(null)}>{zh ? '← 返回页面列表' : '← Back to pages'}</button>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-line/60 px-1.5 py-px text-[10px] font-medium text-ink-dim">{kinds[pageKind(selected)]}</span>
            {selected.reuse_count > 0 && <span className="rounded-full bg-blue/10 px-1.5 text-[10px] leading-4 text-blue">{reused(selected.reuse_count)}</span>}
          </div>
          <h3 className="mt-2 break-words text-lg font-semibold text-ink">{document.data?.title || selected.title}</h3>
          <p className="mt-1 text-xs text-ink-faint">{where(selected)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{selected.path}</p>
          {selected.source && <p className="mt-1 break-all text-xs text-ink-faint">{zh ? '来源 ' : 'Source '}{selected.source}</p>}
          {selected.created && <p className="mt-1 text-xs text-ink-faint">{zh ? '写于 ' : 'Written '}{selected.created}</p>}
          <p className="mt-1 text-xs text-ink-faint">{zh ? '更新于 ' : 'Updated '}{timestamp(document.data?.updated_at ?? selected.updated_at)}</p>
          <p className="mt-1 text-xs text-ink-faint">{selected.reuse_count > 0 ? reused(selected.reuse_count) : (zh ? '尚未被读取过' : 'Not recalled yet')}</p>
          {origin(selected) && <p className="mt-1 text-xs text-ink-faint">{origin(selected)}</p>}
          <KnowledgeReadingGuide item={selected} />
          {(document.data?.description || selected.description) && <p className="mt-4 text-sm leading-relaxed text-ink-dim">{zh && <span className="font-medium">原始摘要： </span>}{document.data?.description || selected.description}</p>}
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
            {document.isPending && <p>{zh ? '正在加载全文…' : 'Loading page…'}</p>}
            {document.isError && <p role="alert" className="text-err">{document.error.message} <button type="button" className="underline" onClick={() => void document.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
            {document.data?.truncated && <p className="mb-3 text-xs text-ink-faint">{zh ? '页面过长，已截断显示。' : 'Page shortened for display.'}</p>}
            {document.data && <>{zh && <p className="mb-3 text-xs text-ink-faint">原文（保留创建时的内容与语言）</p>}<MarkdownContent>{document.data.content}</MarkdownContent></>}
          </div>
        </> : indexLibrary ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setView(null)}>{zh ? '← 返回页面列表' : '← Back to pages'}</button>
          <h3 className="break-words text-lg font-semibold text-ink">{names.index}</h3>
          <p className="mt-1 text-xs text-ink-faint">{where(indexLibrary)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{indexLibrary.root}/INDEX.md</p>
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink"><MarkdownContent>{indexLibrary.index_markdown}</MarkdownContent></div>
        </> : principlesLibrary ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setView(null)}>{zh ? '← 返回列表' : '← Back to the list'}</button>
          {showPrinciples(principlesLibrary)}
        </> : scope === 'principles' ? (principled.length > 0 ? principled.map(showPrinciples) : <p className="text-sm text-ink-faint">{noPrinciples}</p>)
          : scope === 'feed' ? <p className="text-sm text-ink-faint">{zh ? '点击动态里的标题，在这里阅读对应页面。' : 'Click a title in the feed to read its page here.'}</p>
            : <p className="text-sm text-ink-faint">{zh ? '选择页面，阅读完整内容。' : 'Select a page to read it in full.'}</p>}
      </article>
    </div>
  </div>;
}
