import { describe, it, expect } from 'vitest';
import { computeSpend, fraction } from '../lib/cost';
import type { EventMsg } from '../api';

/** Parity with frontend/tui/test/cost.test.ts — web spend math must match the
 *  terminal exactly (both port argus cost accounting). */
describe('computeSpend', () => {
  it('does not aggregate lifecycle-event costs', () => {
    const events: EventMsg[] = [
      { type: 'mission.started' },
      { type: 'life.mission.completed', cost_usd: 0.42 },
      { type: 'engineer.progress', text: 'x' },
      { type: 'mission.completed', cost_usd: 1.4 },
      { type: 'life.mission.completed', cost_usd: 0 }, // ignored (≤0)
      { type: 'life.mission.completed' }, // ignored (no cost)
    ];
    const s = computeSpend(events);
    expect(s.total).toBe(0);
    expect(s.missions).toBe(3);
    expect(s.last).toBe(0);
  });

  it('is empty for a stream with no costs', () => {
    expect(computeSpend([{ type: 'mission.started' }])).toEqual({ total: 0, missions: 0, last: 0 });
  });
});

describe('fraction', () => {
  it('clamps against the cap', () => {
    expect(fraction(9, 180)).toBe(0.05);
    expect(fraction(200, 180)).toBe(1);
    expect(fraction(5, 0)).toBe(0);
    expect(fraction(5, null)).toBe(0);
  });
});
