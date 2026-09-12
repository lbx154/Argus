/** The operator data API is separate from the ordinary user /api contract. */
export type UnknownRecord = Record<string, unknown>;
export type Purpose = 'internal_training' | 'external_sharing';
export type RecordedRole = 'manager' | 'planner' | 'engineer' | 'reviewer' | 'operator' | 'unknown';

export interface ProjectIdentity {
  tenant_id: string;
  sid: string;
}

export interface CollaborationProject extends ProjectIdentity {
  title: string;
  eligible: boolean;
  reason?: string;
  granted_at?: number;
  display_reason_counts?: Record<string, number>;
  display_truncated?: boolean;
}

/** Read-only project metadata, keyed by the requested tenant plus this id. */
export interface DirectoryProject {
  id: string;
  title: string;
  display_name?: string;
  objective?: string;
  created?: number;
  last_active?: number;
  research_deleted?: boolean;
}

export interface ProjectDirectory {
  projects: DirectoryProject[];
  state: string;
  truncated: boolean;
  skipped: number;
  policy: UnknownRecord;
}

export interface TaskRequest {
  text: string;
  event_id: string;
  timestamp: number;
  association: string | null;
  truncated: boolean;
}

export interface MissionBriefSource {
  event_id: string;
  kind: string;
  timestamp: number;
  association: string | null;
}

export interface TaskOutcome {
  state: string;
  label: string;
  evidence_event_ids: string[];
  last_lifecycle: {
    state: string;
    label: string;
    timestamp: number;
    event_id: string;
  } | null;
}

export interface RoleSummary {
  role: string;
  label: string;
  observations: number;
  episodes: number;
  tool_pairs: number;
}

export interface TaskCollection {
  states: Record<string, number>;
  accepted_episodes: number;
  quarantined_episodes: number;
  gaps: number;
  retained_episodes: number;
  observed_events: number;
}

export interface CollaborationTask extends ProjectIdentity {
  id: string;
  task_id: string | null;
  title: string;
  request: TaskRequest | null;
  mission_title: string | null;
  objective: string | null;
  mission_brief_source: MissionBriefSource | null;
  roles: RoleSummary[];
  task_outcome: TaskOutcome;
  collection: TaskCollection;
  quality: { approved_samples: number; candidates: number };
  last_observed_at: number | null;
  unassigned_observations: number;
  /** Always false in the current API: a finished episode is not an entire task. */
  global_complete: boolean;
}

export interface Completeness {
  scope: 'selected_project_page';
  global_complete: boolean;
  page_truncated: boolean;
  reason_counts: Record<string, number>;
  has_more_projects: boolean;
  source_bytes_examined: number;
  source_byte_limit: number;
}

export interface Overview {
  projects: CollaborationProject[];
  tasks: CollaborationTask[];
  offset: number;
  next_offset: number | null;
  has_more_projects: boolean;
  total_projects: number;
  counts: {
    tasks: number;
    roles: number;
    tool_pairs: number;
    approved_samples: number;
    candidates: number;
    observed_episodes: number;
    observed_events: number;
  };
  purpose: Purpose;
  limitations: string[];
  global_complete: boolean;
  completeness: Completeness;
}

export interface CollaborationSegment {
  id: string;
  role: string;
  label: string;
  source_kind: 'journal_event' | 'tool_episode';
  event_id?: string;
  episode_id?: number;
  sample_event_id?: string;
  kind?: string;
  started_at: number | null;
  ended_at: number | null;
  timestamp_basis: 'source_reported' | 'observer_received';
  role_evidence: string | string[] | null;
  status: string;
  summary: string;
  tool_pairs: number;
  tool_name?: string | null;
  content_withheld?: boolean;
  quality_approved?: boolean;
}

export interface ToolPair {
  call_id: string;
  name: string;
  status: 'success' | 'error' | 'unknown';
  call_timestamp: number | null;
  result_timestamp: number | null;
  result_characters: number | null;
}

export interface CollectionIssue {
  reason: string;
  count: number;
}

