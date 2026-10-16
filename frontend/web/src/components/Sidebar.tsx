import { useEffect, useMemo, useRef, useState } from 'react';
import type { ProjectRow } from '../api';
import { LearningLibraryEntry, type LearningScope } from './LearningLibrary';
import { PluginLauncher } from './PluginLauncher';
import { VerticalStoreEntry } from './VerticalStore';
import { WorkspaceSidePanel } from './WorkspaceShell';
import { AppearanceControls } from './AppearanceControls';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { formatRelativeTime, uptime } from '../lib/format';
import { workStatusLabel, type WorkStatus } from '../lib/workStatus';
import { filterProjects, hasHumanProjectLabel } from '../../../core/src/projects';
import type { ThemeMode } from './TopBar';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAnglesLeft,
  faAnglesRight,
  faChevronDown,
  faEllipsis,
  faFolder,
  faGear,
  faMicrochip,
  faPlay,
} from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';
import { Archive, Pin, Flag } from 'lucide-react';
import { writeLocalStorage } from '../lib/storage';

type Scope = 'local' | 'all';
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
    ? projects.filter((project) => project.launch_cwd?.trim() === normalized)
    : [];
  if (local.length === 0) return 'all';
  if (activeId && !local.some((project) => project.id === activeId)) return 'all';
  return 'local';
}

