import { canonicalEventType, EVENT_TYPES } from './eventCatalog.js';
import { eventKey } from './events.js';
import type {
  ArtifactInfo,
  EventMsg,
  MissionAchievement,
  MissionDagNode,
  MissionMetricView,
  MissionRoleView,
  MissionTimelineItem,
  MissionView,
  Snapshot,
} from './types.js';

const ROLE_NAMES = ['manager', 'planner', 'engineer', 'reviewer'] as const;
const ACTIVE_STATUSES = new Set(['running', 'in_progress', 'claimed']);

const S = (event: EventMsg, key: string): string => String(event[key] ?? '').trim();
const N = (event: EventMsg, key: string): number | null => {
  const value = Number(event[key]);
  return Number.isFinite(value) ? value : null;
};

function copyView(view: MissionView): MissionView {
  return JSON.parse(JSON.stringify(view)) as MissionView;
}

export function emptyMissionView(): MissionView {
  return {
    schema_version: 1,
    bootstrapped: false,
    mission: {
      id: '',
      title: '',
      objective: '',
      status: 'idle',
      started_at: null,
      completed_at: null,
      elapsed_seconds: 0,
    },
    stage: { id: '', label: '' },
    round: { current: 0, max: 0 },
    active_role: '',
    roles: ROLE_NAMES.map((role) => ({ role, status: 'waiting', label: 'Waiting', updated_at: 0 })),
    dag: [],
    hypotheses: [],
    experiments: [],
    metrics: [],
    primary_metric: null,
    timeline: [],
    artifacts: [],
    learned_skills: [],
    learned_wiki_pages: [],
    storage: {
      project_skill_dir: '',
      global_skill_dir: '',
      project_skill_count: 0,
      global_skill_count: 0,
      skill_history_compressed: 0,
      wiki_retired_compressed: 0,
      skill_history_bytes_saved: 0,
      wiki_retired_bytes_saved: 0,
      wiki_paths: [],
    },
    achievement: null,
    review: { status: '', reason: '', rejected_attempts: 0 },
    last_event_ts: 0,
    updated_at: 0,
  };
}

function upsert<T extends Record<string, unknown>>(rows: T[], key: keyof T, value: unknown, patch: T): void {
  if (value == null || value === '') return;
  const index = rows.findIndex((row) => row[key] === value);
  if (index >= 0) rows[index] = { ...rows[index], ...patch };
  else rows.push(patch);
}

function setRole(view: MissionView, role: string, status: string, label: string, ts: number): void {
  if (!ROLE_NAMES.includes(role as typeof ROLE_NAMES[number])) return;
  const patch: MissionRoleView = { role, status, label, updated_at: ts };
  upsert(view.roles as Array<MissionRoleView & Record<string, unknown>>, 'role', role, patch as MissionRoleView & Record<string, unknown>);
  if (status === 'active') view.active_role = role;
}

function addTimeline(
  view: MissionView,
  event: EventMsg,
  role: string,
  title: string,
  detail = '',
  tone: MissionTimelineItem['tone'] = 'neutral',
): void {
  const id = eventKey(event);
  if (view.timeline.some((row) => row.id === id)) return;
  const row: MissionTimelineItem = {
    id,
    ts: Number(event.ts ?? Date.now() / 1000),
    type: canonicalEventType(event.type),
    role,
    title: title.slice(0, 180),
    detail: detail.slice(0, 500),
    tone,
  };
  (['item_id', 'branch_id', 'hypothesis_id', 'experiment_id', 'metric_id'] as const).forEach((key) => {
    const value = S(event, key);
    if (value) row[key] = value;
  });
  view.timeline = [...view.timeline, row].slice(-120);
}

const PROGRESS_LABELS: Record<string, string> = {
  agent_message: 'Reporting progress',
  assistant_message: 'Reporting progress',
  command_execution: 'Running a command',
  reasoning: 'Reasoning',
  tool_use: 'Using a tool',
  tool_result: 'Inspecting tool output',
  codex_idle: 'Waiting for model output',
};

