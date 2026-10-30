import { parkReason } from '../../../core/src/missionOutcome';
import { agentRoleName, isAgentRole } from './agentRoles';

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
  stopped: 'label.status.paused',
  cancelled: 'label.status.cancelled',
  aborted: 'label.status.cancelled',
  not_started: 'label.status.waiting',
  available: 'label.status.available',
  absent: 'label.status.unavailable',
  inaccessible: 'label.status.inaccessible',
  degraded: 'label.status.limited',
  healthy: 'label.status.healthy',
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

const ACCELERATOR_KEYS: Record<string, string> = {
  cuda: 'label.resource.nvidiaGpu',
  rocm: 'label.resource.amdGpu',
  mps: 'label.resource.appleGpu',
  cpu: 'label.resource.cpu',
};

/** A status as one word; every park, whatever its reason, reads as paused. */
export function statusLabel(value: string | null | undefined, t: Translate): string {
  const key = String(value ?? '').toLowerCase();
  return t(STATUS_KEYS[key] ?? (parkReason(key) !== null ? 'label.status.paused' : 'label.status.updated'));
}

export function roleLabel(value: string | null | undefined, t: Translate): string {
  const role = String(value ?? '').toLowerCase();
  return isAgentRole(role) ? agentRoleName(role, t) : t(role === 'operator' ? 'role.operator' : 'role.system');
}

export function workKindLabel(value: string | null | undefined, t: Translate): string {
  return t(WORK_KIND_KEYS[String(value ?? '').toLowerCase()] ?? 'label.work.update');
}

export function frontierLabel(value: string | null | undefined, t: Translate): string {
  return t(FRONTIER_KEYS[String(value ?? '').toLowerCase()] ?? 'label.frontier.updated');
}

export function acceleratorLabel(value: string, t: Translate): string {
  return t(ACCELERATOR_KEYS[value.toLowerCase()] ?? 'label.resource.accelerator');
}

export function enforcementLabel(value: string, t: Translate): string {
  return t(value === 'strict' ? 'label.resource.enforced' : 'label.resource.advisory');
}
