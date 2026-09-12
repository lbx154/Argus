import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import type { Locale } from '../i18n';

export interface WorkStatus {
  state: 'running' | 'waiting' | 'paused' | 'step_finished' | 'idle' | 'unknown';
  role: string;
  taskId: string;
  title: string;
  activityAt: number | null;
  activityAgeSeconds: number | null;
  reason: 'provider_wait' | 'no_recent_progress' | 'awaiting_next_step' | 'not_running' | 'task_failed' | '';
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
  const boot = Date.parse(snapshot?.daemon.started_at_iso || '') / 1000;
  const started = Math.max(view?.mission.started_at || 0, task?.started_ts || 0, Number.isFinite(boot) ? boot : 0);
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
    if ((!taskId || id === taskId) && item.ts >= started) times.push(timestamp(item.ts));
  }
  const known = times.filter((time): time is number => time !== null && time <= nowSeconds + 5);
  const activityAt = known.length ? Math.max(...known) : null;
  const result: WorkStatus = {
    state: 'idle', role, taskId,
    title: view?.mission.title || task?.title || view?.mission.objective || '',
    activityAt, activityAgeSeconds: activityAt === null ? null : Math.max(0, nowSeconds - activityAt), reason: '',
  };
  if (!snapshot || snapshot.daemon.read_status === 'error') return { ...result, state: 'unknown' };
  const missionState = String(task?.status || view?.mission.status || '').toLowerCase();
  if (PAUSED.has(missionState) || missionState.startsWith('paused_') || missionState === 'research_incomplete') {
    return { ...result, state: 'paused', role: '', reason: 'not_running' };
  }
  if (['failed', 'error'].includes(missionState)) return { ...result, state: 'step_finished', role: '', reason: 'task_failed' };
  if (FINISHED.has(missionState)) return { ...result, role: '',
    state: snapshot.daemon.alive && (view?.routing?.continuous || snapshot.continuous?.enabled) ? 'waiting' : 'step_finished',
  };
  if (!snapshot.daemon.alive) {
    if (ACTIVE.has(missionState)) return { ...result, state: 'paused', role: '', reason: 'not_running' };
    return { ...result, role: '' };
  }
  const backoff = [...events].reverse().find(event => event.type === 'round.backend_failure.backoff'
    && Number(event.ts || 0) >= started && (!taskId || eventTaskId(event) === taskId));
  if (backoff && Number(backoff.ts || 0) + Number(backoff.seconds || 0) > nowSeconds
    && Number(backoff.ts || 0) >= (activityAt || 0)) return { ...result, state: 'waiting', reason: 'provider_wait' };
  if (snapshot.daemon.health?.stalled) return { ...result, state: 'waiting', reason: 'no_recent_progress' };
  if (role) return { ...result, state: 'running' };
  if (parallel && liveRoles.length > 0 && taskId) return { ...result, state: 'unknown' };
  return { ...result, state: 'waiting', reason: 'awaiting_next_step' };
}

export function workStatusLabel(status: WorkStatus, locale: Locale): string {
  const zh = locale === 'zh-CN';
  if (status.reason === 'task_failed') return zh ? '这一步执行未完成' : 'Execution of this step did not finish';
  if (status.state === 'running') {
    const roles: Record<string, [string, string]> = {
      manager: ['正在安排下一步', 'Coordinating the next step'], planner: ['正在规划下一步', 'Planning the next step'],
      engineer: ['正在执行当前步骤', 'Working on this step'], reviewer: ['正在核对这一步的结果', 'Checking this step’s result'],
    };
    return roles[status.role]?.[zh ? 0 : 1] || (zh ? '正在处理当前任务' : 'Working on the current task');
  }
  if (status.reason === 'provider_wait') return zh ? '模型服务等待后重试' : 'Waiting before retrying the model service';
  if (status.reason === 'no_recent_progress') return zh ? '暂时没有收到新的进展' : 'No new progress received recently';
  const labels: Record<WorkStatus['state'], [string, string]> = {
    running: ['正在处理当前任务', 'Working on the current task'], waiting: ['等待下一步工作', 'Waiting for the next step'],
    paused: ['当前任务未在运行', 'The current task is not running'], step_finished: ['这一步已结束', 'This step has ended'],
    idle: ['尚未开始执行', 'Work has not started'], unknown: ['当前运行状态暂不可读', 'Current runtime status is unavailable'],
  };
  return labels[status.state][zh ? 0 : 1];
}