export function Sidebar({
  projects,
  activeId,
  activeWork,
  localCwd,
  onSelect,
  onPrefetch,
  onManage,
  onResume,
  resumingId,
  onOpenPanel,
  onOpenLearning,
  onOpenVerticals,
  onNew,
  loading,
  creating = false,
  error,
  onRetry,
  mobileOpen = false,
  collapsed = false,
  onToggleCollapse,
  themeMode,
  onCycleTheme,
}: {
  projects: ProjectRow[];
  activeId: string | null;
  activeWork?: { sessionId: string; status: WorkStatus; connected: boolean };
  localCwd: string;
  onSelect: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onManage: (id: string) => void;
  onResume?: (id: string) => void;
  resumingId?: string | null;
  onOpenPanel: (p: 'doctor' | 'config' | 'identity') => void;
  onOpenLearning?: (scope: LearningScope) => void;
  onOpenVerticals?: () => void;
  onNew: () => void;
  loading: boolean;
  creating?: boolean;
  error?: string;
  onRetry?: () => void;
  mobileOpen?: boolean;
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
  const [organizationNotice, setOrganizationNotice] = useState('');
  const zh = locale === 'zh-CN';
  const preference = (id: string) => organization.values[id] ?? DEFAULT_PREFERENCE;
  const organize = (id: string, field: keyof ProjectPreference) => {
    const next = { ...organization.values, [id]: { ...preference(id), [field]: !preference(id)[field] } };
    setOrganization({ values: next, error: !writeLocalStorage(ORGANIZATION_KEY, JSON.stringify(next)) });
    setOrganizing(null);
    setOrganizationNotice(field === 'archived'
      ? zh ? '只整理此浏览器的列表，不删除文件、不停止工作。可在“已归档”中找回。' : 'Only this browser’s list changes. No files are deleted and no work is stopped. Restore from Archived.'
      : zh ? '标记已更新，只保存在此浏览器。' : 'The marker was updated in this browser only.');
  };
  useEffect(() => { setOrganizing(null); }, [activeId]);
  const slim = collapsed && !mobileOpen;
  const normalizedLocalCwd = localCwd.trim();
  const localProjects = useMemo(
    () => normalizedLocalCwd
      ? projects.filter((project) => project.launch_cwd?.trim() === normalizedLocalCwd)
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
    const pinned = visible.filter(project => preference(project.id).pinned);
    const rest = visible.filter(project => !preference(project.id).pinned);
    const first: Array<readonly [string, ProjectRow[]]> = pinned.length ? [['__pinned__', pinned]] : [];
    if (scope === 'local') return [...first, ...(rest.length ? [[normalizedLocalCwd || 'Local', rest] as const] : [])];
    const groups = new Map<string, ProjectRow[]>();
    rest.forEach((project) => {
      const path = project.launch_cwd?.trim() || t('common.unassigned');
      const rows = groups.get(path) ?? [];
      rows.push(project);
      groups.set(path, rows);
    });
    return [...first, ...groups.entries()];
  }, [normalizedLocalCwd, scope, visible]);
  const groupIsCollapsed = (path: string) => collapsedGroups.has(path) && !query.trim();

  return (
    <WorkspaceSidePanel mobileOpen={mobileOpen} collapsed={collapsed}>
      <div className={`chrome-seam-surface flex h-12 shrink-0 items-center border-b border-line/50 ${slim ? 'justify-center' : 'justify-between px-4'}`}>
        {slim ? (
          <Wordmark size={22} compact />
        ) : (
          <>
            <Wordmark size={24} />
            <button type="button" onClick={onToggleCollapse} aria-label={t('sidebar.collapse')} title={`${t('sidebar.collapse')} · Ctrl/⌘ B`} className="icon-control flex h-8 w-8 shrink-0 items-center justify-center">
              <FontAwesomeIcon icon={faAnglesLeft} className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
      {slim ? (
        <div className="flex h-12 shrink-0 items-center justify-center">
          <button type="button" onClick={onToggleCollapse} aria-label={t('sidebar.expand')} title={`${t('sidebar.expand')} · Ctrl/⌘ B`} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink">
            <FontAwesomeIcon icon={faAnglesRight} className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}

      {slim ? <PluginLauncher compact /> : null}
      {slim && onOpenLearning ? <LearningLibraryEntry onOpen={onOpenLearning} compact /> : null}
      {slim && onOpenVerticals ? <VerticalStoreEntry compact onOpen={onOpenVerticals} /> : null}
      {!slim ? (
        <>
          <nav aria-label={locale === 'zh-CN' ? '工具与知识' : 'Tools and knowledge'} data-sidebar-resources
            className="shrink-0 overflow-y-auto overscroll-contain border-b border-line/50 px-2 py-2 scroll-thin [&>section]:mx-2 [&>section]:border-0 [&>section]:py-0 [&>section>div>button:first-child]:px-3"
            style={{ maxHeight: 'min(40dvh, 360px)' }}>
            <PluginLauncher />
            {onOpenLearning && <LearningLibraryEntry onOpen={onOpenLearning} />}
            {onOpenVerticals ? <VerticalStoreEntry onOpen={onOpenVerticals} /> : null}
          </nav>
          <div className="flex shrink-0 items-center gap-2 px-4 pb-1 pt-3">
            <span className="text-xs font-medium text-ink-faint">{locale === 'zh-CN' ? '项目' : 'Projects'}</span>
            {localProjects.length > 0 && localProjects.length < projects.length ? <select aria-label={locale === 'zh-CN' ? '项目范围' : 'Project scope'} value={scope} onChange={event => setScope(event.target.value as Scope)} className="min-w-0 bg-transparent text-xs text-ink-faint">
              <option value="local">{t('common.local')}</option><option value="all">{t('common.all')}</option>
            </select> : null}
            <button
              type="button"
              onClick={onNew}
              disabled={creating}
              aria-label={t('sidebar.create')}
              title={t('sidebar.create')}
              className="ml-auto flex h-8 items-center justify-center rounded-md px-2 text-xs text-ink-dim hover:bg-bg disabled:opacity-40"
            >
              {creating ? '…' : locale === 'zh-CN' ? '+ 新建' : '+ New'}
            </button>
          </div>

          <div className="px-3 py-2">
            <label className="sr-only" htmlFor="daemon-search">{t('sidebar.find')}</label>
            <div className="flex items-center rounded-md border border-line/60 bg-bg/60 px-2 focus-within:border-blue/60">
              <input
                id="daemon-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('sidebar.find')}
                className="h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-ink-faint"
              />
              {query ? (
                <button type="button" aria-label={t('sidebar.clearSearch')} onClick={() => setQuery('')} className="px-1 text-sm text-ink-faint hover:text-ink">×</button>
              ) : null}
            </div>
          </div>
          <div className="flex gap-1 px-3 pb-2" role="group" aria-label={zh ? '整理项目' : 'Organize projects'}>
            {(['active', 'followup', 'archived'] as const).map(filter => <button type="button" key={filter}
              aria-pressed={projectFilter === filter} onClick={() => { setProjectFilter(filter); setOrganizing(null); }}
              className={`rounded-md px-2 py-1 text-xs ${projectFilter === filter ? 'bg-blue/10 text-blue' : 'text-ink-faint hover:bg-bg'}`}>
              {filter === 'active' ? zh ? '最近' : 'Recent' : filter === 'followup' ? zh ? '待跟进' : 'Follow up' : zh ? '已归档' : 'Archived'}
            </button>)}
          </div>
          {organization.error && <p role="alert" className="px-4 pb-2 text-xs text-warn">{zh ? '浏览器无法读取或保存项目标记；服务器项目不受影响。' : 'This browser cannot read or save project markers. Server projects are unaffected.'}</p>}
          {organizationNotice && <p role="status" className="px-4 pb-2 text-[11px] leading-5 text-ink-faint">{organizationNotice}</p>}
          {projectFilter !== 'active' && <p className="px-4 pb-2 text-[11px] text-ink-faint">{projectFilter === 'followup'
            ? zh ? '你手动标记的项目，不代表后台任务状态。' : 'Projects you marked manually, not background task status.'
            : zh ? '归档只收起列表，项目内容和运行不受影响。' : 'Archiving only hides list entries. Project data and running work are unchanged.'}</p>}

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
                  <FontAwesomeIcon icon={faChevronDown} className={`h-2.5 w-2.5 transition-transform ${groupIsCollapsed(path) ? '-rotate-90' : ''}`} />
                  <FontAwesomeIcon icon={faFolder} className="h-3 w-3" />
                  <span className="min-w-0 flex-1 truncate">{path === '__pinned__' ? (zh ? '置顶项目' : 'Pinned projects') : projectGroupLabel(path)}</span>
                </button> : null}
                {(path === '__pinned__' ? !groupIsCollapsed(path) : rows.length === 1 || grouped.length === 1 || !groupIsCollapsed(path)) ? rows.map((project) => {
                  const active = project.id === activeId;
                  const work = active && activeWork?.sessionId === project.id ? activeWork : undefined;
                  const workLabel = work ? workStatusLabel(work.status, locale, work.connected) : undefined;
                  const hasHumanLabel = hasHumanProjectLabel(project);
                  const name = hasHumanLabel
                    ? (project.label || project.display_name || '').trim()
                    : project.objective.trim() || t('sidebar.unnamedSession');
                  const incompatible = project.daemon_alive && project.daemon_protocol_compatible === false;
                  // A release difference does not stop an existing executor.
                  // Keep actual protocol/capability failures visibly distinct.
                  const updateAvailable = incompatible
                    && project.daemon_protocol_error === 'daemon release is incompatible with WebAPI release';
                  const updateRequired = incompatible && !updateAvailable;
                  const resumable = !project.daemon_alive && project.last_active > 0 && Boolean(project.workdir?.trim());
                  return (
                    <div
                      key={project.id}
                      data-active={active ? 'true' : 'false'}
                      onPointerEnter={() => {
                        if (!active) onPrefetch?.(project.id);
                      }}
                      className={`session-card group relative mb-0.5 ${organizing === project.id ? '' : 'h-14'} w-full rounded-md transition-colors duration-150 ease-panel ${
                        active ? 'text-ink' : 'text-ink-dim hover:text-ink'
                      }`}
                    >
                      <span aria-hidden="true" className={`absolute left-0 transition-colors ${active ? 'inset-y-1 w-px bg-blue' : 'inset-y-2 w-px bg-transparent group-hover:bg-ink-faint/30'}`} />
                      <button
                        type="button"
                        onClick={() => onSelect(project.id)}
                        onFocus={() => {
                          if (!active) onPrefetch?.(project.id);
                        }}
                        aria-current={active ? 'page' : undefined}
                        title={`${name}${!hasHumanLabel && name !== project.id ? ` · ${project.id}` : ''}${project.objective && project.objective !== name ? ` — ${project.objective}` : ''}`}
                        className={`flex h-14 w-full min-w-0 flex-col justify-center px-2.5 text-left ${resumable ? 'pr-[4.75rem]' : 'pr-10'}`}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <StatusDot
                            ok={project.daemon_alive && !updateRequired}
                            title={updateRequired ? t('sidebar.updateRequired') : project.daemon_alive ? t('sidebar.daemonAlive') : t('sidebar.stopped')}
                          />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
                          {preference(project.id).followup && <Flag size={12} className="shrink-0 text-warn" aria-label={zh ? '待跟进' : 'Follow up'} />}
                        </div>
                        <div className="mt-1 flex min-w-0 items-center gap-1.5 pl-3.5 text-[11px] text-ink-faint">
                          <span className={`min-w-0 truncate ${updateRequired ? 'text-warn' : ''}`} title={workLabel}
                            data-session-work-state={work?.status.state}>
                            {updateRequired
                              ? t('sidebar.updateRequired')
                              : workLabel ?? (project.daemon_alive
                                ? t('sidebar.runningFor', { uptime: uptime(project.uptime_seconds) })
                                : project.last_active > 0
                                  ? t('sidebar.lastActive', { time: formatRelativeTime(project.last_active, locale) })
                                  : t('sidebar.stopped'))}
                          </span>
                          {updateAvailable && (
                            <span
                              title={t('sidebar.updateAvailableHint')}
                              className="shrink-0 rounded border border-line px-1 text-[10px] leading-4"
                            >
                              {t('sidebar.updateAvailable')}
                            </span>
                          )}
                        </div>
                      </button>
                      {resumable && onResume ? (
                        <button
                          type="button"
                          disabled={resumingId != null}
                          onClick={(event) => {
                            event.stopPropagation();
                            onResume(project.id);
                          }}
                          aria-label={t('sidebar.resume')}
                          title={t('sidebar.resumeHint', { workdir: project.workdir ?? '' })}
                          className="absolute right-9 top-3 flex h-8 w-8 items-center justify-center rounded-md text-blue opacity-100 hover:bg-blue/10 disabled:opacity-40 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                        >
                          {resumingId === project.id ? '…' : <FontAwesomeIcon icon={faPlay} className="h-3 w-3" />}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setOrganizing(current => current === project.id ? null : project.id);
                        }}
                        aria-label={t('sidebar.manage', { name })}
                        title={t('sidebar.manageHint')}
                        aria-expanded={organizing === project.id}
                        className="absolute right-1 top-3 flex h-8 w-8 items-center justify-center rounded-md text-ink-faint opacity-100 transition-opacity hover:bg-panel-raised hover:text-ink sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                      >
                        <FontAwesomeIcon icon={faEllipsis} className="h-4 w-4" />
                      </button>
                      {organizing === project.id && <div className="mx-2 mb-2 flex flex-col gap-1 rounded-lg border border-line bg-panel p-1 text-xs">
                        <button type="button" className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg" onClick={() => organize(project.id, 'pinned')}><Pin size={13} />{preference(project.id).pinned ? zh ? '取消置顶' : 'Unpin' : zh ? '置顶项目' : 'Pin project'}</button>
                        <button type="button" className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg" onClick={() => organize(project.id, 'followup')}><Flag size={13} />{preference(project.id).followup ? zh ? '取消待跟进' : 'Clear follow-up' : zh ? '标记待跟进' : 'Mark for follow-up'}</button>
                        <button type="button" className="flex items-center gap-2 rounded px-2 py-2 text-left hover:bg-bg" onClick={() => organize(project.id, 'archived')}><Archive size={13} />{preference(project.id).archived ? zh ? '移出归档' : 'Restore to recent' : zh ? '归档项目' : 'Archive project'}</button>
                        <button type="button" className="rounded border-t border-line px-2 py-2 text-left hover:bg-bg" onClick={() => { setOrganizing(null); onManage(project.id); }}>{zh ? '名称与项目管理' : 'Name and project settings'}</button>
                      </div>}
                    </div>
                  );
                }) : null}
              </section>
            ))}
          </div>

          <div className="flex min-h-14 items-center justify-between border-t border-line/50 px-4 py-2">
            <button type="button" onClick={() => onOpenPanel('config')} className="icon-control flex h-8 w-8 items-center justify-center" aria-label={t('sidebar.openSettings')} title={t('common.settings')}>
              <FontAwesomeIcon icon={faGear} className="h-3.5 w-3.5" />
            </button>
            {import.meta.env.VITE_ARGUS_HOSTED_TRIAL === '1' ? (
              <a href="/invite/compute" aria-label={t('sidebar.compute')} title={t('sidebar.compute')}
                className="icon-control flex h-8 w-8 items-center justify-center text-blue">
                <FontAwesomeIcon icon={faMicrochip} className="h-3.5 w-3.5" />
              </a>
            ) : null}
            <AppearanceControls themeMode={themeMode} onCycleTheme={onCycleTheme} />
          </div>
        </>
      ) : null}
    </WorkspaceSidePanel>
  );
}
