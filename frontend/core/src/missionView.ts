import { canonicalEventType, EVENT_TYPES } from './eventCatalog.js';
import {
  eventKey,
  isReasoning,
  isStructuredAgentPayload,
  visibleAgentText,
} from './events.js';
import {
  missionOutcomeDimensions,
  missionOutcomePresentation,
} from './missionOutcome.js';
import type {
  ArtifactInfo,
  EventMsg,
  MissionAchievement,
  MissionDagNode,
  MissionRoleWorkItem,
  MissionRoleView,
  MissionSkillView,
  MissionTimelineItem,
  MissionView,
  Snapshot,
} from './types.js';

const ROLE_NAMES = ['manager', 'planner', 'engineer', 'reviewer'] as const;
const PIPELINE_ROLES = new Set(['planner', 'engineer', 'reviewer']);
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
    schema_version: 4,
    bootstrapped: false,
    mission: {
      id: '',
      title: '',
      objective: '',
      summary: '',
      final_output: '',
      status: 'idle',
      started_at: null,
      completed_at: null,
      elapsed_seconds: 0,
      campaign_started_at: null,
      campaign_elapsed_seconds: 0,
    },
    stage: { id: '', label: '' },
    round: { current: 0, max: 0 },
    active_role: '',
    roles: ROLE_NAMES.map((role) => ({ role, status: 'waiting', label: 'Waiting', updated_at: 0 })),
    role_work: [],
    dag: [],
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
    frontier: { change: '', summary: '', updated_at: 0 },
    outcome: {},
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
  if (status === 'active' && PIPELINE_ROLES.has(role)) {
    view.roles.forEach((candidate) => {
      if (PIPELINE_ROLES.has(candidate.role) && candidate.role !== role && candidate.status === 'active') {
        Object.assign(candidate, { status: 'done', label: 'Handed off', updated_at: ts });
      }
    });
  }
  const patch: MissionRoleView = { role, status, label, updated_at: ts };
  upsert(view.roles as Array<MissionRoleView & Record<string, unknown>>, 'role', role, patch as MissionRoleView & Record<string, unknown>);
  if (status === 'active') view.active_role = role;
  else if (view.active_role === role) view.active_role = '';
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
  (['item_id', 'branch_id'] as const).forEach((key) => {
    const value = S(event, key);
    if (value) row[key] = value;
  });
  view.timeline = [...view.timeline, row].slice(-120);
}

function addRoleWork(
  view: MissionView,
  event: EventMsg,
  role: string,
  kind: string,
  title: string,
  detail = '',
  status = '',
): void {
  if (!ROLE_NAMES.includes(role as typeof ROLE_NAMES[number])) return;
  const messageId = S(event, 'message_id');
  const id = messageId ? `${role}:${messageId}` : eventKey(event);
  const existing = view.role_work.find((row) => row.id === id);
  const resolvedDetail = existing && existing.detail.length > detail.length
    ? existing.detail
    : detail;
  const row: MissionRoleWorkItem = {
    id,
    ts: Number(event.ts ?? Date.now() / 1000),
    role,
    kind,
    title: title.slice(0, 240),
    detail: resolvedDetail.slice(0, 4000),
    status,
    item_id: S(event, 'item_id'),
    mission_id: view.mission.id,
    mission_title: view.mission.title.slice(0, 240),
    round_index: N(event, 'round_index'),
  };
  const index = view.role_work.findIndex((candidate) => candidate.id === id);
  if (index >= 0) view.role_work[index] = row;
  else view.role_work.push(row);
  const keep = new Set<string>();
  ROLE_NAMES.forEach((roleName) => {
    view.role_work
      .filter((candidate) => candidate.role === roleName)
      .slice(-40)
      .forEach((candidate) => keep.add(candidate.id));
  });
  view.role_work = view.role_work.filter((candidate) => keep.has(candidate.id));
}

