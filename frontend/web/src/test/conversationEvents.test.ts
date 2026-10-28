import { describe, expect, it } from 'vitest';

import {
  dropSupersededDrafts,
  durableMessageId,
  mergeConversationEvents,
  mergeOptimisticManagerDelta,
  optimisticOperatorEvent,
} from '../lib/conversationEvents';

describe('optimistic Manager conversation', () => {
  it('creates the operator row synchronously before any network result exists', () => {
    const row = optimisticOperatorEvent('s-fast', 1, '你好', 1_000);

    expect(row).toMatchObject({
      type: 'ui.operator',
      text: '你好',
      ts: 1,
      event_id: 'local-s-fast-1-operator',
    });
  });

  it('keys conversation revisions only by transcript ids, never by the page-local placeholder', () => {
    // A reload restarts request ids at 1, so the placeholder would collide across days.
    const operator = optimisticOperatorEvent('s-fast', 1, '你好', 1_000);
    expect(durableMessageId(operator)).toBeUndefined();
    const confirmed = mergeOptimisticManagerDelta([operator], 's-fast', 1, '你好！', 'web-1-argus', 1_010);
    expect(durableMessageId(confirmed[0])).toBe('web-1-operator');
  });

  it('renders and grows Manager SSE blocks without waiting for done', () => {
    const operator = optimisticOperatorEvent('s-fast', 1, '你好', 1_000);
    const first = mergeOptimisticManagerDelta(
      [operator], 's-fast', 1, '你好！', 'web-1-argus', 1_010,
    );
    const second = mergeOptimisticManagerDelta(
      first, 's-fast', 1, '你好！有什么可以帮你？', 'web-1-argus', 1_020,
    );

    expect(first[1].text).toBe('你好！');
    expect(first[0].message_id).toBe('web-1-operator');
    expect(first[1].response_latency_ms).toBe(10);
    expect(second).toHaveLength(2);
    expect(second[1].text).toBe('你好！有什么可以帮你？');
  });

  it('replaces a stale optimistic draft with an authoritative snapshot', () => {
    const operator = optimisticOperatorEvent('s-fast', 1, '你好', 1_000);
    const draft = mergeOptimisticManagerDelta(
      [operator],
      's-fast',
      1,
      'final answer with stale repeated tail',
      'web-1-argus',
      1_010,
    );
    const settled = mergeOptimisticManagerDelta(
      draft,
      's-fast',
      1,
      'final answer',
      'web-1-argus',
      1_020,
      'snapshot',
    );

    expect(settled[1].text).toBe('final answer');
    expect(settled[1].fragment_mode).toBe('snapshot');
  });

  it('drops optimistic rows when live/transcript confirmation arrives', () => {
    let local = [optimisticOperatorEvent('s-fast', 1, '你好', 1_000)];
    local = mergeOptimisticManagerDelta(
      local, 's-fast', 1, '你好！', 'web-1-argus', 1_010,
    );
    const merged = mergeConversationEvents(
      [{
        type: 'ui.argus',
        text: '你好！',
        ts: 1.2,
        message_id: 'web-1-argus',
      }],
      [{ role: 'operator', text: '你好', ts: 1.1 }],
      local,
    );

    expect(merged.filter((event) => event.type === 'ui.operator')).toHaveLength(1);
    expect(merged.filter((event) => event.type === 'ui.argus')).toHaveLength(1);
    expect(merged.find((event) => event.type === 'ui.operator')?.ts).toBe(1);
    expect(merged.find((event) => event.type === 'ui.argus')?.event_id).toBe(
      'local-s-fast-1-argus',
    );
  });

  it('does not hide a repeated new greeting behind old transcript text', () => {
    const local = [optimisticOperatorEvent('s-fast', 2, '你好', 20_000)];
    const merged = mergeConversationEvents(
      [],
      [{ role: 'operator', text: '你好', ts: 1 }],
      local,
    );

    expect(merged.filter((event) => event.type === 'ui.operator')).toHaveLength(2);
  });
});

