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

  it('bounds both retained events and deduplication keys', () => {
    const initial = {
      sid: 's-current',
      events: [] as EventMsg[],
      seen: new Set<string>(),
    };
    const events = Array.from({ length: 2_005 }, (_, i) => ({
      type: 'round.start',
      event_id: String(i),
    }));
    const seeded = streamReducer(initial, {
      kind: 'seed',
      sid: 's-current',
      events,
    });

    expect(seeded.events).toHaveLength(2_000);
    expect(seeded.seen.size).toBe(2_000);

    const replayedOldEvent = streamReducer(seeded, {
      kind: 'push',
      sid: 's-current',
      ev: events[0],
    });
    expect(replayedOldEvent.events).toHaveLength(2_000);
    expect(replayedOldEvent.events[1_999].event_id).toBe('0');
    expect(replayedOldEvent.seen.size).toBe(2_000);
  });
});
