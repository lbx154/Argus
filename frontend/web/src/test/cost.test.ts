import { describe, it, expect } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { computeSpend, fraction } from '../lib/cost';
import type { EventMsg } from '../api';
import { CostGauge } from '../components/CostGauge';

/** Parity with frontend/tui/test/cost.test.ts — web spend math must match the
 *  terminal exactly (both port argus_skill cost accounting). */
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

describe('CostGauge', () => {
  it('renders missing usage as partial instead of $0.00', () => {
    const markup = renderToStaticMarkup(
      React.createElement(CostGauge, {
        spend: { total: 0, missions: 0, last: 0 },
        settledUsd: null,
        spendStatus: 'partial',
        daemon: undefined,
      }),
    );
    expect(markup).toContain('partial');
    expect(markup).not.toContain('$0.00');
  });

  it('surfaces provider fence breaches as a cost-control error', () => {
    const markup = renderToStaticMarkup(
      React.createElement(CostGauge, {
        spend: { total: 1, missions: 1, last: 1 },
        settledUsd: 1,
        daemon: undefined,
        costControl: {
          day: '2026-07-11',
          active_reservations: 0,
          reserved_usd: 0,
          unresolved_calls: 0,
          unresolved: [],
          fence_breach_calls: 1,
          fence_breaches: [],
          fence_breach_remaining_seconds: 600,
          policy: 'block',
          fence_breach_policy: 'block',
        },
      }),
    );
    expect(markup).toContain('fence breaches 1');
    expect(markup).toContain('retry in 10m');
    expect(markup).toContain('text-err');
  });
});
