import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen } from 'lucide-react';
import { api, type SkillCatalog, type SkillLibraryItem, type WikiCatalog, type WikiLibraryItem, type WikiPageKind, type WikiScope } from '../api';
import { useI18n, type Locale } from '../i18n';
import { libraryVertical, skillPresentation, skillSearchText } from '../lib/libraryPresentation';
import { KnowledgeFeedList, useKnowledgeFeed } from './KnowledgeFeed';
import { KnowledgeReadingGuide, SkillReadingGuide } from './LibraryGuide';
import { LearningStatus } from './LearningStatus';
import { MarkdownContent } from './MarkdownContent';
import { ModalHeader } from './Modal';

const wikiKindLabels: Record<'en' | 'zh', Record<WikiPageKind, string>> = {
  en: { fact: 'Fact', lesson: 'Lesson', survey: 'Survey', principles: 'Principles', note: 'Note', profile: 'Profile', page: 'Page' },
  zh: { fact: '事实', lesson: '经验教训', survey: '调研摘要', principles: '原则', note: '个人记录', profile: '个人概况', page: '参考页面' },
};
/** The page kind the host reported, folded onto the ones the browser knows how to label. */
const pageKind = (item: Pick<WikiLibraryItem, 'kind'>): WikiPageKind =>
  (item.kind in wikiKindLabels.en ? item.kind : 'page') as WikiPageKind;
const skillIdentity = (item: SkillLibraryItem) => `${item.library}/${item.path}`;
const recentSkills = (items: SkillLibraryItem[]) => items.filter(item => !item.is_default && item.updated_at != null)
  .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || skillIdentity(a).localeCompare(skillIdentity(b)));
const libraryQuery = { staleTime: 10_000, refetchInterval: 15_000 };

/** What was learned lately, or everything the library holds. */
export type LearningScope = 'feed' | 'global';
export type LearningSelection = { kind: 'knowledge'; item: WikiLibraryItem } | { kind: 'skill'; item: SkillLibraryItem };
const scopes: LearningScope[] = ['feed', 'global'];
const learningScopeLabels = {
  'zh-CN': { feed: '学习动态', global: '全局项目' },
  en: { feed: 'Learning feed', global: 'Across projects' },
};
type Entry = {
  id: string; title: string; description: string; path: string; scope: WikiScope;
  vertical: string; updated: number; search: string;
} & (LearningSelection | { kind: 'index' | 'principles' | 'profile'; content: string });

export function learningEntries(wiki: WikiCatalog | undefined, skills: SkillCatalog | undefined, scope: LearningScope, locale: Locale): Entry[] {
  const zh = locale === 'zh-CN';
  const pages: Entry[] = (wiki?.items ?? []).map(item => ({
    kind: 'knowledge', item, id: `knowledge/${item.scope}/${item.vertical}/${item.path}`,
    title: item.title, description: item.description, path: item.path, scope: item.scope,
    vertical: item.vertical, updated: item.updated_at, search: `${item.title} ${item.description} ${item.path}`,
  }));
  const methods: Entry[] = (scope === 'feed' ? recentSkills(skills?.items ?? []) : skills?.items ?? [])
    .map(item => {
      const presentation = skillPresentation(item, locale);
      return { kind: 'skill', item, id: `skill/${item.library}/${item.path}`,
        title: presentation.title, description: presentation.summary, path: item.path, scope: item.scope,
        vertical: item.vertical, updated: item.updated_at ?? 0, search: skillSearchText(item, locale) };
    });
  const documents: Entry[] = scope === 'feed' ? [] : (wiki?.libraries ?? [])
    .flatMap(library => ([
      { kind: 'index', content: library.index_markdown, name: 'INDEX.md', title: zh ? '索引' : 'Index' },
      { kind: 'principles', content: library.principles, name: 'principles.md', title: zh ? '原则' : 'Principles' },
      { kind: 'profile', content: library.profile, name: 'profile.md', title: zh ? '个人概况（原私有范围）' : 'Personal profile (private scope)' },
    ] as const).flatMap(document => {
      if (!document.content?.trim()) return [];
      return [{ kind: document.kind, content: document.content,
        id: `${document.kind}/${library.scope}/${library.vertical}/${library.root}`,
        title: document.title, description: '', path: `${library.root}/${document.name}`,
        scope: library.scope, vertical: library.vertical, updated: 0, search: `${document.title} ${document.name}` }];
    }));
  return [...pages, ...methods, ...documents].sort((a, b) => b.updated - a.updated || a.id.localeCompare(b.id));
}