function missionTimelineTone(
  tone: ReturnType<typeof missionOutcomePresentation>['tone'],
): MissionTimelineItem['tone'] {
  if (tone === 'ok') return 'success';
  if (tone === 'err') return 'error';
  return 'info';
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

export function reduceMissionViewEvent(view: MissionView, event: EventMsg): MissionView {
  const type = canonicalEventType(event.type);
  const ts = Number(event.ts ?? Date.now() / 1000);
  view.last_event_ts = Math.max(view.last_event_ts, ts);

  if (type === EVENT_TYPES.LIFE_MANAGER_INTENT_STARTED) {
    view.mission.id = S(event, 'item_id') || S(event, 'intent_id');
    view.mission.title = S(event, 'objective').slice(0, 240);
    view.mission.objective = S(event, 'objective');
    view.mission.status = 'grounding';
    setRole(view, 'manager', 'active', 'Grounding project', ts);
    addTimeline(view, event, 'manager', 'Project grounding started', S(event, 'objective'));
    addRoleWork(view, event, 'manager', 'grounding', 'Grounding project', S(event, 'objective'), 'active');
  } else if (type === EVENT_TYPES.LIFE_MANAGER_INTENT_COMPLETED) {
    view.mission.id = S(event, 'item_id');
    view.mission.title = S(event, 'objective').slice(0, 240);
    view.mission.objective = S(event, 'objective');
    view.mission.status = 'framed';
    const currentStage = S(event, 'current_stage');
    const stages = Array.isArray(event.stages) ? event.stages : [];
    if (currentStage) {
      view.stage = {
        id: currentStage,
        label: currentStage.replaceAll('_', ' '),
      };
    } else if (!view.stage.id && stages[0]) {
      const stage = String(stages[0]);
      view.stage = { id: stage, label: stage.replaceAll('_', ' ') };
    }
    setRole(view, 'manager', 'done', 'Goal framed', ts);
    addTimeline(view, event, 'manager', 'Goal framed', S(event, 'reason'), 'success');
    addRoleWork(
      view,
      event,
      'manager',
      'decision',
      'Goal framed',
      S(event, 'reason') || S(event, 'execution_task'),
      'done',
    );
  } else if (type === EVENT_TYPES.LIFE_MANAGER_INTENT_FAILED) {
    view.mission.status = 'failed';
    setRole(view, 'manager', 'error', 'Manager routing failed', ts);
    addTimeline(view, event, 'manager', 'Manager routing failed', S(event, 'error') || S(event, 'reason'), 'error');
    addRoleWork(
      view,
      event,
      'manager',
      'grounding',
      'Manager routing failed',
      S(event, 'error') || S(event, 'reason'),
      'error',
    );
  } else if (type === EVENT_TYPES.LIFE_MANAGER_STAGE_DECISION) {
    const stage = S(event, 'target_stage') || S(event, 'stage') || S(event, 'current_stage');
    if (stage) view.stage = { id: stage, label: stage.replaceAll('_', ' ') };
    setRole(view, 'manager', 'done', stage ? `Stage · ${stage}` : 'Stage reviewed', ts);
    addTimeline(view, event, 'manager', stage ? `Stage → ${stage}` : 'Stage reviewed', S(event, 'reason'));
    addRoleWork(
      view,
      event,
      'manager',
      'stage_decision',
      stage ? `Stage → ${stage}` : 'Stage reviewed',
      S(event, 'reason'),
      S(event, 'action'),
    );
  } else if (type === EVENT_TYPES.LIFE_PLANNER_START) {
    setRole(view, 'planner', 'active', 'Planning next work', ts);
    addRoleWork(view, event, 'planner', 'planning', 'Planning next work', S(event, 'objective'), 'active');
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
    addRoleWork(view, event, 'planner', 'task', node.title || 'Task added', node.objective, 'pending');
  } else if (type === EVENT_TYPES.LIFE_PLANNER_VERDICT) {
    const projectDone = Boolean(event.project_done);
    const label = projectDone ? 'Project reviewed' : 'Planning complete';
    setRole(view, 'planner', 'done', label, ts);
    addTimeline(view, event, 'planner', label, S(event, 'reason'), projectDone ? 'success' : 'neutral');
    addRoleWork(view, event, 'planner', 'verdict', label, S(event, 'reason'), projectDone ? 'done' : 'planned');
  } else if (type === EVENT_TYPES.LIFE_PLANNER_WAITING) {
    setRole(view, 'planner', 'waiting', 'Waiting on external work', ts);
    const detail = S(event, 'reason') || S(event, 'waiting_reason');
    addTimeline(view, event, 'planner', 'Planner waiting', detail);
    addRoleWork(view, event, 'planner', 'waiting', 'Planner waiting', detail, 'waiting');
  } else if (type === EVENT_TYPES.LIFE_MISSION_STARTED) {
    view.review = { status: '', reason: '', rejected_attempts: 0 };
    view.mission.campaign_started_at ??= ts;
    view.mission = {
      ...view.mission,
      id: S(event, 'item_id'),
      title: S(event, 'title'),
      objective: S(event, 'objective'),
      summary: '',
      final_output: '',
      status: 'working',
      started_at: ts,
      completed_at: null,
    };
    setRole(view, 'reviewer', 'waiting', 'Awaiting engineer handoff', ts);
    setRole(view, 'engineer', 'active', 'Starting mission', ts);
    addTimeline(view, event, 'engineer', 'Mission started', S(event, 'title'), 'info');
    addRoleWork(view, event, 'engineer', 'task', S(event, 'title') || 'Mission started', S(event, 'objective'), 'active');
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
    if (
      role === 'engineer'
      && ['assistant_message', 'agent_message', 'message'].includes(kind)
    ) {
      const candidate = visibleAgentText(event.text);
      if (candidate.length >= (view.mission.final_output?.length ?? 0)) {
        view.mission.final_output = candidate;
      }
    }
    const detail = S(event, 'action_summary') || S(event, 'text');
    if (detail && !isReasoning(event) && !isStructuredAgentPayload(event)) {
      addRoleWork(view, event, role, kind || 'progress', label, detail, 'active');
    }
    if (!['reasoning', 'assistant_message', 'agent_message'].includes(kind)) {
      addTimeline(view, event, role, label, S(event, 'action_summary') || S(event, 'text'));
    }
  } else if (type === EVENT_TYPES.ROUND_MAIN_COMPLETED) {
    setRole(view, 'engineer', 'done', 'Engineer handoff ready', ts);
    addRoleWork(
      view,
      event,
      'engineer',
      'handoff',
      'Engineer handoff ready',
      S(event, 'text') || S(event, 'summary'),
      'done',
    );
  } else if (type === EVENT_TYPES.ROUND_REVIEW_STARTED) {
    setRole(view, 'reviewer', 'active', 'Reviewing benchmark evidence', ts);
    addRoleWork(view, event, 'reviewer', 'review', 'Review started', '', 'active');
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
    const frontierChange = S(event, 'frontier_change');
    if (frontierChange) {
      view.frontier = {
        change: frontierChange,
        summary: S(event, 'frontier_summary'),
        updated_at: ts,
      };
    }
    setRole(view, 'reviewer', status === 'done' ? 'done' : 'rejected', status === 'done' ? 'Accepted evidence' : 'Requested another attempt', ts);
    addTimeline(view, event, 'reviewer', status === 'done' ? 'Evidence accepted' : 'Attempt rejected', reason, status === 'done' ? 'success' : 'error');
    const nextAction = S(event, 'next_action');
    addRoleWork(
      view,
      event,
      'reviewer',
      'verdict',
      status === 'done' ? 'Evidence accepted' : 'Attempt rejected',
      nextAction ? `${reason}\n\nNext action: ${nextAction}` : reason,
      status,
    );
  } else if ([EVENT_TYPES.SKILL_CREATED, EVENT_TYPES.SKILL_UPDATED].includes(type as never)) {
    const id = S(event, 'skill_id') || S(event, 'name');
    if (id) {
      upsert(
        view.learned_skills as Array<MissionSkillView & Record<string, unknown>>,
        'id',
        id,
        {
        id,
        name: S(event, 'name'),
        version: N(event, 'version') ?? 1,
        scope: S(event, 'scope'),
        path: S(event, 'path'),
        status: 'active',
        updated_at: ts,
        mission_id: view.mission.id,
        mission_title: view.mission.title,
        } as MissionSkillView & Record<string, unknown>,
      );
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
      else upsert(
        view.learned_skills as Array<MissionSkillView & Record<string, unknown>>,
        'id',
        name,
        {
          id: name,
          name,
          version: 1,
          scope: '',
          path: '',
          status: 'active',
          ...patch,
        } as MissionSkillView & Record<string, unknown>,
      );
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
      rejected_attempts: view.review.rejected_attempts,
      skills_learned: view.learned_skills.filter((row) => row.status === 'active').length,
      artifacts: view.artifacts.length,
      elapsed_seconds: view.mission.elapsed_seconds,
      evidence: Array.isArray(event.evidence) ? event.evidence.map(String) : [],
      reviewer_certified: true,
      certified_at: ts,
    };
  } else if ([EVENT_TYPES.LIFE_MISSION_COMPLETED, EVENT_TYPES.LIFE_MISSION_FAILED].includes(type as never)) {
    const presentation = type === EVENT_TYPES.LIFE_MISSION_FAILED
      ? missionOutcomePresentation({ ...event, outcome_class: 'failed', status: S(event, 'status') || 'failed', success: false })
      : missionOutcomePresentation(event);
    view.mission.id = S(event, 'item_id') || view.mission.id;
    view.mission.title = S(event, 'title') || view.mission.title;
    view.mission.objective = S(event, 'objective') || view.mission.objective;
    view.mission.summary = S(event, 'summary');
    view.mission.final_output = visibleAgentText(event.final_output)
      || view.mission.final_output
      || '';
    view.mission.status = presentation.missionStatus;
    view.mission.completed_at = ts;
    view.outcome = missionOutcomeDimensions(event);
    setRole(
      view,
      'engineer',
      presentation.missionStatus === 'complete' ? 'done' : presentation.missionStatus,
      presentation.label,
      ts,
    );
    addTimeline(
      view,
      event,
      'engineer',
      presentation.label,
      S(event, 'summary') || S(event, 'title') || S(event, 'status'),
      missionTimelineTone(presentation.tone),
    );
    addRoleWork(
      view,
      event,
      'engineer',
      'completion',
      presentation.label,
      S(event, 'summary') || S(event, 'title') || S(event, 'status'),
      presentation.missionStatus,
    );
  }
  view.updated_at = Date.now() / 1000;
  return view;
}

function mergeSnapshot(view: MissionView, snapshot: Snapshot, artifacts: ArtifactInfo[]): boolean {
  const active = snapshot.backlog.find((item) => ACTIVE_STATUSES.has(item.status));
  const queued = snapshot.backlog.find((item) => item.status === 'pending');
  const owner = snapshot.backlog.find((item) => item.id === view.mission.id);
  const selected = active ?? owner;
  const missionContext = Boolean(
    active
    || queued
    || snapshot.continuous?.enabled
    || snapshot.continuous?.done_reason
    || snapshot.continuous?.done_at
    || view.mission.id
    || !['', 'idle'].includes(view.mission.status),
  );
  const objective = selected?.objective
    || selected?.title
    || (snapshot.continuous?.enabled ? snapshot.continuous.objective : '')
    || snapshot.session.objective
    || (!view.mission.id ? queued?.objective : '')
    || (!view.mission.id ? queued?.title : '')
    || view.mission.objective;
  if (objective) {
    view.mission.objective = objective;
    if (selected) view.mission.title = (selected.title || objective.split('\n')[0]).slice(0, 240);
    else if (!view.mission.title) view.mission.title = objective.split('\n')[0].slice(0, 240);
  }
  if (active) {
    view.mission.id = active.id;
    view.mission.status = 'working';
    view.mission.started_at = view.mission.started_at ?? active.started_ts ?? null;
  } else if (owner) {
    if (owner.status === 'pending') view.mission.status = 'queued';
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
    } else if (!missionContext) {
      setRole(view, role.role, 'waiting', 'Waiting', Date.now() / 1000);
    }
    const row = view.roles.find((candidate) => candidate.role === role.role);
    if (row) Object.assign(row, { backend: role.backend, model: role.model, effort: role.effort });
  });
  const activeRoles = snapshot.roles.filter((role) => role.active);
  if (activeRoles.length) view.active_role = activeRoles[activeRoles.length - 1].role;
  else if (!missionContext) view.active_role = '';
  snapshot.backlog.forEach((item) => {
    const node: MissionDagNode = {
      id: item.id,
      title: item.title,
      objective: item.objective,
      status: item.status,
      deps: item.deps ?? [],
      branch_id: item.id,
      parent_branch_id: item.deps?.[0] ?? null,
      acceptance_check: item.acceptance_check ?? '',
      plan_hypothesis: item.plan_hypothesis ?? '',
      goal_contribution: item.goal_contribution ?? '',
      expected_regressions: item.expected_regressions ?? '',
      decision_rule: item.decision_rule ?? '',
      non_goals: item.non_goals ?? [],
    };
    upsert(view.dag as Array<MissionDagNode & Record<string, unknown>>, 'id', node.id, node as MissionDagNode & Record<string, unknown>);
  });
  const latestOutcome = selected?.outcome?.execution_status
    ? selected.outcome
    : !view.mission.id
      ? [...snapshot.backlog]
          .filter((item) => item.outcome?.execution_status)
          .sort((left, right) => Number(left.finished_ts ?? 0) - Number(right.finished_ts ?? 0))
          .at(-1)?.outcome
      : undefined;
  if (!active && latestOutcome) {
    view.outcome = missionOutcomeDimensions({
      outcome: latestOutcome,
      status: 'done',
      success: true,
    });
  }
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
  return missionContext;
}

