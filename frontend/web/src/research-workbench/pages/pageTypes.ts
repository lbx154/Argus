import type { EventMsg, PageId, ProjectRow, Snapshot, StatusView } from '../types';

export interface ActiveWorkbenchPageProps {
  sid: string;
  project: ProjectRow;
  snapshot: Snapshot;
  status?: StatusView;
  events: EventMsg[];
  connected: boolean;
  snapshotUpdatedAt: number;
  refresh: () => void | Promise<void>;
  controls: {
    start: () => Promise<unknown>;
    stop: (drain: boolean) => Promise<unknown>;
    busy: boolean;
    error: string;
  };
  navigate: (page: PageId) => void;
  active: boolean;
}
