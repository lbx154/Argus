import { describe, expect, it } from 'vitest';
import { streamReducer } from '../hooks';
import type { EventMsg } from '../api';

describe('project-scoped event stream', () => {
  it('ignores events from a stale project generation', () => {
    const initial = {
      sid: 's-current',
      events: [] as EventMsg[],
      seen: new Set<string>(),
    };
    const stale = streamReducer(initial, {
      kind: 'push',
      sid: 's-previous',
      ev: { type: 'round.start', round_index: 1 } as EventMsg,
    });
    expect(stale).toBe(initial);

    const current = streamReducer(initial, {
      kind: 'push',
      sid: 's-current',
      ev: { type: 'round.start', round_index: 2 } as EventMsg,
    });
    expect(current.events).toHaveLength(1);
  });
});