export interface CollaborationEpisode {
  episode_id: number;
  sample_event_id: string;
  role: string;
  label: string;
  state: string;
  started_at: number;
  ended_at: number | null;
  last_observed_at: number;
  quality_approved: boolean;
  quality_evidence: {
    reviewer_kind: string;
    human_reviewed: boolean;
    evidence_sha256: string | null;
    reviewed_at: number;
  } | null;
  tool_pairs: ToolPair[];
  sample_eligible: boolean;
  role_evidence: string | null;
  reason?: string;
  capture_policy?: string;
  observed_event_count?: number;
  raw_available?: boolean;
  tool_pairs_total?: number;
  tool_pairs_truncated?: boolean;
  collection_issues?: CollectionIssue[];
  quality_status?: string;
}

export interface CollaborationDetail extends CollaborationTask {
  project: CollaborationProject;
  purpose: Purpose;
  limitations: string[];
  completeness: Completeness;
  segments: CollaborationSegment[];
  episodes: CollaborationEpisode[];
  /** Empty unless the source explicitly recorded a handoff; chronology is not one. */
  handoffs: UnknownRecord[];
  gaps: Array<{ code: string; count: number }>;
}

export interface PublicMessage extends UnknownRecord {
  role?: string;
  channel?: string;
  content?: unknown;
}

export interface ObservedPayload extends UnknownRecord {
  messages?: PublicMessage[];
  tools?: unknown[];
  type?: string;
  delta?: string;
  toolName?: string;
  toolCallId?: string;
  input?: unknown;
  content?: unknown;
  isError?: boolean;
  reason?: string;
  partialResult?: UnknownRecord;
  result?: UnknownRecord;
}

export interface ObservedEvent {
  id?: string;
  sequence: number;
  kind: string;
  observed_at?: number;
  payload: ObservedPayload;
}

export interface ObservedRuntime extends UnknownRecord {
  run_label?: string;
  mission_id?: string;
  capture_policy?: string;
  recovery?: unknown;
}

export interface ObservedEpisode extends ProjectIdentity {
  episode_id: number;
  task_id: string | null;
  role: string;
  label: string;
  session_id: string;
  runtime: ObservedRuntime;
  capture_policy: string;
  state: string;
  started_at: number;
  updated_at: number;
  collection: {
    event_count: number;
    event_counts: Record<string, number>;
    complete: boolean;
    issues: CollectionIssue[];
    historical_data_unavailable: boolean;
  };
  quality: {
    state: string;
    approved: boolean;
    reviewer_kind?: string;
    reviewed_at?: number;
  };
  tool_pairs: ToolPair[];
  tool_pairs_total: number;
  tool_pairs_truncated: boolean;
  events: ObservedEvent[];
}

export interface ObservedPage extends ProjectIdentity {
  task_id: string | null;
  purpose: Purpose;
  episodes: ObservedEpisode[];
  global_complete: boolean;
  pagination: {
    has_more: boolean;
    next_cursor: string | null;
    returned_events: number;
    limit: number;
  };
  scope: 'received_public_observations';
  quality_status: 'not_automatically_approved';
}

export interface AdminIdentity {
  key_id: 'admin';
  role: 'admin';
  readonly: boolean;
  expires_at: number;
}

/** The browser's independent ordinary-user cookie, used only to check a link target. */
export interface WorkspaceIdentity {
  key_id: string;
  role: string;
  readonly: boolean;
}

export interface TenantCaptureStatus {
  state: string;
  counts: Record<string, number>;
  active_leases: number;
  pending_daemon_launches: number;
  last_error_code: string | null;
  last_event_at: number | null;
  last_diagnostic: UnknownRecord | null;
}

export interface CollectorStatus {
  state: string;
  last_poll_at?: number;
  projects?: number;
  inserted_events?: number;
  tenants_without_consent?: number;
  discovery?: Array<{ tenant_id: string; state: string; truncated: boolean; skipped: number }>;
  errors?: Array<{ tenant_id: string; code: string }>;
  tool_capture?: { state: string; tenants: Record<string, TenantCaptureStatus> };
}

export interface AuditEvent {
  id: number;
  created_at: number;
  actor: string;
  action: string;
  outcome: string;
  reviewer_kind: string | null;
  evidence_sha256: string | null;
  purpose: Purpose | null;
  projects: number | null;
  sft: number | null;
  quarantined: number | null;
}

export interface AuditPage { events: AuditEvent[] }
export interface ObservationExport { purpose: Purpose; projects: ProjectIdentity[] }
export interface DownloadedObservations { blob: Blob; filename: string }

export type Project = CollaborationProject;
export type Task = CollaborationTask;
