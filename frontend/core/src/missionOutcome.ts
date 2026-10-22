import type { EventMsg, MissionOutcomeDimensions } from './types.js';

export type MissionOutcomeClass =
  | 'completed'
  | 'incomplete'
  | 'stalled'
  | 'blocked'
  | 'failed'
  | 'ended';

export type MissionOutcomeTone = 'ok' | 'warn' | 'err' | 'info';

/**
 * The stable code for how a task ended. It mirrors the `kind` the Python
 * mission view writes on the engineer role and the timeline row, so a
 * frontend can localize by code rather than by matching the sentence.
 */
export type MissionOutcomeKind =
  | 'mission_completed'
  | 'mission_continued'
  | 'mission_certified'
  | 'mission_incomplete'
  | 'mission_stalled'
  | 'mission_blocked'
  | 'mission_failed'
  | 'mission_paused'
  | 'mission_ended';

export interface MissionOutcomePresentation {
  outcomeClass: MissionOutcomeClass;
  kind: MissionOutcomeKind;
  label: string;
  glyph: string;
  tone: MissionOutcomeTone;
  missionStatus: 'complete' | 'continued' | 'incomplete' | 'stalled' | 'blocked' | 'failed' | 'ended';
  /** A raw status the projection does not recognise; never part of the sentence. */
  technical: string;
}

type MissionOutcomeEvent = EventMsg;

const COMPLETED_STATUSES = new Set(['done', 'success', 'completed']);
const STALLED_STATUSES = new Set(['no_progress', 'max_rounds']);
const FAILED_STATUSES = new Set(['error', 'failed', 'supervisor_error']);

// The park reason each status an older release wrote names; mirrors argus/core/stop_kinds.py.
const LEGACY_PARK_REASONS: Record<string, string> = {
  paused: '',
  paused_budget: 'budget_exhausted',
  paused_cost: 'cost_unreconciled',
  paused_provider_cooldown: 'provider_cooldown',
  paused_provider_fence: 'provider_fence',
  paused_daemon_shutdown: 'daemon_shutdown',
  paused_operator: 'operator_pause',
  paused_external_work: 'external_work',
  research_incomplete: 'research_open',
  paused_no_breakthrough: 'research_open',
  exhausted_current_methods: 'research_open',
  infra_blocked: 'infra',
};

/** Why a row or event with `status` is parked, or `null` when it is not. A `parked`
 * status carries its reason in `recorded`; an older status names its own. */
export function parkReason(status: unknown, recorded?: unknown): string | null {
  const value = normalizedString(status);
  if (value === 'parked') return normalizedString(recorded);
  if (Object.hasOwn(LEGACY_PARK_REASONS, value)) return LEGACY_PARK_REASONS[value];
  return value.startsWith('paused_') ? '' : null;
}

/** Whether an event status reads as a pause: `parked` or an older `paused_*`. */
export function isPauseStatus(status: unknown): boolean {
  const value = normalizedString(status);
  return value === 'parked' || value.startsWith('paused_');
}

const PRESENTATION: Record<MissionOutcomeClass, Omit<MissionOutcomePresentation, 'outcomeClass' | 'kind' | 'label' | 'technical'>> = {
  completed: { glyph: '🎉', tone: 'ok', missionStatus: 'complete' },
  incomplete: { glyph: '◌', tone: 'warn', missionStatus: 'incomplete' },
  stalled: { glyph: '⏸', tone: 'warn', missionStatus: 'stalled' },
  blocked: { glyph: '⛔', tone: 'err', missionStatus: 'blocked' },
  failed: { glyph: '💥', tone: 'err', missionStatus: 'failed' },
  ended: { glyph: '■', tone: 'info', missionStatus: 'ended' },
};

// The same sentences the Python mission view writes for an English session
// (argus/core/mission_view/_wording.py); keep the two in step.
const KINDS: Record<MissionOutcomeClass, MissionOutcomeKind> = {
  completed: 'mission_completed',
  incomplete: 'mission_incomplete',
  stalled: 'mission_stalled',
  blocked: 'mission_blocked',
  failed: 'mission_failed',
  ended: 'mission_ended',
};

