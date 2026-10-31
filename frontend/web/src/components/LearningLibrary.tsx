import { ErrorMessage } from './ErrorMessage';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen } from 'lucide-react';
import { api, type SkillCatalog, type SkillLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { skillPresentation, skillSearchText } from '../lib/libraryPresentation';
import { KnowledgeFeedList, useKnowledgeFeed } from './KnowledgeFeed';
import { TablePreview } from './DataPreview';
import { MarkdownContent } from './MarkdownContent';
import { ModalHeader } from './Modal';

const skillIdentity = (item: SkillLibraryItem) => `${item.library}/${item.path}`;
const recentSkills = (items: SkillLibraryItem[]) => items.filter(item => item.source !== 'bundled' && item.updated_at != null)
  .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || skillIdentity(a).localeCompare(skillIdentity(b)));
const libraryQuery = { staleTime: 10_000, refetchInterval: 15_000 };

/** What was learned lately, or everything the library holds. */
export type LearningScope = 'feed' | 'global';
const scopes: LearningScope[] = ['feed', 'global'];
const learningScopeLabels = {
  'zh-CN': { feed: '学习动态', global: '全局项目' },
  en: { feed: 'Learning feed', global: 'Across projects' },
};
type Entry = {
  id: string; title: string; description: string; path: string; updated: number; search: string;
} & { item: SkillLibraryItem };

function learningEntries(skills: SkillCatalog | undefined, scope: LearningScope): Entry[] {
  const methods: Entry[] = (scope === 'feed' ? recentSkills(skills?.items ?? []) : skills?.items ?? [])
    .map(item => {
      const presentation = skillPresentation(item);
      return { item, id: `skill/${item.library}/${item.path}`,
        title: presentation.title, description: presentation.summary, path: item.path,
        updated: item.updated_at ?? 0, search: skillSearchText(item) };
    });
  return methods.sort((a, b) => b.updated - a.updated || a.id.localeCompare(b.id));
}

