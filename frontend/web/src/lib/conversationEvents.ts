import type { EventMsg } from '../api';
import { eventKey } from '../../../core/src/events';

const LOCAL_REQUEST_FIELD = 'local_request_id';

/**
 * Show the work behind a reply while it is still happening: the tool steps
 * streamed so far become part of the Argus row (created empty when no words
 * have arrived yet), and ``live`` keeps its compact status active.
 */
export function mergeOptimisticManagerSteps(
  localEvents: EventMsg[],
  sid: string,
  requestId: number,
  steps: unknown[],
  nowMs = Date.now(),
  live = true,
  status?: 'completed' | 'failed',
): EventMsg[] {
  const index = localEvents.findIndex((event) => (
    event.type === 'ui.argus'
    && Number(event[LOCAL_REQUEST_FIELD]) === requestId
  ));
  if (index < 0) {
    if (!steps.length && !live) return localEvents;
    return [
      ...localEvents,
      {
        type: 'ui.argus',
        agent_layer: 'manager',
        text: '',
        ts: nowMs / 1_000,
        event_id: `local-${sid}-${requestId}-argus`,
        message_id: `local-${requestId}-argus`,
        steps,
        live,
        ...(status ? { status } : {}),
        [LOCAL_REQUEST_FIELD]: requestId,
      },
    ];
  }
  const next = [...localEvents];
  next[index] = { ...next[index], ...(steps.length ? { steps } : {}), live, ...(status ? { status } : {}) };
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
  status: 'stopped' | 'failed' = 'stopped',
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
          : { ...row, ended_ts: nowMs / 1_000, status };
      })
      : event.steps;
    return { ...event, live: false, status, ...(steps !== undefined ? { steps } : {}) };
  });
}

const localOperatorId = (requestId: number) => `local-${requestId}-operator`;

/** The transcript id of a sent message; a page-local placeholder is not one. */
export function durableMessageId(event: EventMsg): string | undefined {
  const id = event.message_id;
  return typeof id === 'string' && id && id !== localOperatorId(Number(event[LOCAL_REQUEST_FIELD])) ? id : undefined;
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
    message_id: localOperatorId(requestId),
    [LOCAL_REQUEST_FIELD]: requestId,
  };
}

/**
 * The reply has come, as its row in the record: the page's row for it becomes
 * that row, and the message it answers takes the id the record gives it.
 */
export function mergeOptimisticManagerReply(localEvents: EventMsg[], requestId: number, reply: EventMsg): EventMsg[] {
  const mine = (event: EventMsg) => Number(event[LOCAL_REQUEST_FIELD]) === requestId;
  const asked = String(reply.message_id).replace(/-argus$/, '-operator');
  return [
    ...localEvents.flatMap(event => !mine(event) ? [event] : event.type === 'ui.operator' ? [{ ...event, message_id: asked }] : []),
    { ...reply, [LOCAL_REQUEST_FIELD]: requestId },
  ];
}

/** The turn ended without a reply (stopped, or failed): the page's row for it goes, and the
 * conversation reads as the record does, the steps and the failure as the stream sent them. */
export function dropOptimisticManagerReply(localEvents: EventMsg[], requestId: number): EventMsg[] {
  return localEvents.filter(event => event.type !== 'ui.argus' || Number(event[LOCAL_REQUEST_FIELD]) !== requestId);
}

/**
 * The conversation: the record's rows, which the transcript and the live stream
 * both send as the server's one reading of the record makes them, in the
 * record's order; and the page's own rows of a message under way, each until
 * the record has it: by its message id, or for a message whose reply has not
 * come, by its words, recorded once it was sent.
 */
export function mergeConversationEvents(
  streamedEvents: EventMsg[],
  transcript: EventMsg[],
  localEvents: EventMsg[],
): EventMsg[] {
  const recorded = [...new Map([...transcript, ...streamedEvents].map(event => [eventKey(event), event])).values()]
    .sort((left, right) => Number(left.event_id) - Number(right.event_id));
  const claimed = new Set<EventMsg>();
  const pending = localEvents.filter((event) => {
    const twin = recorded.find(row => !claimed.has(row) && row.type === event.type && (row.message_id === event.message_id
      || row.text === event.text && Number(row.ts) >= Number(event.ts) - 5));
    if (twin) claimed.add(twin);
    return !twin;
  });
  return [...recorded, ...pending].sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0));
}