type Pair = readonly [en: string, zh: string];

const LABELS: Record<MissionOutcomeKind, Pair> = {
  mission_completed: ['The task was completed.', '任务已完成。'],
  mission_continued: ['The task was completed and the project continues with the next one.', '任务已完成，项目继续进行下一项。'],
  mission_certified: ['The final submission was checked and approved.', '最终投稿经核对后获得认可。'],
  mission_incomplete: ['The task stopped with work still remaining.', '任务停止时仍有工作未完成。'],
  mission_stalled: ['The task stopped because recent rounds made no useful progress.', '最近几轮没有取得实质进展，任务已停止。'],
  mission_blocked: ['The task cannot continue until something outside it is resolved.', '任务需要先解决外部问题才能继续。'],
  mission_failed: ['The task could not be completed.', '任务没能完成。'],
  mission_paused: ['The task was paused before it finished because {why}; its progress is saved and it can be resumed.', '任务在完成前暂停，因为{why}；进度已保存，可以继续。'],
  mission_ended: ['The task ended without a recorded outcome.', '任务已结束，没有记录到明确的结果。'],
};

// Why work stopped or waits, by stop kind or park reason, as a clause; mirrors
// argus/core/stop_kinds.py, the one table either side keeps.
const CLAUSES: Record<string, Pair> = {
  budget_exhausted: ['the project reached its budget limit', '项目达到了预算上限'],
  cost_unreconciled: ['provider charges are awaiting reconciliation or explicit risk approval', '模型费用尚待核对或明确的风险确认'],
  provider_cooldown: ['the model service asked Argus to wait before calling again', '模型服务要求 Argus 稍后再调用'],
  provider_fence: ['the model provider is not accepting calls right now', '模型服务当前不接受调用'],
  daemon_shutdown: ['the Argus background service stopped or restarted', 'Argus 后台服务停止或重启了'],
  operator_pause: ['you paused the work', '你暂停了工作'],
  operator_abort: ['you cancelled the task', '你取消了这项任务'],
  backend_unavailable: ['the model service was unavailable', '模型服务不可用'],
  transient_error: ['a temporary error interrupted the call', '一次临时错误中断了调用'],
  permanent_error: ['an error that will not clear on its own stopped the call', '一个不会自行消失的错误中止了调用'],
  external_work: ['work outside Argus had to finish first', '需要先等 Argus 之外的工作完成'],
  operator_question: ['a question is waiting for your answer', '有个问题在等你回答'],
  infra: ['the program that runs the work could not start', '运行环境无法启动'],
};

function normalizedString(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

/** Why work stopped or waits, by its stop kind or park reason; empty when the record does not say. */
export function stopClause(kind: unknown, zh: boolean): string {
  const key = normalizedString(kind);
  return Object.hasOwn(CLAUSES, key) ? CLAUSES[key][zh ? 1 : 0] : '';
}

function pauseClause(status: string, recorded: unknown, stopKind: string, zh: boolean): string {
  const reason = parkReason(status, recorded);
  return stopClause(reason, zh) || stopClause(stopKind, zh) ||
    (reason === null ? (zh ? '工作被打断了' : 'the work was interrupted') : (zh ? '工作被暂停了' : 'the work was paused'));
}

function normalizedOutcomeClass(value: unknown): MissionOutcomeClass | null {
  const outcomeClass = normalizedString(value);
  switch (outcomeClass) {
    case 'completed':
    case 'incomplete':
    case 'stalled':
    case 'blocked':
    case 'failed':
    case 'ended':
      return outcomeClass;
    default:
      return null;
  }
}

function derivedOutcomeClass(event: MissionOutcomeEvent): MissionOutcomeClass {
  const status = normalizedString(event.status);
  const park = parkReason(status, event.park_reason);
  if (event.success === true || COMPLETED_STATUSES.has(status)) return 'completed';
  if (park === 'research_open') return 'incomplete';
  if (STALLED_STATUSES.has(status)) return 'stalled';
  if (status === 'blocked' || park === 'infra') return 'blocked';
  if (FAILED_STATUSES.has(status)) return 'failed';
  return 'ended';
}

export function missionOutcomeDimensions(
  event: MissionOutcomeEvent,
): MissionOutcomeDimensions {
  const raw = event.outcome;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const row = raw as Record<string, unknown>;
    return {
      execution_status: normalizedString(row.execution_status) || derivedOutcomeClass(event),
      review_status: normalizedString(row.review_status) || 'not_assessed',
      stage_certification: normalizedString(row.stage_certification) || 'not_assessed',
      interruption_kind: normalizedString(row.interruption_kind) || 'none',
      resumable: row.resumable === true,
    };
  }
  return {
    execution_status: derivedOutcomeClass(event),
    review_status: 'not_assessed',
    stage_certification: 'not_assessed',
    interruption_kind: normalizedString(event.stop_kind) || 'none',
    resumable: event.resumable === true,
  };
}

