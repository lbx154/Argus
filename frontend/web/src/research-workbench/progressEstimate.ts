import type { BacklogItem, EventMsg, Snapshot } from './types';
import { statusLabel } from './enumLabels';
import { eventDetail, eventRole, eventTitle } from './utils';

const DONE = new Set(['done', 'completed', 'accepted', 'success']);
const ACTIVE = new Set(['running', 'in_progress', 'claimed', 'active', 'working']);

export interface ProgressCheckpoint {
  id: string;
  label: string;
  detail: string;
  status: 'done' | 'active' | 'pending' | 'blocked';
}

export interface ProgressEstimate {
  confirmed: number;
  estimate: number | null;
  range: [number, number] | null;
  confidence: 'low' | 'medium' | 'high';
  basis: string;
  currentTask: string;
  currentRole: string;
  currentStep: string;
  currentDetail: string;
  elapsedSeconds: number;
  eta: { minSeconds: number; maxSeconds: number; basis: string } | null;
  etaUnavailableReason: string;
  checkpoints: ProgressCheckpoint[];
  completedTasks: number;
  totalTasks: number;
  pendingTasks: number;
  currentFraction: number;
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function latestUsefulEvent(events: EventMsg[]): EventMsg | null {
  return [...events].reverse().find((event) => {
    const kind = String(event.kind ?? '');
    const type = String(event.type ?? '');
    return kind !== 'reasoning' && !type.startsWith('provider.') && !['ui.operator', 'ui.argus'].includes(type);
  }) ?? null;
}

function currentBacklogItem(snapshot: Snapshot): BacklogItem | null {
  return snapshot.backlog.find((item) => ACTIVE.has(item.status))
    ?? snapshot.backlog.find((item) => item.status === 'pending')
    ?? snapshot.backlog.at(-1)
    ?? null;
}

export function deriveProgressEstimate(snapshot: Snapshot, events: EventMsg[], nowSeconds = Date.now() / 1_000, locale: 'en' | 'zh-CN' = 'zh-CN'): ProgressEstimate {
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en;
  const view = snapshot.mission_view;
  const dag = view?.dag?.length ? view.dag : snapshot.backlog;
  const total = dag.length;
  const completed = dag.filter((item) => DONE.has(item.status)).length;
  const pending = dag.filter((item) => /pending|queued|waiting/.test(item.status)).length;
  const current = currentBacklogItem(snapshot);
  const activeRole = view?.active_role || snapshot.roles.find((role) => role.active)?.role || '';
  const missionStarted = current?.started_ts || view?.mission.started_at || snapshot.daemon.uptime_seconds && nowSeconds - snapshot.daemon.uptime_seconds || nowSeconds;
  const eventWindow = events.filter((event) => Number(event.ts ?? 0) >= Number(missionStarted || 0));
  const latest = latestUsefulEvent(eventWindow);
  const missionStatus = String(view?.mission.status ?? '').toLowerCase();
  const explicitlyComplete = ['complete', 'completed', 'done'].includes(missionStatus);
  const explicitlyIncomplete = ['incomplete', 'failed', 'blocked', 'aborted', 'stopped', 'cancelled'].includes(missionStatus);
  const missionComplete = explicitlyComplete || (!explicitlyIncomplete && Boolean(total && completed === total));
  const daemonStopped = !snapshot.daemon.alive;
  const terminalAt = current?.finished_ts || view?.mission.completed_at || (daemonStopped ? Number(latest?.ts ?? missionStarted) : null);
  const elapsed = Math.max(0, Number(terminalAt ?? nowSeconds) - Number(missionStarted || nowSeconds));
  const hasActiveTask = Boolean(current && ACTIVE.has(current.status) && snapshot.daemon.alive);
  const commands = eventWindow.filter((event) => String(event.kind ?? '') === 'command_execution').length;
  const fileActions = eventWindow.filter((event) => /^(read|write|edit):/i.test(eventDetail(event, 80)) || String(event.kind ?? '') === 'file_change').length;
  const engineerHandoff = Boolean(view?.role_work?.some((item) => item.role === 'engineer' && /handoff|main completed/i.test(`${item.kind} ${item.title}`) && item.ts >= Number(missionStarted || 0)));
  const reviewStarted = eventWindow.some((event) => /review.*started/i.test(String(event.type ?? '')));
  const reviewCompleted = eventWindow.some((event) => /review.*completed/i.test(String(event.type ?? '')));
  const blocked = Boolean(current && /failed|blocked|error/.test(current.status));
  const replan = Boolean(view?.review?.status && /replan|blocked|rejected|continue/.test(view.review.status));

  let fraction = 0;
  if (current && DONE.has(current.status)) fraction = 1;
  else if (current && ACTIVE.has(current.status) && snapshot.daemon.alive) {
    fraction = .14;
    if (commands || fileActions) fraction = Math.min(.58, .27 + Math.log2(1 + commands + fileActions) * .055);
    if (engineerHandoff) fraction = .70;
    if (reviewStarted || activeRole === 'reviewer') fraction = .80;
    if (reviewCompleted) fraction = .93;
  }

  const confirmed = missionComplete ? 1 : explicitlyIncomplete && total && completed === total ? .95 : total ? completed / total : 0;
  const activeInDag = current && dag.some((item) => item.id === current.id) && hasActiveTask && !replan;
  const estimate = missionComplete ? 1 : replan ? confirmed : total ? Math.min(1, (completed + (activeInDag ? fraction : 0)) / total) : null;
  const uncertainty = total ? Math.max(.05, Math.min(.18, .45 / total)) : 0;
  const range: [number, number] | null = missionComplete ? [1, 1] : replan ? [confirmed, confirmed] : estimate == null ? null : [
    Math.max(confirmed, estimate - uncertainty * .45),
    Math.min(.99, Math.max(estimate, estimate + uncertainty)),
  ];

  const durations = snapshot.backlog
    .map((item) => item.started_ts && item.finished_ts ? item.finished_ts - item.started_ts : 0)
    .filter((duration) => duration >= 5 && duration <= 7 * 86_400);
  const typicalDuration = median(durations);
  let eta: ProgressEstimate['eta'] = null;
  let etaUnavailableReason = '';
  if (missionComplete) etaUnavailableReason = text('项目已完成，无需预计完成时间', 'Project complete; no finish-time estimate is needed');
  else if (daemonStopped) etaUnavailableReason = text('Argus 已停止，预计完成时间暂停更新', 'Argus stopped; the expected finish time is paused');
  else if (!hasActiveTask) etaUnavailableReason = text('当前没有执行中的任务，暂时无法预计完成时间', 'No active task; the expected finish time is unavailable');
  else if (replan) etaUnavailableReason = text('Reviewer 正在改变任务范围，暂时无法预计完成时间', 'The Reviewer is changing scope, so the expected finish time is unavailable');
  else if (!typicalDuration) etaUnavailableReason = text('同类已完成任务不足，正在建立时间基线', 'Not enough completed tasks to establish a time baseline');
  else if (!total || estimate == null) etaUnavailableReason = text('任务路线尚未稳定，暂不预计完成时间', 'The task route is not stable enough to estimate a finish time');
  else {
    const remainingEquivalent = Math.max(0, total - completed - (activeInDag ? fraction : 0));
    const center = remainingEquivalent * typicalDuration;
    eta = {
      minSeconds: Math.max(60, center * .68),
      maxSeconds: Math.max(180, center * (durations.length >= 3 ? 1.45 : 1.75)),
      basis: text(`${durations.length} 个已完成任务的中位耗时`, `Median duration of ${durations.length} completed tasks`),
    };
  }

  const confidence: ProgressEstimate['confidence'] = total >= 4 && durations.length >= 3 ? 'high' : total >= 2 && durations.length >= 1 ? 'medium' : 'low';
  const plannerDone = snapshot.roles.find((role) => role.role === 'planner')?.status === 'done' || Boolean(current);
  let checkpoints: ProgressCheckpoint[] = [
    { id: 'plan', label: text('规划任务', 'Plan task'), detail: plannerDone ? text('Planner 已形成当前任务', 'Planner created the current task') : text('等待 Planner', 'Waiting for Planner'), status: plannerDone ? 'done' : activeRole === 'planner' ? 'active' : 'pending' },
    { id: 'start', label: text('启动执行', 'Start execution'), detail: current?.started_ts ? text('任务已领取并启动', 'Task claimed and started') : text('等待执行', 'Waiting to execute'), status: current?.started_ts ? 'done' : current?.status === 'pending' ? 'pending' : blocked ? 'blocked' : 'active' },
    { id: 'work', label: text('运行与产物', 'Execution and artifacts'), detail: text(`${commands} 条命令 · ${fileActions} 次文件动作`, `${commands} commands · ${fileActions} file actions`), status: engineerHandoff ? 'done' : commands || fileActions ? 'active' : blocked ? 'blocked' : 'pending' },
    { id: 'handoff', label: text('Engineer 交接', 'Engineer handoff'), detail: engineerHandoff ? text('已提交 Reviewer', 'Submitted to Reviewer') : text('等待可审查产物', 'Waiting for reviewable artifacts'), status: engineerHandoff ? 'done' : activeRole === 'reviewer' ? 'done' : 'pending' },
    { id: 'review', label: text('Reviewer 认证', 'Reviewer certification'), detail: reviewCompleted ? text('本轮审查已完成', 'Round review complete') : reviewStarted || activeRole === 'reviewer' ? text('Reviewer 正在检查', 'Reviewer is checking') : text('等待审查', 'Waiting for review'), status: reviewCompleted ? 'done' : reviewStarted || activeRole === 'reviewer' ? 'active' : blocked ? 'blocked' : 'pending' },
  ];
  if (missionComplete) checkpoints = checkpoints.map((checkpoint) => ({ ...checkpoint, status: 'done' as const, detail: checkpoint.status === 'done' ? checkpoint.detail : text('项目已完成', 'Project complete') }));
  else if (replan) checkpoints = checkpoints.map((checkpoint) => checkpoint.status === 'done' ? checkpoint : { ...checkpoint, status: 'blocked' as const, detail: text('等待 Reviewer 重新规划任务范围', 'Waiting for Reviewer to replan scope') });
  else if (daemonStopped) checkpoints = checkpoints.map((checkpoint) => checkpoint.status === 'active' ? { ...checkpoint, status: 'blocked' as const, detail: text('Argus 已停止', 'Argus stopped') } : checkpoint);

  return {
    confirmed,
    estimate,
    range,
    confidence,
    basis: replan ? text('Reviewer 正在重新规划，仅显示确定完成部分', 'Reviewer is replanning; only confirmed completion is shown') : total ? text('根据任务状态和事件里程碑估算', 'Estimated from task status and event milestones') : text('任务路线尚未建立', 'Task route not established'),
    currentTask: current?.title || view?.mission.title || text('等待新任务', 'Waiting for a new task'),
    currentRole: daemonStopped ? 'stopped' : activeRole || eventRole(latest ?? {}) || 'idle',
    currentStep: missionComplete ? text('项目已完成', 'Project complete') : replan ? text(`Reviewer 要求重新规划 · ${statusLabel(view?.review?.status || 'replan', text)}`, `Reviewer requested replanning · ${statusLabel(view?.review?.status || 'replan', text)}`) : daemonStopped ? text(`已停止 · 最后执行到 ${latest ? eventTitle(latest) : statusLabel(current?.status, text)}`, `Stopped · last step: ${latest ? eventTitle(latest) : statusLabel(current?.status, text)}`) : latest ? eventTitle(latest) : current?.status ? statusLabel(current.status, text) : text('等待动态', 'Waiting for activity'),
    currentDetail: latest ? eventDetail(latest, 700) : current?.objective || '',
    elapsedSeconds: elapsed,
    eta,
    etaUnavailableReason,
    checkpoints,
    completedTasks: completed,
    totalTasks: total,
    pendingTasks: pending,
    currentFraction: replan ? 0 : fraction,
  };
}
