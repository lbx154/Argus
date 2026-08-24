export type DeadlineKind = 'official' | 'forecast'
export type CampaignStatus = 'running' | 'review' | 'attention' | 'paused' | 'idle' | 'ready' | 'completed' | 'unknown'

export interface Idea {
  id: string
  title: string
  thesis: string
  field: string
  novelty: number
  feasibility: number
  freshness: number | string
  delta: string
  sources: Array<{ kind: 'arXiv' | 'OpenReview' | 'GitHub'; label: string; age: string }>
  compute: string
  risk: string
}

export interface Conference {
  id: string
  venueKey?: string
  deadlineId?: number
  rolling?: boolean
  acronym: string
  name: string
  deadline: string
  deadlineEnd?: string
  kind: DeadlineKind
  track: string
  area: string
  reminderDays: number
  color: string
  ideas: Idea[]
}

export interface EvidenceClaim {
  id: string
  claim: string
  strength: 'supported' | 'partial' | 'blocked'
  evidence: string
  updated: string
}

export interface Campaign {
  id: string
  title: string
  venue: string
  status: CampaignStatus
  executionState: string
  candidateId?: string
  connectionId?: string
  launchTriggered: boolean
  launchEligible: boolean
  launchIneligibilityReason?: string
  canStart: boolean
  canRetryStart: boolean
  canPause: boolean
  canDrain: boolean
  canReview: boolean
  canLockContract: boolean
  phase: string
  progress: number
  summary: string
  objective: string
  branch: string
  source: string
  commit: string
  releasePinned: boolean
  releaseReference?: string
  releaseReferenceSource: 'launch-manifest' | 'campaign-config' | 'runtime-reported' | 'none'
  elapsed: string
  gpuHours: number
  budgetGpuHours: number
  tasksDone: number
  tasksTotal: number
  processAlive?: boolean
  makingProgress?: boolean
  snapshotStale?: boolean
  roles: Array<{ name: string; state: 'active' | 'idle' | 'waiting'; task: string }>
  events: Array<{ time: string; actor: string; text: string; level?: 'normal' | 'warn' }>
  artifacts: Array<{ name: string; type: string; size: string; state: string }>
  claims: EvidenceClaim[]
  prompt: string
}

export interface LockedContractRequest {
  primary_claim: string
  primary_metric: string
  minimum_effect: string
  data_split: string
  confirmatory_seeds: number[]
  strongest_baselines: string[]
  human_approved: true
  approval_reason: string
}

