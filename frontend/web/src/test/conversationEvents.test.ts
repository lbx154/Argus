import replay from '../../../../packages/core/test/fixtures/conversation-fixes/after.json';
import { webEvents, type Recorded } from '../../../../packages/core/src/serve/views';
import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { durableMessageId, mergeConversationEvents, mergeOptimisticManagerReply, optimisticOperatorEvent, optimisticManagerEvent, settleOptimisticManagerTurn } from '../lib/conversationEvents';

const asked: EventMsg = { type: 'ui.operator', agent_layer: 'operator', text: '分三个任务做', ts: 10, event_id: 4, message_id: 'm4-operator', request_id: 'r1' };
const brief: EventMsg = { type: 'ui.argus', agent_layer: 'manager', to: 'planner', text: '# 三个任务\n请规划。', ts: 20, event_id: 7, message_id: 'm4-argus', request_id: 'r1' };

describe('recorded requests replace optimistic conversation rows', () => {
  it('creates both waiting rows before any network response', () => {
    expect(optimisticOperatorEvent('s', 'r1', '你好', 1000)).toMatchObject({ type: 'ui.operator', text: '你好', request_id: 'r1' });
    expect(optimisticManagerEvent('s', 'r1', 1000)).toMatchObject({ type: 'ui.argus', live: true, request_id: 'r1' });
  });
  it('uses the transcript id for revisions once the answer arrives', () => {
    const operator = optimisticOperatorEvent('s', 'r1', '分三个任务做');
    expect(durableMessageId(operator)).toBeUndefined();
    expect(durableMessageId(mergeOptimisticManagerReply([operator], 'r1', brief)[0])).toBe('m4-operator');
  });
  it('keeps the stopped reply until its corresponding record row arrives', () => {
    const waiting = [optimisticOperatorEvent('s', 'r1', '分三个任务做'), optimisticManagerEvent('s', 'r1')];
    const stopped = settleOptimisticManagerTurn(waiting, 'r1', 'stopped');
    expect(stopped[1]).toMatchObject({ live: false, status: 'stopped' });
    const recordedStop = { ...brief, text: '', status: 'stopped' };
    expect(mergeConversationEvents([asked, recordedStop], stopped)).toEqual([asked, recordedStop]);
    expect(mergeConversationEvents([asked, brief], stopped)).toEqual([asked, brief]);
  });
  it('does not turn a final answer into a stopped reply', () => {
    expect(settleOptimisticManagerTurn([brief], 'r1', 'stopped')).toEqual([brief]);
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
