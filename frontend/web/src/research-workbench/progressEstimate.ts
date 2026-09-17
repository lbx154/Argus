import type { BacklogItem, EventMsg, Snapshot } from './types';
import { isStructuredAgentPayload } from '../../../core/src/events';
import { currentWorkStatus, eventTaskId, workStatusLabel } from '../lib/workStatus';
import { readableRecord } from '../map/submap';
import { eventDetail, eventTitle } from './utils';

const DONE = new Set(['done', 'completed', 'accepted', 'success']);
const ACTIVE = new Set(['running', 'in_progress', 'claimed', 'active', 'working']);
const NEEDS_WORK = /continue|replan|reject|blocked|fail|error|incomplete|revoked/;
const PUBLIC_MESSAGE_KINDS = new Set(['assistant_message', 'agent_message', 'message']);
const WORK_EVENTS = new Set([
  'life.planner.task_added', 'life.mission.started', 'life.mission.completed', 'life.mission.failed',
  'life.phase.started', 'life.manager.stage_decision', 'round.start',
  'round.main.completed', 'round.review.started', 'round.review.completed',
  'round.review.deferred', 'round.backend_failure.backoff',
  'work.segment', 'work.segment.started', 'work.segment.completed',
]);

export interface ProgressCheckpoint {
  id: string;
  label: string;
  detail: string;
  status: 'done' | 'active' | 'pending' | 'blocked';
}

export interface ProgressEstimate {
  /** Exact fraction of recorded work items, never an estimate of the research goal. */
  workCompletion: number | null;
  workScope: string;
  currentTask: string;
  currentTaskId: string;
  currentObjective: string;
  currentStep: string;
  currentDetail: string;
  currentEvent: EventMsg | null;
  runtime: ReturnType<typeof currentWorkStatus>;
  elapsedSeconds: number | null;
  etaUnavailableReason: string;
  checkpoints: ProgressCheckpoint[];
  completedTasks: number;
  totalTasks: number;
  pendingTasks: number;
  openEnded: boolean;
  review: {
    state: 'pending' | 'running' | 'passed' | 'needs_work' | 'skipped' | 'self_checked';
    label: string;
    detail: string;
    scope: string;
  };
  acceptanceCriteria: string;
  nextAction: string;
  /** Only the current task's activity is suitable for its progress timeline. */
  taskEvents: EventMsg[];
}

function isWorkEvent(event: EventMsg): boolean {
  const type = String(event.type ?? '');
  const kind = String(event.kind ?? '');
  return kind !== 'reasoning' && !isStructuredAgentPayload(event) && (WORK_EVENTS.has(type) || type === 'engineer.progress'
    && (PUBLIC_MESSAGE_KINDS.has(kind) || ['tool_use', 'tool_result', 'command_execution', 'file_change'].includes(kind)));
}

function readableWorkEvent(event: EventMsg): EventMsg {
  if (event.type !== 'engineer.progress' || !PUBLIC_MESSAGE_KINDS.has(String(event.kind ?? ''))) return event;
  return {
    ...event,
    text: readableRecord(String(event.text ?? '')),
    action_summary: readableRecord(String(event.action_summary ?? '')),
    title: readableRecord(String(event.title ?? '')),
  };
}

function currentBacklogItem(snapshot: Snapshot, taskId: string): BacklogItem | null {
  if (taskId) return snapshot.backlog.find((item) => item.id === taskId) ?? null;
  return snapshot.backlog.find((item) => ACTIVE.has(item.status))
    ?? snapshot.backlog.find((item) => item.status === 'pending')
    ?? snapshot.backlog.at(-1)
    ?? null;
}

