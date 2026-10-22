import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { operatorDecisionCards } from '../../../core/src/decisions';
import { parkReason } from '../../../core/src/missionOutcome';
import type { Locale } from '../i18n';
import { roleActionLabel } from './taskLanguage';

export interface WorkStatus {
  state: 'running' | 'waiting' | 'paused' | 'step_finished' | 'idle' | 'unknown';
  role: string;
  taskId: string;
  title: string;
  activityAt: number | null;
  activityAgeSeconds: number | null;
  reason: 'provider_wait' | 'no_recent_progress' | 'awaiting_next_step' | 'not_running' | 'task_failed' | 'operator_input' | 'launch_confirm' | '';
  /** The Manager is answering a request the operator sent, not tidying up on its own. */
  foreground?: boolean;
}

const ACTIVE = new Set(['running', 'in_progress', 'claimed', 'active', 'working', 'grounding', 'framed']);
const FINISHED = new Set(['complete', 'completed', 'done', 'success', 'ended']);
const PAUSED = new Set(['paused', 'stopped', 'cancelled', 'aborted', 'incomplete', 'blocked', 'stalled']);
const timestamp = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;

export function eventTaskId(event: EventMsg): string {
  return String(event.item_id || event.mission_id || '').split(':attempt:')[0];
}

export function recordedEventRole(event: EventMsg): string {
  const value = String(event.agent_layer || event.actor || event.role || event.run_label || '');
  if (value === 'main') return 'engineer';
  return /^(manager|planner|engineer|reviewer)(?:[-.:]|$)/.exec(value)?.[1] || '';
}

/** The recorded start boundary for this task's current attempt and daemon run. */
export function currentWorkStartedAt(snapshot: Snapshot | undefined, view: MissionView | null | undefined, taskId: string): number {
  const task = snapshot?.backlog?.find(item => item.id === taskId);
  const boot = Date.parse(snapshot?.daemon.started_at_iso || '') / 1000;
  return Math.max(view?.mission.id === taskId ? view.mission.started_at || 0 : 0,
    task?.started_ts || 0, Number.isFinite(boot) ? boot : 0);
}

/** A completion belongs to its call or task, never to every concurrent task. */
export function activeProviderRequest(events: EventMsg[], notBefore = 0): EventMsg | null {
  const calls = new Map<string, EventMsg>();
  for (const event of events) {
    if (Number(event.ts || 0) < notBefore) continue;
    const type = String(event.type || ''), id = String(event.call_id || '');
    if (['provider.request.started', 'agent.io.start'].includes(type) && id) {
      if (!String(event.run_label || '').startsWith('map-summary')) calls.set(id, event);
    } else if (['provider.request.completed', 'provider.request.denied', 'provider.request.failed', 'agent.io.complete'].includes(type) && id) {
      calls.delete(id);
    } else if (['life.mission.completed', 'mission.completed', 'life.mission.failed'].includes(type)) {
      const task = eventTaskId(event);
      if (task) for (const [key, call] of calls) if (eventTaskId(call) === task) calls.delete(key);
    }
  }
  return [...calls.values()].at(-1) ?? null;
}

