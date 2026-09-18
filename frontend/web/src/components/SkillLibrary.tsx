import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, ChevronDown, ChevronRight } from 'lucide-react';
import { api, type SkillLibraryItem, type SkillScope } from '../api';
import { useI18n } from '../i18n';
import { useSidebarFold } from '../lib/sidebarFold';
import { MarkdownContent } from './MarkdownContent';
import { ModalHeader } from './Modal';

const scopes: SkillScope[] = ['global', 'vertical', 'project'];
const labels = {
  en: { global: 'Global', vertical: 'Vertical', project: 'Project', recent: 'Recent updates' },
  zh: { global: '全局', vertical: '垂直领域', project: '项目', recent: '最近更新' },
};
const identity = (item: SkillLibraryItem) => `${item.library}/${item.path}`;
export const recentSkills = (items: SkillLibraryItem[]) => items.filter(item => !item.is_default && item.updated_at != null)
  .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || identity(a).localeCompare(identity(b)));

function useSkillLibrary(sid: string | null, enabled = true) {
  return useQuery({
    queryKey: ['skill-library', sid],
    queryFn: ({ signal }) => api.skillLibrary(sid, signal),
    enabled,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

/** Keep new learning visible beside the project, including shared Skills. */
export function SkillLibraryEntry({ sid, onOpen, compact = false, visible = true, defaultExpanded = false }: {
  sid: string | null;
  onOpen: (item?: SkillLibraryItem) => void;
  compact?: boolean;
  visible?: boolean;
  /** Whether the recent-updates list starts open; the operator's fold is remembered afterwards. */
  defaultExpanded?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const [expanded, toggle] = useSidebarFold('skills', defaultExpanded);
  const catalog = useSkillLibrary(sid, visible && !compact);
  const recent = recentSkills(catalog.data?.items ?? []).slice(0, 3);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return <section className={`shrink-0 border-t border-line/60 ${compact ? 'py-3' : 'mx-3 py-2'}`}>
    <div className={`flex items-center ${compact ? 'justify-center' : ''}`}>
      <button type="button" onClick={() => onOpen()} title={zh ? '技能库' : 'Skill library'} aria-label={zh ? '技能库' : 'Skill library'}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-2 text-sm text-ink-dim hover:bg-bg ${compact ? 'justify-center' : ''}`}>
        <BookOpen className="h-4 w-4 shrink-0" />{!compact && (zh ? '技能库' : 'Skill library')}
      </button>
      {!compact && recent.length > 0 && <button type="button" onClick={toggle} aria-expanded={expanded} data-sidebar-fold="skills"
        aria-label={zh ? (expanded ? '收起最近更新' : '展开最近更新') : (expanded ? 'Hide recent updates' : 'Show recent updates')}
        className="rounded p-1.5 text-ink-faint hover:bg-bg hover:text-ink"><Chevron className="h-3.5 w-3.5" /></button>}
    </div>
    {!compact && expanded && recent.length > 0 && <div className="px-2 pb-1">
      <p className="mb-1 text-[11px] text-ink-faint">{names.recent}</p>
      {recent.map(item => <button type="button" key={identity(item)} onClick={() => onOpen(item)}
        className="block w-full rounded py-1.5 text-left hover:text-blue" title={item.description || item.name}>
        <span className="block truncate text-xs text-ink">{item.name}</span>
        <span className="block text-[10px] text-ink-faint">{names[item.scope]}{item.vertical && ` · ${item.vertical}`}</span>
      </button>)}
    </div>}
    {!compact && catalog.isError && <p className="px-2 text-xs text-ink-faint">{zh ? '技能更新暂时无法加载' : 'Skill updates unavailable'}</p>}
  </section>;
}

export function SkillLibrary({ sid, projectName, initialSelection = null, initialScope = 'recent' }: {
  sid: string | null;
  projectName?: string;
  initialSelection?: SkillLibraryItem | null;
  initialScope?: SkillScope | 'recent';
}) {
  // A project change remounts the browser, including its document selection.
  const initialSid = useRef(sid);
  return <LibraryBrowser key={sid ?? 'no-project'} sid={sid} projectName={projectName} initialSelection={initialSid.current === sid ? initialSelection : null} initialScope={initialScope} />;
}

function LibraryBrowser({ sid, projectName, initialSelection, initialScope }: {
  sid: string | null; projectName?: string; initialSelection: SkillLibraryItem | null; initialScope: SkillScope | 'recent';
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = labels[zh ? 'zh' : 'en'];
  const [scope, setScope] = useState<SkillScope | 'recent'>(initialScope);
  const [search, setSearch] = useState('');
  const [vertical, setVertical] = useState('');
  const [selection, setSelection] = useState(initialSelection);
  const catalog = useSkillLibrary(sid);
  const all = catalog.data?.items ?? [];
  const selected = selection && all.find(item => identity(item) === identity(selection));
  const document = useQuery({
    queryKey: ['skill-document', sid, selected?.library, selected?.path, selected?.updated_at],
    queryFn: ({ signal }) => api.skillDocument(sid, selected!.library, selected!.path, signal),
    enabled: Boolean(selected),
  });
  const query = search.trim().toLocaleLowerCase();
  const candidates = scope === 'recent' ? recentSkills(all) : all.filter(item => item.scope === scope);
  const items = candidates.filter(item => (scope !== 'vertical' || !vertical || item.vertical === vertical)
    && (!query || `${item.name} ${item.description} ${item.path} ${item.vertical}`.toLocaleLowerCase().includes(query)));
  const pickScope = (value: SkillScope | 'recent') => { setScope(value); setSelection(null); };
  const timestamp = (value: number) => new Date(value * 1000).toLocaleString(zh ? 'zh-CN' : 'en-US');
  const source = (item: SkillLibraryItem) => item.is_default ? (zh ? '内置技能' : 'Built-in')
    : item.scope === 'project' ? (projectName || (zh ? '当前项目' : 'This project'))
      : (zh ? '共享技能库' : 'Shared library');

  return <div className="flex h-[min(780px,84dvh)] min-h-0 flex-col" data-skill-library>
    <ModalHeader title={zh ? '技能库' : 'Skill library'} sub={zh ? '查看工作中沉淀的方法，以及各层级可用的技能。' : 'See what work has taught Argus and read the skills available at each level.'} />
    <nav aria-label={zh ? '技能分类' : 'Skill categories'} className="flex shrink-0 overflow-x-auto border-b border-line/60 px-4">
      {(['recent', ...scopes] as const).map(value => <button key={value} type="button" aria-pressed={scope === value} onClick={() => pickScope(value)}
        className={`shrink-0 border-b-2 px-2 py-2.5 text-xs sm:px-3 sm:text-sm ${scope === value ? 'border-blue text-ink' : 'border-transparent text-ink-faint hover:text-ink'}`}>
        {value === 'recent' ? <>{zh ? '最近' : 'Recent'}<span className="hidden sm:inline">{zh ? '更新' : ' updates'}</span></> : names[value]}{value !== 'recent' && catalog.data && <span className="ml-1.5 text-xs text-ink-faint">{all.filter(item => item.scope === value).length}</span>}
      </button>)}
    </nav>
    <div className="flex min-h-0 flex-1">
      <div className={`${selected ? 'hidden md:flex' : 'flex'} min-h-0 w-full flex-col md:w-80 md:shrink-0 md:border-r md:border-line/60`}>
        <div className="space-y-2 p-4">
          <input type="search" value={search} onChange={event => setSearch(event.target.value)} aria-label={zh ? '搜索技能' : 'Search skills'}
            placeholder={zh ? '搜索技能' : 'Search skills'} className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink" />
          {scope === 'vertical' && <select value={vertical} onChange={event => { setVertical(event.target.value); setSelection(null); }} aria-label={zh ? '垂直领域' : 'Vertical'}
            className="w-full rounded-md border border-line bg-bg px-2 py-2 text-sm text-ink">
            <option value="">{zh ? '所有领域' : 'All verticals'}</option>
            {catalog.data?.verticals.map(value => <option key={value} value={value}>{value}{value === catalog.data.active_vertical ? (zh ? ' · 当前项目' : ' · this project') : ''}</option>)}
          </select>}
          {scope === 'recent' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '按文件更新时间排列，涵盖所有分类；不含未修改的内置技能。' : 'All classes, newest file updates first. Unchanged built-in skills are excluded.'}</p>}
          {scope === 'global' && <p className="text-xs leading-relaxed text-ink-faint">{zh ? '所有项目和任务均可访问，执行时按需读取。' : 'Available to every project and task; read as needed during work.'}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4" aria-label={zh ? '技能列表' : 'Skills'}>
          {catalog.isPending && <p className="p-3 text-sm text-ink-faint">{zh ? '正在加载技能…' : 'Loading skills…'}</p>}
          {catalog.isError && <div role="alert" className="p-3 text-sm text-err">{zh ? '无法加载技能库。' : 'Could not load the skill library.'} <button type="button" className="underline" onClick={() => void catalog.refetch()}>{zh ? '重试' : 'Retry'}</button></div>}
          {!catalog.isPending && !catalog.isError && items.length === 0 && <p className="p-3 text-sm leading-relaxed text-ink-faint">{query || (scope === 'vertical' && vertical) ? <>{zh ? '当前筛选条件下没有技能。' : 'No skills match the current filters.'} <button type="button" className="underline" onClick={() => { setSearch(''); setVertical(''); setSelection(null); }}>{zh ? '清除筛选' : 'Clear filters'}</button></>
            : scope === 'project' && !sid ? (zh ? '选择一个项目以查看它的技能。' : 'Select a project to see its skills.')
              : scope === 'recent' ? (zh ? '尚无技能更新。工作中保存的新技能和修改会自动出现在这里。' : 'No skill updates yet. Skills saved or changed during work will appear here automatically.')
                : scope === 'project' ? (zh ? '该项目尚未保存技能。工作中沉淀的技能会出现在这里。' : 'This project has no saved skills yet. Skills learned during work will appear here.')
                  : (zh ? '该分类暂无技能。' : 'No skills in this class yet.')}</p>}
          {items.map(item => <button key={identity(item)} type="button" onClick={() => setSelection(item)} aria-pressed={selected && identity(selected) === identity(item) || false}
            className={`block w-full rounded-md px-3 py-3 text-left ${selected && identity(selected) === identity(item) ? 'bg-blue/10' : 'hover:bg-bg'}`}>
            <span className="block break-words text-sm font-medium text-ink">{item.name}</span>
            {item.description && <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink-dim">{item.description}</span>}
            <span className="mt-1.5 block text-[11px] text-ink-faint">{names[item.scope]}{item.vertical && ` · ${item.vertical}`} · {source(item)}</span>
            {item.updated_at != null && <time className="mt-1 block text-[11px] text-ink-faint" dateTime={new Date(item.updated_at * 1000).toISOString()}>{timestamp(item.updated_at)}</time>}
          </button>)}
          {catalog.data?.errors.length ? <p role="status" className="p-3 text-xs text-err">{zh ? '部分技能文件无法读取。' : 'Some skill files could not be read.'}</p> : null}
        </div>
      </div>
      <article className={`${selected ? 'block' : 'hidden md:block'} min-w-0 flex-1 overflow-y-auto p-5 sm:p-6`} aria-label={zh ? '技能全文' : 'Skill document'}>
        {selected ? <>
          <button type="button" className="mb-4 text-sm text-blue md:hidden" onClick={() => setSelection(null)}>{zh ? '← 返回列表' : '← Back to skills'}</button>
          <h3 className="break-words text-lg font-semibold text-ink">{selected.name}</h3>
          <p className="mt-1 text-xs text-ink-faint">{names[selected.scope]}{selected.vertical && ` · ${selected.vertical}`} · {source(selected)}</p>
          <p className="mt-1 break-all text-xs text-ink-faint">{selected.path}</p>
          {selected.updated_at != null && <p className="mt-1 text-xs text-ink-faint">{zh ? '文件更新于 ' : 'File updated '}{timestamp(selected.updated_at)}</p>}
          {selected.description && <p className="mt-4 text-sm leading-relaxed text-ink-dim">{selected.description}</p>}
          <div className="mt-5 border-t border-line/60 pt-5 text-sm text-ink">
            {document.isPending && <p>{zh ? '正在加载全文…' : 'Loading document…'}</p>}
            {document.isError && <p role="alert" className="text-err">{document.error.message} <button type="button" className="underline" onClick={() => void document.refetch()}>{zh ? '重试' : 'Retry'}</button></p>}
            {document.data && <MarkdownContent>{document.data.content}</MarkdownContent>}
          </div>
        </> : <p className="text-sm text-ink-faint">{zh ? '选择技能，查看完整方法与使用条件。' : 'Select a skill to read its full instructions and when to use it.'}</p>}
      </article>
    </div>
  </div>;
}
