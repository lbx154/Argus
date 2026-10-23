import { translate } from '../i18n';
import { parkReason } from '../../../core/src/missionOutcome';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';

export type WorkbenchText = (zh: string, en: string) => string;

const STATUS_LABELS: Record<string, readonly [string, string]> = {
  active: ['进行中', 'In progress'],
  claimed: ['进行中', 'In progress'],
  in_progress: ['进行中', 'In progress'],
  running: ['进行中', 'In progress'],
  working: ['进行中', 'In progress'],
  pending: ['等待中', 'Waiting'],
  queued: ['等待中', 'Waiting'],
  waiting: ['等待中', 'Waiting'],
  idle: ['等待中', 'Waiting'],
  accepted: ['已完成', 'Completed'],
  complete: ['已完成', 'Completed'],
  completed: ['已完成', 'Completed'],
  done: ['已完成', 'Completed'],
  success: ['已完成', 'Completed'],
  blocked: ['已阻塞', 'Blocked'],
  failed: ['失败', 'Failed'],
  error: ['失败', 'Failed'],
  rejected: ['需要修改', 'Needs changes'],
  continue: ['需要修改', 'Needs changes'],
  replan: ['需要重新规划', 'Needs replanning'],
  replan_requested: ['需要重新规划', 'Needs replanning'],
  incomplete: ['尚未完成', 'Incomplete'],
  no_progress: ['暂无新进展，已暂停', 'Paused without new progress'],
  max_rounds: ['已到本次轮次上限', 'Round limit reached'],
  supervisor_error: ['执行异常', 'Execution failed'],
  skipped: ['已跳过', 'Skipped'],
  stopped: ['已暂停', 'Paused'],
  cancelled: ['已暂停', 'Paused'],
  aborted: ['已暂停', 'Paused'],
  not_started: ['等待中', 'Waiting'],
  healthy: ['状态正常', 'Healthy'],
  degraded: ['部分受限', 'Limited'],
};

const ROLE_LABELS: Record<string, readonly [string, string]> = {
  system: ['Argus', 'Argus'],
  operator: ['你', 'You'],
  stopped: ['已暂停', 'Paused'],
  idle: ['等待中', 'Waiting'],
};

function label(
  value: string | null | undefined,
  labels: Record<string, readonly [string, string]>,
  fallback: readonly [string, string],
  text: WorkbenchText,
): string {
  const pair = labels[String(value ?? '').toLowerCase()] ?? fallback;
  return text(pair[0], pair[1]);
}

export function statusLabel(value: string | null | undefined, text: WorkbenchText): string {
  if (parkReason(value) !== null) return text('已暂停', 'Paused');
  return label(value, STATUS_LABELS, ['状态已更新', 'Status updated'], text);
}

export function roleLabel(value: string | null | undefined, text: WorkbenchText): string {
  const role = String(value ?? '').toLowerCase();
  if (isAgentRole(role)) return agentRoleName(role, key => text(translate(key, {}, 'zh-CN'), translate(key, {}, 'en')));
  return label(value, ROLE_LABELS, ['Argus', 'Argus'], text);
}
