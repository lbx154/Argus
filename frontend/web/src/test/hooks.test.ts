import { describe, expect, it } from 'vitest';
import {
  ACTIVE_DAEMON_POLL_MS,
  PROJECT_COST_POLL_MS,
  PROJECT_POLL_MS,
  SNAPSHOT_POLL_MS,
  STREAM_RENDER_BATCH_MS,
  artifactRefreshEventKey,
  projectPollInterval,
  snapshotPollInterval,
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
    expect(ACTIVE_DAEMON_POLL_MS).toBeLessThanOrEqual(2_000);
    expect(STREAM_RENDER_BATCH_MS).toBeGreaterThanOrEqual(16);
    expect(STREAM_RENDER_BATCH_MS).toBeLessThanOrEqual(100);
  });
  it('polls live daemons quickly enough to show their automatic exit', () => {
    expect(projectPollInterval({ projects: [], local_cwd: '' })).toBe(PROJECT_POLL_MS);
    expect(projectPollInterval({
      projects: [{ daemon_alive: true } as never],
      local_cwd: '',
    })).toBe(ACTIVE_DAEMON_POLL_MS);
    expect(snapshotPollInterval()).toBe(SNAPSHOT_POLL_MS);
    expect(snapshotPollInterval({ daemon: { alive: true } } as never)).toBe(
      ACTIVE_DAEMON_POLL_MS,
    );
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
    const replayed = streamReducer(initial, {
      kind: 'push-many',
      sid: 's-current',
      events,
    });

    expect(replayed.events).toHaveLength(2_000);
    expect(replayed.seen.size).toBe(2_000);

    const replayedOldEvent = streamReducer(replayed, {
      kind: 'push',
      sid: 's-current',
      ev: events[0],
    });
    expect(replayedOldEvent.events).toHaveLength(2_000);
    expect(replayedOldEvent.events[1_999].event_id).toBe('0');
    expect(replayedOldEvent.seen.size).toBe(2_000);
  });

  it('deduplicates a live batch with one reducer clone boundary', () => {
    const initial = {
      sid: 's-current',
      events: [] as EventMsg[],
      seen: new Set<string>(),
    };
    const first = { type: 'round.start', event_id: 'first' } as EventMsg;
    const second = { type: 'engineer.progress', event_id: 'second' } as EventMsg;
    const batched = streamReducer(initial, {
      kind: 'push-many',
      sid: 's-current',
      events: [first, first, second],
    });

    expect(batched.events).toEqual([first, second]);
    expect(batched.seen.size).toBe(2);
  });

  it('appends live events after replay and ignores repeated replay frames', () => {
    const initial = {
      sid: 's-current',
      events: [] as EventMsg[],
      seen: new Set<string>(),
    };
    const live = { type: 'round.review.completed', event_id: 'live' } as EventMsg;
    const historical = { type: 'round.start', event_id: 'history' } as EventMsg;
    const replayed = streamReducer(initial, {
      kind: 'push-many',
      sid: 's-current',
      events: [historical],
    });

    const pushed = streamReducer(replayed, {
      kind: 'push-many',
      sid: 's-current',
      events: [historical, live],
    });

    expect(pushed.events.map((event) => event.event_id)).toEqual(['history', 'live']);
    expect(pushed.seen.size).toBe(2);
  });

  it('clears the previous project window and deduplication keys on a switch', () => {
    const old = { type: 'ui.argus', event_id: '1' } as EventMsg;
    const reset = streamReducer({ sid: 'old', events: [old], seen: new Set(['1']) }, { kind: 'reset', sid: 'new' });
    expect(reset.events).toEqual([]);
    expect(reset.seen.size).toBe(0);
    expect(streamReducer(reset, { kind: 'push', sid: 'new', ev: old }).events).toEqual([old]);
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
      { type: 'life.mission.completed', item_id: 'done', ts: 5.5 } as EventMsg,
    ])).not.toBe('');
    expect(snapshotRefreshEventKey([
      { type: 'engineer.progress', kind: 'assistant_message', ts: 6 } as EventMsg,
    ])).toBe('');
  });
});