function finalizeSnapshot(
  view: MissionView,
  snapshot: Snapshot,
  artifacts: ArtifactInfo[],
  missionContext: boolean,
): void {
  if (!missionContext) {
    snapshot.roles.forEach((role) => {
      if (!role.active) setRole(view, role.role, 'waiting', 'Waiting', Date.now() / 1000);
    });
    view.active_role = '';
  }
  const now = Date.now() / 1000;
  const campaignStartedAt = view.mission.campaign_started_at
    ?? snapshot.session.created
    ?? view.mission.started_at;
  if (campaignStartedAt) {
    view.mission.campaign_started_at = campaignStartedAt;
    view.mission.campaign_elapsed_seconds = Math.max(0, now - campaignStartedAt);
  }
  if (view.mission.started_at && view.mission.status === 'working') {
    view.mission.elapsed_seconds = Math.max(0, now - view.mission.started_at);
  } else if (view.mission.started_at && view.mission.completed_at) {
    view.mission.elapsed_seconds = Math.max(0, view.mission.completed_at - view.mission.started_at);
  }
  if (view.achievement?.reviewer_certified) {
    view.achievement.elapsed_seconds = view.mission.elapsed_seconds;
    view.achievement.rejected_attempts = view.review.rejected_attempts;
    view.achievement.skills_learned = view.learned_skills.filter((row) => row.status === 'active').length;
    view.achievement.artifacts = artifacts.filter((artifact) => artifact.exists).length;
  }
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
  view.role_work ??= [];
  view.outcome ??= {};
  const seedTs = view.last_event_ts;
  // The snapshot is a baseline. Events newer than mission_view.last_event_ts
  // are authoritative and must not be overwritten by an older backlog/role
  // projection that happened to arrive in the same refresh.
  const missionContext = mergeSnapshot(view, snapshot, artifacts);
  events
    .filter((event) => event.ts == null || Number(event.ts) > seedTs)
    .sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0))
    .forEach((event) => reduceMissionViewEvent(view, event));
  if (!view.mission.final_output) {
    view.mission.final_output = recoverMissionFinalOutput(events, view.mission.id);
  }
  finalizeSnapshot(view, snapshot, artifacts, missionContext);
  return view;
}

