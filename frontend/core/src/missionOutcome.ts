import type { EventMsg, MissionOutcomeDimensions } from './types.js';

export type MissionOutcomeClass =
  | 'completed'
  | 'incomplete'
  | 'stalled'
  | 'blocked'
  | 'failed'
  | 'ended';

export type MissionOutcomeTone = 'ok' | 'warn' | 'err' | 'info';

export interface MissionOutcomePresentation {
  outcomeClass: MissionOutcomeClass;
  label: string;
  glyph: string;
  tone: MissionOutcomeTone;
  missionStatus: 'complete' | 'incomplete' | 'stalled' | 'blocked' | 'failed' | 'ended';
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

const PRESENTATION: Record<MissionOutcomeClass, Omit<MissionOutcomePresentation, 'outcomeClass' | 'label'>> = {
  completed: { glyph: '🎉', tone: 'ok', missionStatus: 'complete' },
  incomplete: { glyph: '◌', tone: 'warn', missionStatus: 'incomplete' },
  stalled: { glyph: '⏸', tone: 'warn', missionStatus: 'stalled' },
  blocked: { glyph: '⛔', tone: 'err', missionStatus: 'blocked' },
  failed: { glyph: '💥', tone: 'err', missionStatus: 'failed' },
  ended: { glyph: '■', tone: 'info', missionStatus: 'ended' },
};

const LABELS: Record<MissionOutcomeClass, string> = {
  completed: 'Mission completed',
  incomplete: 'Mission incomplete',
  stalled: 'Mission stalled',
  blocked: 'Mission blocked',
  failed: 'Mission failed',
  ended: 'Mission ended',
};

function normalizedString(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
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
      scientific_decision: normalizedString(row.scientific_decision) || 'undecided',
      failure_source: normalizedString(row.failure_source),
      interruption_kind: normalizedString(row.interruption_kind) || 'none',
      resumable: row.resumable === true,
    };
  }
  return {
    execution_status: derivedOutcomeClass(event),
    review_status: 'not_assessed',
    stage_certification: 'not_assessed',
    scientific_decision: 'undecided',
    failure_source: '',
    interruption_kind: normalizedString(event.stop_kind) || 'none',
    resumable: event.resumable === true,
  };
}

export function outcomeDimensionSummary(
  outcome: Partial<MissionOutcomeDimensions> | null | undefined,
): string[] {
  if (!outcome?.execution_status) return [];
  return [
    `execution=${outcome.execution_status}`,
    outcome.review_status && outcome.review_status !== 'not_assessed'
      ? `review=${outcome.review_status}` : '',
    outcome.stage_certification && outcome.stage_certification !== 'not_assessed'
      ? `stage=${outcome.stage_certification}` : '',
    outcome.scientific_decision && outcome.scientific_decision !== 'undecided'
      ? `science=${outcome.scientific_decision}` : '',
    outcome.failure_source ? `failure=${outcome.failure_source}` : '',
    outcome.interruption_kind && outcome.interruption_kind !== 'none'
      ? `interrupt=${outcome.interruption_kind}` : '',
    outcome.resumable ? 'resumable=yes' : '',
  ].filter(Boolean);
}

export function missionOutcomePresentation(
  event: MissionOutcomeEvent,
): MissionOutcomePresentation {
  const outcomeClass = normalizedOutcomeClass(event.outcome_class) ?? derivedOutcomeClass(event);
  const status = String(event.status ?? '').trim();
  const base = PRESENTATION[outcomeClass];
  return {
    outcomeClass,
    label: outcomeClass === 'ended' && status ? `Mission ended · ${status}` : LABELS[outcomeClass],
    glyph: base.glyph,
    tone: base.tone,
    missionStatus: base.missionStatus,
  };
}