export function outcomeDimensionSummary(
  outcome: Partial<MissionOutcomeDimensions> | null | undefined,
): string[] {
  if (!outcome?.execution_status) return [];
  const stageLabels: Record<string, string> = {
    certified: 'Stage approved',
    not_certified: 'Stage not approved',
    revoked: 'Stage approval revoked',
    intentionally_skipped: 'Stage decision not needed',
    deferred: 'Stage decision pending',
    not_assessed: '',
  };
  return [
    `execution=${outcome.execution_status}`,
    outcome.review_status && outcome.review_status !== 'not_assessed'
      ? `review=${outcome.review_status}` : '',
    outcome.stage_certification ? stageLabels[outcome.stage_certification] : '',
    outcome.interruption_kind && outcome.interruption_kind !== 'none'
      ? `interrupt=${outcome.interruption_kind}` : '',
    outcome.resumable ? 'resumable=yes' : '',
  ].filter(Boolean);
}

export function missionOutcomePresentation(
  event: MissionOutcomeEvent,
  locale: 'en' | 'zh-CN' = 'en',
): MissionOutcomePresentation {
  const zh = locale === 'zh-CN';
  const say = (kind: MissionOutcomeKind) => LABELS[kind][zh ? 1 : 0];
  if (event.success === true && event.campaign_continues === true) {
    return {
      outcomeClass: 'completed',
      kind: 'mission_continued',
      label: say('mission_continued'),
      glyph: '↻',
      tone: 'info',
      missionStatus: 'continued',
      technical: '',
    };
  }
  const outcomeClass = normalizedOutcomeClass(event.outcome_class) ?? derivedOutcomeClass(event);
  const status = normalizedString(event.status);
  const base = PRESENTATION[outcomeClass];
  let kind: MissionOutcomeKind = KINDS[outcomeClass];
  let label = say(kind);
  let technical = '';
  if (outcomeClass === 'completed' && event.final_submission_certified === true) {
    kind = 'mission_certified';
    label = say('mission_certified');
  } else if (outcomeClass === 'ended') {
    // An unrecognised status is a technical fact, not part of the sentence.
    technical = String(event.status ?? '').trim();
    if (isPauseStatus(status) || event.resumable === true) {
      const outcome = event.outcome && typeof event.outcome === 'object' && !Array.isArray(event.outcome)
        ? event.outcome as Record<string, unknown>
        : {};
      const stopKind = normalizedString(event.stop_kind) || normalizedString(outcome.interruption_kind);
      kind = 'mission_paused';
      label = say('mission_paused').replace('{why}', pauseClause(status, event.park_reason, stopKind, zh));
    }
  }
  return {
    outcomeClass,
    kind,
    label,
    glyph: base.glyph,
    tone: base.tone,
    missionStatus: base.missionStatus,
    technical,
  };
}