/** Recover full handoffs produced before ``life.mission.completed`` gained a
 * dedicated ``final_output`` field.  The compact snapshot can be newer than
 * the REST event window, so inspect that window explicitly instead of relying
 * on the incremental reducer to replay older rows. */
function recoverMissionFinalOutput(events: EventMsg[], missionId: string): string {
  let completionIndex = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (canonicalEventType(event.type) !== EVENT_TYPES.LIFE_MISSION_COMPLETED) continue;
    if (missionId && S(event, 'item_id') !== missionId) continue;
    completionIndex = index;
    break;
  }
  if (completionIndex < 0) return '';

  let startIndex = 0;
  for (let index = completionIndex - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (canonicalEventType(event.type) !== EVENT_TYPES.LIFE_MISSION_STARTED) continue;
    if (missionId && S(event, 'item_id') !== missionId) continue;
    startIndex = index + 1;
    break;
  }

  let output = '';
  for (let index = startIndex; index < completionIndex; index += 1) {
    const event = events[index];
    if (canonicalEventType(event.type) !== EVENT_TYPES.ENGINEER_PROGRESS) continue;
    const role = S(event, 'agent_layer') || S(event, 'actor');
    const kind = S(event, 'kind');
    if (!['engineer', 'main'].includes(role)) continue;
    if (!['assistant_message', 'agent_message', 'message'].includes(kind)) continue;
    const candidate = visibleAgentText(event.text);
    if (candidate.length >= output.length) output = candidate;
  }
  return output;
}

/**
 * Decode only presentation-layer escaping introduced by Markdown/JSON
 * transport. The persisted objective remains byte-for-byte authoritative.
 */
export function displayObjective(value: string): string {
  return String(value || '')
    .replace(/\\([*_`~])/g, '$1')
    .replace(/\\\\(?=[A-Za-z])/g, '\\');
}

export function formatMissionElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${total}s`;
}

export type { MissionAchievement };
