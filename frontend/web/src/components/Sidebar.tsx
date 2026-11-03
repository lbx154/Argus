import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type ProjectRow } from '../api';
import { LearningLibraryEntry, type LearningScope } from './LearningLibrary';
import { WorkspaceSidePanel } from './WorkspaceShell';
import { AppearanceControls } from './AppearanceControls';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { registerModal } from './modalStack';
import { errorText, shortcut } from '../lib/format';
import { filterProjects } from '../../../core/src/projects';
import type { ThemeMode } from './TopBar';
import { useI18n } from '../i18n';
import { Archive, ChevronDown, Folder, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pin, Flag, Search, Settings, LogOut, Clock, BookOpen } from 'lucide-react';
import { dailyText, localTime, shownName, utcTime } from '../lib/daily';
import { writeLocalStorage } from '../lib/storage';
import { SETTINGS_CAPABILITY, useCapability } from '../lib/capabilities';
import { projectSpendTotal, SPEND_ESTIMATE_HINT, spendEstimateLabel } from '../lib/projectOverview';


type Scope = 'local' | 'all';
const rankChiefFirst = (rows: ProjectRow[]): ProjectRow[] => [...rows.filter(row => row.chief), ...rows.filter(row => !row.chief)];

type ProjectPreference = { pinned: boolean; archived: boolean; followup: boolean };
const ORGANIZATION_KEY = 'argus.project.organization.v1';
const DEFAULT_PREFERENCE: ProjectPreference = { pinned: false, archived: false, followup: false };

function readOrganization(): { values: Record<string, ProjectPreference>; error: boolean } {
  try {
    const raw = globalThis.localStorage?.getItem(ORGANIZATION_KEY);
    if (!raw) return { values: {}, error: false };
    const saved: unknown = JSON.parse(raw);
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) throw new Error('Invalid project preferences');
    const values: Record<string, ProjectPreference> = {};
    for (const [id, value] of Object.entries(saved)) {
      if (!value || typeof value !== 'object' || typeof value.pinned !== 'boolean'
        || typeof value.archived !== 'boolean' || typeof value.followup !== 'boolean') throw new Error('Invalid project preference');
      values[id] = { pinned: value.pinned, archived: value.archived, followup: value.followup };
    }
    return { values, error: false };
  } catch { return { values: {}, error: true }; }
}

function projectGroupLabel(path: string): string {
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts.at(-1) || path;
}

export function recommendedSidebarScope(
  projects: ProjectRow[],
  activeId: string | null,
  localCwd: string,
): Scope {
  if (projects.length === 0) return 'local';
  const normalized = localCwd.trim();
  const local = normalized
    ? projects.filter((project) => project.workdir?.trim() === normalized)
    : [];
  if (local.length === 0) return 'all';
  if (activeId && !local.some((project) => project.id === activeId)) return 'all';
  return 'local';
}

