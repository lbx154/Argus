import { useEffect, useMemo, useRef, useState } from 'react';
import type { ProjectRow } from '../api';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { ago, uptime } from '../lib/format';
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
  faLanguage,
  faMoon,
  faPlay,
  faSun,
} from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';

type Scope = 'local' | 'all';

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
  localCwd,
  onSelect,
  onPrefetch,
  onManage,
  onResume,
  resumingId,
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
}: {
  projects: ProjectRow[];
  activeId: string | null;
  localCwd: string;
  onSelect: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onManage: (id: string) => void;
  onResume?: (id: string) => void;
  resumingId?: string | null;
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
}) {
  const { locale, setLocale, t } = useI18n();
  const [scope, setScope] = useState<Scope>('local');
  const initialScopeResolved = useRef(false);
  const [query, setQuery] = useState('');
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
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
  const visible = rawVisible;
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
  const groupIsCollapsed = (path: string) => collapsedGroups.has(path) && !query.trim();

  return (
    <aside
      data-state={slim ? 'collapsed' : 'expanded'}
      data-resizable-panel="left"
      className={`glass-panel glass-panel--side fixed inset-y-0 left-0 z-50 flex h-full shrink-0 flex-col border-r transition-[width,transform,visibility] duration-panel ease-panel lg:visible lg:static lg:z-auto lg:translate-x-0 ${
        slim ? 'w-14' : 'w-64 lg:w-[var(--sidebar-width)]'
      } ${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}
    >
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
                <button
                  type="button"
                  aria-expanded={!groupIsCollapsed(path)}
                  title={path}
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
                  <span className="min-w-0 flex-1 truncate">{projectGroupLabel(path)}</span>
                  <span className="font-mono text-[10px]">{rows.length}</span>
                </button>
                {!groupIsCollapsed(path) ? rows.map((project) => {
                  const active = project.id === activeId;
                  const hasHumanLabel = hasHumanProjectLabel(project);
                  const name = hasHumanLabel
                    ? (project.label || project.display_name || '').trim()
                    : project.objective.trim() || project.id || t('sidebar.unnamedSession');
                  const incompatible = project.daemon_alive && project.daemon_protocol_compatible === false;
                  const resumable = !project.daemon_alive && project.last_active > 0 && Boolean(project.workdir?.trim());
                  return (
                    <div
                      key={project.id}
                      data-active={active ? 'true' : 'false'}
                      onPointerEnter={() => {
                        if (!active) onPrefetch?.(project.id);
                      }}
                      className={`session-card group relative mb-0.5 h-14 w-full rounded-md transition-colors duration-150 ease-panel ${
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
                            ok={project.daemon_alive && !incompatible}
                            title={incompatible ? t('sidebar.updateRequired') : project.daemon_alive ? t('sidebar.daemonAlive') : t('sidebar.stopped')}
                          />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
                        </div>
                        <div className="mt-1 min-w-0 truncate pl-3.5 text-[11px] text-ink-faint">
                          <span className={`min-w-0 truncate ${incompatible ? 'text-warn' : ''}`}>
                            {incompatible
                              ? t('sidebar.updateRequired')
                              : project.daemon_alive
                                ? t('sidebar.runningFor', { uptime: uptime(project.uptime_seconds) })
                                : ago(project.last_active)}
                          </span>
                        </div>
                      </button>
                      {resumable && onResume ? (
                        <button
                          type="button"
                          disabled={resumingId != null}
                          onClick={() => onResume(project.id)}
                          aria-label={t('sidebar.resume')}
                          title={t('sidebar.resumeHint', { workdir: project.workdir ?? '' })}
                          className="absolute right-9 top-3 flex h-8 w-8 items-center justify-center rounded-md text-blue opacity-100 hover:bg-blue/10 disabled:opacity-40 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                        >
                          {resumingId === project.id ? '…' : <FontAwesomeIcon icon={faPlay} className="h-3 w-3" />}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => onManage(project.id)}
                        aria-label={t('sidebar.manage', { name })}
                        title={t('sidebar.manageHint')}
                        className="absolute right-1 top-3 flex h-8 w-8 items-center justify-center rounded-md text-ink-faint opacity-100 transition-opacity hover:bg-panel-raised hover:text-ink sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                      >
                        <FontAwesomeIcon icon={faEllipsis} className="h-4 w-4" />
                      </button>
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
