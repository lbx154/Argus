import { demoData } from '../data/demo'
import type { ArgusArtifactImport, ArgusArtifactIndex, ArgusArtifactRole, Campaign, Conference, Connection, DashboardData, DatasetSnapshot, EpisodeVerification, Idea, IdeationRun, LockedContractRequest, LockedContractResult, ResearchEpisode, ResourceSettings, TeamIntakeDraft, TeamProfile, ViewerReport } from '../types'

export type DataMode = 'live' | 'demo'

const configuredApiBase = (import.meta.env.VITE_API_BASE_URL || '/api').trim().replace(/\/+$/, '') || '/api'
const demoDataEnabled = String(import.meta.env.VITE_ENABLE_DEMO_DATA || '').trim().toLowerCase() === 'true'
const API_BASE = /^(?:https?:)?\/\//i.test(configuredApiBase) || configuredApiBase.startsWith('/')
  ? configuredApiBase
  : `/${configuredApiBase}`

export function apiUrl(path = ''): string {
  const suffix = path ? (path.startsWith('/') ? path : `/${path}`) : ''
  return `${API_BASE}${suffix}`
}

export function apiWebSocketUrl(path = '/ws'): string {
  const url = new URL(apiUrl(path), window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function timeoutFor(path: string, method = 'GET'): number {
  // Mutations that may already have crossed the server boundary get a long,
  // operation-specific window.  The client never retries them automatically.
  if (/\/campaigns\/[^/]+\/review-panel$/.test(path)) return 360_000
  if (/\/campaigns\/[^/]+\/start$/.test(path)) return 180_000
  if (path.endsWith('/review-imports/openreview')) return 25_000
  if (path.startsWith('/sources/sync') || path.startsWith('/releases/')) return 120_000
  return method.toUpperCase() === 'GET' ? 15_000 : 30_000
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeoutMs = timeoutFor(path, init?.method)
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      signal: controller.signal,
    })
    if (!response.ok) {
      const raw = await response.text()
      let detail = response.statusText
      try {
        const parsed = JSON.parse(raw) as { detail?: unknown }
        const value = parsed.detail
        detail = typeof value === 'string'
          ? value
          : value && typeof value === 'object' && 'message' in value
            ? String((value as { message: unknown }).message)
            : detail
      } catch { /* retain the HTTP status text for a non-JSON error */ }
      throw new Error(`API ${response.status}: ${detail}`)
    }
    return (await response.json()) as T
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(`请求超过 ${Math.round(timeoutMs / 1000)} 秒。服务端可能仍在处理；请先刷新状态，确认结果后再决定是否重试。`)
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function loadDashboard(): Promise<{ data: DashboardData; mode: DataMode }> {
  try {
    const raw = await request<Record<string, unknown>>('/dashboard')
    const [ideas, venues, campaigns, connections, resources, settings] = await Promise.all([
      request<{ items: Array<Record<string, unknown>> }>('/ideas?limit=500').catch(() => ({ items: [] })),
      request<{ items: Array<Record<string, unknown>> }>('/venues').catch(() => ({ items: [] })),
      request<{ items: Array<Record<string, unknown>> }>('/campaigns?limit=500'),
      request<{ items: Array<Record<string, unknown>> }>('/connections').catch(() => ({ items: [] })),
      request<{ items: Array<Record<string, unknown>> }>('/resources').catch(() => ({ items: [] })),
      request<{ values: Record<string, unknown> }>('/settings').catch(() => ({ values: {} })),
    ])
    return { data: adaptBackendDashboard(raw, ideas.items, venues.items, campaigns.items, connections.items, resources.items, settings.values), mode: 'live' }
  } catch (error) {
    if (demoDataEnabled) return { data: demoData, mode: 'demo' }
    throw error
  }
}

export type ReleaseStatus = {
  registry: Record<string, unknown>
  policy: Record<string, unknown>
  staged: Array<Record<string, unknown>>
}

export async function loadReleaseStatus(): Promise<ReleaseStatus> {
  return request('/releases')
}

export async function loadTeamProfiles(includeDisabled = true): Promise<TeamProfile[]> {
  return request(`/team-profiles?include_disabled=${includeDisabled ? 'true' : 'false'}`)
}

export async function saveTeamProfile(payload: Omit<TeamProfile, 'id'>, profileId?: string): Promise<TeamProfile> {
  return request(profileId ? `/team-profiles/${encodeURIComponent(profileId)}` : '/team-profiles', {
    method: profileId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  })
}

export async function extractTeamIntake(rawText: string): Promise<TeamIntakeDraft> {
  return request('/team-intakes/extract', {
    method: 'POST',
    body: JSON.stringify({ raw_text: rawText }),
  })
}

export async function confirmTeamIntake(
  intakeId: string,
  profile: Omit<TeamProfile, 'id'>,
): Promise<{ team_profile_id: string }> {
  return request(`/team-intakes/${encodeURIComponent(intakeId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({
      actor: 'flywheel-ui',
      name: profile.name,
      profile: {
        expertise: profile.expertise,
        methods: profile.methods,
        data_access: profile.data_access,
        constraints: profile.constraints,
        goals: profile.goals,
        policy: profile.policy,
      },
      training_consent: profile.training_consent,
      license_basis: profile.license_basis,
    }),
  })
}

export async function loadEpisodes(): Promise<ResearchEpisode[]> {
  const response = await request<{ items: Array<Record<string, unknown>> }>('/episodes')
  return response.items.map(adaptEpisode)
}

export async function loadEpisode(episodeId: string): Promise<ResearchEpisode> {
  return adaptEpisode(await request<Record<string, unknown>>(`/episodes/${encodeURIComponent(episodeId)}`))
}

export async function createEpisode(payload: Record<string, unknown>): Promise<ResearchEpisode> {
  return adaptEpisode(await request<Record<string, unknown>>('/episodes', { method: 'POST', body: JSON.stringify(payload) }))
}

export async function sealEpisode(
  episodeId: string,
  reason: string,
  terminalState?: string,
): Promise<Record<string, unknown>> {
  return request(`/episodes/${encodeURIComponent(episodeId)}/seal`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'flywheel-ui', reason, terminal_state: terminalState || undefined }),
  })
}

export async function verifyEpisode(episodeId: string): Promise<EpisodeVerification> {
  const raw = await request<Record<string, unknown>>(`/episodes/${encodeURIComponent(episodeId)}/verify`, { method: 'POST' })
  const head = json(raw.head_revision)
  return {
    valid: raw.valid === true,
    episode_id: episodeId,
    head_revision: number(head.revision_number, 0),
    manifest_sha256: text(raw.manifest_sha256) || undefined,
    checks: records(raw.checks).map((check) => ({ name: text(check.name, 'unknown'), passed: check.valid === true, detail: text(check.detail) || undefined })),
  }
}

export async function createReviewImport(
  episodeId: string,
  payload: { source_kind: 'paste' | 'json' | 'pdf' | 'openreview'; raw_text?: string; payload?: unknown; source_ref?: string },
): Promise<Record<string, unknown>> {
  return request(`/episodes/${encodeURIComponent(episodeId)}/review-imports`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createOpenReviewImport(episodeId: string, forumId: string): Promise<Record<string, unknown>> {
  return request(`/episodes/${encodeURIComponent(episodeId)}/review-imports/openreview`, {
    method: 'POST',
    body: JSON.stringify({ forum_id: forumId }),
  })
}

export async function confirmReviewImport(
  batchId: string,
  payload: { parsed?: Record<string, unknown> | unknown[]; redaction_confirmed: true; training_consent: boolean; license_basis: string },
): Promise<Record<string, unknown>> {
  return request(`/review-imports/${encodeURIComponent(batchId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'flywheel-ui', ...payload }),
  })
}

