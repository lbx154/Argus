import assert from 'node:assert/strict';
import { test } from 'node:test';
import { eventColor, eventLine, eventRole, isReasoning } from '../src/eventColor.js';

test('isReasoning only matches engineer.progress reasoning', () => {
  assert.equal(isReasoning({ type: 'engineer.progress', kind: 'reasoning' } as never), true);
  assert.equal(isReasoning({ type: 'engineer.progress', kind: 'assistant_message' } as never), false);
  assert.equal(isReasoning({ type: 'mission.started' } as never), false);
});

test('eventRole maps events to their driving role', () => {
  assert.equal(eventRole({ type: 'life.planner.step' } as never), 'planner');
  assert.equal(eventRole({ type: 'round.review.completed' } as never), 'reviewer');
  assert.equal(eventRole({ type: 'life.manager.intent' } as never), 'manager');
  assert.equal(eventRole({ type: 'engineer.progress' } as never), 'engineer');
  assert.equal(eventRole({ agent_layer: 'planner', type: 'x' } as never), 'planner');
});

test('eventColor keys off review verdict + type', () => {
  assert.equal(eventColor({ type: 'round.review.completed', status: 'done' } as never), '#3aa76a');
  assert.equal(eventColor({ type: 'round.review.completed', status: 'blocked' } as never), '#d15c6a');
  assert.equal(eventColor({ type: 'mission.error' } as never), '#d15c6a');
});

test('eventLine summarizes an engineer message', () => {
  const s = eventLine({ type: 'engineer.progress', kind: 'assistant_message', text: 'writing kernel' } as never);
  assert.ok(s.includes('writing kernel'));
});

test('eventLine accepts the supervised round.start schema', () => {
  assert.equal(eventLine({ type: 'round.start', round: 3 } as never), '── round 3');
});