export function LearningLibraryEntry({ onOpen, compact = false }: {
  onOpen: (scope: LearningScope) => void; compact?: boolean;
}) {
  const { locale } = useI18n();
  const label = locale === 'zh-CN' ? '技能库' : 'Skill library';
  return <section className={`shrink-0 ${compact ? 'py-2' : 'mx-2 py-1'}`} data-learning-navigation>
    <button type="button" onClick={() => onOpen('feed')} aria-label={label} title={label}
      className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-ink-dim hover:bg-bg hover:text-ink ${compact ? 'justify-center' : ''}`}>
      <BookOpen size={16} aria-hidden="true" />{!compact && label}
    </button>
  </section>;
}

export function LearningLibrary({ sid, initialScope = 'feed', onUse }: {
  sid: string | null; initialScope?: LearningScope;
  onUse?: (title: string, content: string, scope: string) => void;
}) {
  return <LearningBrowser key={sid ?? 'no-project'} sid={sid} initialScope={initialScope}
    onUse={onUse} />;
}

function LearningBrowser({ sid, initialScope, onUse }: {
  sid: string | null; initialScope: LearningScope;
  onUse?: (title: string, content: string, scope: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const labels = learningScopeLabels[locale];
  const [scope, setScope] = useState(initialScope);
  const [search, setSearch] = useState('');
  const [selection, setSelection] = useState<SkillLibraryItem | null>(null);
  const skills = useQuery({ ...libraryQuery, queryKey: ['skill-library', sid], queryFn: ({ signal }) => api.skillLibrary(sid, signal) });
  const feed = useKnowledgeFeed(scope === 'feed');
  const all = learningEntries(skills.data, scope);
  const selected = selection && (all.find(entry => entry.id === `skill/${selection.library}/${selection.path}`)
    ?? { item: selection, id: `skill/${selection.library}/${selection.path}`, title: selection.name,
      description: selection.description, path: selection.path, updated: selection.updated_at ?? 0, search: '' });
  const skill = selected?.item;
  const skillDocument = useQuery({
    queryKey: ['skill-document', sid, skill?.library, skill?.path, skill?.updated_at],
    queryFn: ({ signal }) => {
      if (!skill) throw new Error('No skill document selected');
      return api.skillDocument(sid, skill.library, skill.path, signal);
    }, enabled: Boolean(skill),
  });
  const query = search.trim().toLocaleLowerCase();
  const entries = all.filter(entry => !query || entry.search.includes(query));
  const location = (entry: Entry) => ({ bundled: zh ? 'Argus 自带' : 'Argus bundled', store: zh ? '商店' : 'Store', shared: zh ? '团队' : 'Team' })[entry.item.source];
  const kind = (path: string) => path.endsWith('SKILL.md') ? (zh ? '技能' : 'Skill') : (zh ? '文档' : 'Document');
  const document = skillDocument;
  const body = document.data?.content.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '');
  const pick = (next: LearningScope) => { setScope(next); setSelection(null); };

  return <div className="flex h-[min(780px,84dvh)] min-h-0 flex-col" data-learning-library>
    <ModalHeader title={zh ? '技能库' : 'Skill library'}
      sub={zh ? '浏览可复用的方法和原始技能文档。' : 'Browse reusable methods and original skill documents.'} />
    <nav aria-label={zh ? '学习资源分类' : 'Learning resource categories'} className="flex shrink-0 overflow-x-auto border-b border-line/60 px-4">
      {scopes.map(value => <button key={value} type="button" aria-pressed={scope === value} onClick={() => pick(value)}
        className={`shrink-0 border-b-2 px-3 py-2.5 text-sm ${scope === value ? 'border-blue text-ink' : 'border-transparent text-ink-faint hover:text-ink'}`}>{labels[value]}</button>)}
    </nav>
    <div className="flex min-h-0 flex-1">
      <aside className={`${selected ? 'hidden md:flex' : 'flex'} min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r md:border-line/60`}>
        <div className="space-y-2 p-4">
          <input type="search" value={search} onChange={event => setSearch(event.target.value)}
            aria-label={zh ? '搜索技能' : 'Search skills'} placeholder={zh ? '搜索技能' : 'Search skills'}
            className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink" />
          {scope === 'global' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '跨项目共享的方法。' : 'Shared methods across projects.'}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          {skills.isError && <p role="alert" className="p-3 text-xs text-err">{zh ? '技能无法加载。' : 'Skills could not load.'} <button className="underline" onClick={() => void skills.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
          {scope === 'feed' && <>
            <h3 className="px-3 py-2 text-xs font-medium text-ink-faint">{zh ? '学习记录' : 'Learning records'}</h3>
            {feed.isPending && <p className="p-3 text-xs text-ink-faint">{zh ? '正在加载学习记录…' : 'Loading learning records…'}</p>}
            {feed.isError && <p role="alert" className="p-3 text-xs text-err">{zh ? '学习记录无法加载。' : 'Learning records could not load.'} <button className="underline" onClick={() => void feed.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
            {feed.data && <KnowledgeFeedList events={feed.data.events} items={skills.data?.items ?? []} query={search}
              selected={skill} onSelect={setSelection} />}
            <h3 className="px-3 py-2 text-xs font-medium text-ink-faint">{zh ? '文件更新' : 'File updates'}</h3>
          </>}
          {skills.isPending && <p className="p-3 text-xs text-ink-faint">{zh ? '正在加载资料…' : 'Loading material…'}</p>}
          {!skills.isPending && !skills.isError && !entries.length && <p className="p-3 text-sm text-ink-faint">{zh ? '当前分类或筛选条件下没有资料。' : 'No material matches this category or filter.'}</p>}
          {entries.map(entry => <button key={entry.id} type="button" onClick={() => setSelection(entry.item)} aria-pressed={selected?.id === entry.id}
            className={`block w-full rounded-md px-3 py-3 text-left ${selected?.id === entry.id ? 'bg-blue/10' : 'hover:bg-bg'}`}>
            <span className="flex items-baseline gap-2"><span className="shrink-0 rounded bg-line/60 px-1.5 text-[10px] text-ink-dim">{kind(entry.path)}</span>
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
          <p className="mt-1 text-xs text-ink-faint">{kind(selected.path)} · {location(selected)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{selected.path}</p>
          {sid && onUse && <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" disabled={!document.data || document.isError || document.isPending}
              onClick={() => {
                const content = document.data?.content;
                if (content) onUse(selected.title, content, `${location(selected)} · ${selected.path}`);
              }}
              className="rounded-lg border border-blue/30 bg-blue/5 px-3 py-2 text-sm text-blue disabled:opacity-40">{zh ? '用于当前对话' : 'Use in this conversation'}</button>
            <span className="text-xs text-ink-faint">{zh ? '加入附件草稿，发送前可移除。' : 'Adds a draft attachment you can remove before sending.'}
            </span>
          </div>}
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
              {document.isPending && <p>{zh ? '正在加载全文…' : 'Loading document…'}</p>}
              {document.isError && <div role="alert" className="text-err"><ErrorMessage error={document.error} /> <button className="underline" onClick={() => void document.refetch()}>{zh ? '重试' : 'Retry'}</button></div>}
              {document.data && (selected.path.endsWith('.tsv')
                ? <TablePreview value={document.data.content} delimiter={'\t'} />
                : <MarkdownContent>{body ?? ''}</MarkdownContent>)}
          </div>
        </> : <div className="flex items-start gap-2 text-sm text-ink-faint"><BookOpen size={18} aria-hidden="true" />{zh ? '选择一份资料查看。' : 'Select material to read it.'}</div>}
      </article>
    </div>
  </div>;
}
