import replay from '../../../../packages/core/test/fixtures/conversation-fixes/after.json';
import { webEvents, type Recorded } from '../../../../packages/core/src/serve/views';
import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { conversationEventKey, durableMessageId, mergeConversationEvents, mergeManagerReply, optimisticOperatorEvent } from '../lib/conversationEvents';

const asked: EventMsg = { type: 'ui.operator', agent_layer: 'operator', text: '分三个任务做', ts: 10, event_id: 4, message_id: 'm4-operator', request_id: 'r1' };
const brief: EventMsg = { type: 'ui.argus', agent_layer: 'manager', to: 'planner', text: '# 三个任务\n请规划。', ts: 20, event_id: 7, message_id: 'm4-argus', request_id: 'r1' };

describe('recorded requests replace optimistic conversation rows', () => {
  it('creates only the operator row before any network response', () => {
    expect(optimisticOperatorEvent('s', 'r1', '你好', 1000)).toMatchObject({ type: 'ui.operator', text: '你好', request_id: 'r1' });
  });
  it('uses the transcript id for revisions once the answer arrives', () => {
    const operator = optimisticOperatorEvent('s', 'r1', '分三个任务做');
    expect(durableMessageId(operator)).toBeUndefined();
    expect(durableMessageId(mergeManagerReply([operator], 'r1', brief)[0])).toBe('m4-operator');
  });
  it('keeps one identity from local send through server acceptance and final reply', () => {
    const operator = optimisticOperatorEvent('s', 'r1', '分三个任务做');
    expect(conversationEventKey(operator)).toBe(conversationEventKey(asked));
    expect(conversationEventKey(asked)).not.toBe(conversationEventKey(brief));
    expect(conversationEventKey({ ...brief, status: 'running' })).toBe(conversationEventKey(brief));
  });
  it('keeps persistent identities for distinct tasks and questions on one request', () => {
    const rows = [
      { ...brief, type: 'ui.question', row_id: 'question:q1' },
      { ...brief, type: 'ui.question', row_id: 'question:q2' },
      { ...brief, type: 'ui.task', row_id: 'task:t1' },
      { ...brief, type: 'ui.task', row_id: 'task:t2' },
      { ...brief, request_id: undefined, row_id: 'message:old' },
    ];
    expect(rows.map(conversationEventKey)).toEqual(['question:q1', 'question:q2', 'task:t1', 'task:t2', 'message:old']);
  });
  it('does not confuse repeated messages, even with equal text and timestamps', () => {
    const repeated = optimisticOperatorEvent('s', 'r2', asked.text as string, 10000);
    expect(mergeConversationEvents([asked], [repeated])).toEqual([asked, repeated]);
    expect(mergeConversationEvents([asked], [optimisticOperatorEvent('s', 'r1', 'with attachments', 10000)])).toEqual([asked]);
  });
  it('preserves server row order', () => {
    expect(mergeConversationEvents([brief, asked], [])).toEqual([brief, asked]);
  });
});
// Captured at the real cockpit on port 18801, with the browser clock one hour ahead/behind.
describe('browser record: request identity and clock skew', () => {
  const events = webEvents(replay.events as Recorded[]).filter(row => row.type === 'ui.operator' || row.type === 'ui.argus');
  const first = events[0];
  const secondRequest = replay.requests[1];
  it('claims the send with its request id even when the browser is an hour ahead', () => {
    const local = optimisticOperatorEvent('browser', replay.requests[0].request_id, replay.requests[0].text, Number(first.ts) * 1000 + 3600000);
    expect(mergeConversationEvents([first], [local])).toEqual([first]);
  });
  it('appends a repeated send after recorded rows, even when the browser is an hour behind', () => {
    const local = optimisticOperatorEvent('browser', secondRequest.request_id, secondRequest.text, Number(first.ts) * 1000 - 3600000);
    const recorded = events.slice(0, 2);
    expect(mergeConversationEvents(recorded, [local])).toEqual([...recorded, local]);
    expect(mergeConversationEvents(events.slice(0, 3), [local])).toEqual(events.slice(0, 3));
  });
  it('uses only request ids: equal text, timestamps and message ids do not claim a row', () => {
    const local = { ...first, request_id: secondRequest.request_id };
    expect(mergeConversationEvents([first], [local])).toEqual([first, local]);
    const missing = { ...first, request_id: undefined };
    expect(mergeConversationEvents([missing], [missing])).toEqual([missing, missing]);
    expect(mergeConversationEvents([first], [{ ...first, type: 'ui.argus' }])).toHaveLength(2);
  });
});
