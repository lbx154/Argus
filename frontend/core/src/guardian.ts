import type { EventMsg } from './types.js';
import { canonicalEventType, EVENT_TYPES } from './eventCatalog.js';

export type AlertTone = 'block' | 'warn';
export interface GuardianAlert {
  tone: AlertTone;
  text: string;
  kind?: 'budget' | 'validation';
}

const ALERT_TYPES: Record<string, AlertTone> = {
  [EVENT_TYPES.LIFE_LIFECYCLE_BLOCK]: 'block',
  [EVENT_TYPES.ROUND_REVIEWER_BACKEND_FAILURE]: 'block',
  [EVENT_TYPES.LIFE_BUDGET_PAUSE]: 'warn',
  [EVENT_TYPES.ROUND_STALL]: 'warn',
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

function alertOf(event: EventMsg, zh: boolean): GuardianAlert | null {
  const type = canonicalEventType(event.canonical_type ?? event.type);
  const say = (en: string, chinese: string) => (zh ? chinese : en);
  if (event.event_validation?.status === 'invalid') {
    if (type === EVENT_TYPES.ROLE_SESSION_TURN) return null;
    // The record names the malformed event; the banner says what it means.
    return {
      tone: 'warn',
      kind: 'validation',
      text: say("Argus skipped a record it couldn't read.", 'Argus 跳过了一条读不懂的记录。'),
    };
  }
  if (BUDGET_ALARM_TYPES.has(type)) {
    return {
      tone: 'block',
      kind: 'budget',
      text: type === EVENT_TYPES.BUDGET_UNPRICED_BLOCKED
        ? say('Provider charges are still being reconciled; new model calls wait until then.', '模型费用还在对账，新的模型调用先等一等。')
        : say('The budget is used up, so new model calls are on hold; raise the budget to continue.', '预算用完了，新的模型调用先停下；提高预算后会继续。'),
    };
  }
  const tone = event.operator_alert === true ? 'block' : ALERT_TYPES[type];
  if (!tone) return null;
  return {
    tone,
    text: String(event.text ?? event.reason ?? '').trim() || say('Something needs your attention.', '有件事需要你看一下。'),
  };
}

export function activeGuardianAlert(events: EventMsg[], locale: 'en' | 'zh-CN' = 'en'): GuardianAlert | null {
  let alert: GuardianAlert | null = null;
  for (const event of events) {
    const type = canonicalEventType(event.canonical_type ?? event.type);
    const next = alertOf(event, locale === 'zh-CN');
    if (next) alert = next;
    else if (alert?.kind === 'validation') alert = null;
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