export function LearningLibraryEntry({ onOpen, compact = false }: {
  onOpen: (scope: LearningScope) => void; compact?: boolean;
}) {
  const { locale } = useI18n();
  const label = locale === 'zh-CN' ? '知识与技能' : 'Knowledge & skills';
  return <section className={`shrink-0 ${compact ? 'py-2' : 'mx-2 py-1'}`} data-learning-navigation>
    <button type="button" onClick={() => onOpen('feed')} aria-label={label} title={label}
      className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-ink-dim hover:bg-bg hover:text-ink ${compact ? 'justify-center' : ''}`}>
      <BookOpen size={16} aria-hidden="true" />{!compact && label}
    </button>
  </section>;
}

export function LearningLibrary({ sid, initialScope = 'feed', initialSelection = null, projectNames = {}, onUse }: {
  sid: string | null; initialScope?: LearningScope; initialSelection?: LearningSelection | null;
  projectNames?: Record<string, string>;
  onUse?: (title: string, content: string, scope: string) => void;
}) {
  const originalSid = useRef(sid);
  return <LearningBrowser key={sid ?? 'no-project'} sid={sid} initialScope={initialScope}
    initialSelection={originalSid.current === sid ? initialSelection : null} projectNames={projectNames} onUse={onUse} />;
}

function LearningBrowser({ sid, initialScope, initialSelection, projectNames, onUse }: {
  sid: string | null; initialScope: LearningScope; initialSelection: LearningSelection | null;
  projectNames: Record<string, string>;
  onUse?: (title: string, content: string, scope: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const labels = learningScopeLabels[locale];
  const [scope, setScope] = useState(initialScope);
  const [search, setSearch] = useState('');
  const [selection, setSelection] = useState<string | null>(() => initialSelection
    ? initialSelection.kind === 'skill'
      ? `skill/${initialSelection.item.library}/${initialSelection.item.path}`
      : `knowledge/${initialSelection.item.scope}/${initialSelection.item.vertical}/${initialSelection.item.path}`
    : null);
  const wiki = useQuery({ ...libraryQuery, queryKey: ['wiki-library', sid], queryFn: ({ signal }) => api.wikiLibrary(sid, signal) });
  const skills = useQuery({ ...libraryQuery, queryKey: ['skill-library', sid], queryFn: ({ signal }) => api.skillLibrary(sid, signal) });
  const feed = useKnowledgeFeed(scope === 'feed');
  const all = learningEntries(wiki.data, skills.data, scope, locale);
  const selected = all.find(entry => entry.id === selection);
  const knowledge = selected?.kind === 'knowledge' ? selected.item : null;
  const skill = selected?.kind === 'skill' ? selected.item : null;
  const wikiDocument = useQuery({
    queryKey: ['wiki-document', sid, knowledge?.scope, knowledge?.vertical, knowledge?.path, knowledge?.updated_at],
    queryFn: ({ signal }) => {
      if (!knowledge) throw new Error('No knowledge document selected');
      return api.wikiDocument(sid, knowledge.scope, knowledge.vertical, knowledge.path, signal);
    }, enabled: Boolean(knowledge),
  });
  const skillDocument = useQuery({
    queryKey: ['skill-document', sid, skill?.library, skill?.path, skill?.updated_at],
    queryFn: ({ signal }) => {
      if (!skill) throw new Error('No skill document selected');
      return api.skillDocument(sid, skill.library, skill.path, signal);
    }, enabled: Boolean(skill),
  });
  const query = search.trim().toLocaleLowerCase();
  const entries = all.filter(entry => !query || `${entry.search} ${entry.vertical}`.toLocaleLowerCase().includes(query));
  const location = (entry: Entry) => entry.scope === 'private'
    ? (zh ? '个人记录' : 'Personal record')
    : `${labels.global}${entry.vertical ? ` · ${libraryVertical(entry.vertical, locale)}` : ''}`;
  const kind = (entry: Entry) => entry.kind === 'skill' ? (zh ? '技能' : 'Skill')
    : entry.kind === 'knowledge' ? wikiKindLabels[zh ? 'zh' : 'en'][pageKind(entry.item)]
      : entry.kind === 'principles' ? (zh ? '原则' : 'Principles')
        : entry.kind === 'profile' ? (zh ? '个人概况' : 'Profile') : (zh ? '索引' : 'Index');
  const document = skill ? skillDocument : wikiDocument;
  const guide = skill ? skillPresentation(skill, locale).guide : null;
  const pick = (next: LearningScope) => { setScope(next); setSelection(null); };
  const pickKnowledge = (item: WikiLibraryItem) => {
    setSelection(`knowledge/${item.scope}/${item.vertical}/${item.path}`);
  };

  return <div className="flex h-[min(780px,84dvh)] min-h-0 flex-col" data-learning-library>
    <ModalHeader title={zh ? '知识与技能' : 'Knowledge & skills'}
      sub={zh ? '浏览知识、可复用的方法和原始资料。' : 'Browse knowledge, reusable methods and original sources.'} />
    <LearningStatus sid={sid} onOpenKnowledge={() => pick('feed')} onOpenSkills={() => pick('feed')} />
    <nav aria-label={zh ? '学习资源分类' : 'Learning resource categories'} className="flex shrink-0 overflow-x-auto border-b border-line/60 px-4">
      {scopes.map(value => <button key={value} type="button" aria-pressed={scope === value} onClick={() => pick(value)}
        className={`shrink-0 border-b-2 px-3 py-2.5 text-sm ${scope === value ? 'border-blue text-ink' : 'border-transparent text-ink-faint hover:text-ink'}`}>{labels[value]}</button>)}
    </nav>
    <div className="flex min-h-0 flex-1">
      <aside className={`${selected ? 'hidden md:flex' : 'flex'} min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r md:border-line/60`}>
        <div className="space-y-2 p-4">
          <input type="search" value={search} onChange={event => setSearch(event.target.value)}
            aria-label={zh ? '搜索知识与技能' : 'Search knowledge and skills'} placeholder={zh ? '搜索知识与技能' : 'Search knowledge and skills'}
            className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink" />
          {scope === 'global' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '跨项目共享的知识和方法。' : 'Shared knowledge and methods across projects.'}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          {wiki.isError && <p role="alert" className="p-3 text-xs text-err">{zh ? '知识无法加载。' : 'Knowledge could not load.'} <button className="underline" onClick={() => void wiki.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
          {skills.isError && <p role="alert" className="p-3 text-xs text-err">{zh ? '技能无法加载。' : 'Skills could not load.'} <button className="underline" onClick={() => void skills.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
          {(wiki.data?.errors.length || skills.data?.errors.length) ? <p role="status" className="p-3 text-xs text-err">{zh ? '部分资料无法读取；已加载的内容仍可查看。' : 'Some sources could not be read; loaded material remains available.'}</p> : null}
          {scope === 'feed' && <>
            <h3 className="px-3 py-2 text-xs font-medium text-ink-faint">{zh ? '学习记录' : 'Learning records'}</h3>
            {feed.isPending && <p className="p-3 text-xs text-ink-faint">{zh ? '正在加载学习记录…' : 'Loading learning records…'}</p>}
            {feed.isError && <p role="alert" className="p-3 text-xs text-err">{zh ? '学习记录无法加载。' : 'Learning records could not load.'} <button className="underline" onClick={() => void feed.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
            {feed.data && <KnowledgeFeedList events={feed.data.events} items={wiki.data?.items ?? []} sid={sid} projectNames={projectNames} query={search}
              selected={knowledge} onSelect={pickKnowledge} />}
            <h3 className="px-3 py-2 text-xs font-medium text-ink-faint">{zh ? '文件更新' : 'File updates'}</h3>
          </>}
          {(wiki.isPending || skills.isPending) && <p className="p-3 text-xs text-ink-faint">{zh ? '正在加载资料…' : 'Loading material…'}</p>}
          {!wiki.isPending && !skills.isPending && !wiki.isError && !skills.isError && !entries.length && <p className="p-3 text-sm text-ink-faint">{zh ? '当前分类或筛选条件下没有资料。' : 'No material matches this category or filter.'}</p>}
          {entries.map(entry => <button key={entry.id} type="button" onClick={() => setSelection(entry.id)} aria-pressed={selected?.id === entry.id}
            className={`block w-full rounded-md px-3 py-3 text-left ${selected?.id === entry.id ? 'bg-blue/10' : 'hover:bg-bg'}`}>
            <span className="flex items-baseline gap-2"><span className="shrink-0 rounded bg-line/60 px-1.5 text-[10px] text-ink-dim">{kind(entry)}</span>
              <span className="break-words text-sm font-medium text-ink">{entry.title}</span></span>
            {entry.description && <span className="mt-1 block line-clamp-2 text-xs text-ink-dim">{entry.description}</span>}
            <span className="mt-1 block text-[11px] text-ink-faint">{location(entry)}</span>
            {entry.updated > 0 && <time className="mt-1 block text-[11px] text-ink-faint" dateTime={new Date(entry.updated * 1000).toISOString()}>{new Date(entry.updated * 1000).toLocaleString(locale)}</time>}
          </button>)}
        </div>
      </aside>
      <article className={`${selected ? 'block' : 'hidden md:block'} min-w-0 flex-1 overflow-y-auto p-5 sm:p-6`}>
        {selected ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setSelection(null)}>{zh ? '← 返回列表' : '← Back to the list'}</button>
          <h3 className="break-words text-lg font-semibold text-ink">{selected.title}</h3>
          <p className="mt-1 text-xs text-ink-faint">{kind(selected)} · {location(selected)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{selected.path}</p>
          {sid && onUse && <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" disabled={(selected.kind === 'knowledge' || selected.kind === 'skill') && (!document.data || document.isError || document.isPending)}
              onClick={() => {
                const content = selected.kind === 'knowledge' || selected.kind === 'skill' ? document.data?.content : selected.content;
                if (content) onUse(selected.title, content, `${location(selected)} · ${selected.path}${knowledge && wikiDocument.data?.truncated ? (zh ? ' · 仅当前可见节选' : ' · Visible excerpt only') : ''}`);
              }}
              className="rounded-lg border border-blue/30 bg-blue/5 px-3 py-2 text-sm text-blue disabled:opacity-40">{zh ? '用于当前对话' : 'Use in this conversation'}</button>
            <span className="text-xs text-ink-faint">{zh ? '加入附件草稿，发送前可移除。' : 'Adds a draft attachment you can remove before sending.'}
              {selected.scope === 'private' && <span className="mt-1 block text-warn">{zh ? '这份资料来自私有范围。发送后，所选内容会作为附件保存在当前项目；请先确认范围合适。' : 'This material comes from a private scope. Sending saves the selected content as an attachment in the current project; confirm that this scope is appropriate.'}</span>}
            </span>
          </div>}
          {knowledge && <KnowledgeReadingGuide item={knowledge} />}
          {skill && guide && <SkillReadingGuide item={skill} guide={guide} />}
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
            {selected.kind === 'knowledge' || selected.kind === 'skill' ? <>
              {document.isPending && <p>{zh ? '正在加载全文…' : 'Loading document…'}</p>}
              {document.isError && <p role="alert" className="text-err">{document.error.message} <button className="underline" onClick={() => void document.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
              {knowledge && wikiDocument.data?.truncated && <p role="status" className="mb-3 text-xs text-ink-faint">{zh ? '页面过长，已截断显示。' : 'Page shortened for display.'}</p>}
              {document.data && (guide ? <details><summary className="cursor-pointer py-2 text-blue">{zh ? '查看原始技能文档' : 'Read the original skill'}</summary>
                <MarkdownContent>{document.data.content}</MarkdownContent></details> : <MarkdownContent>{document.data.content}</MarkdownContent>)}
            </> : <MarkdownContent>{selected.content}</MarkdownContent>}
          </div>
        </> : <div className="flex items-start gap-2 text-sm text-ink-faint"><BookOpen size={18} aria-hidden="true" />{zh ? '选择一份资料查看。' : 'Select material to read it.'}</div>}
      </article>
    </div>
  </div>;
}
