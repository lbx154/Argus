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
const INCOMPLETE_STATUSES = new Set([
  'research_incomplete',
  'paused_no_breakthrough',
  'exhausted_current_methods',
]);
const STALLED_STATUSES = new Set(['no_progress', 'max_rounds']);
const BLOCKED_STATUSES = new Set(['blocked', 'infra_blocked']);
const FAILED_STATUSES = new Set(['error', 'failed', 'supervisor_error']);

const PRESENTATION: Record<MissionOutcomeClass, Omit<MissionOutcomePresentation, 'outcomeClass' | 'kind' | 'label' | 'technical'>> = {
  completed: { glyph: '🎉', tone: 'ok', missionStatus: 'complete' },
  incomplete: { glyph: '◌', tone: 'warn', missionStatus: 'incomplete' },
  stalled: { glyph: '⏸', tone: 'warn', missionStatus: 'stalled' },
  blocked: { glyph: '⛔', tone: 'err', missionStatus: 'blocked' },
  failed: { glyph: '💥', tone: 'err', missionStatus: 'failed' },
  ended: { glyph: '■', tone: 'info', missionStatus: 'ended' },
};

// The same sentences the Python mission view writes for an English session
// (argus_skill/core/mission_view/_wording.py); keep the two in step.
const KINDS: Record<MissionOutcomeClass, MissionOutcomeKind> = {
  completed: 'mission_completed',
  incomplete: 'mission_incomplete',
  stalled: 'mission_stalled',
  blocked: 'mission_blocked',
  failed: 'mission_failed',
  ended: 'mission_ended',
};

const LABELS: Record<MissionOutcomeKind, string> = {
  mission_completed: 'The task was completed.',
  mission_continued: 'The task was completed and the project continues with the next one.',
  mission_certified: 'The final submission was checked and approved.',
  mission_incomplete: 'The task stopped with work still remaining.',
  mission_stalled: 'The task stopped because recent rounds made no useful progress.',
  mission_blocked: 'The task cannot continue until something outside it is resolved.',
  mission_failed: 'The task could not be completed.',
  mission_paused: 'The task was paused before it finished because {why}; its progress is saved and it can be resumed.',
  mission_ended: 'The task ended without a recorded outcome.',
};

// Why a call stopped, as a clause; mirrors argus_skill/core/stop_kinds.py.
const STOP_KIND_CLAUSES: Record<string, string> = {
  budget_exhausted: 'the project reached its budget limit',
  provider_cooldown: 'the model service asked Argus to wait before calling again',
  provider_fence: 'the model provider is not accepting calls right now',
  daemon_shutdown: 'Argus was stopped',
  operator_pause: 'the operator paused the work',
  operator_abort: 'the operator cancelled the task',
  backend_unavailable: 'the model service was unavailable',
  transient_error: 'a temporary error interrupted the call',
  permanent_error: 'an error that will not clear on its own stopped the call',
};

const PAUSE_STATUS_STOP_KINDS: Record<string, string> = {
  paused_budget: 'budget_exhausted',
  paused_provider_cooldown: 'provider_cooldown',
  paused_provider_fence: 'provider_fence',
  paused_daemon_shutdown: 'daemon_shutdown',
  paused_operator: 'operator_pause',
};

function normalizedString(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

function pauseClause(status: string, stopKind: string): string {
  if (status === 'paused_external_work') return 'work outside Argus had to finish first';
  const viaStatus = PAUSE_STATUS_STOP_KINDS[status];
  if (viaStatus) return STOP_KIND_CLAUSES[viaStatus];
  if (STOP_KIND_CLAUSES[stopKind]) return STOP_KIND_CLAUSES[stopKind];
  if (status.startsWith('paused_')) return 'the work was paused';
  return 'the work was interrupted';
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
  if (event.success === true || COMPLETED_STATUSES.has(status)) return 'completed';
  if (INCOMPLETE_STATUSES.has(status)) return 'incomplete';
  if (STALLED_STATUSES.has(status)) return 'stalled';
  if (BLOCKED_STATUSES.has(status)) return 'blocked';
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
): MissionOutcomePresentation {
  if (event.success === true && event.campaign_continues === true) {
    return {
      outcomeClass: 'completed',
      kind: 'mission_continued',
      label: LABELS.mission_continued,
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
  let label = LABELS[kind];
  let technical = '';
  if (outcomeClass === 'completed' && event.final_submission_certified === true) {
    kind = 'mission_certified';
    label = LABELS.mission_certified;
  } else if (outcomeClass === 'ended') {
    // An unrecognised status is a technical fact, not part of the sentence.
    technical = String(event.status ?? '').trim();
    if (status.startsWith('paused_') || event.resumable === true) {
      const outcome = event.outcome && typeof event.outcome === 'object' && !Array.isArray(event.outcome)
        ? event.outcome as Record<string, unknown>
        : {};
      const stopKind = normalizedString(event.stop_kind) || normalizedString(outcome.interruption_kind);
      kind = 'mission_paused';
      label = LABELS.mission_paused.replace('{why}', pauseClause(status, stopKind));
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
