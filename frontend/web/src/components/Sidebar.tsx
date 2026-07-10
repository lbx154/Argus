import { useState } from 'react';
import type { ProjectRow } from '../api';
import { Wordmark } from './Wordmark';
import { StatusDot } from './primitives';
import { ago, uptime } from '../lib/format';
import { filterProjects, hasHumanProjectLabel } from '../../../core/src/projects';

/**
 * Left rail: wordmark + the project switcher. Each project shows a live daemon
 * dot, its objective, and uptime/last-active. Bottom holds the utility actions.
 */
export function Sidebar({
  projects,
  activeId,
  onSelect,
  onOpenPanel,
  onNew,
  loading,
  creating = false,
  error,
  onRetry,
  mobileOpen = false,
}: {
  projects: ProjectRow[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onOpenPanel: (p: 'doctor' | 'config' | 'identity') => void;
  onNew: () => void;
  loading: boolean;
  creating?: boolean;
  error?: string;
  onRetry?: () => void;
  mobileOpen?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const [query, setQuery] = useState('');
  const primary = projects.filter(
    (project) =>
      project.daemon_alive ||
      hasHumanProjectLabel(project) ||
      Boolean(project.objective?.trim()) ||
      project.id === activeId,
  );
  const hiddenCount = Math.max(0, projects.length - primary.length);
  const searching = Boolean(query.trim());
  const visible = searching ? filterProjects(projects, query) : showAll ? projects : primary;
  return (
    <aside className={`fixed inset-y-0 left-0 z-40 flex h-full w-60 shrink-0 flex-col border-r border-line bg-surface transition-[transform,visibility] md:visible md:static md:z-auto md:translate-x-0 ${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-3.5">
        <Wordmark size={18} tag="console" />
      </div>

      <div className="flex items-center justify-between px-3 pt-3 pb-1">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">Sessions</span>
        <button
          onClick={onNew}
          disabled={creating}
          title="create a new session"
          className="rounded border border-line px-2 py-0.5 text-[11px] font-medium text-ink-dim transition-colors hover:border-ink-faint hover:bg-panel hover:text-ink disabled:cursor-wait disabled:opacity-50"
        >
          {creating ? 'Creating…' : '+ New'}
        </button>
      </div>
      {(projects.length > 4 || searching) && (
        <div className="px-3 pb-2">
          <label className="sr-only" htmlFor="daemon-search">Find a daemon</label>
          <div className="flex items-center rounded border border-line bg-bg/40 px-2 focus-within:border-blue-deep">
            <span aria-hidden="true" className="mr-1.5 text-[11px] text-ink-faint">/</span>
            <input
              id="daemon-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault();
                  setQuery('');
                }
              }}
              placeholder="Find name, ID, objective…"
              className="h-8 min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-ink-faint"
            />
            {searching && (
              <button
                type="button"
                aria-label="clear daemon search"
                onClick={() => setQuery('')}
                className="rounded px-1 text-sm text-ink-faint hover:bg-panel hover:text-ink"
              >
                ×
              </button>
            )}
          </div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto scroll-thin px-2">
        {loading && projects.length === 0 && (
          <div className="px-2 py-3 text-xs text-ink-faint">loading…</div>
        )}
        {!loading && !error && projects.length === 0 && (
          <div className="px-2 py-3 text-xs text-ink-faint">No sessions yet.</div>
        )}
        {error && (
          <button
            type="button"
            onClick={onRetry}
            className="mb-2 w-full rounded-md border border-err/30 bg-err/5 px-2.5 py-2 text-left text-[11px] text-err"
          >
            Project refresh failed · retry
          </button>
        )}
        {!loading && !error && searching && visible.length === 0 && (
          <div className="px-2 py-3 text-xs text-ink-faint">
            no daemons match “{query.trim()}”
          </div>
        )}
        {visible.map((p) => {
          const active = p.id === activeId;
          return (
            <button
              key={p.id}
              onClick={() => onSelect(p.id)}
              aria-current={active ? 'page' : undefined}
              title={`${p.label || p.id}${p.objective ? ` — ${p.objective}` : ''}`}
              className={`group mb-0.5 w-full border-l-2 px-2.5 py-2 text-left transition-colors ${
                active ? 'border-blue bg-panel' : 'border-transparent hover:bg-panel/60'
              }`}
            >
              <div className="flex items-center gap-2">
                <StatusDot ok={p.daemon_alive} title={p.daemon_alive ? 'daemon alive' : 'stopped'} />
                <span className={`truncate text-sm font-medium ${active ? 'text-ink' : 'text-ink-dim'}`}>
                  {p.label || p.id}
                </span>
              </div>
              <div className="mt-0.5 truncate pl-4 text-[11px] text-ink-faint">
                {p.objective || 'no objective'}
              </div>
              <div className="mt-0.5 pl-4 text-[10px] text-ink-faint">
                {p.daemon_alive ? `up ${uptime(p.uptime_seconds)}` : `active ${ago(p.last_active)}`}
              </div>
            </button>
          );
        })}
        {!searching && hiddenCount > 0 && (
          <button
            onClick={() => setShowAll((value) => !value)}
            className="mb-2 w-full rounded-md px-2.5 py-2 text-left text-[11px] text-ink-faint transition-colors hover:bg-panel/60 hover:text-ink-dim"
          >
            {showAll ? 'Hide unnamed sessions' : `Show ${hiddenCount} other sessions`}
          </button>
        )}
      </div>

      <div className="flex items-center gap-1.5 border-t border-line px-3 py-2.5">
        {(['doctor', 'config', 'identity'] as const).map((p) => (
          <button
            key={p}
            onClick={() => onOpenPanel(p)}
            className="rounded-md px-2 py-1 text-[11px] text-ink-faint transition-colors hover:bg-panel hover:text-ink-dim"
          >
            {p}
          </button>
        ))}
      </div>
    </aside>
  );
}
