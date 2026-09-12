import { translate } from '../i18n';
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
  research_incomplete: ['研究尚未完成', 'Research incomplete'],
  paused_no_breakthrough: ['暂未取得突破，已暂停', 'Paused without a breakthrough'],
  exhausted_current_methods: ['当前方法已尝试完', 'Current methods exhausted'],
  no_progress: ['暂无新进展，已暂停', 'Paused without new progress'],
  max_rounds: ['已到本次轮次上限', 'Round limit reached'],
  infra_blocked: ['等待运行条件恢复', 'Waiting for the runtime to recover'],
  supervisor_error: ['执行异常', 'Execution failed'],
  skipped: ['已跳过', 'Skipped'],
  paused: ['已暂停', 'Paused'],
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

const STAGE_LABELS: Record<string, readonly [string, string]> = {
  scope: ['研究定义', 'Scope'],
  research: ['文献与假设', 'Literature and hypotheses'],
  implementation: ['方法实现', 'Implementation'],
  experiment: ['实验验证', 'Experiments'],
  analysis: ['结果分析', 'Analysis'],
  writing: ['论文写作', 'Writing'],
  review: ['最终审核', 'Final review'],
  delivery: ['成果交付', 'Delivery'],
};

const CERTIFICATION_LABELS: Record<string, readonly [string, string]> = {
  certified: ['阶段已通过', 'Stage approved'],
  not_certified: ['阶段未通过', 'Stage not approved'],
  revoked: ['阶段批准已撤回', 'Stage approval revoked'],
  intentionally_skipped: ['无需阶段审核', 'Stage review not needed'],
  deferred: ['阶段审核待定', 'Stage review pending'],
  not_assessed: ['尚未审核阶段', 'Stage not reviewed'],
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
  if (String(value ?? '').startsWith('paused_') && !STATUS_LABELS[String(value)]) return text('已暂停', 'Paused');
  return label(value, STATUS_LABELS, ['状态已更新', 'Status updated'], text);
}

export function roleLabel(value: string | null | undefined, text: WorkbenchText): string {
  const role = String(value ?? '').toLowerCase();
  if (isAgentRole(role)) return agentRoleName(role, key => text(translate(key, {}, 'zh-CN'), translate(key, {}, 'en')));
  return label(value, ROLE_LABELS, ['Argus', 'Argus'], text);
}

export function stageLabel(value: string | null | undefined, text: WorkbenchText): string {
  return label(value, STAGE_LABELS, ['未分阶段', 'Unstaged'], text);
}

export function certificationLabel(value: string | null | undefined, text: WorkbenchText): string {
  return label(value, CERTIFICATION_LABELS, ['阶段状态已更新', 'Stage status updated'], text);
}
