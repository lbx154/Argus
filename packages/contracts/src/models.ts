/** Shared wire models consumed by the browser, TUI and Node services. */

export type { EventMsg } from './events.js';
import type { EventMsg } from './events.js';

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
  /** The tasks its calls under way are on: one role can work on several side by side. */
  tasks?: string[];
}

export interface Daemon {
  alive: boolean;
  pid: number | null;
  /** Nothing is under way, and Resume has work to take up. */
  resumable?: boolean;
  uptime_seconds: number | null;
  started_at_iso?: string | null;
  backend: string | null;
  backend_label?: string | null;
}

export interface BacklogItem {
  id: string;
  title: string;
  objective: string;
  status: string;
  priority: number;
  iterate?: boolean;
  pending_question?: string;
  operator_decision?: import('./decisions.js').OperatorDecisionCard;
  ts?: number;
  tags?: string[];
  notes?: string;
  started_ts?: number | null;
  finished_ts?: number | null;
  /** The latest thing a role said on this task: a message while it worked, or a letter. */
  latest_action?: { role: string; text: string; ts: number };
  /** Files named by the handoff that closed this task, in opening order. */
  result_paths?: string[];
  last_error?: string;
  iteration_max_cycles?: number;
  iteration_cycles_done?: number;
  iteration_cost_usd?: number;
  original_objective?: string;
  orphan_retries?: number;
  deps?: string[];
  /** The Planner's plan this task belongs to; empty for a task taken from a request. */
  plan_id?: string;
  acceptance_check?: string;
  plan_hypothesis?: string;
  goal_contribution?: string;
  expected_regressions?: string;
  decision_rule?: string;
  non_goals?: string[];
  outcome?: MissionOutcomeDimensions;
}

export interface MissionOutcomeDimensions {
  execution_status: string;
  review_status: string;
  stage_certification: string;
  interruption_kind: string;
  resumable: boolean;
  /** Why a ``parked`` row is parked; older ``paused_*`` rows name it in their status. */
  park?: { reason: string };
}

/** One safe, workspace-relative file selected for a completed delivery. */
export interface DeliveryTarget {
  /** Delivery occurrences of this path, including unchanged content, counted from the record. */
  delivery_number?: number;
  path: string;
  label: string;
  source: string;
}

/** The Manager's delivery receipt, shared by the chat card and preview. */
export interface DeliveryReceipt {
  schema_version: number;
  delivery_id: string;
  kind: 'task_completed' | 'submission_certified' | 'stage_package' | string;
  item_id: string;
  title: string;
  summary: string;
  status: string;
  review_status: string;
  /** Who settled ``review_status``: ``reviewer`` is an independent review role; anything else is the worker's own check. Absent on older receipts. */
  review_source?: string;
  /** One check approved unverified on an accepted risk (operator, or Manager with no operator); shown instead of a plain pass. */
  residual_risk?: string;
  delivered_at: number;
  primary_target: DeliveryTarget | null;
  targets: DeliveryTarget[];
  /** The version of each delivered file; ``stored`` text versions can be diffed. */
  snapshots?: DeliverySnapshot[];
}

