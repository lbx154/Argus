import type { EventMsg } from './types.js';

export type AlertTone = 'block' | 'warn';
export interface GuardianAlert {
  tone: AlertTone;
  text: string;
}

const ALERT_TYPES: Record<string, AlertTone> = {
  'life.lifecycle.block': 'block',
  'round.reviewer_backend_failure': 'block',
  'life.budget.pause': 'warn',
  'round.stall': 'warn',
  'round.escalated': 'warn',
  'life.planner.stall_escalation': 'warn',
};

const RESOLVING_TYPES = new Set([
  'life.mission.started',
  'mission.started',
  'round.main.completed',
  'life.mission.completed',
  'mission.completed',
  'loop.completed',
  'round.started',
  'round.start',
  'ui.operator',
]);

function alertOf(event: EventMsg): GuardianAlert | null {
  const type = String(event.type ?? '');
  const tone = event.operator_alert === true ? 'block' : ALERT_TYPES[type];
  if (!tone) return null;
  return {
    tone,
    text: String(event.text ?? event.reason ?? type).trim(),
  };
}

export function activeGuardianAlert(events: EventMsg[]): GuardianAlert | null {
  let alert: GuardianAlert | null = null;
  for (const event of events) {
    const next = alertOf(event);
    if (next) alert = next;
    else if (alert && RESOLVING_TYPES.has(String(event.type ?? ''))) alert = null;
  }
  return alert;
}
