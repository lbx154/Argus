import assert from 'node:assert/strict';
import { test } from 'node:test';
import { computeSpend, fraction } from '../src/cost.js';

test('computeSpend sums cost_usd across completed missions', () => {
  const events = [
    { type: 'mission.started' },
    { type: 'life.mission.completed', cost_usd: 0.42 },
    { type: 'engineer.progress', text: 'x' },
    { type: 'mission.completed', cost_usd: 1.4 },
    { type: 'life.mission.completed', cost_usd: 0 }, // ignored (≤0)
    { type: 'life.mission.completed' }, // ignored (no cost)
  ];
  const s = computeSpend(events as never);
  assert.equal(Number(s.total.toFixed(2)), 1.82);
  assert.equal(s.missions, 2);
  assert.equal(s.last, 1.4);
});

test('computeSpend is empty for a stream with no costs', () => {
  const s = computeSpend([{ type: 'mission.started' }] as never);
  assert.deepEqual(s, { total: 0, missions: 0, last: 0 });
});

test('fraction clamps against the cap', () => {
  assert.equal(fraction(9, 180), 0.05);
  assert.equal(fraction(200, 180), 1); // clamp
  assert.equal(fraction(5, 0), 0); // no cap
  assert.equal(fraction(5, null), 0);
});