function positiveTime(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

export function deriveProgressEstimate(snapshot: Snapshot, events: EventMsg[], nowSeconds = Date.now() / 1_000, locale: 'en' | 'zh-CN' = 'zh-CN'): ProgressEstimate {
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en;
  const view = snapshot.mission_view;
  const runtime = currentWorkStatus(snapshot, view, events, nowSeconds);
  const current = currentBacklogItem(snapshot, runtime.taskId || view?.mission.id || '');
  const taskId = current?.id || view?.mission.id || '';
  const matchesView = Boolean(taskId && taskId === view?.mission.id);
  const dag = view?.dag?.length ? view.dag : snapshot.backlog;
  const completed = dag.filter((item) => DONE.has(item.status)).length;
  const pending = dag.filter((item) => /^(pending|queued|waiting)$/.test(item.status)).length;
  const openEnded = Boolean(view?.routing?.open_ended || view?.routing?.lifetime === 'standing' || snapshot.continuous?.open_ended);
  const startedAt = positiveTime(current?.started_ts) ?? (matchesView ? positiveTime(view?.mission.started_at) : null);
  const finishedAt = positiveTime(current?.finished_ts) ?? (matchesView ? positiveTime(view?.mission.completed_at) : null);
  const parallelTasks = snapshot.backlog.filter((item) => ACTIVE.has(item.status)).length > 1;
  const taskEvents = events.filter((event) => {
    if (!taskId || (startedAt !== null && Number(event.ts ?? 0) < startedAt)) return false;
    const id = eventTaskId(event);
    // Legacy unscoped progress is usable only when one task could own it.
    // Positive review evidence below always requires an explicit task identity.
    return id ? id === taskId : !parallelTasks && startedAt !== null;
  }).sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0));
  const activityEvents = taskEvents.filter(isWorkEvent).map(readableWorkEvent).filter((event) =>
    !PUBLIC_MESSAGE_KINDS.has(String(event.kind ?? '')) || Boolean(event.text || event.action_summary || event.title));
  const latest = activityEvents.at(-1);
  const running = runtime.state === 'running';
  const elapsedEnd = finishedAt ?? (snapshot.daemon.alive ? nowSeconds : positiveTime(taskEvents.at(-1)?.ts) ?? startedAt);
  const elapsedSeconds = startedAt === null || elapsedEnd === null ? null : Math.max(0, elapsedEnd - startedAt);

  const latestRoundStart = [...taskEvents].reverse().find((event) => eventTaskId(event) === taskId && event.type === 'round.start');
  const round = Math.max(matchesView ? Number(view?.round.current ?? 0) : 0, Number(latestRoundStart?.round_index ?? 0));
  const roundStart = [...taskEvents].reverse().find((event) => eventTaskId(event) === taskId
    && event.type === 'round.start' && Number(event.round_index) === round);
  const belongsToRound = (event: EventMsg) => {
    if (eventTaskId(event) !== taskId || round <= 0) return false;
    if (roundStart && Number(event.ts ?? 0) < Number(roundStart.ts ?? 0)) return false;
    const eventRound = Number(event.round_index ?? 0);
    if (eventRound > 0) return eventRound === round;
    return roundStart != null && Number(event.ts ?? 0) >= Number(roundStart.ts ?? 0);
  };
  const roundEvents = taskEvents.filter(belongsToRound);
  const reviewEvent = [...roundEvents].reverse().find((event) => event.type === 'round.review.completed');
  const reviewStarted = [...roundEvents].reverse().find((event) => event.type === 'round.review.started');
  const newReviewStarted = Boolean(reviewStarted && (!reviewEvent || Number(reviewStarted.ts) > Number(reviewEvent.ts)));
  const reviewing = running && runtime.role === 'reviewer' && newReviewStarted;
  const currentReview = newReviewStarted ? undefined : reviewEvent;
  const rawReviewStatus = String(currentReview?.status ?? '').toLowerCase();
  const viewReviewStatus = matchesView ? String(view?.review.status ?? '').toLowerCase() : '';
  const reviewSkipped = currentReview?.review_skipped === true || rawReviewStatus === 'skipped';
  const selfReview = currentReview?.review_source === 'engineer_self_review';
  const independentReview = !currentReview?.review_source || currentReview.review_source === 'reviewer';
  // Snapshot review/achievement can survive a new round. Only a verdict tied
  // to this task and round confirms that this round passed review.
  const passed = !reviewSkipped && independentReview && rawReviewStatus === 'done';
  const needsWork = !reviewSkipped && (NEEDS_WORK.test(rawReviewStatus)
    || (!currentReview && !reviewing && NEEDS_WORK.test(viewReviewStatus)));
  const reviewState: ProgressEstimate['review']['state'] = reviewing ? 'running'
    : reviewSkipped ? 'skipped' : passed ? 'passed'
    : selfReview && rawReviewStatus === 'done' ? 'self_checked'
    : needsWork ? 'needs_work' : 'pending';
  const reviewLabels = {
    pending: text('结论待核对', 'Conclusion awaiting review'),
    running: text('正在核对证据', 'Checking the evidence'),
    passed: text('本轮工作通过核对', 'This round passed review'),
    needs_work: text('仍需补充或修改', 'More work is needed'),
    skipped: text('本轮未做审查', 'This round was not reviewed'),
    self_checked: text('执行者已自查，待独立核对', 'Self-checked; independent review pending'),
  };
  const reviewDefaults = {
    pending: text('尚无能对应到当前工作项和轮次的通过记录。', 'No passing review is linked to the current work item and round.'),
    running: text('审阅者正在核对本轮结果及其证据。', 'The Reviewer is checking this round’s results and evidence.'),
    passed: text('通过记录仅适用于本轮提交的工作与证据。', 'The passing verdict applies to the work and evidence submitted in this round.'),
    needs_work: text('审查发现仍有待处理的问题，需要继续工作。', 'The review found issues that require further work.'),
    skipped: text('执行结束并不代表证据已经通过核对。', 'The end of execution does not establish that the evidence passed review.'),
    self_checked: text('这条记录来自执行者自查，尚无本轮独立审查通过记录。', 'This record is a self-check by the worker; no independent passing review is recorded for this round.'),
  };
  const review = {
    state: reviewState,
    label: reviewLabels[reviewState],
    detail: String(currentReview?.reason || (needsWork && view?.review.reason) || reviewDefaults[reviewState]),
    scope: text(`当前工作项${round > 0 ? ` · 第 ${round} 轮` : ''}。整体目标是否成立，仍须核对其完整验收条件。`,
      `Current work item${round > 0 ? ` · Round ${round}` : ''}. The overall goal still requires checking its full acceptance criteria.`),
  };
  const handoff = [...roundEvents].reverse().find((event) => event.type === 'round.main.completed');
  const reviewCheckpointStatus: ProgressCheckpoint['status'] = reviewState === 'passed' ? 'done'
    : reviewState === 'running' ? 'active' : reviewState === 'needs_work' ? 'blocked' : 'pending';
  const checkpoints: ProgressCheckpoint[] = [
    { id: 'plan', label: text('工作项已记录', 'Work item recorded'), detail: current?.title || view?.mission.title || text('等待任务', 'Waiting for a task'), status: current || matchesView ? 'done' : 'pending' },
    { id: 'start', label: text('开始执行', 'Execution started'), detail: startedAt !== null ? text('有任务启动时间记录', 'A task start time is recorded') : text('尚无启动记录', 'No start is recorded'), status: startedAt !== null ? 'done' : 'pending' },
    { id: 'handoff', label: text('本轮执行已结束', 'Round execution ended'), detail: handoff ? text('已记录本轮执行结束，结果是否可用仍需核对', 'The round’s execution ended; its results still need to be checked') : text('尚无本轮执行结束记录', 'No end of execution is recorded for this round'), status: handoff ? 'done' : running && runtime.role === 'engineer' ? 'active' : 'pending' },
    { id: 'review', label: text('核对本轮证据', 'Check this round’s evidence'), detail: review.label, status: reviewCheckpointStatus },
  ];
  const workItemComplete = Boolean(current && DONE.has(current.status)) || (matchesView && ['complete', 'completed', 'done'].includes(view?.mission.status ?? ''));
  const etaUnavailableReason = !snapshot.daemon.alive
    ? text('运行已停止，无法预计完成时间。', 'Execution has stopped; a finish time is unavailable.')
    : openEnded ? text('开放研究的剩余工作量尚不确定，无法可靠预计整体完成时间。', 'Open research has no known remaining workload, so its overall finish time cannot be reliably estimated.')
    : needsWork ? text('审查要求继续补充或调整工作范围，暂无法预计完成时间。', 'Review requires further work or a scope change; a finish time is unavailable.')
    : workItemComplete ? text('当前工作项已结束；这不提供整体目标的完成时间。', 'The current work item has ended; this does not establish when the overall goal will be reached.')
    : text('当前记录没有可比较的固定工作量基线，暂无法可靠预计完成时间。', 'The current records contain no comparable, fixed-workload baseline for a reliable finish-time estimate.');

  return {
    workCompletion: dag.length ? completed / dag.length : null,
    workScope: text('仅统计已记录的工作项；清单可随研究增减，完成比例不代表整体目标已成立。', 'Counts recorded work items only. The list can change during research; its completion fraction does not establish the overall goal.'),
    currentTask: current?.title || view?.mission.title || text('等待新任务', 'Waiting for a new task'),
    currentTaskId: taskId,
    currentObjective: current?.objective || (matchesView ? view?.mission.objective : '') || '',
    currentStep: running && latest ? eventTitle(latest, locale) : workStatusLabel(runtime, locale),
    currentDetail: latest ? eventDetail(latest, 700) : current?.objective || '',
    currentEvent: latest ?? null,
    runtime,
    elapsedSeconds,
    etaUnavailableReason,
    checkpoints,
    completedTasks: completed,
    totalTasks: dag.length,
    pendingTasks: pending,
    openEnded,
    review,
    acceptanceCriteria: current?.acceptance_check || dag.find((item) => item.id === taskId)?.acceptance_check || '',
    nextAction: current?.pending_question || String(currentReview?.next_action || ''),
    taskEvents: activityEvents,
  };
}
