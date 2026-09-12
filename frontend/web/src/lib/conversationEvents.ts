import type { EventMsg } from '../api';
import { mergeFragment, type FragmentMode } from '../../../core/src/events';

interface TranscriptTurn {
  ts: number;
  role: string;
  text: string;
  message_id?: string;
  mission_result?: boolean;
  item_id?: string;
  success?: boolean;
  summary?: string;
  delivery_id?: string;
  delivery?: unknown;
  steps?: unknown;
}

const LOCAL_REQUEST_FIELD = 'local_request_id';

const hasSteps = (value: unknown): value is unknown[] => Array.isArray(value) && value.length > 0;

/**
 * Show the work behind a reply while it is still happening: the tool steps
 * streamed so far become part of the Argus row (created empty when no words
 * have arrived yet), and ``live`` tells the renderer to keep them unfolded.
 */
export function mergeOptimisticManagerSteps(
  localEvents: EventMsg[],
  sid: string,
  requestId: number,
  steps: unknown[],
  nowMs = Date.now(),
  live = true,
): EventMsg[] {
  const index = localEvents.findIndex((event) => (
    event.type === 'ui.argus'
    && Number(event[LOCAL_REQUEST_FIELD]) === requestId
  ));
  if (index < 0) {
    if (!steps.length) return localEvents;
    return [
      ...localEvents,
      {
        type: 'ui.argus',
        agent_layer: 'manager',
        text: '',
        ts: nowMs / 1_000,
        event_id: `local-${sid}-${requestId}-argus`,
        message_id: `local-${requestId}-argus`,
        fragment_mode: 'snapshot',
        steps,
        live,
        [LOCAL_REQUEST_FIELD]: requestId,
      },
    ];
  }
  const next = [...localEvents];
  next[index] = { ...next[index], ...(steps.length ? { steps } : {}), live };
  return next;
}

/**
 * The turn is over (answered, failed or stopped): nothing is live any more,
 * and a step still open is recorded as stopped at ``nowMs`` so its clock
 * does not keep running on screen.
 */
export function settleOptimisticManagerTurn(
  localEvents: EventMsg[],
  requestId: number,
  nowMs = Date.now(),
): EventMsg[] {
  return localEvents.map((event) => {
    if (event.type !== 'ui.argus' || Number(event[LOCAL_REQUEST_FIELD]) !== requestId || event.live !== true) {
      return event;
    }
    const steps = Array.isArray(event.steps)
      ? event.steps.map((step) => {
        if (!step || typeof step !== 'object') return step;
        const row = step as Record<string, unknown>;
        return row.ended_ts || row.status !== 'running'
          ? step
          : { ...row, ended_ts: nowMs / 1_000, status: 'stopped' };
      })
      : event.steps;
    return { ...event, live: false, ...(steps !== undefined ? { steps } : {}) };
  });
}

export function optimisticOperatorEvent(
  sid: string,
  requestId: number,
  text: string,
  nowMs = Date.now(),
): EventMsg {
  return {
    type: 'ui.operator',
    agent_layer: 'operator',
    text,
    ts: nowMs / 1_000,
    event_id: `local-${sid}-${requestId}-operator`,
    message_id: `local-${requestId}-operator`,
    [LOCAL_REQUEST_FIELD]: requestId,
  };
}

/** Grow the one locally-rendered Manager row for a request as SSE blocks arrive. */
export function mergeOptimisticManagerDelta(
  localEvents: EventMsg[],
  sid: string,
  requestId: number,
  fragment: string,
  messageId: string,
  nowMs = Date.now(),
  mode: FragmentMode = 'auto',
): EventMsg[] {
  const text = fragment.trim();
  if (!text) return localEvents;
  const index = localEvents.findIndex((event) => (
    event.type === 'ui.argus'
    && Number(event[LOCAL_REQUEST_FIELD]) === requestId
  ));
  const confirmedOperatorId = messageId.endsWith('-argus')
    ? `${messageId.slice(0, -'-argus'.length)}-operator`
    : '';
  const withConfirmedOperator = confirmedOperatorId
    ? localEvents.map((event) => (
        event.type === 'ui.operator'
        && Number(event[LOCAL_REQUEST_FIELD]) === requestId
          ? { ...event, message_id: confirmedOperatorId }
          : event
      ))
    : localEvents;
  const operator = withConfirmedOperator.find((event) => (
    event.type === 'ui.operator'
    && Number(event[LOCAL_REQUEST_FIELD]) === requestId
  ));
  const responseLatencyMs = operator
    ? Math.max(0, nowMs - Number(operator.ts ?? nowMs / 1_000) * 1_000)
    : 0;
  if (index < 0) {
    return [
      ...withConfirmedOperator,
      {
        type: 'ui.argus',
        agent_layer: 'manager',
        text,
        ts: nowMs / 1_000,
        event_id: `local-${sid}-${requestId}-argus`,
        message_id: messageId || `local-${requestId}-argus`,
        fragment_mode: mode,
        response_latency_ms: responseLatencyMs,
        [LOCAL_REQUEST_FIELD]: requestId,
      },
    ];
  }
  const current = withConfirmedOperator[index];
  const next = [...withConfirmedOperator];
  next[index] = {
    ...current,
    text: mergeFragment(String(current.text ?? ''), text, mode),
    message_id: messageId || current.message_id,
    fragment_mode: mode,
  };
  return next;
}