describe('tool steps behind a Manager reply', () => {
  it('shows the work before any words arrive, then keeps it under the reply', async () => {
    const { mergeOptimisticManagerSteps, settleOptimisticManagerTurn } = await import('../lib/conversationEvents');
    const operator = optimisticOperatorEvent('s-fast', 1, '数一下行数', 1_000);
    const step = { kind: 'command_execution', label: '$ wc -l', status: 'running', started_ts: 1, ended_ts: 0 };
    const working = mergeOptimisticManagerSteps([operator], 's-fast', 1, [step], 1_500, true);

    expect(working).toHaveLength(2);
    expect(working[1]).toMatchObject({ type: 'ui.argus', text: '', live: true, steps: [step] });

    const replied = mergeOptimisticManagerDelta(working, 's-fast', 1, '一行。', 'web-1-argus', 2_000, 'snapshot');
    expect(replied).toHaveLength(2);
    // The reply is timed by its first words, not by the empty row put up when the message was sent.
    expect(replied[1]).toMatchObject({ text: '一行。', live: true, steps: [step], ts: 2 });
    expect(mergeOptimisticManagerDelta(replied, 's-fast', 1, '一行，共 1 行。', 'web-1-argus', 2_500, 'snapshot')[1]).toMatchObject({ ts: 2 });

    const settled = settleOptimisticManagerTurn(replied, 1, 3_000);
    expect(settled[1]).toMatchObject({ live: false });
    expect((settled[1].steps as Array<Record<string, unknown>>)[0]).toMatchObject({ status: 'stopped', ended_ts: 3 });
  });

  it('prefers the journaled steps over the live trail once the transcript has them', async () => {
    const { mergeOptimisticManagerSteps } = await import('../lib/conversationEvents');
    const operator = optimisticOperatorEvent('s-fast', 1, '数一下行数', 1_000);
    const live = mergeOptimisticManagerSteps([operator], 's-fast', 1, [{ kind: 'tool_use', label: 'x', status: 'running', started_ts: 1, ended_ts: 0 }], 1_500, true);
    const replied = mergeOptimisticManagerDelta(live, 's-fast', 1, '一行。', 'web-1-argus', 2_000, 'snapshot');
    const journaled = [{ kind: 'tool_use', label: 'x', status: 'completed', started_ts: 1, ended_ts: 2 }];

    const merged = mergeConversationEvents(
      [],
      [
        { ts: 1, role: 'operator', text: '数一下行数', message_id: 'web-1-operator' },
        { ts: 2, role: 'argus', text: '一行。', message_id: 'web-1-argus', steps: journaled },
      ],
      replied,
    );

    const reply = merged.find((event) => event.type === 'ui.argus');
    expect(reply).toMatchObject({ text: '一行。', live: false, steps: journaled });
  });

  it('carries steps on transcript rows replayed after a reload', () => {
    const steps = [{ kind: 'tool_use', label: 'x', status: 'completed', started_ts: 1, ended_ts: 2 }];
    const merged = mergeConversationEvents([], [{ ts: 2, role: 'argus', text: 'done', steps }], []);
    expect(merged[0]).toMatchObject({ type: 'ui.argus', steps });
  });
});

describe('task receipt replay and live deduplication', () => {
  it('retains missing task metadata when the same receipt is already in the live feed', () => {
    const live = { type: 'ui.argus', text: 'External interrupt: daemon stop requested',
      ts: 1789216024, message_id: 'mission-result-task-a-paused_daemon_shutdown' };
    const merged = mergeConversationEvents([live], [{ role: 'argus', text: live.text,
      ts: 1789216023, message_id: live.message_id, mission_result: true, item_id: 'task-a', success: false }], []);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toEqual({ ...live, mission_result: true, item_id: 'task-a', success: false });
    expect(live).not.toHaveProperty('mission_result');
  });

  it('does not overwrite existing live task metadata', () => {
    const live = { type: 'ui.argus', text: 'A recorded result', ts: 20, message_id: 'receipt-a',
      mission_result: false, item_id: 'task-a', success: true };
    const merged = mergeConversationEvents([live], [{ role: 'argus', text: live.text, ts: 19,
      message_id: live.message_id, mission_result: true, item_id: 'task-a', success: false }], []);
    expect(merged).toEqual([live]);
  });

  it.each([
    { message_id: 'receipt-b', item_id: 'task-b' },
    { message_id: 'receipt-b', item_id: 'task-a' },
    { message_id: 'receipt-a', item_id: 'task-b' },
    { item_id: 'task-a' },
  ])('keeps identical receipt text separate without compatible durable identity: %j', identity => {
    const live = { type: 'ui.argus', text: 'External interrupt: daemon stop requested', ts: 20, ...identity };
    const merged = mergeConversationEvents([live], [{ role: 'argus', text: live.text, ts: 19,
      message_id: 'receipt-a', mission_result: true, item_id: 'task-a', success: false }], []);
    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({ message_id: 'receipt-a', item_id: 'task-a', mission_result: true });
    expect(merged[1]).toEqual(live);
  });

  it('matches receipt identity before another live copy with the same template text', () => {
    const text = 'External interrupt: daemon stop requested';
    const merged = mergeConversationEvents([
      { type: 'ui.argus', text, ts: 20, message_id: 'receipt-a' },
      { type: 'ui.argus', text, ts: 21, message_id: 'receipt-b' },
    ], [{ role: 'argus', text, ts: 19, message_id: 'receipt-a', mission_result: true, item_id: 'task-a', success: false }], []);
    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({ message_id: 'receipt-a', mission_result: true, item_id: 'task-a', success: false });
    expect(merged[1]).not.toHaveProperty('mission_result');
  });

  it('shows a streamed dispatch acknowledgement once when its transcript row carries the same id', () => {
    // 2026-10-28 trial: "收到，马上开始：…" twice — the local row was made at the
    // first fragment, long before the acknowledgement was journaled.
    const text = '收到，马上开始：CartPole 三算法样本效率与稳定性研究。';
    const local = mergeOptimisticManagerDelta(
      [optimisticOperatorEvent('s', 1, '做一个研究')], 's', 1, text, 'dispatch-0f3a', 1_000_000, 'snapshot',
    );
    const merged = mergeConversationEvents([], [
      { role: 'operator', text: '做一个研究', ts: 1_000, message_id: 'web-1-operator' },
      { role: 'argus', text, ts: 1_038, message_id: 'dispatch-0f3a' },
    ], local);
    expect(merged.filter(event => event.type === 'ui.argus' && event.text === text)).toHaveLength(1);
  });

  it('keeps the existing content fallback for ordinary dispatch acknowledgements', () => {
    const live = { type: 'ui.argus', text: 'Scheduled.', ts: 20, message_id: 'dispatch-a' };
    expect(mergeConversationEvents([live], [{ role: 'argus', text: live.text, ts: 19, message_id: 'journal-a' }], [])).toEqual([live]);
  });
});

