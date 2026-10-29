import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { operatorDecisionCards } from '../../../core/src/decisions';
import { ROLE_NAMES } from '../../../core/src/eventRender';
import type { Locale } from '../i18n';
import { roleActionLabel } from './taskLanguage';

export interface WorkStatus {
  state: 'running' | 'waiting' | 'paused' | 'idle' | 'unknown';
  /** The role at work: the Manager answering the operator, or the first of the roles at work. */
  role: string;
  /** Every role at work now; tasks run side by side. */
  roles: string[];
  taskId: string;
  title: string;
  activityAt: number | null;
  activityAgeSeconds: number | null;
  reason: 'awaiting_next_step' | 'not_running' | 'operator_input' | '';
  /** The Manager is answering a request the operator sent, not tidying up on its own. */
  foreground?: boolean;
}

const timestamp = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;

/** Work that writes only Argus's own records (``.argus/…``): its failure is
 * Argus's housekeeping, not the person's work, and never undoes a result that
 * was delivered and reviewed (ux-principles rules 6 and 7). */
export function internalRecordsOnly(item: unknown): boolean {
  const owned = (item as { owns_paths?: unknown } | undefined)?.owns_paths;
  if (!Array.isArray(owned) || owned.length === 0) return false;
  return owned.every((path) => {
    const flat = String(path).replace(/\\/g, '/').replace(/^\.\//, '');
    return flat === '.argus' || flat.startsWith('.argus/');
  });
}

export function eventTaskId(event: EventMsg): string {
  return String(event.item_id || event.mission_id || '').split(':attempt:')[0];
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

/** Where the project stands, as the server says it: the Manager answering the operator, the roles
 * at work, a question waiting for the operator, a run between steps, or no run at all. Nothing is
 * inferred from the record; it only dates the last thing a role did. */
export function currentWorkStatus(
  snapshot: Snapshot | undefined,
  _view?: MissionView | null,
  events: EventMsg[] = [],
  nowSeconds = Date.now() / 1000,
): WorkStatus {
  // What a role did, never what the operator said.
  const done = events.filter(event => event.agent_layer && event.agent_layer !== 'operator');
  const times = [...done.map(event => timestamp(event.ts)), ...(snapshot?.roles ?? []).map(role => timestamp(role.last?.ts))]
    .filter((time): time is number => time !== null && time <= nowSeconds + 5);
  const activityAt = times.length ? Math.max(...times) : null;
  const roles = (snapshot?.roles ?? []).filter(role => role.active).map(role => role.role);
  // One task under way names the work; several side by side name none of them.
  const underWay = (snapshot?.backlog ?? []).filter(item => item.status === 'in_progress');
  const task = underWay.length === 1 ? underWay[0] : undefined;
  const status: WorkStatus = {
    state: 'idle', role: roles[0] ?? '', roles, taskId: task?.id ?? '', title: task?.title ?? '',
    activityAt, activityAgeSeconds: activityAt === null ? null : Math.max(0, nowSeconds - activityAt), reason: '',
  };
  if (!snapshot || snapshot.daemon.read_status === 'error') return { ...status, state: 'unknown' };
  if (snapshot.manager_requests?.some(request => request.status === 'running')) {
    return { ...status, state: 'running', role: 'manager', foreground: true };
  }
  if (roles.length) return { ...status, state: 'running' };
  const card = operatorDecisionCards(snapshot.pending_questions ?? [], snapshot.backlog.map(item => ({ ...item })))[0];
  if (card) return { ...status, state: 'paused', reason: 'operator_input', taskId: card.item_id, title: card.task_title || card.title };
  if (snapshot.daemon.alive) return { ...status, state: 'waiting', reason: 'awaiting_next_step' };
  // A task that failed is not done: the work stopped short of it.
  return snapshot.backlog.some(item => item.status !== 'done') ? { ...status, state: 'paused', reason: 'not_running' } : status;
}

export function workStatusLabel(status: WorkStatus, locale: Locale, connected = true): string {
  const zh = locale === 'zh-CN';
  if (!connected) return zh ? '实时连接已断开' : 'Live connection lost';
  if (status.reason === 'operator_input') return zh ? '有个问题在等你回答' : 'A question is waiting for your answer';
  if (status.state === 'running') {
    if (status.foreground) return zh ? '正在处理你的请求' : 'Working on your request';
    // With several tasks under way none names the work: it says who is at work.
    if (status.roles.length > 1 || !status.title && ['engineer', 'reviewer'].includes(status.role)) {
      const names = status.roles.map(role => ROLE_NAMES[`role.${role}`]?.[zh ? 1 : 0] ?? role);
      return zh ? `${names.join('、')}正在工作` : `${names.join(', ')} at work`;
    }
    return roleActionLabel(status.role, zh, status.title);
  }
  const labels: Record<WorkStatus['state'], [string, string]> = {
    running: ['正在处理当前任务', 'Working on the current task'], waiting: ['等待下一步工作', 'Waiting for the next step'],
    paused: ['当前任务未在运行', 'The current task is not running'],
    idle: ['已完成', 'Done'], unknown: ['暂时无法确认任务进度', 'Unable to confirm task progress right now'],
  };
  return labels[status.state][zh ? 0 : 1];
}

/** Whether this project has no queued or completed work. */
export function isFreshProject(snapshot: Pick<Snapshot, 'backlog' | 'daemon' | 'continuous'> | null | undefined): boolean {
  if (!snapshot) return false;
  return snapshot.backlog.every((item) => ['skipped', 'cancelled'].includes(item.status))
    && !snapshot.daemon.alive
    && !snapshot.continuous?.enabled;
}
