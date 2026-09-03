import type { MissionOutcomeDimensions, MissionView } from '../../../core/src/types';

type Translate = (key: string, variables?: Record<string, string | number>) => string;

const STATUS_KEYS: Record<string, string> = {
  active: 'label.status.inProgress',
  claimed: 'label.status.inProgress',
  in_progress: 'label.status.inProgress',
  running: 'label.status.inProgress',
  working: 'label.status.inProgress',
  pending: 'label.status.waiting',
  queued: 'label.status.waiting',
  waiting: 'label.status.waiting',
  idle: 'label.status.waiting',
  accepted: 'label.status.completed',
  complete: 'label.status.completed',
  completed: 'label.status.completed',
  done: 'label.status.completed',
  success: 'label.status.completed',
  blocked: 'label.status.blocked',
  failed: 'label.status.failed',
  error: 'label.status.failed',
  rejected: 'label.status.needsChanges',
  continue: 'label.status.needsChanges',
  skipped: 'label.status.skipped',
  paused: 'label.status.paused',
  stopped: 'label.status.paused',
  cancelled: 'label.status.paused',
  aborted: 'label.status.paused',
  not_started: 'label.status.waiting',
  available: 'label.status.available',
  absent: 'label.status.unavailable',
  inaccessible: 'label.status.inaccessible',
  degraded: 'label.status.limited',
  healthy: 'label.status.healthy',
};

const ROLE_KEYS: Record<string, string> = {
  manager: 'label.role.manager',
  planner: 'label.role.planner',
  engineer: 'label.role.engineer',
  reviewer: 'label.role.reviewer',
  system: 'label.role.argus',
  operator: 'label.role.you',
};

const WORK_KIND_KEYS: Record<string, string> = {
  task: 'label.work.task',
  tool_use: 'label.work.action',
  command_execution: 'label.work.action',
  handoff: 'label.work.handoff',
  review: 'label.work.review',
  planning: 'label.work.planning',
  message: 'label.work.update',
};

const STAGE_KEYS: Record<string, string> = {
  scope: 'label.stage.scope',
  research: 'label.stage.research',
  implementation: 'label.stage.implementation',
  experiment: 'label.stage.experiment',
  analysis: 'label.stage.analysis',
  writing: 'label.stage.writing',
  review: 'label.stage.review',
  delivery: 'label.stage.delivery',
};

const FRONTIER_KEYS: Record<string, string> = {
  branch_reopened: 'label.frontier.reopened',
  branch_opened: 'label.frontier.added',
  task_added: 'label.frontier.added',
  branch_closed: 'label.frontier.completed',
  task_completed: 'label.frontier.completed',
  branch_replaced: 'label.frontier.revised',
  narrowed: 'label.frontier.narrowed',
  expanded: 'label.frontier.expanded',
};

const EXECUTION_KEYS: Record<string, string> = {
  completed: 'label.outcome.workCompleted',
  done: 'label.outcome.workCompleted',
  success: 'label.outcome.workCompleted',
  paused: 'label.outcome.workPaused',
  blocked: 'label.outcome.workBlocked',
  failed: 'label.outcome.workFailed',
  error: 'label.outcome.workFailed',
  aborted: 'label.outcome.workEnded',
  ended: 'label.outcome.workEnded',
  incomplete: 'label.outcome.workIncomplete',
  research_incomplete: 'label.outcome.workIncomplete',
  paused_no_breakthrough: 'label.outcome.workIncomplete',
  exhausted_current_methods: 'label.outcome.workIncomplete',
  stalled: 'label.outcome.workStalled',
  no_progress: 'label.outcome.workStalled',
  max_rounds: 'label.outcome.workStalled',
  infra_blocked: 'label.outcome.workBlocked',
  supervisor_error: 'label.outcome.workFailed',
};

const REVIEW_KEYS: Record<string, string> = {
  accepted: 'label.outcome.reviewPassed',
  done: 'label.outcome.reviewPassed',
  passed: 'label.outcome.reviewPassed',
  continue: 'label.outcome.reviewNeedsChanges',
  rejected: 'label.outcome.reviewNeedsChanges',
  blocked: 'label.outcome.reviewBlocked',
  stale: 'label.outcome.reviewOutdated',
  pending: 'label.outcome.reviewPending',
  pending_review: 'label.outcome.reviewPending',
};