function refreshPrimaryMetric(view: MissionView): void {
  const metrics = view.metrics.filter((metric) => Number.isFinite(metric.value));
  if (!metrics.length) {
    view.primary_metric = null;
    return;
  }
  let candidates = metrics.some((metric) => metric.primary)
    ? metrics.filter((metric) => metric.primary)
    : metrics;
  const accepted = candidates.filter((metric) => metric.verification_status === 'accepted');
  if (accepted.length) candidates = accepted;
  const name = candidates[candidates.length - 1].name;
  const same = candidates.filter((metric) => metric.name === name);
  const direction = same[same.length - 1].direction;
  view.primary_metric = direction === 'minimize'
    ? same.reduce((best, metric) => metric.value < best.value ? metric : best)
    : direction === 'target'
    ? same[same.length - 1]
    : same.reduce((best, metric) => metric.value > best.value ? metric : best);
}

function refreshAchievement(view: MissionView): void {
  const metric = view.primary_metric;
  if (view.mission.status !== 'complete' || metric?.verification_status !== 'accepted') return;
  if (view.achievement?.reviewer_certified) return;
  const baseline = metric.baseline;
  const gain = baseline == null ? null : metric.value - baseline;
  view.achievement = {
    id: `derived-${view.mission.id || 'mission'}`,
    title: view.mission.title || 'Argus achievement',
    goal: view.mission.objective || view.mission.title,
    metric_id: metric.id,
    metric_name: metric.name,
    baseline,
    best: metric.value,
    gain,
    unit: metric.unit,
    experiments_run: view.experiments.filter((row) => row.status === 'completed').length,
    rejected_attempts: view.review.rejected_attempts,
    skills_learned: view.learned_skills.filter((row) => row.status === 'active').length,
    artifacts: view.artifacts.length,
    elapsed_seconds: view.mission.elapsed_seconds,
    reviewer_certified: true,
    certified_at: view.mission.completed_at,
  };
}

