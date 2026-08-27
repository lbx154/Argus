import type {
  ArtifactInfo,
  CounterexampleDashboard,
  EventMsg,
  GitDiffView,
  JournalEntry,
  ProjectRow,
  Snapshot,
  StatusView,
  Turn,
} from '../types';

export interface WorkspacePageProps {
  sid: string;
  project: ProjectRow;
  snapshot: Snapshot;
  status?: StatusView;
  events: EventMsg[];
  transcript: Turn[];
  artifacts: ArtifactInfo[];
  counterexamples?: CounterexampleDashboard;
  gitDiff?: GitDiffView;
  journal: JournalEntry[];
  connected: boolean;
  snapshotUpdatedAt: number;
  refresh: () => void | Promise<void>;
  controls: {
    start: () => Promise<unknown>;
    stop: (drain: boolean) => Promise<unknown>;
    busy: boolean;
    error: string;
  };
  navigate: (page: import('../types').PageId) => void;
}