it('does not duplicate a legacy delivery when transcript lacks its message id', () => {
  const row = { text: 'Result ready', ts: 100, mission_result: true, delivery_id: 'delivery:one' };
  const merged = mergeConversationEvents(
    [{ ...row, type: 'ui.argus', message_id: 'web-one-argus', ts: 100.001 }],
    [{ ...row, role: 'argus' }], [],
  );
  expect(merged).toHaveLength(1);
  expect(merged[0].message_id).toBe('web-one-argus');
});

describe('persisted Manager turn steps', () => {
  it('keeps them out of the conversation feed because the reply row lists them', () => {
    const step = { type: 'engineer.progress', kind: 'command_execution', agent_layer: 'manager', item_id: 'turn:web-1', turn_step: true, text: '$ wc -l README.md', ts: 2 };
    const engineerWork = { type: 'engineer.progress', kind: 'command_execution', agent_layer: 'engineer', item_id: 'task-a', text: '$ pytest', ts: 3 };
    const reply = { type: 'ui.argus', message_id: 'web-1-argus', text: '一行。', steps: [{ kind: 'command_execution', label: '$ wc -l README.md' }], ts: 4 };

    const merged = mergeConversationEvents([step, engineerWork, reply], [], []);

    expect(merged.map((event) => event.type)).toEqual(['engineer.progress', 'ui.argus']);
    expect(merged[0].agent_layer).toBe('engineer');
  });
});

describe('Manager reply in progress', () => {
  const op = { type: 'ui.operator', text: 'format_ratio 是干嘛的？', ts: 100, message_id: 'web-1-operator' };
  const draft = { type: 'ui.argus', text: '我先看一下 core.py', ts: 102.6, message_id: 'web-1-argus', in_progress: true, live: true };
  const final = { type: 'ui.argus', text: '`format_ratio` 把比例格式化成百分比。', ts: 120, message_id: 'web-1-argus' };

  it('shows the first words at once, marked live', () => {
    const feed = mergeConversationEvents([op, draft], [], []);
    expect(feed.map((event) => event.text)).toEqual([op.text, draft.text]);
    expect(feed[1].live).toBe(true);
  });

  it('the final reply replaces the first words', () => {
    expect(mergeConversationEvents([op, draft, final], [], []).map((event) => event.text)).toEqual([op.text, final.text]);
    const journaled = [{ ts: 120, role: 'argus', text: final.text, message_id: 'web-1-argus' }];
    expect(mergeConversationEvents([op, draft], journaled, []).map((event) => event.text)).toEqual([op.text, final.text]);
  });

  it('a later message retires first words whose turn never finished', () => {
    const next = { type: 'ui.operator', text: '算了', ts: 130, message_id: 'web-2-operator' };
    expect(dropSupersededDrafts([op, draft, next]).map((event) => event.text)).toEqual([op.text, next.text]);
  });

  it('a turn cancelled or crashed before its reply leaves no early line after reload', () => {
    const closed = { type: 'ui.argus', text: '', ts: 104, message_id: 'web-1-argus', in_progress: true, draft_closed: true };
    expect(mergeConversationEvents([op, draft, closed], [], []).map((event) => event.text)).toEqual([op.text]);
    // A retried turn may speak again under the same id: its new first words show.
    const retry = { ...draft, text: '再看一次 core.py', ts: 110 };
    expect(mergeConversationEvents([op, draft, closed, retry], [], []).map((event) => event.text)).toEqual([op.text, retry.text]);
    // The closer never removes a final reply.
    expect(mergeConversationEvents([op, draft, final, closed], [], []).map((event) => event.text)).toEqual([op.text, final.text]);
  });
});