export function reduceMissionViewEvent(view: MissionView, event: EventMsg): MissionView {
  const type = canonicalEventType(event.type);
  const ts = Number(event.ts ?? Date.now() / 1000);
  view.last_event_ts = Math.max(view.last_event_ts, ts);

  if (type === EVENT_TYPES.LIFE_MANAGER_INTENT_COMPLETED) {
    view.mission.id = S(event, 'item_id');
    view.mission.title = S(event, 'objective').slice(0, 240);
    view.mission.objective = S(event, 'objective');
    view.mission.status = 'framed';
    const stages = Array.isArray(event.stages) ? event.stages : [];
    if (!view.stage.id && stages[0]) {
      const stage = String(stages[0]);
      view.stage = { id: stage, label: stage.replaceAll('_', ' ') };
    }
    setRole(view, 'manager', 'done', 'Goal framed', ts);
    addTimeline(view, event, 'manager', 'Goal framed', S(event, 'reason'), 'success');
  } else if (type === EVENT_TYPES.LIFE_MANAGER_STAGE_DECISION) {
    const stage = S(event, 'target_stage') || S(event, 'stage') || S(event, 'current_stage');
    if (stage) view.stage = { id: stage, label: stage.replaceAll('_', ' ') };
    setRole(view, 'manager', 'done', stage ? `Stage · ${stage}` : 'Stage reviewed', ts);
    addTimeline(view, event, 'manager', stage ? `Stage → ${stage}` : 'Stage reviewed', S(event, 'reason'));
  } else if (type === EVENT_TYPES.LIFE_PLANNER_START) {
    setRole(view, 'planner', 'active', 'Planning next work', ts);
  } else if (type === EVENT_TYPES.LIFE_PLANNER_TASK_ADDED) {
    const id = S(event, 'item_id');
    const node: MissionDagNode = {
      id,
      title: S(event, 'title'),
      objective: S(event, 'objective'),
      status: 'pending',
      deps: Array.isArray(event.deps) ? event.deps.map(String) : [],
      branch_id: S(event, 'branch_id') || id,
      parent_branch_id: S(event, 'parent_branch_id') || null,
    };
    upsert(view.dag as Array<MissionDagNode & Record<string, unknown>>, 'id', id, node as MissionDagNode & Record<string, unknown>);
    setRole(view, 'planner', 'done', 'Research branch added', ts);
    addTimeline(view, event, 'planner', 'Research branch added', node.title, 'info');
  } else if (type === EVENT_TYPES.LIFE_MISSION_STARTED) {
    view.mission = {
      ...view.mission,
      id: S(event, 'item_id'),
      title: S(event, 'title'),
      objective: S(event, 'objective'),
      status: 'working',
      started_at: ts,
      completed_at: null,
    };
    setRole(view, 'engineer', 'active', 'Starting mission', ts);
    addTimeline(view, event, 'engineer', 'Mission started', S(event, 'title'), 'info');
  } else if (type === EVENT_TYPES.ROUND_START) {
    view.round = { current: N(event, 'round_index') ?? 0, max: N(event, 'round_max') ?? view.round.max };
    setRole(view, 'engineer', 'active', `Running round ${view.round.current}`, ts);
    addTimeline(view, event, 'engineer', `Round ${view.round.current} started`);
  } else if (type === EVENT_TYPES.ENGINEER_PROGRESS) {
    const rawRole = S(event, 'agent_layer') || S(event, 'actor') || 'engineer';
    const role = rawRole === 'main' ? 'engineer' : rawRole;
    const kind = S(event, 'kind');
    const label = PROGRESS_LABELS[kind] ?? 'Working';
    setRole(view, role, 'active', label, ts);
    if (!['reasoning', 'assistant_message', 'agent_message'].includes(kind)) {
      addTimeline(view, event, role, label, S(event, 'action_summary') || S(event, 'text'));
    }
  } else if (type === EVENT_TYPES.ROUND_REVIEW_STARTED) {
    setRole(view, 'reviewer', 'active', 'Reviewing benchmark evidence', ts);
  } else if (type === EVENT_TYPES.ROUND_REVIEW_DEFERRED) {
    const nextStep = S(event, 'next_step');
    setRole(view, 'engineer', 'active', 'Continuing before review', ts);
    setRole(view, 'reviewer', 'waiting', 'Review deferred for one round', ts);
    addTimeline(view, event, 'engineer', 'Continued before review', nextStep, 'info');
  } else if (type === EVENT_TYPES.ROUND_REVIEW_COMPLETED) {
    const status = S(event, 'status');
    const reason = S(event, 'reason');
    view.review = {
      status,
      reason,
      rejected_attempts: view.review.rejected_attempts + (['continue', 'blocked'].includes(status) ? 1 : 0),
    };
    setRole(view, 'reviewer', status === 'done' ? 'done' : 'rejected', status === 'done' ? 'Accepted evidence' : 'Requested another attempt', ts);
    addTimeline(view, event, 'reviewer', status === 'done' ? 'Evidence accepted' : 'Attempt rejected', reason, status === 'done' ? 'success' : 'error');
    if (status === 'done') {
      const round = N(event, 'round_index');
      const candidates = view.metrics.filter((metric) =>
        metric.verification_status === 'reported' && (round == null || metric.round_index == null || metric.round_index === round));
      const latest = candidates[candidates.length - 1];
      if (latest) {
        latest.verification_status = 'accepted';
        latest.reviewer_reason = reason;
        latest.verified_at = ts;
      }
    }
  } else if (type === EVENT_TYPES.RESEARCH_HYPOTHESIS_PROPOSED) {
    const id = S(event, 'hypothesis_id');
    upsert(view.hypotheses, 'id', id, {
      id,
      title: S(event, 'title'),
      statement: S(event, 'statement'),
      branch_id: S(event, 'branch_id'),
      parent_branch_id: S(event, 'parent_branch_id') || null,
      status: 'proposed',
      ts,
    });
    addTimeline(view, event, 'engineer', 'Hypothesis proposed', S(event, 'title'), 'info');
  } else if (type === EVENT_TYPES.RESEARCH_EXPERIMENT_STARTED) {
    const id = S(event, 'experiment_id');
    upsert(view.experiments, 'id', id, {
      id,
      title: S(event, 'title'),
      status: 'running',
      hypothesis_id: S(event, 'hypothesis_id'),
      branch_id: S(event, 'branch_id'),
      started_at: ts,
      completed_at: null,
      summary: S(event, 'summary'),
    });
    setRole(view, 'engineer', 'active', `Running ${S(event, 'title')}`, ts);
    addTimeline(view, event, 'engineer', 'Experiment started', S(event, 'title'), 'info');
  } else if (type === EVENT_TYPES.RESEARCH_EXPERIMENT_COMPLETED) {
    const id = S(event, 'experiment_id');
    upsert(view.experiments, 'id', id, {
      id,
      status: S(event, 'status'),
      completed_at: ts,
      summary: S(event, 'summary'),
      evidence: Array.isArray(event.evidence) ? event.evidence.map(String) : [],
    });
    addTimeline(view, event, 'engineer', `Experiment ${S(event, 'status')}`, S(event, 'summary'), S(event, 'status') === 'completed' ? 'success' : 'error');
  } else if (type === EVENT_TYPES.RESEARCH_METRIC_REPORTED) {
    const metric: MissionMetricView = {
      id: S(event, 'metric_id'),
      name: S(event, 'name'),
      baseline: N(event, 'baseline'),
      value: N(event, 'value') ?? 0,
      unit: S(event, 'unit'),
      direction: S(event, 'direction'),
      evidence: S(event, 'evidence'),
      experiment_id: S(event, 'experiment_id'),
      hypothesis_id: S(event, 'hypothesis_id'),
      branch_id: S(event, 'branch_id'),
      round_index: N(event, 'round_index'),
      primary: Boolean(event.primary),
      verification_status: 'reported',
      reported_at: ts,
    };
    upsert(view.metrics as Array<MissionMetricView & Record<string, unknown>>, 'id', metric.id, metric as MissionMetricView & Record<string, unknown>);
    addTimeline(view, event, 'engineer', 'Metric reported', `${metric.name} = ${metric.value}${metric.unit}`, 'metric');
  } else if (type === EVENT_TYPES.RESEARCH_METRIC_VERIFIED) {
    const metric = view.metrics.find((row) => row.id === S(event, 'metric_id'));
    if (metric) {
      metric.verification_status = S(event, 'status');
      metric.reviewer_reason = S(event, 'reviewer_reason');
      metric.verified_at = ts;
    }
    const accepted = S(event, 'status') === 'accepted';
    addTimeline(view, event, 'reviewer', accepted ? 'Metric verified' : 'Metric rejected', S(event, 'reviewer_reason'), accepted ? 'success' : 'error');
  } else if (type === EVENT_TYPES.RESEARCH_ARTIFACT_REGISTERED) {
    const id = S(event, 'artifact_id');
    upsert(view.artifacts, 'id', id, {
      id,
      path: S(event, 'path'),
      kind: S(event, 'kind'),
      title: S(event, 'title'),
      why: S(event, 'why'),
      experiment_id: S(event, 'experiment_id'),
      branch_id: S(event, 'branch_id'),
      registered_at: ts,
    });
    addTimeline(view, event, 'engineer', 'Artifact registered', S(event, 'path'), 'info');
  } else if ([EVENT_TYPES.SKILL_CREATED, EVENT_TYPES.SKILL_UPDATED].includes(type as never)) {
    const id = S(event, 'skill_id') || S(event, 'name');
    if (id) {
      upsert(view.learned_skills, 'id', id, {
        id,
        name: S(event, 'name'),
        version: N(event, 'version') ?? 1,
        scope: S(event, 'scope'),
        path: S(event, 'path'),
        status: 'active',
        updated_at: ts,
      });
      addTimeline(view, event, 'reviewer', type === EVENT_TYPES.SKILL_CREATED ? 'Capability unlocked' : 'Capability upgraded', S(event, 'name'), 'skill');
    }
  } else if (type === EVENT_TYPES.SKILL_EVOLUTION_COMPLETED) {
    view.storage.project_skill_dir = S(event, 'project_skill_dir') || view.storage.project_skill_dir;
    view.storage.global_skill_dir = S(event, 'global_skill_dir') || view.storage.global_skill_dir;
    view.storage.project_skill_count = N(event, 'project_skill_count') ?? view.storage.project_skill_count;
    view.storage.global_skill_count = N(event, 'global_skill_count') ?? view.storage.global_skill_count;
  } else if (type === EVENT_TYPES.SKILL_HISTORY_COMPRESSED) {
    view.storage.skill_history_compressed += N(event, 'count') ?? 0;
    view.storage.skill_history_bytes_saved += N(event, 'bytes_saved') ?? 0;
  } else if (type === EVENT_TYPES.SKILL_TIDIED) {
    const name = S(event, 'name');
    if (name) {
      const existing = view.learned_skills.find((skill) => skill.name === name);
      const patch = {
        source_path: S(event, 'path'),
        source_placement: S(event, 'placement'),
        source_vertical: S(event, 'vertical'),
        updated_at: ts,
      };
      if (existing) Object.assign(existing, patch);
      else upsert(view.learned_skills, 'id', name, { id: name, name, version: 1, scope: '', path: '', status: 'active', ...patch });
      addTimeline(view, event, 'manager', 'Capability promoted to source', name, 'skill');
    }
  } else if ([EVENT_TYPES.WIKI_INITIALIZED, EVENT_TYPES.WIKI_EVOLUTION_COMPLETED].includes(type as never)) {
    const candidates = [
      ...((Array.isArray(event.paths) ? event.paths : []).map((path) => String(path))),
      S(event, 'path'),
    ].filter(Boolean);
    view.storage.wiki_paths = [...new Set([...view.storage.wiki_paths, ...candidates])];
  } else if (type === EVENT_TYPES.WIKI_RETIRED_COMPRESSED) {
    view.storage.wiki_retired_compressed += N(event, 'count') ?? 0;
    view.storage.wiki_retired_bytes_saved += N(event, 'bytes_saved') ?? 0;
  } else if ([EVENT_TYPES.WIKI_CREATED, EVENT_TYPES.WIKI_UPDATED].includes(type as never)) {
    const id = S(event, 'page_id');
    if (id) {
      upsert(view.learned_wiki_pages, 'id', id, {
        id,
        title: S(event, 'title') || id,
        card_type: S(event, 'card_type'),
        status: S(event, 'status') || 'scratch',
        path: S(event, 'path'),
        updated_at: ts,
      });
      addTimeline(view, event, 'reviewer', type === EVENT_TYPES.WIKI_CREATED ? 'Knowledge captured' : 'Knowledge refined', S(event, 'title') || id, 'skill');
    }
  } else if (type === EVENT_TYPES.WIKI_RETIRED) {
    const id = S(event, 'page_id');
    if (id) {
      const existing = view.learned_wiki_pages.find((page) => page.id === id);
      if (existing) Object.assign(existing, { status: 'retired', updated_at: ts });
      else upsert(view.learned_wiki_pages, 'id', id, { id, title: id, card_type: S(event, 'card_type'), status: 'retired', path: '', updated_at: ts });
      addTimeline(view, event, 'reviewer', 'Knowledge retired', id, 'error');
    }
  } else if ([EVENT_TYPES.WIKI_PROMOTION_PROMOTED, EVENT_TYPES.WIKI_PROMOTION_DEMOTED].includes(type as never)) {
    const id = S(event, 'page_id');
    if (id) {
      const existing = view.learned_wiki_pages.find((page) => page.id === id);
      if (existing) Object.assign(existing, { status: S(event, 'to_status'), updated_at: ts });
      else upsert(view.learned_wiki_pages, 'id', id, { id, title: id, card_type: S(event, 'card_type'), status: S(event, 'to_status'), path: '', updated_at: ts });
      const promoted = type === EVENT_TYPES.WIKI_PROMOTION_PROMOTED;
      addTimeline(view, event, 'reviewer', promoted ? 'Knowledge promoted' : 'Knowledge demoted', `${id} → ${S(event, 'to_status')}`, promoted ? 'success' : 'neutral');
    }
  } else if (type === EVENT_TYPES.RESEARCH_ACHIEVEMENT_CERTIFIED) {
    view.achievement = {
      id: S(event, 'achievement_id'),
      title: S(event, 'title'),
      goal: S(event, 'goal'),
      summary: S(event, 'summary'),
      metric_id: S(event, 'metric_id'),
      reviewer_certified: true,
      certified_at: ts,
    };
  } else if ([EVENT_TYPES.LIFE_MISSION_COMPLETED, EVENT_TYPES.LIFE_MISSION_FAILED].includes(type as never)) {
    const success = type === EVENT_TYPES.LIFE_MISSION_COMPLETED && event.success === true;
    view.mission.id = S(event, 'item_id') || view.mission.id;
    view.mission.title = S(event, 'title') || view.mission.title;
    view.mission.objective = S(event, 'objective') || view.mission.objective;
    view.mission.status = success ? 'complete' : S(event, 'status') || 'failed';
    view.mission.completed_at = ts;
    addTimeline(view, event, 'engineer', success ? 'Mission achievement' : 'Mission failed', S(event, 'title') || S(event, 'status'), success ? 'success' : 'error');
  }

  refreshPrimaryMetric(view);
  refreshAchievement(view);
  view.updated_at = Date.now() / 1000;
  return view;
}