const CERTIFICATION_KEYS: Record<string, string> = {
  certified: 'label.outcome.stageApproved',
  not_certified: 'label.outcome.stageNotApproved',
  revoked: 'label.outcome.stageRevoked',
  intentionally_skipped: 'label.outcome.stageNotNeeded',
  deferred: 'label.outcome.stagePending',
};

const INTERRUPTION_KEYS: Record<string, string> = {
  budget_exhausted: 'label.outcome.budgetPaused',
  budget_pause: 'label.outcome.budgetPaused',
  operator_input_required: 'label.outcome.waitingForYou',
  operator_abort: 'label.outcome.stoppedByYou',
  operator_pause: 'label.outcome.pausedByYou',
  daemon_shutdown: 'label.outcome.sessionPaused',
  backend_unavailable: 'label.outcome.serviceUnavailable',
  provider_cooldown: 'label.outcome.serviceCoolingDown',
  provider_fence: 'label.outcome.serviceUnavailable',
  transient_error: 'label.outcome.temporaryIssue',
  permanent_error: 'label.outcome.serviceError',
  planner_empty_plan: 'label.outcome.needsPlan',
};

const ACCELERATOR_KEYS: Record<string, string> = {
  cuda: 'label.resource.nvidiaGpu',
  rocm: 'label.resource.amdGpu',
  mps: 'label.resource.appleGpu',
  cpu: 'label.resource.cpu',
};

export function statusLabel(value: string | null | undefined, t: Translate): string {
  return t(STATUS_KEYS[String(value ?? '').toLowerCase()] ?? 'label.status.updated');
}

export function roleLabel(value: string | null | undefined, t: Translate): string {
  return t(ROLE_KEYS[String(value ?? '').toLowerCase()] ?? 'label.role.argus');
}

export function workKindLabel(value: string | null | undefined, t: Translate): string {
  return t(WORK_KIND_KEYS[String(value ?? '').toLowerCase()] ?? 'label.work.update');
}

export function stageLabel(value: string | null | undefined, t: Translate): string {
  return t(STAGE_KEYS[String(value ?? '').toLowerCase()] ?? 'label.stage.unstaged');
}

export function frontierLabel(value: string | null | undefined, t: Translate): string {
  return t(FRONTIER_KEYS[String(value ?? '').toLowerCase()] ?? 'label.frontier.updated');
}

export function priorityLabel(value: number, t: Translate): string {
  return t('label.priority', { priority: value });
}

export function outcomeLabels(
  outcome: Partial<MissionOutcomeDimensions> | null | undefined,
  t: Translate,
): string[] {
  if (!outcome?.execution_status) return [];
  const rows = [
    t(EXECUTION_KEYS[outcome.execution_status.toLowerCase()] ?? 'label.outcome.workUpdated'),
  ];
  const review = REVIEW_KEYS[String(outcome.review_status ?? '').toLowerCase()];
  const certification = CERTIFICATION_KEYS[String(outcome.stage_certification ?? '').toLowerCase()];
  const interruption = INTERRUPTION_KEYS[String(outcome.interruption_kind ?? '').toLowerCase()];
  if (review) rows.push(t(review));
  if (certification) rows.push(t(certification));
  if (interruption) rows.push(t(interruption));
  if (outcome.resumable) rows.push(t('label.outcome.canResume'));
  return rows;
}

export function routingLabels(routing: MissionView['routing'], t: Translate): string[] {
  const route = routing.route === 'team'
    ? t('label.routing.team')
    : routing.route ? t('label.routing.individual') : '';
  const vertical = routing.vertical === 'research'
    ? t('label.routing.research')
    : routing.vertical === 'software' ? t('label.routing.software') : '';
  const workflow = routing.workflow_mode === 'staged'
    ? t('label.routing.staged')
    : routing.workflow_mode ? t('label.routing.flexible') : '';
  const lifetime = routing.lifetime === 'standing'
    ? t('label.routing.ongoing')
    : routing.lifetime ? t('label.routing.defined') : '';
  return [route, vertical, workflow, lifetime].filter(Boolean);
}

export function acceleratorLabel(value: string, t: Translate): string {
  return t(ACCELERATOR_KEYS[value.toLowerCase()] ?? 'label.resource.accelerator');
}

export function enforcementLabel(value: string, t: Translate): string {
  return t(value === 'strict' ? 'label.resource.enforced' : 'label.resource.advisory');
}

export function yieldDecisionLabel(value: string, t: Translate): string {
  return t(value === 'yield' ? 'label.resource.released' : 'label.resource.kept');
}
