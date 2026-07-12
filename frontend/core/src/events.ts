import type { EventMsg } from './types.js';
import { canonicalEventType, EVENT_TYPES } from './eventCatalog.js';

function stableJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  const rec = value as Record<string, unknown>;
  return `{${Object.keys(rec)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(rec[key])}`)
    .join(',')}}`;
}

function hash(text: string): string {
  let value = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 0x01000193);
  }
  return (value >>> 0).toString(36);
}

/**
 * A replay-safe event identity. Array positions are deliberately excluded:
 * REST replay and WebSocket replay see the same row at different positions.
 */
export function eventKey(event: EventMsg): string {
  const explicit = event.event_id ?? event.id ?? event.seq ?? event._offset;
  const type = String(event.type ?? 'event');
  if (explicit !== undefined && explicit !== null && explicit !== '') {
    return `${type}-${String(explicit)}`;
  }
  const ts = String(event.ts ?? event.time ?? '');
  return `${type}-${ts}-${hash(stableJson(event))}`;
}

export function isReasoning(event: EventMsg): boolean {
  return event.type === EVENT_TYPES.ENGINEER_PROGRESS && event.kind === 'reasoning';
}

/** Reviewer backends stream their machine-readable verdict through the same
 * assistant-message channel used for human progress prose.  The structured
 * payload is protocol, not transcript content: the settled
 * `round.review.completed` event renders the useful verdict separately. */
export function isStructuredAgentPayload(event: EventMsg): boolean {
  if (event.type !== EVENT_TYPES.ENGINEER_PROGRESS) return false;
  if (!['assistant_message', 'agent_message', 'message'].includes(String(event.kind ?? ''))) {
    return false;
  }
  const role = String(event.agent_layer ?? event.actor ?? '');
  const text = String(event.text ?? '').trimStart();
  if (!text.startsWith('{')) return false;
  return role === 'reviewer' || role === 'planner';
}

export function mergeFragment(accumulator: string, fragment: string): string {
  const current = (accumulator || '').trim();
  const next = (fragment || '').trim();
  if (!current) return next;
  if (!next || current.includes(next)) return current;
  if (next.includes(current)) return next;
  return `${current}\n${next}`;
}

export const EVENT_VIEW_FILTERS = ['all', 'attention', 'milestones', 'messages'] as const;
export type EventViewFilter = (typeof EVENT_VIEW_FILTERS)[number];

export interface EventPresentation {
  role?: string;
  label?: string;
  text?: string;
  tone?: string;
  rule?: boolean;
  reasoning?: boolean;
}

const MILESTONE_TYPES = new Set([
  EVENT_TYPES.LIFE_MISSION_STARTED,
  EVENT_TYPES.LIFE_MISSION_COMPLETED,
  EVENT_TYPES.LIFE_MISSION_FAILED,
  EVENT_TYPES.LOOP_START, EVENT_TYPES.LOOP_DONE,
  EVENT_TYPES.LIFE_PLANNER_VERDICT, 'final.report.ready', 'pptx.report.ready',
  'plan.completed', EVENT_TYPES.LIFE_BUDGET_PAUSE,
  EVENT_TYPES.LIFE_LIFECYCLE_BLOCK,
]);

/** Shared filter/search semantics so Web and Ink surface the same event subset. */
export function eventMatchesView(
  event: EventMsg,
  presentation: EventPresentation,
  filter: EventViewFilter = 'all',
  query = '',
): boolean {
  const type = canonicalEventType(event.canonical_type ?? event.type);
  const kind = String(event.kind ?? '');
  if (
    filter === 'attention' &&
    !['warn', 'err'].includes(String(presentation.tone ?? '')) &&
    event.operator_alert !== true
  ) return false;
  if (
    filter === 'milestones' &&
    !(presentation.rule && !type.startsWith('ui.')) &&
    !MILESTONE_TYPES.has(type)
  ) return false;
  if (
    filter === 'messages' &&
    presentation.tone !== 'bright' &&
    !['assistant_message', 'agent_message', 'message'].includes(kind) &&
    !['ui.operator', 'ui.argus'].includes(type)
  ) return false;

  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  const fields = [
    type,
    kind,
    presentation.role,
    presentation.label,
    presentation.text,
    event.title,
    event.objective,
    event.text,
    event.summary,
    event.reason,
    event.error,
    event.status,
    event.action_summary,
    event.command,
    event.path,
    Array.isArray(event.tags) ? event.tags.join(' ') : event.tags,
  ];
  return fields.some((value) => String(value ?? '').toLocaleLowerCase().includes(needle));
}
