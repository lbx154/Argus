import type { EventMsg } from './types.js';
import { canonicalEventType, EVENT_TYPES } from './eventCatalog.js';

export type AlertTone = 'block' | 'warn';
export interface GuardianAlert {
  tone: AlertTone;
  text: string;
  kind?: 'budget';
}

const ALERT_TYPES: Record<string, AlertTone> = {
  [EVENT_TYPES.LIFE_LIFECYCLE_BLOCK]: 'block',
  [EVENT_TYPES.ROUND_REVIEWER_BACKEND_FAILURE]: 'block',
  [EVENT_TYPES.LIFE_BUDGET_PAUSE]: 'warn',
  [EVENT_TYPES.ROUND_STALL]: 'warn',
  [EVENT_TYPES.ROUND_ESCALATED]: 'warn',
  [EVENT_TYPES.LIFE_PLANNER_STALL_ESCALATION]: 'warn',
};
const BUDGET_ALARM_TYPES = new Set<string>([
  EVENT_TYPES.BUDGET_RESERVATION_DENIED,
  EVENT_TYPES.BUDGET_UNPRICED_BLOCKED,
]);

const RESOLVING_TYPES = new Set([
  EVENT_TYPES.LIFE_MISSION_STARTED,
  EVENT_TYPES.ROUND_MAIN_COMPLETED,
  EVENT_TYPES.LIFE_MISSION_COMPLETED,
  EVENT_TYPES.LOOP_DONE,
  EVENT_TYPES.ROUND_START,
  'ui.operator',
]);
const BUDGET_RESOLVING_TYPES = new Set<string>([
  EVENT_TYPES.BUDGET_RESERVATION_CREATED,
  EVENT_TYPES.PROVIDER_REQUEST_STARTED,
]);

function alertOf(event: EventMsg): GuardianAlert | null {
  const type = canonicalEventType(event.canonical_type ?? event.type);
  if (event.event_validation?.status === 'invalid') {
    return {
      tone: 'warn',
      text: `invalid event ${type || 'unknown'}: ${event.event_validation.errors.join('; ')}`,
    };
  }
  if (BUDGET_ALARM_TYPES.has(type)) {
    const reason = String(event.reason ?? event.text ?? type).trim();
    return {
      tone: 'block',
      kind: 'budget',
      text: `Budget exhausted or blocked — ${reason}`,
    };
  }
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
    const type = canonicalEventType(event.canonical_type ?? event.type);
    const next = alertOf(event);
    if (next) alert = next;
    else if (
      alert?.kind === 'budget'
      && BUDGET_RESOLVING_TYPES.has(type)
    ) alert = null;
    else if (
      alert
      && alert.kind !== 'budget'
      && RESOLVING_TYPES.has(type)
    ) alert = null;
  }
  return alert;
}
