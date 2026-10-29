import { describe, expect, it } from 'vitest';

import type { EventMsg } from '../api';
import {
  durableMessageId,
  mergeConversationEvents,
  mergeOptimisticManagerReply,
  mergeOptimisticManagerSteps,
  optimisticOperatorEvent,
  settleOptimisticManagerTurn,
} from '../lib/conversationEvents';

// Rows as the server's reading of the record makes them: the line is the event id.
const asked: EventMsg = { type: 'ui.operator', agent_layer: 'operator', text: '分三个任务做', ts: 10, event_id: 4, message_id: 'm4-operator' };
const brief: EventMsg = { type: 'ui.argus', agent_layer: 'manager', to: 'planner', text: '# 三个任务\n请规划。', ts: 20, event_id: 7, message_id: 'm4-argus' };

describe('optimistic Manager conversation', () => {
  it('creates the operator row synchronously before any network result exists', () => {
    expect(optimisticOperatorEvent('s-fast', 1, '你好', 1_000)).toMatchObject({
      type: 'ui.operator', text: '你好', ts: 1, event_id: 'local-s-fast-1-operator',
    });
  });

  it('keys conversation revisions only by transcript ids, never by the page-local placeholder', () => {
    // A reload restarts request ids at 1, so the placeholder would collide across days.
    const operator = optimisticOperatorEvent('s-fast', 1, '分三个任务做', 9_000);
    expect(durableMessageId(operator)).toBeUndefined();
    expect(durableMessageId(mergeOptimisticManagerReply([operator], 1, brief)[0])).toBe('m4-operator');
  });

  it('the reply is its row in the record, with whom it is to, in place of the row put up while waiting', () => {
    const operator = optimisticOperatorEvent('s', 1, '分三个任务做', 9_000);
    const waiting = mergeOptimisticManagerSteps([operator], 's', 1, [], 9_500, true);
    const replied = mergeOptimisticManagerReply(waiting, 1, brief);
    expect(replied).toHaveLength(2);
    expect(replied[1]).toMatchObject({ ...brief, local_request_id: 1 });
    expect(replied[1]).not.toHaveProperty('live');
  });

  it('the record replaces the page’s rows: the same id, or the same words recorded once they were sent', () => {
    const operator = optimisticOperatorEvent('s', 1, '分三个任务做', 9_000);
    // The Manager has taken the message up; its reply has not come.
    expect(mergeConversationEvents([asked], [], [operator])).toEqual([asked]);
    const replied = mergeOptimisticManagerReply([operator], 1, brief);
    expect(mergeConversationEvents([asked, brief], [], replied)).toEqual([asked, brief]);
    // Before the stream has the reply, the page shows it as the record will.
    expect(mergeConversationEvents([asked], [], replied)).toEqual([asked, { ...brief, local_request_id: 1 }]);
  });

  it('does not hide a repeated new greeting behind an old one', () => {
    const old = { ...asked, text: '你好', ts: 1 };
    expect(mergeConversationEvents([], [old], [optimisticOperatorEvent('s', 2, '你好', 20_000)])).toHaveLength(2);
  });

  it('the transcript and the stream are the same rows, kept once, in the record’s order', () => {
    const step: EventMsg = { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'planner', text: '规划中', ts: 20, event_id: 6 };
    expect(mergeConversationEvents([brief, step], [asked, brief], [])).toEqual([asked, step, brief]);
  });
});

describe('tool steps behind a Manager reply', () => {
  it('shows the work before any words arrive, and stops its clock when the turn ends without a reply', () => {
    const operator = optimisticOperatorEvent('s-fast', 1, '数一下行数', 1_000);
    const step = { kind: 'command_execution', label: '$ wc -l', status: 'running', started_ts: 1, ended_ts: 0 };
    const working = mergeOptimisticManagerSteps([operator], 's-fast', 1, [step], 1_500, true);
    expect(working[1]).toMatchObject({ type: 'ui.argus', text: '', live: true, steps: [step] });

    const settled = settleOptimisticManagerTurn(working, 1, 3_000);
    expect(settled[1]).toMatchObject({ live: false });
    expect((settled[1].steps as Array<Record<string, unknown>>)[0]).toMatchObject({ status: 'stopped', ended_ts: 3 });
  });
});