/**
 * Build one conversation feed from transcript replay, live events, and local
 * optimistic rows. A server-confirmed message replaces its local row by stable
 * message id; exact type+text is the fallback for transcript rows and dispatch
 * acknowledgements whose ids are generated in separate layers.
 */
export function mergeConversationEvents(
  liveEvents: EventMsg[],
  transcript: TranscriptTurn[],
  localEvents: EventMsg[],
): EventMsg[] {
  const liveCounts = new Map<string, number>();
  liveEvents.forEach((event) => {
    const type = String(event.type ?? '');
    if (type !== 'ui.operator' && type !== 'ui.argus') return;
    const key = `${type}\u0000${String(event.text ?? '')}`;
    liveCounts.set(key, (liveCounts.get(key) ?? 0) + 1);
  });
  const history: EventMsg[] = transcript.map((turn) => ({
    type: turn.role === 'operator' ? 'ui.operator' : 'ui.argus',
    agent_layer: turn.role === 'operator' ? 'operator' : 'manager',
    text: turn.text,
    ts: turn.ts,
    message_id: turn.message_id || `transcript-${turn.ts}-${turn.role}`,
    ...(turn.mission_result === true ? { mission_result: true } : {}),
    ...(typeof turn.item_id === 'string' ? { item_id: turn.item_id } : {}),
    ...(typeof turn.success === 'boolean' ? { success: turn.success } : {}),
    ...(typeof turn.summary === 'string' ? { summary: turn.summary } : {}),
    ...(typeof turn.delivery_id === 'string' ? { delivery_id: turn.delivery_id } : {}),
    ...(turn.delivery && typeof turn.delivery === 'object' ? { delivery: turn.delivery } : {}),
    ...(hasSteps(turn.steps) ? { steps: turn.steps } : {}),
  }));
  const keepHistory = new Array(history.length).fill(true);
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const event = history[index];
    const key = `${String(event.type)}\u0000${String(event.text ?? '')}`;
    const count = liveCounts.get(key) ?? 0;
    if (count > 0) {
      keepHistory[index] = false;
      liveCounts.set(key, count - 1);
    }
  }
  const confirmed = [
    ...history.filter((_event, index) => keepHistory[index]),
    ...liveEvents,
  ];
  const keepConfirmed = new Array(confirmed.length).fill(true);
  const claimedConfirmed = new Set<number>();
  const preferredLocal = localEvents.map((event) => {
    const messageId = String(event.message_id ?? '');
    let matchIndex = messageId
      ? confirmed.findIndex((candidate, index) => (
          !claimedConfirmed.has(index)
          && String(candidate.message_id ?? '') === messageId
        ))
      : -1;
    const localTs = Number(event.ts ?? 0);
    if (matchIndex < 0) {
      matchIndex = confirmed.findIndex((candidate, index) => {
        if (claimedConfirmed.has(index)) return false;
        if (candidate.type !== event.type || candidate.text !== event.text) return false;
        const confirmedTs = Number(candidate.ts ?? 0);
        // Exact text is only a fallback correlation near this request. Without
        // the time bound, a new repeated greeting would hide behind an old one.
        return Math.abs(confirmedTs - localTs) <= 5;
      });
    }
    if (matchIndex >= 0) {
      // Prefer the local row after confirmation: it carries the real client
      // click/arrival timestamp and response latency. The durable server row
      // will naturally replace it after a project switch or page reload.
      claimedConfirmed.add(matchIndex);
      keepConfirmed[matchIndex] = false;
      const authoritative = confirmed[matchIndex];
      return {
        ...event,
        ...(authoritative.mission_result === true ? { mission_result: true } : {}),
        ...(typeof authoritative.item_id === 'string' ? { item_id: authoritative.item_id } : {}),
        ...(typeof authoritative.success === 'boolean' ? { success: authoritative.success } : {}),
        ...(typeof authoritative.summary === 'string' ? { summary: authoritative.summary } : {}),
        ...(typeof authoritative.delivery_id === 'string' ? { delivery_id: authoritative.delivery_id } : {}),
        ...(authoritative.delivery && typeof authoritative.delivery === 'object'
          ? { delivery: authoritative.delivery }
          : {}),
        // The journaled steps carry real end times and outcomes; once they
        // exist they replace the live trail and the row is no longer live.
        ...(hasSteps(authoritative.steps) ? { steps: authoritative.steps, live: false } : {}),
      };
    }
    return event;
  });
  return [
    ...confirmed.filter((_event, index) => keepConfirmed[index]),
    ...preferredLocal,
  ].sort(
    (left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0),
  );
}
