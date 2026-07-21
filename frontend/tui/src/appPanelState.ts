import { useState, type Dispatch, type SetStateAction } from 'react';
import type { ApiClient, ProjectRow } from './api.js';
import type { PanelState } from './components/panels.js';
import { filterProjects, rankProjects } from '../../core/src/projects.js';

export interface PanelStateController {
  panel: PanelState | null;
  setPanel: Dispatch<SetStateAction<PanelState | null>>;
  openPanel: (kind: PanelState['kind'], opts?: Partial<PanelState>) => void;
}

export function usePanelState(api: ApiClient, project: string): PanelStateController {
  const [panel, setPanel] = useState<PanelState | null>(null);

  const openPanel = (kind: PanelState['kind'], opts: Partial<PanelState> = {}) => {
    const needsFetch = !['help', 'backlog', 'events'].includes(kind);
    setPanel({ kind, page: 0, ...opts, loading: needsFetch });
    if (!needsFetch) return;
    const fetchers: Record<string, () => Promise<unknown>> = {
      status: () => api.getStatus(),
      doctor: () => api.getDoctor(),
      journal: () => api.getJournal(20),
      config: () => api.getConfig(),
      identity: () => api.getIdentity(),
      daemons: () => api.listProjects(),
      artifacts: () => api.getArtifacts(),
      artifact: () => api.getArtifact(String(opts.path ?? '')),
      task: () => api.getBacklogItem(String(opts.itemId ?? '')),
    };
    const fetcher = fetchers[kind];
    if (!fetcher) return;
    fetcher().then(
      (data) => setPanel((current) => {
        if (!current || current.kind !== kind) return current;
        let selection = current.selection ?? 0;
        if (kind === 'daemons') {
          const ranked = filterProjects(
            rankProjects(data as ProjectRow[]),
            String(opts.query ?? ''),
          );
          const activeIndex = ranked.findIndex((row) => row.id === project);
          selection = activeIndex >= 0 ? activeIndex : 0;
        }
        return { ...current, loading: false, data, selection };
      }),
      (error) => setPanel((current) => (
        current && current.kind === kind
          ? { ...current, loading: false, error: (error as Error).message }
          : current
      )),
    );
  };

  return { panel, setPanel, openPanel };
}