export function Sidebar({
  projects,
  activeId,
  localCwd,
  onSelect,
  onPrefetch,
  onManage,
  onOpenPanel,
  onOpenLearning,
  onNew,
  onSearch,
  onOpenTrash,
  onSignOut,
  loading,
  creating = false,
  error,
  onRetry,
  mobileOpen = false,
  onCloseMobile,
  separator,
  collapsed = false,
  onToggleCollapse,
  themeMode,
  onCycleTheme,
}: {
  projects: ProjectRow[];
  separator?: ReactNode;
  activeId: string | null;
  localCwd: string;
  onSelect: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onManage: (id: string) => void;
  onOpenPanel: (p: 'config') => void;
  onOpenLearning?: (scope: LearningScope) => void;
  /** Opens the current project's delivered results. */
  onNew: () => void;
  onSearch?: () => void;
  onOpenTrash?: () => void;
  onSignOut?: () => void;
  loading: boolean;
  creating?: boolean;
  error?: string;
  onRetry?: () => void;
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
  collapsed?: boolean;
  onToggleCollapse: () => void;
  themeMode: ThemeMode;
  onCycleTheme: () => void;
}) {
  const { t, locale } = useI18n();
  const [scope, setScope] = useState<Scope>('local');
  const initialScopeResolved = useRef(false);
  const [query, setQuery] = useState('');
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [organization, setOrganization] = useState(readOrganization);
  const [projectFilter, setProjectFilter] = useState<'active' | 'followup' | 'archived'>('active');
  const [organizing, setOrganizing] = useState<string | null>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeMobileRef = useRef(onCloseMobile);
  closeMobileRef.current = onCloseMobile;
  useEffect(() => {
    if (!mobileOpen) { setOrganizing(null); return; }
    if (!sidebarRef.current) return;
    return registerModal(sidebarRef.current, () => closeMobileRef.current?.());
  }, [mobileOpen]);
  useEffect(() => {
    if (!organizing || !menuRef.current) return;
    return registerModal(menuRef.current, () => setOrganizing(null));
  }, [organizing]);
  const [organizationNotice, setOrganizationNotice] = useState('');
  const [syncingOrganization, setSyncingOrganization] = useState(true);
  const [syncAttempt, setSyncAttempt] = useState(0);
  const [settingsHint, setSettingsHint] = useState(false);
  const settings = useCapability(SETTINGS_CAPABILITY);
  const zh = locale === 'zh-CN';
  const openKnowledge = onOpenLearning;
  const spendHint = `${SPEND_ESTIMATE_HINT[zh ? 'zh' : 'en']} ${zh ? '不含进行中的调用。' : 'Excludes calls in progress.'}`;
  const totalSpend = spendEstimateLabel(projectSpendTotal(projects), zh, true) || '—';
  const preference = (id: string) => organization.values[id] ?? DEFAULT_PREFERENCE;
  useEffect(() => {
    const focus = () => setSyncAttempt(value => value + 1);
    window.addEventListener('focus', focus);
    return () => window.removeEventListener('focus', focus);
  }, []);
  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    setSyncingOrganization(true);
    void (async () => {
      let remote = await api.organization();
      if (cancelled) return;
      if (remote.revision === 0) {
        for (const { id } of projects) {
          const local = readOrganization().values[id];
          if (!local) continue;
          for (const field of ['pinned', 'archived', 'followup'] as const) {
            if (local[field]) remote = await api.organize(id, field, true);
          }
        }
      }
      if (!cancelled) {
        setOrganization({ values: remote.value, error: !writeLocalStorage(ORGANIZATION_KEY, JSON.stringify(remote.value)) });
        setSyncingOrganization(false);
        setOrganizationNotice('');
      }
    })().catch(problem => {
      if (!cancelled) {
        setOrganizationNotice(`${zh ? '项目标记尚未同步：' : 'Project markers not synced: '}${errorText(problem)}`);
        setSyncingOrganization(false);
      }
    });
    return () => { cancelled = true; };
  }, [loading, syncAttempt, zh]);
  /** The chief's daily letter: the operator gives its time in their own time of day, or none to stop it. */
  const changeDaily = async (project: ProjectRow) => {
    const given = window.prompt(zh ? '每天几点来信？例如 08:00；留空则不再每天来信。' : 'What time each day? For example 08:00; blank for none.',
      project.repeat ? localTime(project.repeat.at) : '08:00');
    if (given === null) return;
    const at = given.trim() ? utcTime(given) : null;
    if (given.trim() && !at) { setOrganizationNotice(zh ? `看不懂这个时间：${given}` : `Not a time of day: ${given}`); return; }
    try {
      await api.setWakes(project.id, { repeat: at ? { at, text: project.repeat?.text || dailyText(zh) } : null });
      setOrganizationNotice(at ? zh ? `以后每天 ${localTime(at)} 来信` : `A letter every day at ${localTime(at)}` : zh ? '不再每天来信' : 'No daily letter');
    } catch (problem) {
      setOrganizationNotice(`${zh ? '没有保存：' : 'Not saved: '}${errorText(problem)}`);
    }
  };
  const organize = async (id: string, field: keyof ProjectPreference) => {
    const next = !preference(id)[field];
    setSyncingOrganization(true);
    try {
      const remote = await api.organize(id, field, next);
      setOrganization({ values: remote.value, error: !writeLocalStorage(ORGANIZATION_KEY, JSON.stringify(remote.value)) });
    } catch (problem) {
      setOrganizationNotice(`${zh ? '标记未保存：' : 'Marker not saved: '}${errorText(problem)}`);
      return;
    } finally { setSyncingOrganization(false); }
    setOrganizing(null);
    setOrganizationNotice(field !== 'archived'
      ? zh ? '标记已同步。' : 'Marker synced.'
      : next ? zh ? '已归档，可在“已归档”中找回。' : 'Archived. Restore it from Archived.'
        : zh ? '已移回最近。' : 'Moved back to Recent.');
  };
  useEffect(() => { setOrganizing(null); setSettingsHint(false); }, [activeId]);
  const slim = collapsed && !mobileOpen;
  const normalizedLocalCwd = localCwd.trim();
  const localProjects = useMemo(
    () => normalizedLocalCwd
      ? projects.filter((project) => project.workdir?.trim() === normalizedLocalCwd)
      : [],
    [normalizedLocalCwd, projects],
  );
  useEffect(() => {
    if (initialScopeResolved.current || loading || projects.length === 0) return;
    initialScopeResolved.current = true;
    setScope(recommendedSidebarScope(projects, activeId, normalizedLocalCwd));
  }, [activeId, loading, normalizedLocalCwd, projects]);
  const scoped = scope === 'local' ? localProjects : projects;
  const rawVisible = query.trim() ? filterProjects(scoped, query) : scoped;
  const visible = rawVisible.filter(project => projectFilter === 'archived'
    ? preference(project.id).archived
    : !preference(project.id).archived && (projectFilter !== 'followup' || preference(project.id).followup));
  const grouped = useMemo(() => {
    // The chief is always at the top, above the projects it looks after.
    const pinned = rankChiefFirst(visible.filter(project => project.chief || preference(project.id).pinned));
    const rest = visible.filter(project => !project.chief && !preference(project.id).pinned);
    const first: Array<readonly [string, ProjectRow[]]> = pinned.length ? [['__pinned__', pinned]] : [];
    if (scope === 'local') return [...first, ...(rest.length ? [[normalizedLocalCwd || 'Local', rest] as const] : [])];
    const groups = new Map<string, ProjectRow[]>();
    rest.forEach((project) => {
      const path = project.workdir?.trim() || t('common.unassigned');
      const rows = groups.get(path) ?? [];
      rows.push(project);
      groups.set(path, rows);
    });
    return [...first, ...groups.entries()];
  }, [normalizedLocalCwd, scope, visible]);
  const groupIsCollapsed = (path: string) => collapsedGroups.has(path) && !query.trim();

  return (
    <div className="contents" role="complementary" aria-label={t('mobile.sessions')}>
    <WorkspaceSidePanel ref={sidebarRef} mobileOpen={mobileOpen} collapsed={collapsed}
      role={mobileOpen ? "dialog" : undefined} aria-modal={mobileOpen ? true : undefined}
      aria-label={mobileOpen ? t('mobile.sessions') : undefined} tabIndex={mobileOpen ? -1 : undefined}>
      <div className={`chrome-seam-surface flex h-12 shrink-0 items-center border-b border-line/50 ${slim ? 'justify-center' : 'justify-between px-4'}`}>
        {slim ? (
          <Wordmark size={22} compact />
        ) : (
          <>
            <Wordmark size={24} />
            <button type="button" onClick={mobileOpen ? onCloseMobile : onToggleCollapse} aria-label={t(mobileOpen ? 'common.closeSessions' : 'sidebar.collapse')} title={`${t('sidebar.collapse')} · ${shortcut('B')}`} className="icon-control flex h-8 w-8 shrink-0 items-center justify-center">
              <PanelLeftClose size={16} />
            </button>
          </>
        )}
      </div>
      {slim ? (
        <div className="flex h-12 shrink-0 items-center justify-center">
          <button type="button" onClick={onToggleCollapse} aria-label={t('sidebar.expand')} title={`${t('sidebar.expand')} · ${shortcut('B')}`} className="icon-control flex h-8 w-8 shrink-0 items-center justify-center">
            <PanelLeftOpen size={16} />
          </button>
        </div>
      ) : null}

      {slim && openKnowledge ? <LearningLibraryEntry onOpen={openKnowledge} compact /> : null}
      {!slim ? (
        <>
          <div className="flex shrink-0 items-center gap-2 px-4 pb-1 pt-3">
            <span className="text-xs font-medium text-ink-faint">{locale === 'zh-CN' ? '项目' : 'Projects'}</span>
            <span data-project-spend-total className="text-[11px] tabular-nums text-ink-faint" title={spendHint}
              aria-label={`${zh ? '全部项目合计' : 'All projects total'} ${totalSpend}`}>{totalSpend}</span>
            {localProjects.length > 0 && localProjects.length < projects.length ? <select aria-label={locale === 'zh-CN' ? '项目范围' : 'Project scope'} value={scope} onChange={event => setScope(event.target.value as Scope)} className="min-w-0 bg-transparent text-xs text-ink-faint">
              <option value="local">{t('common.local')}</option><option value="all">{t('common.all')}</option>
            </select> : null}
            {onSearch && <button type="button" onClick={onSearch} className="sidebar-search ml-auto" aria-label={zh ? '搜索历史与成果' : 'Search history and results'} title={zh ? '搜索历史与成果' : 'Search history and results'}>
              <Search size={15} />
            </button>}
            <button
              type="button"
              onClick={onNew}
              disabled={creating}
              title={t('sidebar.create')}
              className={onSearch ? 'sidebar-new' : 'sidebar-new ml-auto'}
            >
              {creating ? '…' : locale === 'zh-CN' ? '新建' : 'New'}
            </button>
          </div>

          <div className="px-3 py-2">
            <label className="sr-only" htmlFor="daemon-search">{zh ? '筛选项目名称' : 'Filter project names'}</label>
            <div className="sidebar-project-search">
              <Search size={14} className="shrink-0 text-ink-faint" aria-hidden="true" />
              <input
                id="daemon-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={zh ? '筛选项目名称' : 'Filter project names'}
                className="h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-ink-faint"
              />
              {query ? (
                <button type="button" aria-label={t('sidebar.clearSearch')} onClick={() => setQuery('')} className="px-1 text-sm text-ink-faint hover:text-ink">×</button>
              ) : null}
            </div>
          </div>
          <div className="sidebar-project-filters" role="group" aria-label={zh ? '整理项目' : 'Organize projects'}>
            {(['active', 'followup', 'archived'] as const).map(filter => <button type="button" key={filter}
              aria-pressed={projectFilter === filter} onClick={() => { setProjectFilter(filter); setOrganizing(null); }}
              >
              {filter === 'active' ? zh ? '最近' : 'Recent' : filter === 'followup' ? zh ? '待跟进' : 'Follow up' : zh ? '已归档' : 'Archived'}
            </button>)}
            {onOpenTrash && <button type="button" className="sidebar-trash" onClick={onOpenTrash}>{zh ? '回收站' : 'Trash'}</button>}
          </div>
          {organization.error && <p role="alert" className="px-4 pb-2 text-xs text-warn">{zh ? '浏览器无法读取或保存项目标记。' : 'This browser cannot read or save project markers.'}</p>}
          {organizationNotice && <p role="status" className="px-4 pb-2 text-[11px] leading-5 text-ink-faint">{organizationNotice}
            <button type="button" disabled={syncingOrganization} onClick={() => setSyncAttempt(value => value + 1)} className="ml-2 underline">{zh ? '重新同步' : 'Sync again'}</button></p>}

          <div className="mobile-scroll-region min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-3 pb-3 scroll-thin">
            {loading && projects.length === 0 ? <div className="px-1 py-3 text-xs text-ink-faint">{t('common.loading')}</div> : null}
            {error ? (
              <button type="button" onClick={onRetry} className="mb-2 w-full rounded-md bg-err/5 px-3 py-2 text-left text-xs text-err">
                {t('sidebar.refreshFailed')}
              </button>
            ) : null}
            {!loading && !error && visible.length === 0 ? (
              <div className="px-1 py-4 text-xs text-ink-faint">
                <div>{query.trim() ? t('sidebar.noMatches', { query: query.trim() }) : projectFilter === 'followup'
                  ? zh ? '还没有标记待跟进的项目。' : 'No projects marked for follow-up.'
                  : projectFilter === 'archived' ? zh ? '还没有归档的项目。' : 'No archived projects.' : t('sidebar.noSessions')}</div>
                {query.trim() ? (
                  <button
                    type="button"
                    onClick={() => setQuery('')}
                    className="mt-2 text-xs text-ink-dim underline underline-offset-2 hover:text-ink"
                  >
                    {t('sidebar.clearSearch')}
                  </button>
                ) : null}
              </div>
            ) : null}
            {grouped.map(([path, rows]) => (
              <section key={path} className="mb-1 last:mb-0">
                {path === '__pinned__' || rows.length > 1 && grouped.length > 1 ? <button
                  type="button"
                  aria-expanded={!groupIsCollapsed(path)}
                  title={path === '__pinned__' ? (zh ? '置顶项目' : 'Pinned projects') : path}
                  onClick={() => setCollapsedGroups((current) => {
                    const next = new Set(current);
                    if (next.has(path)) next.delete(path);
                    else next.add(path);
                    return next;
                  })}
                  className="mb-1 flex h-7 w-full items-center gap-2 rounded-md px-1.5 text-left text-[11px] font-medium text-ink-faint hover:bg-bg/70 hover:text-ink-dim"
                >
                  <ChevronDown size={12} className={`transition-transform ${groupIsCollapsed(path) ? '-rotate-90' : ''}`} />
                  <Folder size={14} />
                  <span className="min-w-0 flex-1 truncate">{path === '__pinned__' ? (zh ? '置顶项目' : 'Pinned projects') : projectGroupLabel(path)}</span>
                </button> : null}
                {(path === '__pinned__' ? !groupIsCollapsed(path) : rows.length === 1 || grouped.length === 1 || !groupIsCollapsed(path)) ? rows.map((project) => {
                  const active = project.id === activeId;
                  const work = project.work_status;
                  const workLabel = work?.label[locale === 'zh-CN' ? 'zh-CN' : 'en'];
                  const message = project.last_message || workLabel;
                  const name = shownName(project, zh, t('sidebar.unnamedSession'));
                  return (
                    <div
                      key={project.id}
                      data-active={active ? 'true' : 'false'}
                      onPointerEnter={() => {
                        if (!active) onPrefetch?.(project.id);
                      }}
                      className={`session-card group relative mb-0.5 w-full rounded-md transition-colors duration-150 ease-panel ${
                        active ? 'text-ink' : 'text-ink-dim hover:text-ink'
                      }`}
                    >
                      <span aria-hidden="true" className={`absolute left-0 transition-colors ${active ? 'inset-y-3 w-px bg-accent' : 'inset-y-3 w-px bg-transparent'}`} />
                      <button
                        type="button"
                        onClick={() => onSelect(project.id)}
                        onFocus={() => {
                          if (!active) onPrefetch?.(project.id);
                        }}
                        aria-current={active ? 'page' : undefined}
                        title={project.display_name?.trim() ? name : `${name} · ${project.id}`}
                        className="flex min-h-14 w-full min-w-0 flex-col justify-center px-2.5 py-2 pr-10 text-left"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <StatusDot
                            ok={project.daemon_alive}
                          />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
                          {preference(project.id).followup && <Flag size={12} className="shrink-0 text-warn" aria-label={zh ? '待跟进' : 'Follow up'} />}
                        </div>
                        <div className={`mt-1 flex min-w-0 items-start gap-1.5 pl-3.5 text-[11px] leading-4 ${active ? 'text-ink-dim' : 'text-ink-faint'}`}>
                          <span className="min-w-0 flex-1 truncate" title={message}
                            data-session-work-state={work?.state}>
                            {message}
                          </span>
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setOrganizing(current => current === project.id ? null : project.id);
                        }}
                        aria-label={t('sidebar.manage', { name })}
                        title={t('sidebar.manageHint')}
                        aria-expanded={organizing === project.id}
                        className="project-manage absolute right-1 top-3 flex h-8 w-8 items-center justify-center rounded-md text-ink-faint opacity-100 transition-opacity hover:bg-panel-raised hover:text-ink sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                      >
                        <MoreHorizontal size={18} />
                      </button>
                      {organizing === project.id && <div ref={menuRef} role="dialog" aria-modal="true"
                        aria-label={zh ? `管理 ${name}` : `Manage ${name}`} tabIndex={-1} className="project-organization-menu">
                        <button type="button" disabled={syncingOrganization} className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg disabled:opacity-50" onClick={() => void organize(project.id, 'pinned')}><Pin size={13} />{preference(project.id).pinned ? zh ? '取消置顶' : 'Unpin' : zh ? '置顶项目' : 'Pin project'}</button>
                        <button type="button" disabled={syncingOrganization} className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg disabled:opacity-50" onClick={() => void organize(project.id, 'followup')}><Flag size={13} />{preference(project.id).followup ? zh ? '取消待跟进' : 'Clear follow-up' : zh ? '标记待跟进' : 'Mark for follow-up'}</button>
                        <button type="button" disabled={syncingOrganization} className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg disabled:opacity-50" onClick={() => void organize(project.id, 'archived')}><Archive size={13} />{preference(project.id).archived ? zh ? '移出归档' : 'Restore to recent' : zh ? '归档项目' : 'Archive project'}</button>
                        {project.chief && <button type="button" className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg" onClick={() => { setOrganizing(null); void changeDaily(project); }}><Clock size={13} />{project.repeat
                          ? zh ? `每天 ${localTime(project.repeat.at)} 来信（改）` : `Daily letter at ${localTime(project.repeat.at)} (change)`
                          : zh ? '设定每天来信的时间' : 'Set a daily letter'}</button>}
                        <button type="button" className="rounded border-t border-line px-2 py-2 text-left hover:bg-bg" onClick={() => { setOrganizing(null); onManage(project.id); }}>{zh ? '名称与项目管理' : 'Name and project settings'}</button>
                      </div>}
                    </div>
                  );
                }) : null}
              </section>
            ))}
          </div>

          {/* Settings stay one click away; rarer tools fold into one quiet menu. */}
          <div className="sidebar-footer">
            {settings && <button type="button" onClick={() => activeId ? onOpenPanel('config') : setSettingsHint(true)} className="icon-control flex h-8 items-center gap-2 px-2 text-xs" aria-label={t('sidebar.openSettings')} title={zh ? '项目设置' : 'Project settings'}>
              <Settings size={15} />{zh ? '设置' : 'Settings'}
            </button>}
            {openKnowledge && <button type="button" onClick={() => openKnowledge('feed')} className="icon-control flex h-8 items-center gap-2 px-2 text-xs" aria-label={zh ? '技能库' : 'Skill library'}>
              <BookOpen size={15} />{zh ? '技能库' : 'Skill library'}
            </button>}
            <details className="sidebar-more">
              <summary className="icon-control flex h-8 w-8 items-center justify-center" aria-label={zh ? '更多' : 'More'} title={zh ? '语言、外观与登录' : 'Language, appearance and sign-in'}>
                <MoreHorizontal size={16} aria-hidden="true" />
              </summary>
              <div className="sidebar-more-menu">
                {onSignOut && <button type="button" onClick={onSignOut} className="icon-control flex h-8 items-center gap-2 px-2 text-xs"><LogOut size={15} />{zh ? '退出登录' : 'Sign out'}</button>}
                <AppearanceControls themeMode={themeMode} onCycleTheme={onCycleTheme} />
              </div>
            </details>
            {settingsHint && <p role="status" className="sidebar-settings-hint">{zh ? '先打开或创建一个项目，再调整该项目的设置。' : 'Open or create a project first to adjust its settings.'}</p>}
          </div>
        </>
      ) : null}
    </WorkspaceSidePanel>
    {separator}
    </div>
  );
}
