import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { ProjectRow } from '../api';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { ago, uptime } from '../lib/format';
import { filterProjects, hasHumanProjectLabel } from '../../../core/src/projects';
import type { ThemeMode } from './TopBar';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { DaemonSpendBadge } from './DaemonSpendBadge';
import {
  faAnglesLeft,
  faAnglesRight,
  faEllipsis,
  faGear,
  faLanguage,
  faMoon,
  faSun,
} from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';

type Scope = 'local' | 'all';

type SidebarProjectRow = ProjectRow & {
  created?: number;
  created_at?: number;
  health?: string;
  status?: string;
};

function projectNeedsAttention(project: ProjectRow): boolean {
  const { health, status } = project as SidebarProjectRow;
  return status === 'failed' || status === 'error' || health === 'red' || health === 'critical';
}

function projectCost(project: ProjectRow): number {
  return project.spend_usd ?? project.known_cost_usd ?? 0;
}

function projectCreatedAt(project: ProjectRow): number {
  const { created, created_at: createdAt } = project as SidebarProjectRow;
  return createdAt ?? created ?? 0;
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
  localCwd,
  onSelect,
  onPrefetch,
  onManage,
  onOpenPanel,
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
  expandedWidth = 256,
}: {
  projects: ProjectRow[];
  activeId: string | null;
  localCwd: string;
  onSelect: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onManage: (id: string) => void;
  onOpenPanel: (p: 'doctor' | 'config' | 'identity') => void;
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
  expandedWidth?: number;
}) {
  const { locale, setLocale, t } = useI18n();
  const [scope, setScope] = useState<Scope>('local');
  const initialScopeResolved = useRef(false);
  const [query, setQuery] = useState('');
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
  const visible = [...rawVisible].sort((a, b) => {
    const aNeedsAttention = projectNeedsAttention(a);
    const bNeedsAttention = projectNeedsAttention(b);
    if (aNeedsAttention !== bNeedsAttention) return aNeedsAttention ? -1 : 1;
    const aCost = projectCost(a);
    const bCost = projectCost(b);
    if (aCost !== bCost) return bCost - aCost;
    return projectCreatedAt(b) - projectCreatedAt(a);
  });
  const grouped = useMemo(() => {
    if (scope === 'local') return visible.length > 0 ? [[normalizedLocalCwd || 'Local', visible] as const] : [];
    const groups = new Map<string, ProjectRow[]>();
    visible.forEach((project) => {
      const path = project.launch_cwd?.trim() || t('common.unassigned');
      const rows = groups.get(path) ?? [];
      rows.push(project);
      groups.set(path, rows);
    });
    return [...groups.entries()];
  }, [normalizedLocalCwd, scope, visible]);
  const themeIcon = themeMode === 'light' ? faSun : faMoon;
  const nextTheme = themeMode === 'light' ? 'dark' : 'light';

  return (
    <aside
      data-state={slim ? 'collapsed' : 'expanded'}
      style={{ '--sidebar-width': `${expandedWidth}px` } as CSSProperties}
      className={`glass-panel glass-panel--side fixed inset-y-0 left-0 z-50 flex h-full shrink-0 flex-col border-r transition-[width,transform,visibility] duration-panel ease-panel lg:visible lg:static lg:z-auto lg:translate-x-0 ${
        slim ? 'w-14' : 'w-64 lg:w-[var(--sidebar-width)]'
      } ${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}
    >
      <div className={`flex h-12 shrink-0 items-center border-b border-line/50 ${slim ? 'justify-center' : 'justify-between px-4'}`}>
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

      {!slim ? (
        <>
          <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line/50 px-3">
            {(['local', 'all'] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setScope(value)}
                className={`h-8 rounded-md px-3 text-xs font-medium capitalize transition-colors ${
                  scope === value ? 'bg-bg text-ink' : 'text-ink-faint hover:text-ink-dim'
                }`}
              >
                {t(`common.${value}`)}
                <span className="ml-1.5 font-mono text-ink-faint">
                  {value === 'local' ? localProjects.length : projects.length}
                </span>
              </button>
            ))}
            <button
              type="button"
              onClick={onNew}
              disabled={creating}
              aria-label={t('sidebar.create')}
              title={t('sidebar.create')}
              className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-lg text-blue hover:bg-bg disabled:opacity-40"
            >
              {creating ? '…' : '+'}
            </button>
          </div>

          <div className="px-3 py-2">
            <label className="sr-only" htmlFor="daemon-search">{t('sidebar.find')}</label>
            <div className="flex items-center rounded-md border border-line/60 bg-bg/60 px-2 focus-within:border-blue/60">
              <span aria-hidden="true" className="mr-1.5 text-xs text-ink-faint">/</span>
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

          <div className="mobile-scroll-region min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-3 pb-3 scroll-thin">
            {loading && projects.length === 0 ? <div className="px-1 py-3 text-xs text-ink-faint">{t('common.loading')}</div> : null}
            {error ? (
              <button type="button" onClick={onRetry} className="mb-2 w-full rounded-md bg-err/5 px-3 py-2 text-left text-xs text-err">
                {t('sidebar.refreshFailed')}
              </button>
            ) : null}
            {!loading && !error && visible.length === 0 ? (
              <div className="px-1 py-4 text-xs text-ink-faint">
                <div>{query.trim() ? t('sidebar.noMatches', { query: query.trim() }) : t('sidebar.noSessions')}</div>
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
              <section key={path} className="mb-4 last:mb-0">
                <div className="mb-1 truncate px-1 font-mono text-xs text-ink-faint" title={path}>{path}</div>
                {rows.map((project) => {
                  const active = project.id === activeId;
                  const name = hasHumanProjectLabel(project)
                    ? (project.label || project.display_name || '').trim()
                    : project.objective.trim() || t('sidebar.unnamedSession');
                  const cost = projectCost(project);
                  return (
                    <div
                      key={project.id}
                      data-active={active ? 'true' : 'false'}
                      onPointerEnter={() => {
                        if (!active) onPrefetch?.(project.id);
                      }}
                      className={`session-card group relative mb-1 w-full rounded-md transition-colors duration-150 ease-panel ${
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
                        title={`${name}${project.objective && project.objective !== name ? ` — ${project.objective}` : ''}`}
                        className="w-full min-w-0 px-3 py-2.5 pr-10 text-left"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <StatusDot ok={project.daemon_alive} title={project.daemon_alive ? t('sidebar.daemonAlive') : t('sidebar.stopped')} />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
                        </div>
                        {project.objective ? <div className="mt-0.5 truncate pl-4 text-xs text-ink-faint">{project.objective}</div> : null}
                        <div className="mt-1 flex min-w-0 items-center justify-between gap-2 pl-4 text-xs text-ink-faint">
                          <span className="min-w-0 truncate">
                            {project.daemon_alive ? t('sidebar.runningFor', { uptime: uptime(project.uptime_seconds) }) : ago(project.last_active)}
                          </span>
                          {cost ? (
                            <DaemonSpendBadge
                              settledUsd={project.spend_usd}
                              knownUsd={project.known_cost_usd}
                              status={project.spend_status}
                              calls={project.usage_calls}
                              premiumRequests={project.premium_requests}
                              live={project.daemon_alive}
                            />
                          ) : null}
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => onManage(project.id)}
                        aria-label={t('sidebar.manage', { name })}
                        title={t('sidebar.manageHint')}
                        className="absolute right-1.5 top-1.5 flex h-8 w-8 items-center justify-center rounded-md text-ink-faint opacity-100 transition-opacity hover:bg-panel-raised hover:text-ink sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                      >
                        <FontAwesomeIcon icon={faEllipsis} className="h-4 w-4" />
                      </button>
                    </div>
                  );
                })}
              </section>
            ))}
          </div>

          <div className="flex min-h-14 items-center justify-between border-t border-line/50 px-4 py-2">
            <button type="button" onClick={() => onOpenPanel('config')} className="icon-control flex h-8 w-8 items-center justify-center" aria-label={t('sidebar.openSettings')} title={t('common.settings')}>
              <FontAwesomeIcon icon={faGear} className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')}
              title={t('language.switchTo', { language: locale === 'zh-CN' ? t('language.english') : t('language.chinese') })}
              aria-label={t('language.switchTo', { language: locale === 'zh-CN' ? t('language.english') : t('language.chinese') })}
              className="icon-control flex h-8 w-8 items-center justify-center"
            >
              <FontAwesomeIcon icon={faLanguage} className="h-3.5 w-3.5" />
            </button>
            <button type="button" onClick={onCycleTheme} title={t('sidebar.theme', { current: themeMode, next: nextTheme })} aria-label={t('sidebar.theme', { current: themeMode, next: nextTheme })} className="icon-control flex h-8 w-8 items-center justify-center">
              <FontAwesomeIcon icon={themeIcon} className="h-3.5 w-3.5" />
            </button>
          </div>
        </>
      ) : null}
    </aside>
  );
}
