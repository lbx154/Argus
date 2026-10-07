import type { BacklogItem, EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { operatorDecisionCards } from '../../../core/src/decisions';
import type { Locale } from '../i18n';

/** The team the current task waits on, as far as the runtime has read it. */
export interface BackgroundWait {
  workId: string;
  /** Counts by kind of subtask, routes before their reviews. */
  parts: Array<{ role: string; done: number; total: number; running: number }>;
  done: number;
  total: number;
  lastProgressAt: number | null;
}

export interface WorkStatus {
  state: 'running' | 'waiting' | 'paused' | 'step_finished' | 'idle' | 'unknown';
  role: string;
  taskId: string;
  title: string;
  activityAt: number | null;
  activityAgeSeconds: number | null;
  reason: 'provider_wait' | 'no_recent_progress' | 'awaiting_next_step' | 'not_running' | 'task_failed' | 'operator_input' | 'background_work' | '';
  /** Set when the task is waiting on work outside its own rounds. */
  waitingOn?: BackgroundWait;
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

const WAIT_EVENTS = new Set(['round.external_work_wait.started', 'round.external_work_wait.completed']);

/** The background work the task waits on: the wait parked on the backlog item,
 * or the in-round wait the journal records for a running task; its progress
 * comes from the snapshot, which read the team board once for every surface. */
export function backgroundWait(
  snapshot: Snapshot | undefined,
  task: BacklogItem | undefined,
  events: EventMsg[],
  started = 0,
): BackgroundWait | null {
  if (!snapshot || !task || !snapshot.daemon.alive) return null;
  let workId = '';
  let over = false;
  if (task.status === 'paused_external_work') {
    workId = String(task.outcome?.external_wait?.work_id || '');
  } else if (ACTIVE.has(task.status)) {
    // The page keeps a longer journal than the snapshot: what it saw last wins.
    const latest = [...events].reverse().find(event => WAIT_EVENTS.has(String(event.type || ''))
      && eventTaskId(event) === task.id && Number(event.ts || 0) >= started);
    if (latest?.type === 'round.external_work_wait.started') workId = String(latest.work_id || '');
    over = latest?.type === 'round.external_work_wait.completed';
  } else return null;
  if (over) return null;
  const entry = (snapshot.background_work || []).find(candidate =>
    (workId && candidate.work_id === workId) || candidate.waited_by?.includes(task.id));
  if (!entry) return workId ? { workId, parts: [], done: 0, total: 0, lastProgressAt: null } : null;
  return {
    workId: entry.work_id,
    parts: (entry.parts || []).map(part => ({ role: part.role, done: part.done, total: part.total, running: part.running })),
    done: entry.done,
    total: entry.total,
    lastProgressAt: timestamp(entry.last_progress_ts),
  };
}

/** Runtime status is separate from progress toward the user's research goal. */
export function currentWorkStatus(
  snapshot: Snapshot | undefined,
  view?: MissionView | null,
  events: EventMsg[] = [],
  nowSeconds = Date.now() / 1000,
): WorkStatus {
  // A mission parked on its background team is still the current one.
  const taskId = view?.mission.id
    || snapshot?.backlog?.find(item => ACTIVE.has(item.status) || item.status === 'paused_external_work')?.id || '';
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
  // A task waiting on its own background team is neither stalled nor paused:
  // one sentence, with the team's progress, wherever the status is shown.
  const wait = backgroundWait(snapshot, task, events, started);
  if (wait) {
    const at = wait.lastProgressAt ?? activityAt;
    return {
      ...result,
      state: 'waiting',
      role: '',
      reason: 'background_work',
      waitingOn: wait,
      activityAt: at,
      activityAgeSeconds: at === null ? null : Math.max(0, nowSeconds - at),
    };
  }
  const missionState = String(task?.status || view?.mission.status || '').toLowerCase();
  if (PAUSED.has(missionState) || missionState.startsWith('paused_') || missionState === 'research_incomplete') {
    const needsReply = operatorDecisionCards(snapshot.pending_questions ?? [], snapshot.backlog.map(item => ({ ...item })), taskId)
      .some(card => card.item_id === taskId);
    return { ...result, state: 'paused', role: '', reason: needsReply ? 'operator_input' : 'not_running' };
  }
  if (['failed', 'error'].includes(missionState)) return { ...result, state: 'step_finished', role: '', reason: 'task_failed' };
  if (FINISHED.has(missionState)) {
    // Between steps a live Planner is choosing the next one. Post-step
    // housekeeping is not a new step, so no other role counts here.
    if (snapshot.daemon.alive && liveRoles.some(item => item.role === 'planner')) {
      return { ...result, state: 'running', role: 'planner' };
    }
    return { ...result, role: '',
      state: snapshot.daemon.alive && (view?.routing?.continuous || snapshot.continuous?.enabled) ? 'waiting' : 'step_finished',
    };
  }
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

/** "3 分钟前" / "3 min ago", for the last time something moved. */
export function progressAgeLabel(seconds: number | null, locale: Locale): string {
  const zh = locale === 'zh-CN';
  if (seconds === null) return '';
  if (seconds < 60) return zh ? '刚刚' : 'just now';
  if (seconds < 3600) return zh ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 60)} min ago`;
  return zh ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 3600)} hr ago`;
}

const PART_NOUNS: Record<string, [zh: string, en: [string, string]]> = {
  'idea-route': ['条研究路线', ['research route', 'research routes']],
  'idea-review': ['次独立复核', ['independent review', 'independent reviews']],
  'idea-selector': ['次方案选择', ['idea selection', 'idea selections']],
};

function partPhrase(part: BackgroundWait['parts'][number], zh: boolean): string {
  if (!part.total) return '';
  const [nounZh, [one, many]] = PART_NOUNS[part.role] ?? ['项子任务', ['subtask', 'subtasks']];
  const noun = part.total === 1 ? one : many;
  if (part.done === part.total) return zh ? `${part.total} ${nounZh}全部完成` : `all ${part.total} ${noun} done`;
  return zh ? `${part.total} ${nounZh}中 ${part.done} ${nounZh.slice(0, 1)}完成` : `${part.done} of ${part.total} ${noun} done`;
}

/** One sentence for a task waiting on its background team: what it waits
 * for, how far that is, and when it last moved. */
export function backgroundWaitLabel(status: WorkStatus, locale: Locale): string {
  const zh = locale === 'zh-CN';
  const wait = status.waitingOn;
  const parts = (wait?.parts ?? []).map(part => partPhrase(part, zh)).filter(Boolean);
  const progress = parts.length ? parts.join(zh ? '、' : ', ')
    : wait?.total ? (zh ? `${wait.total} 项子任务中 ${wait.done} 项完成` : `${wait.done} of ${wait.total} subtasks done`) : '';
  const age = progressAgeLabel(status.activityAgeSeconds, locale);
  const recent = !age ? '' : zh ? (age === '刚刚' ? '，最近一次进展就在刚刚' : `，最近一次进展在 ${age}`) : `, last progress ${age}`;
  return progress
    ? zh ? `正在等待后台团队：${progress}${recent}` : `Waiting for the background team: ${progress}${recent}`
    : zh ? `正在等待后台团队完成工作${recent}` : `Waiting for the background team to finish${recent}`;
}

export function workStatusLabel(status: WorkStatus, locale: Locale, connected = true): string {
  const zh = locale === 'zh-CN';
  if (!connected) return zh ? '实时连接已断开' : 'Live connection lost';
  if (status.reason === 'operator_input') return zh ? '当前任务等待你的回复' : 'This task is waiting for your reply';
  if (status.reason === 'task_failed') return zh ? '这一步执行未完成' : 'Execution of this step did not finish';
  if (status.reason === 'background_work') return backgroundWaitLabel(status, locale);
  if (status.state === 'running') {
    const roles: Record<string, [string, string]> = {
      // The Manager also tidies progress on its own; only an operator's request is "yours".
      manager: status.foreground ? ['正在处理你的请求', 'Working on your request'] : ['统筹者正在整理进展', 'The Manager is summarizing progress'],
      planner: ['正在规划下一步', 'Planning the next step'],
      engineer: ['正在执行当前步骤', 'Working on this step'], reviewer: ['正在核对这一步的结果', 'Checking this step’s result'],
    };
    return roles[status.role]?.[zh ? 0 : 1] || (zh ? '正在处理当前任务' : 'Working on the current task');
  }
  if (status.reason === 'provider_wait') return zh ? '模型服务等待后重试' : 'Waiting before retrying the model service';
  if (status.reason === 'no_recent_progress') return zh ? '暂时没有收到新的进展' : 'No new progress received recently';
  const labels: Record<WorkStatus['state'], [string, string]> = {
    running: ['正在处理当前任务', 'Working on the current task'], waiting: ['等待下一步工作', 'Waiting for the next step'],
    paused: ['当前任务未在运行', 'The current task is not running'], step_finished: ['这一步已结束', 'This step has ended'],
    idle: ['等待新任务', 'Ready for a new task'], unknown: ['当前运行状态暂不可读', 'Current runtime status is unavailable'],
  };
  return labels[status.state][zh ? 0 : 1];
}
