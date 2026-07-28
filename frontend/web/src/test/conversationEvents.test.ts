import { describe, expect, it } from 'vitest';

import {
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