function mergeSnapshot(view: MissionView, snapshot: Snapshot, artifacts: ArtifactInfo[]): void {
  const active = snapshot.backlog.find((item) => ACTIVE_STATUSES.has(item.status));
  const queued = snapshot.backlog.find((item) => item.status === 'pending');
  const objective = snapshot.continuous?.objective
    || snapshot.session.objective
    || active?.objective
    || active?.title
    || queued?.objective
    || queued?.title
    || view.mission.objective;
  if (objective) {
    view.mission.objective = objective;
    if (!view.mission.title) view.mission.title = objective.split('\n')[0].slice(0, 240);
  }
  if (active) {
    view.mission.id = active.id;
    view.mission.status = 'working';
    view.mission.started_at = view.mission.started_at ?? active.started_ts ?? null;
  } else if (snapshot.continuous?.done_reason || snapshot.continuous?.done_at) {
    view.mission.status = 'complete';
  } else if (queued || snapshot.continuous?.enabled) {
    view.mission.status = 'queued';
  } else if (snapshot.daemon.alive) {
    view.mission.status = 'idle';
  }
  snapshot.roles.forEach((role) => {
    if (role.active) {
      setRole(view, role.role, 'active', role.label || role.status || 'Working', Date.now() / 1000 - (role.age_s ?? 0));
    } else {
      setRole(view, role.role, 'waiting', 'Waiting', Date.now() / 1000);
    }
    const row = view.roles.find((candidate) => candidate.role === role.role);
    if (row) Object.assign(row, { backend: role.backend, model: role.model, effort: role.effort });
  });
  view.active_role = snapshot.roles.find((role) => role.active)?.role ?? '';
  snapshot.backlog.forEach((item) => {
    const node: MissionDagNode = {
      id: item.id,
      title: item.title,
      objective: item.objective,
      status: item.status,
      deps: item.deps ?? [],
      branch_id: item.id,
      parent_branch_id: item.deps?.[0] ?? null,
    };
    upsert(view.dag as Array<MissionDagNode & Record<string, unknown>>, 'id', node.id, node as MissionDagNode & Record<string, unknown>);
  });
  artifacts.forEach((artifact) => {
    upsert(view.artifacts, 'path', artifact.path, {
      id: artifact.path,
      path: artifact.path,
      title: artifact.name,
      kind: artifact.kind,
      why: artifact.why,
      exists: artifact.exists,
      source: artifact.source,
    });
  });
  const now = Date.now() / 1000;
  if (view.mission.started_at && view.mission.status === 'working') {
    view.mission.elapsed_seconds = Math.max(0, now - view.mission.started_at);
  } else if (view.mission.started_at && view.mission.completed_at) {
    view.mission.elapsed_seconds = Math.max(0, view.mission.completed_at - view.mission.started_at);
  }
  refreshPrimaryMetric(view);
  refreshAchievement(view);
}