export interface DeliverySnapshot {
  path: string;
  version: string;
  size: number;
  stored: boolean;
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

export interface Snapshot {
  schema_version?: number;
  /** When the server read it, in milliseconds; a later read is larger. A page holds the later of two. */
  read_at?: number;
  session: {
    id: string;
    display_name: string;
    objective: string;
    created?: number;
    last_active: number;
    workdir: string;
  };
  daemon: Daemon;
  roles: Role[];
  work_status?: ProjectWorkStatus;
  /** The newest model call this project opened: what it actually ran on. */
  last_call?: { backend: string; model: string; effort: string; run_label: string; role?: string; ts: number } | null;
  backlog: BacklogItem[];
  spend_usd?: number | null;
  spend_status?: 'empty' | 'priced' | 'partial' | 'unpriced' | 'not_billed';
  usage_summary?: UsageSummary;
  pending_questions?: Array<Record<string, unknown>>;
  /** Foreground Manager/SELF requests that remain cancellable across a page reload. */
  manager_requests?: Array<{ request_id: string; status: 'running' }>;
  /** The committed operator objective, for overview headings. */
  goal?: string;
}

/** How many of a role's newest entries its log holds: a round can run for hours, and its earlier
 * entries are in the record. */
export const ROLE_LOG = 30;

/** A role's log kept to its newest `ROLE_LOG` entries, in order. */
export function newestPerRole(events: EventMsg[]): EventMsg[] {
  const seen = new Map<unknown, number>();
  return [...events].reverse().filter(event => {
    const count = (seen.get(event.agent_layer) ?? 0) + 1;
    seen.set(event.agent_layer, count);
    return count <= ROLE_LOG;
  }).reverse();
}

export interface ProjectWorkStatus {
  state: 'running' | 'waiting' | 'paused' | 'idle';
  reason: 'operator_input' | 'not_running' | 'awaiting_next_step' | '';
  role: string;
  roles: string[];
  taskId: string;
  title: string;
  foreground: boolean;
  activityAt: number | null;
  label: { 'zh-CN': string; en: string };
}

/** A page counts dialogue and task rows; tool steps belong only to role logs. */
export interface ConversationPage {
  rows: EventMsg[];
  before: number | null;
  cursor: number;
}

export interface ProjectRow {
  work_status?: ProjectWorkStatus;
  id: string;
  label: string;
  display_name?: string;
  workdir: string;
  last_active: number;
  daemon_alive: boolean;
  daemon_pid: number | null;
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
  | 'model'
  | 'binary';

/** A registered project file exposed by the protected artifact API. */
export interface ArtifactInfo {
  path: string;
  name: string;
  kind: ArtifactKind;
  mime: string;
  size: number;
  mtime: number | null;
  source?: 'manager_live' | 'reviewer_evidence' | 'research_registered' | 'delivery' | 'reply';
  group_title?: string;
  /** Included by the single-artifact endpoint for text/HTML files only. */
  preview?: string;
  truncated?: boolean;
  /** For a URDF: files beside it that declare a loop closure or constraint the URDF tree cannot hold. */
  constraint_files?: string[];
}

export interface GitDiffView {
  available: boolean;
  branch: string;
  status: string;
  stat: string;
  diff: string;
  truncated: boolean;
}

// --- Vertical store (GET /api/verticals; capability "verticals.store.v1") ---

/** Where a vertical comes from: shipped in core, bundled with the package, installed from the catalog, or only listed there. */
export type VerticalKind = 'builtin' | 'package' | 'installed' | 'available';

export type VerticalAction = 'install' | 'update' | 'enable' | 'disable' | 'uninstall';

/** The store's record of the last or current lifecycle job for one vertical. */
export interface VerticalOperation {
  status: 'running' | 'done' | 'failed';
  action: string;
  /** Integer percent 0–100; 0 means the job has not reported a step yet. */
  progress: number;
  message: string | null;
  /** ISO-8601 UTC timestamps, e.g. "2026-09-14T19:00:00Z". */
  started: string;
  finished: string | null;
}

export interface VerticalRow {
  name: string;
  /** Category/domain/optional specialty; absent on older servers. Not skill inheritance. */
  routing_path?: string[];
  purpose: string;
  purpose_zh: string | null;
  kind: VerticalKind;
  version: string | null;
  installed_version: string | null;
  enabled: boolean;
  update_available: boolean;
  /** Other verticals this one needs, by name. */
  requires: string[];
  /** Verticals this one shares resources with, by name. */
  shared: string[];
  python_requirements: string[];
  /** Python distributions not importable from the environment Argus runs in. */
  missing_python: string[];
  /** Declared runtime incompatibilities of the installed version; absent on older servers. */
  runtime_issues?: string[];
  /** Declared runtime incompatibilities of the catalog install/update dependency closure. */
  install_issues?: string[];
  tags: string[];
  size_bytes: number | null;
  /** Project sids currently bound to this vertical. */
  used_by: string[];
  operation: VerticalOperation | null;
  managed_by_host: boolean;
  /** The only actions the server accepts right now; the UI offers nothing else. */
  actions: VerticalAction[];
}

export interface VerticalCatalogStatus {
  source: string;
  /** ISO-8601 UTC, or null when the catalog was never fetched. */
  fetched_at: string | null;
  release_tag: string | null;
  error: string | null;
}

export interface VerticalsPayload {
  verticals: VerticalRow[];
  catalog: VerticalCatalogStatus;
  host: {
    managed_by_host: boolean;
    store_root: string;
  };
}

/** 202 for install/update/uninstall jobs, 200 for enable/disable. */
export interface VerticalManageResult {
  name: string;
  action: VerticalAction;
  operation: VerticalOperation | null;
}

/** A completed operator upload, kept separately from the message text. */
export interface UploadedAttachment {
  attachment_id: string;
  relative_path: string;
  original_name: string;
  stored_name: string;
  mime: string;
  size_bytes: number;
}
