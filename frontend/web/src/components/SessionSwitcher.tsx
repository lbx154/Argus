import { useMemo, useState } from 'react';
import type { ProjectRow } from '../api';
import { filterProjects } from '../../../core/src/projects';
import { ago, uptime } from '../lib/format';
import { Modal } from './Modal';

export function SessionSwitcher({
  open,
  projects,
  activeId,
  onClose,
  onSelect,
  onNew,
}: {
  open: boolean;
  projects: ProjectRow[];
  activeId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const [query, setQuery] = useState('');
  const visible = useMemo(
    () => query.trim() ? filterProjects(projects, query) : projects,
    [projects, query],
  );
  return (
    <Modal open={open} onClose={onClose} label="Sessions" width="max-w-xl">
      <div className="border-b border-line/70 bg-panel-raised p-3">
        <div className="flex items-center gap-2">
          <input
            data-autofocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find a session"
            className="h-9 min-w-0 flex-1 rounded-md border border-line/80 bg-bg/60 px-3 text-[13px] text-ink outline-none placeholder:text-ink-faint focus:border-blue-deep"
          />
          <button type="button" onClick={onNew} className="h-9 rounded-md bg-blue-deep px-3 text-xs font-medium text-white hover:bg-blue-deep/85">
            New
          </button>
        </div>
      </div>
      <div className="max-h-[58vh] overflow-y-auto p-2 scroll-thin">
        {visible.map((project) => {
          const active = project.id === activeId;
          return (
            <button
              key={project.id}
              type="button"
              onClick={() => onSelect(project.id)}
              className={`mb-1 grid w-full grid-cols-[8px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                active ? 'bg-blue-deep/10' : 'hover:bg-surface'
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${project.daemon_alive ? 'bg-ok' : 'bg-ink-faint/35'}`} />
              <span className="min-w-0">
                <span className="block truncate text-[13px] font-medium text-ink">{project.label || project.id}</span>
                {project.objective ? <span className="mt-0.5 block truncate text-[10px] text-ink-faint">{project.objective}</span> : null}
              </span>
              <span className="font-mono text-[9px] text-ink-faint">
                {project.daemon_alive ? uptime(project.uptime_seconds) : ago(project.last_active)}
              </span>
            </button>
          );
        })}
        {visible.length === 0 ? <div className="py-10 text-center text-xs text-ink-faint">No sessions</div> : null}
      </div>
    </Modal>
  );
}
