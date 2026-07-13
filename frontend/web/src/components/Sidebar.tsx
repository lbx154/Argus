import { useMemo, useState, type CSSProperties } from 'react';
import type { ProjectRow } from '../api';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { ago, uptime } from '../lib/format';
import { filterProjects } from '../../../core/src/projects';
import type { ThemeMode } from './TopBar';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAnglesLeft,
  faAnglesRight,
  faDesktop,
  faEllipsis,
  faGear,
  faMoon,
  faSun,
} from '@fortawesome/free-solid-svg-icons';

type Scope = 'local' | 'all';

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
  const [scope, setScope] = useState<Scope>('local');
  const [query, setQuery] = useState('');
  const slim = collapsed && !mobileOpen;
  const normalizedLocalCwd = localCwd.trim();
  const localProjects = useMemo(
    () => normalizedLocalCwd
      ? projects.filter((project) => project.launch_cwd?.trim() === normalizedLocalCwd)
      : [],
    [normalizedLocalCwd, projects],
  );
  const scoped = scope === 'local' ? localProjects : projects;
  const visible = query.trim() ? filterProjects(scoped, query) : scoped;
  const grouped = useMemo(() => {
    if (scope === 'local') return visible.length > 0 ? [[normalizedLocalCwd || 'Local', visible] as const] : [];
    const groups = new Map<string, ProjectRow[]>();
    visible.forEach((project) => {
      const path = project.launch_cwd?.trim() || 'Unassigned';
      const rows = groups.get(path) ?? [];
      rows.push(project);
      groups.set(path, rows);
    });
    return [...groups.entries()];
  }, [normalizedLocalCwd, scope, visible]);
  const themeIcon = themeMode === 'light' ? faSun : themeMode === 'dark' ? faMoon : faDesktop;
  const nextTheme = themeMode === 'system' ? 'light' : themeMode === 'light' ? 'dark' : 'system';

  return (
    <aside
      data-state={slim ? 'collapsed' : 'expanded'}
      style={{ '--sidebar-width': `${expandedWidth}px` } as CSSProperties}
      className={`fixed inset-y-0 left-0 z-40 flex h-full shrink-0 flex-col border-r border-line/60 bg-panel transition-[width,transform,visibility] duration-panel ease-panel lg:visible lg:static lg:z-auto lg:translate-x-0 ${
        slim ? 'w-14' : 'w-64 lg:w-[var(--sidebar-width)]'
      } ${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}
    >
      <div className={`flex h-12 shrink-0 items-center border-b border-line/50 ${slim ? 'justify-center' : 'justify-between px-4'}`}>
        {slim ? (
          <Wordmark size={22} compact />
        ) : (
          <>
            <Wordmark size={24} />
            <button type="button" onClick={onToggleCollapse} aria-label="Collapse sessions" title="Collapse sessions · Ctrl/⌘ B" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink">
              <FontAwesomeIcon icon={faAnglesLeft} className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
      {slim ? (
        <div className="flex h-12 shrink-0 items-center justify-center">
          <button type="button" onClick={onToggleCollapse} aria-label="Expand sessions" title="Expand sessions · Ctrl/⌘ B" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink">
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
                {value}
                <span className="ml-1.5 font-mono text-ink-faint">
                  {value === 'local' ? localProjects.length : projects.length}
                </span>
              </button>
            ))}
            <button
              type="button"
              onClick={onNew}
              disabled={creating}
              aria-label="Create session"
              title="Create session"
              className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-lg text-blue hover:bg-bg disabled:opacity-40"
            >
              {creating ? '…' : '+'}
            </button>
          </div>

          <div className="px-3 py-2">
            <label className="sr-only" htmlFor="daemon-search">Find a session</label>
            <div className="flex items-center rounded-md border border-line/60 bg-bg/60 px-2 focus-within:border-blue/60">
              <span aria-hidden="true" className="mr-1.5 text-xs text-ink-faint">/</span>
              <input
                id="daemon-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Find a session"
                className="h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-ink-faint"
              />
              {query ? (
                <button type="button" aria-label="Clear search" onClick={() => setQuery('')} className="px-1 text-sm text-ink-faint hover:text-ink">×</button>
              ) : null}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-3 pb-3 scroll-thin">
            {loading && projects.length === 0 ? <div className="px-1 py-3 text-xs text-ink-faint">Loading…</div> : null}
            {error ? (
              <button type="button" onClick={onRetry} className="mb-2 w-full rounded-md bg-err/5 px-3 py-2 text-left text-xs text-err">
                Refresh failed · retry
              </button>
            ) : null}
            {!loading && !error && visible.length === 0 ? <div className="px-1 py-4 text-xs text-ink-faint">No sessions</div> : null}
            {grouped.map(([path, rows]) => (
              <section key={path} className="mb-4 last:mb-0">
                <div className="mb-1 truncate px-1 font-mono text-xs text-ink-faint" title={path}>{path}</div>
                {rows.map((project) => {
                  const active = project.id === activeId;
                  return (
                    <div
                      key={project.id}
                      onPointerEnter={() => {
                        if (!active) onPrefetch?.(project.id);
                      }}
                      className={`group relative mb-1 w-full rounded-md transition-colors duration-150 ease-panel ${
                        active ? 'bg-blue/10 text-ink' : 'text-ink-dim hover:bg-bg/70 hover:text-ink'
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
                        title={`${project.label || project.id}${project.objective ? ` — ${project.objective}` : ''}`}
                        className="w-full min-w-0 px-3 py-2.5 pr-10 text-left"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <StatusDot ok={project.daemon_alive} title={project.daemon_alive ? 'daemon alive' : 'stopped'} />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{project.label || project.id}</span>
                        </div>
                        <div className="mt-1 truncate pl-4 text-xs text-ink-faint">
                          {project.daemon_alive ? `running · ${uptime(project.uptime_seconds)}` : ago(project.last_active)}
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => onManage(project.id)}
                        aria-label={`Manage ${project.label || project.id}`}
                        title="Rename, pause, or delete"
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
            <button type="button" onClick={() => onOpenPanel('config')} className="flex h-8 w-8 items-center justify-center rounded-md text-ink-faint hover:bg-bg hover:text-ink" aria-label="Open settings" title="Settings">
              <FontAwesomeIcon icon={faGear} className="h-3.5 w-3.5" />
            </button>
            <button type="button" onClick={onCycleTheme} title={`${themeMode} theme · switch to ${nextTheme}`} aria-label={`${themeMode} theme; switch to ${nextTheme}`} className="flex h-8 w-8 items-center justify-center rounded-md text-ink-faint hover:bg-bg hover:text-ink">
              <FontAwesomeIcon icon={themeIcon} className="h-3.5 w-3.5" />
            </button>
          </div>
        </>
      ) : null}
    </aside>
  );
}