export async function discardReviewImport(batchId: string, reason: string): Promise<Record<string, unknown>> {
  return request(`/review-imports/${encodeURIComponent(batchId)}/discard`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'flywheel-ui', reason }),
  })
}

export async function loadArgusArtifacts(episodeId: string): Promise<ArgusArtifactIndex> {
  return request(`/episodes/${encodeURIComponent(episodeId)}/argus-artifacts`)
}

export async function loadArgusArtifactImports(episodeId: string): Promise<ArgusArtifactImport[]> {
  const response = await request<{ items: ArgusArtifactImport[] }>(`/episodes/${encodeURIComponent(episodeId)}/argus-artifact-imports`)
  return response.items
}

export async function loadArgusArtifactImport(importId: string): Promise<ArgusArtifactImport> {
  return request(`/argus-artifact-imports/${encodeURIComponent(importId)}`)
}

export async function stageArgusArtifactImport(
  episodeId: string,
  payload: {
    artifact_path: string
    role: ArgusArtifactRole
    expected_entry_sha256: string
    idempotency_key: string
  },
): Promise<ArgusArtifactImport> {
  return request(`/episodes/${encodeURIComponent(episodeId)}/argus-artifact-imports`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function confirmArgusArtifactImport(
  importId: string,
  payload: {
    expected_source_sha256: string
    redaction_confirmed: true
    manual_redaction_confirmed: boolean
    training_consent: boolean
    license_basis: string
    disposition: 'as_is' | 'replace_text'
    replacement_text?: string
  },
): Promise<ArgusArtifactImport> {
  return request(`/argus-artifact-imports/${encodeURIComponent(importId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'flywheel-ui', ...payload }),
  })
}

export async function discardArgusArtifactImport(importId: string, reason: string): Promise<ArgusArtifactImport> {
  return request(`/argus-artifact-imports/${encodeURIComponent(importId)}/discard`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'flywheel-ui', reason }),
  })
}

export async function loadDatasetSnapshots(): Promise<DatasetSnapshot[]> {
  const response = await request<{ items: Array<Record<string, unknown>> }>('/dataset-snapshots')
  return response.items.map(adaptDatasetSnapshot)
}

export async function previewDatasetSnapshot(): Promise<Record<string, unknown>> {
  return request('/dataset-snapshots/preview', { method: 'POST', body: JSON.stringify({ episode_ids: [], require_training_consent: true }) })
}

export async function createDatasetSnapshot(preview: Record<string, unknown>, name: string, licenseBasis: string): Promise<DatasetSnapshot> {
  const raw = await request<Record<string, unknown>>('/dataset-snapshots', {
    method: 'POST',
    body: JSON.stringify({
      name,
      actor: 'flywheel-ui',
      license_basis: licenseBasis,
      episode_ids: [],
      require_training_consent: true,
      expected_selection_sha256: preview.selection_sha256,
    }),
  })
  return adaptDatasetSnapshot(raw)
}

export async function loadIdeationRuns(profileId?: string): Promise<IdeationRun[]> {
  return request(`/ideation/runs${profileId ? `?team_profile_id=${encodeURIComponent(profileId)}` : ''}`)
}

export async function loadIdeationRun(runId: string): Promise<IdeationRun> {
  return request(`/ideation/runs/${encodeURIComponent(runId)}`)
}

export async function createIdeationRun(payload: Record<string, unknown>): Promise<IdeationRun> {
  return request('/ideation/runs', { method: 'POST', body: JSON.stringify(payload) })
}

export async function importIdeationCandidates(runId: string, payload: Record<string, unknown>): Promise<IdeationRun> {
  return request(`/ideation/runs/${encodeURIComponent(runId)}/candidates`, { method: 'POST', body: JSON.stringify(payload) })
}

export type CandidateCampaignReceipt = {
  id: string
  execution_state: string
  candidate_prompt_sha256: string
  launch_triggered: false
  idempotent: boolean
}

export async function createCandidateCampaign(
  candidateId: string,
  payload: { completion_target?: string; stop_criteria?: string[]; title?: string } = {},
): Promise<CandidateCampaignReceipt> {
  return request(`/ideation/candidates/${encodeURIComponent(candidateId)}/campaign`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function labelIdeationCandidate(candidateId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request(`/ideation/candidates/${encodeURIComponent(candidateId)}/labels`, { method: 'POST', body: JSON.stringify(payload) })
}

export async function savePairwisePreference(runId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request(`/ideation/runs/${encodeURIComponent(runId)}/pairwise`, { method: 'POST', body: JSON.stringify(payload) })
}

export async function downloadIdeationTrainingDataset(): Promise<{ count: string; automatic: string }> {
  const response = await fetch(apiUrl('/datasets/training-export'))
  if (!response.ok) throw new Error(`Training export failed: ${response.status}`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'argus-flywheel-conditioned-ideation.jsonl'
  anchor.click()
  URL.revokeObjectURL(url)
  return { count: response.headers.get('x-training-record-count') ?? 'unknown', automatic: response.headers.get('x-automatic-training') ?? 'unknown' }
}

export async function inspectReleaseCandidate(
  repository: string,
  ref: string,
): Promise<Record<string, unknown>> {
  return request('/releases/inspect', {
    method: 'POST',
    body: JSON.stringify({ repository, ref }),
  })
}

export async function stageReleaseCandidate(
  repository: string,
  ref: string,
  expectedSha: string,
): Promise<Record<string, unknown>> {
  return request('/releases/stage', {
    method: 'POST',
    body: JSON.stringify({
      repository,
      ref,
      expected_sha: expectedSha,
      confirm_isolated_stage: true,
    }),
  })
}

export async function lockWinnerContract(
  campaignId: string,
  contract: LockedContractRequest,
): Promise<LockedContractResult> {
  return request(`/campaigns/${encodeURIComponent(campaignId)}/locked-contract`, {
    method: 'POST',
    body: JSON.stringify(contract),
  })
}

const colors = ['#7C6CF2', '#2BB9A7', '#E2A84B', '#5EA1FF', '#EF6A72']
const text = (value: unknown, fallback = '') => typeof value === 'string' && value ? value : fallback
const number = (value: unknown, fallback = 0) => typeof value === 'number' && Number.isFinite(value) ? value : fallback
const json = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  if (typeof value === 'string') { try { const parsed = JSON.parse(value); return parsed && typeof parsed === 'object' ? parsed : {} } catch { return {} } }
  return {}
}
const structuredJson = (value: unknown): Record<string, unknown> | unknown[] | undefined => {
  if (Array.isArray(value)) return value
  if (value && typeof value === 'object') return value as Record<string, unknown>
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)
      if (Array.isArray(parsed)) return parsed
      if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>
    } catch { /* malformed staged data remains unavailable instead of being rewritten */ }
  }
  return undefined
}
const records = (value: unknown) => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : []
const elapsed = (seconds: unknown, fallback: string) => typeof seconds === 'number' ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : fallback

function adaptEpisode(raw: Record<string, unknown>): ResearchEpisode {
  const metadata = json(raw.metadata ?? raw.metadata_json)
  const gates = json(raw.gates)
  const eligibility = json(raw.data_eligibility)
  const revisions = records(raw.revisions).map((revision) => ({
    id: text(revision.id),
    episode_id: text(revision.episode_id, text(raw.id)),
    revision: number(revision.revision_number, 0),
    trigger_type: text(revision.reason, 'human_checkpoint'),
    manifest_sha256: text(revision.manifest_sha256),
    parent_manifest_sha256: text(revision.parent_revision_id) || null,
    sealed_by: text(revision.sealed_by, 'unknown'),
    sealed_at: text(revision.sealed_at),
    terminal_state: text(raw.state) || null,
  }))
  const revisionCount = number(raw.revision_count, revisions.length)
  const state = text(raw.state, 'active')
  const terminalStates = new Set(['closed', 'NO_WINNER', 'NOVELTY_COLLISION', 'RESOURCE_INFEASIBLE', 'NEGATIVE_RESULT', 'INCONCLUSIVE', 'KILLED', 'DEFERRED', 'POLICY_BLOCKED', 'SUBMISSION_READY_FOR_HUMAN_REVIEW', 'ACCEPTED', 'REJECTED', 'WITHDRAWN'])
  const integrity = eligibility.head_integrity_valid === true ? 'verified' : revisions.length ? 'fail' : 'unchecked'
  const humanGate = Object.values(gates).every(Boolean) ? 'approved' : 'pending'
  return {
    id: text(raw.id),
    title: text(raw.title, 'Untitled Research Episode'),
    team_profile_id: text(raw.team_profile_id) || undefined,
    team_name: text(raw.team_name) || undefined,
    venue_id: typeof raw.venue_id === 'number' ? raw.venue_id : undefined,
    venue_key: text(raw.venue_key) || undefined,
    venue_name: text(raw.venue_name) || undefined,
    deadline_id: typeof raw.deadline_id === 'number' ? raw.deadline_id : null,
    phase: text(metadata.phase, state),
    execution_state: state,
    human_gate_state: humanGate,
    integrity_state: integrity,
    terminal_state: terminalStates.has(state) ? state : null,
    head_revision: revisionCount,
    data_eligible: typeof eligibility.eligible === 'boolean' ? eligibility.eligible : null,
    eligibility_reasons: Object.entries(eligibility).filter(([key, value]) => key !== 'eligible' && value === false).map(([key]) => key),
    revisions,
    links: records(raw.links).map((link) => ({ entity_type: text(link.entity_type), entity_id: text(link.entity_id), relation: text(link.relation), created_at: text(link.created_at) || undefined })),
    review_imports: records(raw.review_imports).map((item) => ({
      id: text(item.id),
      source_kind: text(item.source_kind),
      source_ref: text(item.source_ref),
      state: text(item.state, 'draft'),
      raw_object_sha256: text(item.raw_object_sha256) || undefined,
      parsed: structuredJson(item.parsed ?? item.parsed_json),
      created_at: text(item.created_at) || undefined,
    })),
    created_at: text(raw.created_at),
    updated_at: text(raw.updated_at),
  }
}

function adaptDatasetSnapshot(raw: Record<string, unknown>): DatasetSnapshot {
  return {
    id: text(raw.id),
    schema_version: number(raw.schema_version, 1),
    record_count: number(raw.member_count, number(raw.record_count)),
    manifest_sha256: text(raw.manifest_sha256) || undefined,
    manifest_object_sha256: text(raw.manifest_object_sha256) || undefined,
    policy_object_sha256: text(raw.selection_sha256) || undefined,
    created_by: text(raw.created_by, 'unknown'),
    sealed_at: text(raw.created_at, text(raw.sealed_at)),
    valid: typeof raw.valid === 'boolean' ? raw.valid : undefined,
  }
}

function adaptCampaign(item: Record<string, unknown>, rawEvents: Array<Record<string, unknown>> = []): Campaign {
  const execution = text(item.execution_state).trim().toLowerCase() || 'unknown'; const progress = Math.round(number(item.progress) * 100); const snapshot = json(item.last_snapshot ?? item.last_snapshot_json); const config = json(item.config ?? item.config_json)
  const missionView = json(snapshot.mission_view); const mission = json(missionView.mission); const stage = json(missionView.stage); const usage = json(snapshot.usage_summary)
  const rawRoles = records(missionView.roles).length ? records(missionView.roles) : records(snapshot.roles)
  const backlog = records(snapshot.backlog).length ? records(snapshot.backlog) : records(missionView.dag)
  const timeline = records(missionView.timeline); const artifactRows = records(snapshot.foundry_artifacts).length ? records(snapshot.foundry_artifacts) : records(missionView.artifacts)
  const done = backlog.filter((task) => ['done', 'completed', 'verified'].includes(text(task.state, text(task.status)).toLowerCase())).length
  const reviewState = text(item.review_state).toLowerCase()
  const status: Campaign['status'] = execution === 'running' || execution === 'starting' ? 'running' : execution === 'needs_attention' || execution === 'failed' ? 'attention' : reviewState.includes('review') || reviewState === 'queued' ? 'review' : execution === 'paused' || execution === 'draining' ? 'paused' : execution === 'idle' ? 'idle' : execution === 'ready' ? 'ready' : execution === 'completed' ? 'completed' : 'unknown'
  const hasArgusProject = Boolean(text(item.argus_project_id))
  const hasConnection = Boolean(text(item.connection_id))
  const hasLaunchCommand = Boolean(text(item.launch_command_id))
  const launchTriggered = hasLaunchCommand || hasArgusProject || Boolean(text(item.started_at))
  const launchEligible = item.launch_eligible === true
  const launchIneligibilityReason = text(item.launch_ineligibility_reason) || undefined
  const canStart = launchEligible && hasConnection && (execution === 'idle' || execution === 'ready') && !launchTriggered
  const canRetryStart = launchEligible && hasConnection && ['failed', 'needs_attention'].includes(execution) && hasLaunchCommand && !hasArgusProject
  const canPause = hasArgusProject && hasConnection && ['starting', 'running', 'draining', 'needs_attention'].includes(execution)
  const canDrain = hasArgusProject && hasConnection && ['starting', 'running', 'needs_attention'].includes(execution)
  const canReview = hasArgusProject && hasConnection && ['running', 'draining', 'paused', 'completed', 'needs_attention'].includes(execution)
  const canLockContract = ['idle', 'ready', 'paused', 'completed', 'failed', 'needs_attention'].includes(execution)
  const configuredReleaseSha = text(config.argus_release_sha)
  const reportedSourceSha = text(snapshot.source_sha)
  const apiReleaseReference = text(item.release_reference)
  const releaseReference = apiReleaseReference || configuredReleaseSha || reportedSourceSha || undefined
  const releasePinned = item.release_pinned === true || (!('release_pinned' in item) && Boolean(configuredReleaseSha))
  const releaseReferenceSource = text(item.release_reference_source) === 'launch-manifest' ? 'launch-manifest' : configuredReleaseSha ? 'campaign-config' : reportedSourceSha ? 'runtime-reported' : 'none'
  return {
    id: text(item.id), title: text(item.title, 'Untitled campaign'), venue: text(item.venue_name, text(item.venue_key)), status, executionState: execution, candidateId: text(config.candidate_id) || undefined, connectionId: text(item.connection_id) || undefined, launchTriggered, launchEligible, launchIneligibilityReason, canStart, canRetryStart, canPause, canDrain, canReview, canLockContract, phase: text(stage.label, text(item.science_state, execution)), progress,
    summary: text(item.last_summary, text(mission.summary, 'No runtime summary has been reported yet.')), objective: text(item.objective, text(mission.objective, 'Objective not compiled.')), branch: text(config.branch, 'managed by Argus'), source: text(item.connection_id, 'No runtime selected'), commit: releaseReference ?? 'not reported',
    releasePinned, releaseReference, releaseReferenceSource,
    elapsed: elapsed(mission.elapsed_seconds, text(item.started_at, 'not started')), gpuHours: number(usage.gpu_hours, number(snapshot.gpu_hours)), budgetGpuHours: number(config.gpu_hours, number(config.gpu_budget_hours)), tasksDone: done, tasksTotal: backlog.length,
    processAlive: Boolean(item.process_alive), makingProgress: Boolean(item.making_progress), snapshotStale: Boolean(item.snapshot_stale),
    roles: rawRoles.map((role) => { const stateValue = text(role.state, text(role.status, 'idle')).toLowerCase(); const state = stateValue.includes('active') || stateValue.includes('running') ? 'active' : stateValue.includes('wait') || stateValue.includes('block') ? 'waiting' : 'idle'; return { name: text(role.name, text(role.role, 'Argus role')), state, task: text(role.task, text(role.current_task, 'No current task reported')) } }),
    events: [...timeline.map((event) => ({ time: text(event.time, text(event.created_at)).slice(11, 19), actor: text(event.actor, text(event.role, 'Argus')), text: text(event.text, text(event.summary, text(event.event, 'Runtime event'))), level: (text(event.level) === 'warning' || text(event.level) === 'error' ? 'warn' : 'normal') as 'warn' | 'normal' })), ...rawEvents.filter((event) => event.entity_id === item.id || !event.entity_id).map((event) => ({ time: text(event.created_at).slice(11, 19), actor: text(event.topic, 'Flywheel'), text: text(event.event_type), level: (text(event.severity) === 'warning' || text(event.severity) === 'error' ? 'warn' : 'normal') as 'warn' | 'normal' }))],
    artifacts: artifactRows.map((artifact) => ({ name: text(artifact.name, text(artifact.path, 'Unnamed artifact')), type: text(artifact.type, text(artifact.kind, 'artifact')), size: text(artifact.size, '—'), state: text(artifact.state, text(artifact.status, 'reported')) })), claims: [], prompt: text(item.objective),
  }
}

export async function loadCampaign(campaignId: string): Promise<Campaign> {
  const raw = await request<Record<string, unknown>>(`/campaigns/${encodeURIComponent(campaignId)}`)
  return adaptCampaign(raw, records(raw.events))
}

export type OutcomeRecord = {
  id: string
  campaign_id: string
  campaign_title?: string
  submission_version: string
  reviewer_feedback: Array<{ reviewer: string; score?: number; opinion_redacted: string }>
  decision: string
  rebuttal_objective?: string
  follow_up_campaign_id?: string
  training_export_eligible: boolean
  training_export_ineligibility_reasons?: string[]
  created_at?: string
}

export type OutcomeCreate = {
  campaign_id: string
  submission_version: string
  reviewer_feedback: Array<{ reviewer: string; score?: number; opinion_redacted: string }>
  decision: string
  consent_to_training_export: boolean
  review_license_confirmed: boolean
  redaction_confirmed: boolean
}

export async function loadOutcomes(): Promise<OutcomeRecord[]> {
  const response = await request<{ items: OutcomeRecord[] }>('/outcomes/submissions?limit=500')
  return response.items
}

export async function createOutcome(payload: OutcomeCreate): Promise<OutcomeRecord> {
  return request('/outcomes/submissions', { method: 'POST', body: JSON.stringify(payload) })
}

export async function createOutcomeFollowUp(outcomeId: string, approvalReason: string): Promise<Record<string, unknown>> {
  return request(`/outcomes/submissions/${encodeURIComponent(outcomeId)}/follow-up`, { method: 'POST', body: JSON.stringify({ actor: 'flywheel-ui', approval_reason: approvalReason }) })
}

export async function downloadOutcomeTrainingSample(outcomeId: string): Promise<void> {
  const response = await fetch(apiUrl(`/outcomes/submissions/${encodeURIComponent(outcomeId)}/training-export`))
  if (!response.ok) throw new Error(`Training sample export failed: ${response.status}`)
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `argus-outcome-${outcomeId}.json`; anchor.click(); URL.revokeObjectURL(url)
}

function adaptIdea(raw: Record<string, unknown>): Idea {
  const meta = json(raw.metadata ?? raw.metadata_json)
  const delta = json(raw.differentiation)
  const nearest = records(delta.nearest_items)
  const sourceKind = (url: string): 'arXiv' | 'OpenReview' | 'GitHub' => url.includes('openreview.net') ? 'OpenReview' : url.includes('github.com') ? 'GitHub' : 'arXiv'
  return {
    id: String(raw.id), title: text(raw.title_zh, 'Untitled candidate'), thesis: text(raw.core_hypothesis, text(raw.problem_gap, 'Hypothesis not yet specified.')),
    field: text(raw.reusable_program, text(raw.venue_name, 'Research candidate')), novelty: number(meta.novelty_score, -1), feasibility: number(meta.feasibility_score, -1), freshness: text(raw.freshness_state, number(meta.freshness_score, 0).toString()),
    delta: text(delta.differentiation_summary), sources: nearest.map((item) => ({ kind: sourceKind(text(item.url)), label: text(item.title, text(item.item_id, 'Source item')), age: 'snapshot' })), compute: text(raw.compute_fit), risk: text(raw.risk_level),
  }
}

function adaptBackendDashboard(raw: Record<string, unknown>, rawIdeas: Array<Record<string, unknown>>, rawVenues: Array<Record<string, unknown>>, rawCampaigns: Array<Record<string, unknown>>, rawConnections: Array<Record<string, unknown>>, rawResources: Array<Record<string, unknown>>, settings: Record<string, unknown>): DashboardData {
  const ideaMap = new Map<string, Idea[]>()
  rawIdeas.forEach((item) => { const key = text(item.venue_key); const list = ideaMap.get(key) ?? []; list.push(adaptIdea(item)); ideaMap.set(key, list) })
  const deadlines = Array.isArray(raw.upcoming_deadlines) ? raw.upcoming_deadlines as Array<Record<string, unknown>> : []
  const conferences: Conference[] = deadlines.map((item, index) => {
    const key = text(item.venue_key, `venue-${index}`); const evidence = text(item.evidence_status, 'forecast')
    const official = evidence === 'official' || evidence === 'official_confirmed' || evidence === 'verified'
    return { id: `${key}-${String(item.id)}`, venueKey: key, deadlineId: number(item.id), acronym: text(item.display_name, key), name: text(item.display_name, key), deadline: official ? text(item.deadline_date) : text(item.forecast_window_start, text(item.deadline_date)), deadlineEnd: official ? undefined : text(item.forecast_window_end) || undefined, kind: official ? 'official' : 'forecast', track: text(item.round_note, 'Full paper'), area: text(item.category_id, 'Research'), reminderDays: number(item.days_remaining), color: colors[index % colors.length], ideas: ideaMap.get(key) ?? [] }
  })
  const withRolling = [...conferences]
  rawVenues.forEach((venue, index) => { const key = text(venue.venue_key); if (key && !conferences.some((item) => item.venueKey === key)) withRolling.push({ id: `${key}-rolling`, venueKey: key, rolling: true, acronym: text(venue.display_name, key), name: text(venue.official_name, text(venue.display_name, key)), deadline: '', kind: 'official', track: 'Rolling / date not registered', area: text(venue.category_id, 'Research'), reminderDays: 9999, color: colors[index % colors.length], ideas: ideaMap.get(key) ?? [] }) })
  const rawEvents = Array.isArray(raw.recent_events) ? raw.recent_events as Array<Record<string, unknown>> : []
  const campaigns: Campaign[] = rawCampaigns.map((item) => adaptCampaign(item, rawEvents))
  const campaignSources = new Map(rawCampaigns.map((item) => [text(item.id), item]))
  const viewerReports: ViewerReport[] = campaigns.flatMap((campaign) => {
    const source = campaignSources.get(campaign.id); const score = number(source?.viewer_score, -1)
    const reviewerScores = json(source?.reviewer_scores ?? source?.reviewer_scores_json)
    const feedback = json(source?.latest_review_feedback)
    const dimensionNotes = json(feedback.dimension_notes)
    const dimensions = Object.entries(reviewerScores).flatMap(([label, value]) => typeof value === 'number' && Number.isFinite(value) ? [{ label: label.replaceAll('_', ' '), score: value, note: text(dimensionNotes[label], text(feedback.report, 'Independent evaluator score; see the frozen review artifact for rationale.')) }] : [])
    if (score < 0 || dimensions.length === 0) return []
    const blockers = Array.isArray(feedback.blockers) ? feedback.blockers.filter((item): item is string => typeof item === 'string') : []
    const oralState = text(feedback.oral_readiness, text(source?.latest_review_recommendation, 'unknown'))
    const oralReadiness = number(feedback.oral_readiness, -1)
    return [{ id: `viewer-${campaign.id}`, campaignId: campaign.id, venue: campaign.venue, updated: text(source?.latest_review_updated_at, 'from backend'), verdict: text(feedback.report, text(feedback.state, 'Independent review completed.')), overall: score, confidence: number(feedback.confidence, -1), oralReadiness, oralReadinessLabel: oralState, dimensions, blockers }]
  })
  const connections: Connection[] = rawConnections.map((item) => { const meta = json(item.metadata ?? item.metadata_json); const state = text(item.status) === 'online' ? 'connected' : text(item.status) === 'testing' ? 'testing' : 'offline'; const backendReady = typeof meta.backend_ready === 'boolean' ? meta.backend_ready : null; const tokenSource = text(item.token_source) === 'environment' ? 'environment' as const : text(item.token_source) === 'memory' ? 'memory' as const : null; return { id: text(item.id), name: text(item.name), kind: text(item.kind) === 'local' ? 'local' : 'remote', address: text(item.base_url), state, version: text(meta.argus_revision, text(meta.argus_release_id, text(meta.release_sha, 'not reported'))), latency: text(meta.latency, '—'), capabilities: Array.isArray(meta.capabilities) ? meta.capabilities.map(String) : [], managed: text(meta.managed_by) === 'argus-flywheel', backendReady, lastError: text(item.last_error), tokenSource } })
  const gpus = rawResources.flatMap((item, poolIndex) => { const capacity = json(item.capacity ?? item.capacity_json); const devices = Array.isArray(capacity.devices) ? capacity.devices as Array<Record<string, unknown>> : []; return devices.map((device, index) => ({ id: `${text(item.id)}-${index}`, poolId: text(item.id), label: text(device.name, `GPU ${index}`), memory: typeof device.memory_total_mib === 'number' ? `${device.memory_total_mib} MiB` : text(device.memory_total, text(capacity.gpu_memory, 'unknown')), host: text(item.name, `Pool ${poolIndex + 1}`), enabled: Boolean(item.enabled) })) })
  const pools = rawResources.map((item) => ({ id: text(item.id), label: text(item.name, 'Unnamed pool'), type: text(item.resource_type, 'unconfigured'), enabled: Boolean(item.enabled) }))
  const resources: ResourceSettings = { gpus, pools, roles: Array.isArray(settings.role_models) ? settings.role_models as ResourceSettings['roles'] : [] }
  const approvals = campaigns.filter((campaign) => { const source = campaignSources.get(campaign.id); return source?.schedule_state === 'awaiting_approval' || source?.review_state === 'human_review' }).map((campaign) => ({ id: `approval-${campaign.id}`, title: 'Campaign requires a human decision', campaign: campaign.title, kind: 'Human gate', requested: 'pending', risk: 'medium' as const, detail: campaign.summary }))
  return { conferences: withRolling, campaigns, viewerReports, connections, approvals, resources }
}

export async function performAction(action: string, payload: Record<string, unknown>, mode: DataMode): Promise<{ ok: boolean; simulated?: boolean; message?: string }> {
  if (mode === 'demo') {
    return { ok: false, simulated: true, message: 'Demo 模式：仅模拟界面反馈，没有改变服务器状态，也没有启动真实 Argus。' }
  }
  if (action === 'campaigns') {
    const created = await request<{ id: string }>('/campaigns', { method: 'POST', body: JSON.stringify(payload) })
    try {
      await request(`/campaigns/${created.id}/start`, { method: 'POST', body: JSON.stringify({ human_approved: true, approval_reason: 'Human approved bounded portfolio screening from Flywheel UI', actor: 'flywheel-ui' }) })
      return { ok: true, message: `Campaign ${created.id.slice(0, 8)} 已创建，Argus Portfolio 筛选已启动。` }
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'unknown start error'
      return { ok: false, message: `Campaign ${created.id.slice(0, 8)} 已保存，但 Argus 未启动：${detail}。请在 Campaign 详情中修正后重试。` }
    }
  }
  if (action === 'settings') {
    await request('/settings', { method: 'PATCH', body: JSON.stringify({ values: payload }) })
    return { ok: true, message: '设置已保存。' }
  }
  if (action.startsWith('patch:')) {
    await request(`/${action.slice('patch:'.length)}`, { method: 'PATCH', body: JSON.stringify(payload) })
    return { ok: true, message: '配置已更新。' }
  }
  const actionPayload = action.startsWith('campaigns/') && action.endsWith('/start')
    ? { ...payload, human_approved: true, approval_reason: 'Human approved campaign start or retry from Flywheel UI', actor: 'flywheel-ui' }
    : payload
  await request(`/${action}`, { method: 'POST', body: JSON.stringify(actionPayload) })
  return { ok: true, message: '操作已由服务器接受。' }
}
