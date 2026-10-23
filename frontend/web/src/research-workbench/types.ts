import type {
  BacklogItem,
  ContinuousState,
  Daemon,
  EventMsg,
  MissionDagNode,
  ProjectRow,
  Role,
  Snapshot,
} from '../../../core/src/types';

export type { BacklogItem, EventMsg, MissionDagNode, ProjectRow, Snapshot };

export interface ProjectIndex {
  projects: ProjectRow[];
  local_cwd: string;
}

export interface StatusView {
  identity: string;
  backlog_pending: BacklogItem[];
  pending_questions: Array<Record<string, unknown>>;
  journal: JournalEntry[];
  continuous: ContinuousState;
  inbox_pending: number;
  daemon: Daemon;
  roles: Role[];
  active_role: string | null;
}

export interface JournalEntry {
  id: string;
  ts: number;
  kind: string;
  title: string;
  summary: string;
  tags: string[];
  cost_usd?: number;
  extra?: Record<string, unknown>;
}

export type PageId = 'runtime' | 'experiments' | 'ide' | 'timeline';