export function projectMissionView(
  snapshot: Snapshot,
  events: EventMsg[] = [],
  artifacts: ArtifactInfo[] = [],
): MissionView {
  const view = snapshot.mission_view ? copyView(snapshot.mission_view) : emptyMissionView();
  view.storage ??= emptyMissionView().storage;
  view.storage.skill_history_compressed ??= 0;
  view.storage.wiki_retired_compressed ??= 0;
  view.storage.skill_history_bytes_saved ??= 0;
  view.storage.wiki_retired_bytes_saved ??= 0;
  view.learned_wiki_pages ??= [];
  const seedTs = view.last_event_ts;
  events
    .filter((event) => event.ts == null || Number(event.ts) > seedTs)
    .sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0))
    .forEach((event) => reduceMissionViewEvent(view, event));
  mergeSnapshot(view, snapshot, artifacts);
  return view;
}

export function missionMetricGain(metric: MissionMetricView | null): number | null {
  if (!metric || metric.baseline == null) return null;
  return metric.value - metric.baseline;
}

export function missionMetricImprovement(metric: MissionMetricView | null): number | null {
  const gain = missionMetricGain(metric);
  if (gain == null) return null;
  return metric?.direction === 'minimize' ? -gain : gain;
}

export function formatMissionElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${total}s`;
}

export function metricDisplay(metric: MissionMetricView | null): string {
  if (!metric) return '—';
  return `${metric.value}${metric.unit || ''}`;
}

export type { MissionAchievement };
