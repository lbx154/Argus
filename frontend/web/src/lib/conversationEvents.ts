import type { EventMsg, UploadedAttachment } from '../api';

const localOperatorId = (requestId: string) => `local-${requestId}-operator`;

/** A page placeholder is replaced by the corresponding row of the request in the record. */
export function optimisticOperatorEvent(
  sid: string,
  requestId: string,
  text: string,
  nowMs = Date.now(),
  attachments: UploadedAttachment[] = [],
): EventMsg {
  return {
    type: 'ui.operator',
    agent_layer: 'operator',
    text,
    ...(attachments.length ? { attachments } : {}),
    ts: nowMs / 1_000,
    event_id: `local-${sid}-${requestId}-operator`,
    message_id: localOperatorId(requestId),
    request_id: requestId,
  };
}

export function optimisticManagerEvent(sid: string, requestId: string, nowMs = Date.now()): EventMsg {
  return { type: 'ui.argus', agent_layer: 'manager', text: '', ts: nowMs / 1000,
    event_id: `local-${sid}-${requestId}-argus`, message_id: `local-${requestId}-argus`, request_id: requestId, live: true, status: 'waiting' };
}

/** The transcript id of a sent message; a page-local placeholder is not one. */
export function durableMessageId(event: EventMsg): string | undefined {
  const id = event.message_id;
  return typeof id === 'string' && id && id !== localOperatorId(String(event.request_id)) ? id : undefined;
}

export function mergeOptimisticManagerReply(localEvents: EventMsg[], requestId: string, reply: EventMsg): EventMsg[] {
  const asked = String(reply.message_id).replace(/-argus$/, '-operator');
  return [
    ...localEvents.flatMap(event => event.request_id !== requestId ? [event] : event.type === 'ui.operator' ? [{ ...event, message_id: asked }] : []),
    { ...reply, request_id: requestId },
  ];
}

export function settleOptimisticManagerTurn(localEvents: EventMsg[], requestId: string, status: 'stopped' | 'failed'): EventMsg[] {
  return localEvents.map(event => event.type === 'ui.argus' && event.request_id === requestId && event.live === true
    ? { ...event, live: false, status } : event);
}

/** The server owns order and state once the recorded request row arrives. */
export function mergeConversationEvents(recorded: EventMsg[], localEvents: EventMsg[]): EventMsg[] {
  const pending = localEvents.filter(event => !recorded.some(row =>
    row.type === event.type && typeof event.request_id === 'string' && event.request_id !== '' && row.request_id === event.request_id));
  return [...recorded, ...pending];
}