/** Runtime status is separate from progress toward the user's research goal. */
export function currentWorkStatus(
  snapshot: Snapshot | undefined,
  view?: MissionView | null,
  events: EventMsg[] = [],
  nowSeconds = Date.now() / 1000,
): WorkStatus {
  const taskId = view?.mission.id || snapshot?.backlog?.find(item => ACTIVE.has(item.status))?.id || '';
  const task = snapshot?.backlog?.find(item => item.id === taskId);
  const started = currentWorkStartedAt(snapshot, view, taskId);
  const liveRoles = snapshot?.roles?.filter(role => role.active) || [];
  const parallel = (snapshot?.backlog?.filter(item => ACTIVE.has(item.status)).length || 0) > 1;
  const roleWork = new Map<string, MissionView['role_work'][number]>();
  for (const row of view?.role_work || []) {
    if ((taskId && (row.item_id || row.mission_id) !== taskId) || row.ts < started) continue;
    if (!roleWork.has(row.role) || roleWork.get(row.role)!.ts < row.ts) roleWork.set(row.role, row);
  }
  const scopedRole = [...roleWork.values()].filter(row => ACTIVE.has(row.status))
    .sort((left, right) => right.ts - left.ts).find(row => liveRoles.some(role => role.role === row.role))?.role;
  const role = scopedRole || (!parallel ? (liveRoles.find(role => role.role === view?.active_role) || liveRoles[0])?.role : '')
    || (!snapshot ? view?.roles.find(role => role.status === 'active')?.role : '') || '';
  const times = events.filter(event => {
    const id = eventTaskId(event);
    return (!taskId || id === taskId) && Number(event.ts || 0) >= started
      && !String(event.run_label || '').startsWith('map-summary')
      && /(?:progress|round\.(?:main|review)|life\.(?:mission|phase)|work\.segment)/.test(String(event.type || ''));
  }).map(event => timestamp(event.ts));
  for (const item of view?.role_work || []) {
    const id = item.item_id || item.mission_id;
    if ((!taskId || id === taskId) && item.ts >= started
      && item.kind !== 'waiting' && !['waiting', 'idle'].includes(item.status)) times.push(timestamp(item.ts));
  }
  const known = times.filter((time): time is number => time !== null && time <= nowSeconds + 5);
  const activityAt = known.length ? Math.max(...known) : null;
  const result: WorkStatus = {
    state: 'idle', role, taskId,
    title: view?.mission.title || task?.title || view?.mission.objective || '',
    activityAt, activityAgeSeconds: activityAt === null ? null : Math.max(0, nowSeconds - activityAt), reason: '',
  };
  if (!snapshot || snapshot.daemon.read_status === 'error') return { ...result, state: 'unknown' };
  // A first task planned and held: nothing runs until the operator starts it.
  if (snapshot.launch_hold && !snapshot.daemon.alive) return { ...result, state: 'paused', role: '', reason: 'launch_confirm' };
  const managerRunning = snapshot.manager_requests?.some(request => request.status === 'running') ?? false;
  if (managerRunning) {
    const provider = activeProviderRequest(events);
    const at = timestamp(provider?.ts) ?? activityAt;
    return {
      ...result,
      state: 'running',
      role: 'manager',
      foreground: true,
      activityAt: at,
      activityAgeSeconds: at === null ? null : Math.max(0, nowSeconds - at),
    };
  }
  // SELF execution does not create a daemon backlog item. Its durable mission
  // receipt is the execution boundary even when Argus-Pi records no tool
  // steps; ordinary chat and item-bound queue receipts remain excluded.
  if (!taskId) {
    const direct = [...events].reverse().find(event => event.type === 'ui.argus'
      && event.mission_result === true && !event.item_id && event.live !== true);
    if (direct) {
      const at = timestamp(direct.ts);
      return {
        ...result,
        state: 'step_finished',
        activityAt: at,
        activityAgeSeconds: at === null ? null : Math.max(0, nowSeconds - at),
        reason: direct.success === false ? 'task_failed' : '',
      };
    }
  }
  const missionState = String(task?.status || view?.mission.status || '').toLowerCase();
  // A question waiting on the operator, whichever task asked it, is where the
  // project stands whenever nothing else is under way.
  const card = operatorDecisionCards(snapshot.pending_questions ?? [], snapshot.backlog.map(item => ({ ...item })), taskId)[0];
  const asked: WorkStatus | null = card ? {
    ...result, state: 'paused', role: '', reason: 'operator_input', taskId: card.item_id, title: card.task_title || card.title,
  } : null;
  if (PAUSED.has(missionState) || parkReason(missionState) !== null) {
    return asked ?? { ...result, state: 'paused', role: '', reason: 'not_running' };
  }
  if (['failed', 'error'].includes(missionState)) return { ...result, state: 'step_finished', role: '', reason: 'task_failed' };
  if (FINISHED.has(missionState)) {
    // Between steps a live Planner is choosing the next one. Post-step
    // housekeeping is not a new step, so no other role counts here.
    if (snapshot.daemon.alive && liveRoles.some(item => item.role === 'planner')) {
      return { ...result, state: 'running', role: 'planner' };
    }
    return asked ?? { ...result, role: '',
      state: snapshot.daemon.alive && (view?.routing?.continuous || snapshot.continuous?.enabled) ? 'waiting' : 'step_finished',
    };
  }
  if (!snapshot.daemon.alive) {
    if (ACTIVE.has(missionState)) return { ...result, state: 'paused', role: '', reason: 'not_running' };
    return asked ?? { ...result, role: '' };
  }
  const backoff = [...events].reverse().find(event => event.type === 'round.backend_failure.backoff'
    && Number(event.ts || 0) >= started && (!taskId || eventTaskId(event) === taskId));
  if (backoff && Number(backoff.ts || 0) + Number(backoff.seconds || 0) > nowSeconds
    && Number(backoff.ts || 0) >= (activityAt || 0)) return { ...result, state: 'waiting', reason: 'provider_wait' };
  if (snapshot.daemon.health?.stalled) return { ...result, state: 'waiting', reason: 'no_recent_progress' };
  if (role) return { ...result, state: 'running' };
  if (parallel && liveRoles.length > 0 && taskId) return { ...result, state: 'unknown' };
  return asked ?? { ...result, state: 'waiting', reason: 'awaiting_next_step' };
}

/** "3 分钟前" / "3 min ago", for the last time something moved. */
export function progressAgeLabel(seconds: number | null, locale: Locale): string {
  const zh = locale === 'zh-CN';
  if (seconds === null) return '';
  if (seconds < 60) return zh ? '刚刚' : 'just now';
  if (seconds < 3600) return zh ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 60)} min ago`;
  return zh ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 3600)} hr ago`;
}

export function workStatusLabel(status: WorkStatus, locale: Locale, connected = true): string {
  const zh = locale === 'zh-CN';
  if (!connected) return zh ? '实时连接已断开' : 'Live connection lost';
  if (status.reason === 'operator_input') return zh ? '有个问题在等你回答' : 'A question is waiting for your answer';
  if (status.reason === 'launch_confirm') return zh ? '计划已排好，等你确认开始' : 'Plan ready; waiting for your go-ahead';
  if (status.reason === 'task_failed') return zh ? '这一步执行未完成' : 'Execution of this step did not finish';
  if (status.state === 'running') {
    if (status.foreground) return zh ? '正在处理你的请求' : 'Working on your request';
    return roleActionLabel(status.role, zh, status.title);
  }
  if (status.reason === 'provider_wait') return zh ? '模型服务暂时未能完成请求，等待后会重试' : 'The model service could not complete the request; waiting before retrying';
  if (status.reason === 'no_recent_progress') return zh ? '暂时没有收到新的进展' : 'No new progress received recently';
  const labels: Record<WorkStatus['state'], [string, string]> = {
    running: ['正在处理当前任务', 'Working on the current task'], waiting: ['等待下一步工作', 'Waiting for the next step'],
    paused: ['当前任务未在运行', 'The current task is not running'], step_finished: ['这一步已结束', 'This step has ended'],
    idle: ['可以开始新任务', 'Ready for a new task'], unknown: ['暂时无法确认任务进度', 'Unable to confirm task progress right now'],
  };
  return labels[status.state][zh ? 0 : 1];
}
