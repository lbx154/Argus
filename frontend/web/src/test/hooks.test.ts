import { describe, expect, it } from 'vitest';
import {
  ARTIFACTS_POLL_MS,
  GIT_DIFF_POLL_MS,
  PROJECT_COST_POLL_MS,
  PROJECT_POLL_MS,
  SNAPSHOT_POLL_MS,
  artifactRefreshEventKey,
  snapshotRefreshEventKey,
  streamReducer,
} from '../hooks';
import type { EventMsg } from '../api';

describe('project-scoped event stream', () => {
  it('keeps background polling below interaction-jank cadence', () => {
    expect(PROJECT_POLL_MS).toBeGreaterThanOrEqual(15_000);
    expect(PROJECT_COST_POLL_MS).toBeGreaterThanOrEqual(4_000);
    expect(PROJECT_COST_POLL_MS).toBeLessThan(PROJECT_POLL_MS);
    expect(SNAPSHOT_POLL_MS).toBeGreaterThanOrEqual(8_000);
    expect(ARTIFACTS_POLL_MS).toBeGreaterThanOrEqual(10_000);
    expect(GIT_DIFF_POLL_MS).toBeGreaterThanOrEqual(10_000);
  });
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

  it('requests an immediate artifact refresh for live-view and file events', () => {
    const unchanged = artifactRefreshEventKey([
      { type: 'engineer.progress', kind: 'assistant_message', ts: 1 } as EventMsg,
    ]);
    const manager = artifactRefreshEventKey([
      { type: 'manager.live_view.updated', ts: 2 } as EventMsg,
    ]);
    const file = artifactRefreshEventKey([
      { type: 'engineer.progress', kind: 'file_change', ts: 3 } as EventMsg,
    ]);

    expect(unchanged).toBe('');
    expect(manager).not.toBe('');
    expect(file).not.toBe('');
  });

  it('requests an immediate snapshot refresh for pending-question changes', () => {
    expect(snapshotRefreshEventKey([
      { type: 'life.operator_question.pending', item_id: 'blocked', ts: 4 } as EventMsg,
    ])).not.toBe('');
    expect(snapshotRefreshEventKey([
      { type: 'life.operator_question.answered', item_id: 'blocked', ts: 5 } as EventMsg,
    ])).not.toBe('');
    expect(snapshotRefreshEventKey([
      { type: 'engineer.progress', kind: 'assistant_message', ts: 6 } as EventMsg,
    ])).toBe('');
  });
});