export interface LockedContractResult {
  id?: string
  promoted_from_campaign_id?: string
  campaign_id?: string
  contract_hash?: string
  contract_version?: number
  idempotent?: boolean
  locked_contract?: {
    contract_sha256?: string
    version?: number
    idempotent?: boolean
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface ViewerReport {
  id: string
  campaignId: string
  venue: string
  updated: string
  verdict: string
  overall: number
  confidence: number
  oralReadiness: number
  oralReadinessLabel?: string
  dimensions: Array<{ label: string; score: number; note: string }>
  blockers: string[]
}

export interface Connection {
  id: string
  name: string
  kind: 'local' | 'remote'
  address: string
  state: 'connected' | 'offline' | 'testing'
  version: string
  latency: string
  capabilities: string[]
  managed?: boolean
  backendReady?: boolean | null
  lastError?: string
  tokenSource?: 'environment' | 'memory' | null
}

export interface Approval {
  id: string
  title: string
  campaign: string
  kind: string
  requested: string
  risk: 'low' | 'medium' | 'high'
  detail: string
}

export interface ResourceSettings {
  gpus: Array<{ id: string; poolId?: string; label: string; memory: string; host: string; enabled: boolean }>
  pools: Array<{ id: string; label: string; type: string; enabled: boolean }>
  roles: Array<{ role: string; provider: string; model: string; budget: string }>
}

export interface DashboardData {
  conferences: Conference[]
  campaigns: Campaign[]
  viewerReports: ViewerReport[]
  connections: Connection[]
  approvals: Approval[]
  resources: ResourceSettings
}

export interface TeamProfile {
  id: string
  name: string
  expertise: string[]
  methods: string[]
  data_access: string[]
  constraints: Record<string, unknown>
  goals: Record<string, unknown>
  policy: Record<string, unknown>
  training_consent: boolean
  license_basis: string
  enabled: boolean
  metadata?: Record<string, unknown>
  updated_at?: string
}

export interface IdeationCandidate {
  id: string
  candidate_key: string
  title: string
  candidate: Record<string, unknown>
  evidence_refs: unknown[]
  imported_from: string
  artifact_sha256?: string
  labels: Array<{ id: string; decision: string; dimensions: Record<string, number | null>; labeler_alias: string; training_consent: boolean }>
}

export interface IdeationRun {
  id: string
  team_profile_id: string
  team_name?: string
  venue_key?: string
  venue_name?: string
  deadline_id?: number
  resource_id?: string
  connection_id?: string
  campaign_id?: string
  state: string
  condition_schema_version: number
  condition_snapshot: Record<string, unknown>
  condition_sha256?: string
  objective_sha256: string
  candidate_artifact_sha256?: string
  candidate_manifest?: Record<string, unknown>
  objective_path: string
  objective?: string
  source_snapshot_ref?: string
  source_snapshot_sha256?: string
  training_consent: boolean
  license_basis: string
  launch_triggered?: boolean
  candidates?: IdeationCandidate[]
  pairwise_preferences?: Array<Record<string, unknown>>
  created_at?: string
}

export interface EpisodeRevision {
  id: string
  episode_id: string
  revision: number
  trigger_type: string
  manifest_sha256: string
  parent_manifest_sha256?: string | null
  sealed_by: string
  sealed_at: string
  terminal_state?: string | null
}

export interface EpisodeLink {
  entity_type: string
  entity_id: string
  relation: string
  created_at?: string
}

export interface ResearchEpisode {
  id: string
  title: string
  team_profile_id?: string
  team_name?: string
  venue_id?: number
  venue_key?: string
  venue_name?: string
  deadline_id?: number | null
  phase: string
  execution_state: string
  human_gate_state: string
  integrity_state: string
  terminal_state?: string | null
  head_revision: number
  data_eligible: boolean | null
  eligibility_reasons?: string[]
  revisions?: EpisodeRevision[]
  links?: EpisodeLink[]
  review_imports?: Array<{
    id: string
    source_kind: string
    source_ref: string
    state: string
    raw_object_sha256?: string
    parsed?: Record<string, unknown> | unknown[]
    created_at?: string
  }>
  created_at: string
  updated_at: string
}

export interface EpisodeVerification {
  valid: boolean
  episode_id: string
  head_revision: number
  manifest_sha256?: string
  checks: Array<{ name: string; passed: boolean; detail?: string }>
  errors?: string[]
}

export type ArgusArtifactRole =
  | 'condition_snapshot'
  | 'prompt_contract'
  | 'trajectory'
  | 'experiment_spec'
  | 'experiment_result'
  | 'paper'
  | 'outcome'
  | 'review_certificate'
  | 'integrity_report'
  | 'reproducibility_manifest'

export type ArgusArtifactImportState = 'draft' | 'confirmed' | 'discarded'

export interface ArgusArtifactIndexItem {
  path: string
  kind: string
  exists: boolean
  size: number | null
  sha256?: string | null
  content_type?: string | null
  media_type?: string | null
  name?: string | null
  modified_at?: string | null
  entry_sha256?: string
}

export interface ArgusArtifactIndex {
  episode_id: string
  campaign_id?: string | null
  argus_project_id?: string | null
  items: ArgusArtifactIndexItem[]
  limits: {
    max_index_items: number
    max_artifact_bytes: number
    max_draft_bytes_per_episode: number
  }
}

export interface ArgusArtifactPreview {
  available: boolean
  text?: string
  truncated?: boolean
  max_bytes?: number
}

export interface ArgusArtifactImport {
  id: string
  episode_id: string
  role: ArgusArtifactRole
  state: ArgusArtifactImportState
  source_entry: ArgusArtifactIndexItem
  source_entry_sha256: string
  source_sha256: string
  source_byte_length: number
  media_type?: string
  scan_state?: string
  manual_redaction_required: boolean
  content_object_sha256?: string | null
  license_basis?: string
  training_consent?: boolean
  confirmed_by?: string | null
  confirmed_at?: string | null
  discarded_by?: string | null
  discarded_at?: string | null
  sealed_in_head: boolean
  sealed_revision_id: string | null
  created_at?: string
  updated_at?: string
  preview?: ArgusArtifactPreview
}

export interface DatasetSnapshot {
  id: string
  schema_version: number
  record_count: number
  manifest_sha256?: string
  manifest_object_sha256?: string
  policy_object_sha256?: string
  created_by: string
  sealed_at: string
  valid?: boolean
}

export interface TeamIntakeDraft {
  id: string
  state: string
  raw_text?: string
  extracted: {
    name?: string
    expertise?: string[]
    methods?: string[]
    data_access?: string[]
    constraints?: Record<string, unknown>
    goals?: Record<string, unknown>
    policy?: Record<string, unknown>
    training_consent?: boolean
    license_basis?: string
    enabled?: boolean
  }
  uncertainties?: string[]
}
