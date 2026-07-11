/** Shared wire types consumed by both the browser cockpit and the Ink TUI. */

export interface EventMsg {
  type?: string;
  [key: string]: unknown;
}

export interface Role {
  role: string;
  backend: string;
  backend_label: string;
  model: string;
  effort: string | null;
  active: boolean;
  label: string;
  status: string;
  age_s: number | null;
}

export interface Daemon {
  alive: boolean;
  pid: number | null;
  uptime_seconds: number | null;
  backend: string | null;
  backend_label?: string | null;
  per_mission_cap_usd: number | null;
  daily_cap_usd: number | null;
  global_daily_cap_usd: number | null;
}

export interface BacklogItem {
  id: string;
  title: string;
  objective: string;
  status: string;
  priority: number;
  max_cost_usd: number;
  iterate?: boolean;
  pending_question?: string;
  ts?: number;
  tags?: string[];
  notes?: string;
  started_ts?: number | null;
  finished_ts?: number | null;
  last_error?: string;
  iteration_max_cycles?: number;
  iteration_budget_usd?: number;
  iteration_cycles_done?: number;
  iteration_cost_usd?: number;
  original_objective?: string;
  orphan_retries?: number;
  deps?: string[];
}

export interface ContinuousState {
  enabled: boolean;
  objective: string;
  done_reason?: string;
  done_at?: string;
}

export interface ProviderRequestUsage {
  provider: string;
  day: string;
  daily_calls: number;
  daily_cap: number;
  remaining: number | null;
  completed_calls?: number;
  failed_calls?: number;
  premium_requests?: number;
  premium_cap?: number;
  premium_remaining?: number | null;
  blocked_until?: number;
  blocked_reason?: string;
}

export interface RequestUsage {
  day: string;
  codex: ProviderRequestUsage;
  copilot: ProviderRequestUsage;
}

export interface DaemonAdmission {
  admission_required: boolean;
  requested_at: number;
  target_sid: string;
  resume_continuous: boolean;
  limit: number;
  active_count: number;
  error: string;
  running_daemons: ProjectRow[];
}

export interface Snapshot {
  session: {
    id: string;
    display_name: string;
    objective: string;
    last_active: number;
    cwd: string;
  };
  daemon: Daemon;
  roles: Role[];
  backlog: BacklogItem[];
  recent_events: EventMsg[];
  spend_usd?: number;
  request_usage?: RequestUsage;
  daemon_admission?: DaemonAdmission;
  /** Present on compact UI snapshots. */
  continuous?: ContinuousState;
  /** Present on compact UI snapshots. */
  pending_questions?: Array<Record<string, unknown>>;
}

export interface ProjectRow {
  id: string;
  label: string;
  objective: string;
  display_name?: string;
  cwd?: string;
  launch_cwd?: string;
  last_active: number;
  daemon_alive: boolean;
  daemon_pid: number | null;
  uptime_seconds: number | null;
  active_role?: string;
  activity?: string;
  current_task?: string;
  unfinished_tasks?: number;
  continuous_enabled?: boolean;
  continuous_objective?: string;
}

export type ArtifactKind = 'text' | 'image' | 'pdf' | 'binary';

/** Reviewer-approved result file exposed by the protected artifact API. */
export interface ArtifactInfo {
  path: string;
  name: string;
  why: string;
  exists: boolean;
  kind: ArtifactKind;
  mime: string;
  size: number;
  mtime: number | null;
  source?: 'manager_live' | 'reviewer_evidence';
  group_title?: string;
  /** Included by the single-artifact endpoint for text files only. */
  preview?: string;
  truncated?: boolean;
}
