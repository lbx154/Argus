/** Shared wire types consumed by both the browser cockpit and the Ink TUI. */

export interface EventMsg {
  type?: string;
  ts?: number;
  event_schema_version?: number;
  canonical_type?: string;
  event_validation?: {
    status: 'invalid';
    errors: string[];
  };
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
  global_daily_cap_usd: number | null;
  read_status?: 'ok' | 'error';
  read_error?: string;
  protocol?: { name: string; major: number | null; minor: number | null };
  capabilities?: string[];
  runtime?: Record<string, unknown> | null;
  protocol_compatible?: boolean | null;
  protocol_error?: string;
}

export interface BacklogItem {
  id: string;
  title: string;
  objective: string;
  status: string;
  priority: number;
  iterate?: boolean;
  pending_question?: string;
  ts?: number;
  tags?: string[];
  notes?: string;
  started_ts?: number | null;
  finished_ts?: number | null;
  last_error?: string;
  iteration_max_cycles?: number;
  iteration_cycles_done?: number;
  iteration_cost_usd?: number;
  original_objective?: string;
  orphan_retries?: number;
  deps?: string[];
  outcome?: MissionOutcomeDimensions;
}

export interface MissionOutcomeDimensions {
  execution_status: string;
  review_status: string;
  stage_certification: string;
  scientific_decision: string;
  failure_source: string;
  failure_layer: string;
  interruption_kind: string;
  resumable: boolean;
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

export interface CostControlSnapshot {
  day: string;
  active_reservations: number;
  unresolved_calls: number;
  blocking_unresolved_calls?: number;
  unresolved: Array<Record<string, unknown>>;
  policy: 'block' | 'allow';
}

export interface DaemonCommandReceipt {
  command_id: string;
  operation: string;
  status: 'accepted' | 'running' | 'applied' | 'failed' | 'rejected';
  revision: number;
  expected_revision: number | null;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string;
  submitted_at: number;
  updated_at: number;
}

export interface DaemonCommandState {
  schema_version: 1;
  revision: number;
  recent: DaemonCommandReceipt[];
}

export interface ObservabilitySnapshot {
  schema_version: 1;
  provider: Record<string, unknown>;
  daemon_commands: Record<string, unknown>;
  web: Record<string, unknown>;
  event_validation_failures: number;
  cost_control: CostControlSnapshot | Record<string, unknown>;
  slo: {
    status: 'healthy' | 'degraded';
    violations: string[];
  };
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

export interface UsageSummary {
  call_count: number;
  known_cost_usd: number;
  cost_usd: number | null;
  pricing_status: string;
  priced_calls: number;
  partial_calls: number;
  unpriced_calls: number;
  not_billed_calls: number;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
  premium_requests: number;
  total_nano_aiu: number;
  premium_request_cost_usd: number;
}

export type MissionRoleStatus = 'active' | 'done' | 'waiting' | 'rejected' | 'error';

export interface MissionRoleView {
  role: string;
  status: MissionRoleStatus | string;
  label: string;
  updated_at: number;
  backend?: string;
  model?: string;
  effort?: string | null;
}

export interface MissionDagNode {
  id: string;
  title: string;
  objective: string;
  status: string;
  deps: string[];
  branch_id: string;
  parent_branch_id: string | null;
}

export interface MissionMetricView {
  id: string;
  name: string;
  baseline: number | null;
  value: number;
  unit: string;
  direction: 'maximize' | 'minimize' | 'target' | string;
  evidence: string;
  experiment_id?: string;
  hypothesis_id?: string;
  branch_id?: string;
  round_index?: number | null;
  primary?: boolean;
  verification_status: 'reported' | 'accepted' | 'rejected' | string;
  reviewer_reason?: string;
  reported_at?: number;
  verified_at?: number;
}

export interface MissionTimelineItem {
  id: string;
  ts: number;
  type: string;
  role: string;
  title: string;
  detail: string;
  tone: 'neutral' | 'info' | 'success' | 'error' | 'metric' | 'skill' | string;
  item_id?: string;
  branch_id?: string;
  hypothesis_id?: string;
  experiment_id?: string;
  metric_id?: string;
}

export interface MissionAchievement {
  id: string;
  title: string;
  goal: string;
  summary?: string;
  metric_id?: string;
  metric_name?: string;
  baseline?: number | null;
  best?: number | null;
  gain?: number | null;
  unit?: string;
  experiments_run?: number;
  rejected_attempts?: number;
  skills_learned?: number;
  artifacts?: number;
  elapsed_seconds?: number;
  reviewer_certified: boolean;
  certified_at?: number | null;
}

export interface MissionStorageView {
  project_skill_dir: string;
  global_skill_dir: string;
  project_skill_count: number;
  global_skill_count: number;
  skill_history_compressed: number;
  wiki_retired_compressed: number;
  skill_history_bytes_saved: number;
  wiki_retired_bytes_saved: number;
  wiki_paths: string[];
}

export interface MissionView {
  schema_version: 1;
  bootstrapped?: boolean;
  mission: {
    id: string;
    title: string;
    objective: string;
    status: string;
    started_at: number | null;
    completed_at: number | null;
    elapsed_seconds: number;
    campaign_started_at: number | null;
    campaign_elapsed_seconds: number;
  };
  stage: { id: string; label: string };
  round: { current: number; max: number };
  active_role: string;
  roles: MissionRoleView[];
  dag: MissionDagNode[];
  hypotheses: Array<Record<string, unknown>>;
  experiments: Array<Record<string, unknown>>;
  metrics: MissionMetricView[];
  primary_metric: MissionMetricView | null;
  timeline: MissionTimelineItem[];
  artifacts: Array<Record<string, unknown>>;
  learned_skills: Array<Record<string, unknown>>;
  learned_wiki_pages: Array<Record<string, unknown>>;
  storage: MissionStorageView;
  achievement: MissionAchievement | null;
  review: { status: string; reason: string; rejected_attempts: number };
  outcome: Partial<MissionOutcomeDimensions>;
  last_event_ts: number;
  updated_at: number;
}

export interface Snapshot {
  schema_version?: number;
  session: {
    id: string;
    display_name: string;
    objective: string;
    created?: number;
    last_active: number;
    cwd: string;
    workdir?: string;
    launch_cwd?: string;
  };
  daemon: Daemon;
  roles: Role[];
  backlog: BacklogItem[];
  recent_events: EventMsg[];
  spend_usd?: number | null;
  spend_status?: 'empty' | 'priced' | 'partial' | 'unpriced' | 'not_billed';
  usage_summary?: UsageSummary;
  global_spend_usd?: number | null;
  global_spend_status?: 'empty' | 'priced' | 'partial' | 'unpriced' | 'not_billed';
  global_usage_summary?: UsageSummary;
  request_usage?: RequestUsage | null;
  cost_control?: CostControlSnapshot | null;
  daemon_commands?: DaemonCommandState | null;
  observability?: ObservabilitySnapshot | null;
  mission_view?: MissionView | null;
  daemon_admission?: DaemonAdmission;
  /** Present on compact UI snapshots. */
  continuous?: ContinuousState;
  /** Present on compact UI snapshots. */
  pending_questions?: Array<Record<string, unknown>>;
  partial?: boolean;
  diagnostics?: Array<{
    section: string;
    error_type: string;
    message: string;
  }>;
}

export interface ProjectRow {
  id: string;
  label: string;
  objective: string;
  display_name?: string;
  cwd?: string;
  workdir?: string;
  launch_cwd?: string;
  last_active: number;
  daemon_alive: boolean;
  daemon_pid: number | null;
  uptime_seconds: number | null;
  daemon_protocol_compatible?: boolean | null;
  daemon_protocol_error?: string;
  daemon_source_owned?: boolean;
  daemon_upgrade_pending?: boolean;
  active_role?: string;
  activity?: string;
  current_task?: string;
  unfinished_tasks?: number;
  continuous_enabled?: boolean;
  continuous_objective?: string;
  spend_usd?: number | null;
  known_cost_usd?: number;
  spend_status?: 'empty' | 'priced' | 'partial' | 'unpriced' | 'not_billed';
  usage_calls?: number;
  premium_requests?: number;
  cost_updated_at?: number;
}

export interface ProjectCostRow {
  id: string;
  spend_usd: number | null;
  known_cost_usd: number;
  spend_status: 'empty' | 'priced' | 'partial' | 'unpriced' | 'not_billed' | string;
  usage_calls: number;
  premium_requests: number;
  updated_at: number;
}

export type ArtifactKind =
  | 'text'
  | 'markdown'
  | 'html'
  | 'json'
  | 'table'
  | 'image'
  | 'pdf'
  | 'audio'
  | 'video'
  | 'binary';

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
  source?: 'manager_live' | 'reviewer_evidence' | 'research_registered';
  group_title?: string;
  /** Included by the single-artifact endpoint for text/HTML files only. */
  preview?: string;
  truncated?: boolean;
}

export interface GitDiffView {
  available: boolean;
  branch: string;
  status: string;
  stat: string;
  diff: string;
  truncated: boolean;
}
