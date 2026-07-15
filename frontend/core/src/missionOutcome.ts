import type { EventMsg } from './types.js';

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
