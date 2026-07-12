import assert from 'node:assert/strict';
import { test } from 'node:test';
import { activeGuardianAlert } from '../src/guardian.js';
import type { EventMsg } from '../src/api.js';

const ev = (o: Record<string, unknown>) => o as EventMsg;

test('no alert when the stream is calm', () => {
  assert.equal(activeGuardianAlert([ev({ type: 'round.main.completed' })]), null);
});

test('surfaces a hard block as a block-tone alert', () => {
  const a = activeGuardianAlert([
    ev({ type: 'round.started' }),
    ev({ type: 'life.lifecycle.block', reason: 'missing GPU credentials' }),
  ]);
  assert.equal(a?.tone, 'block');
  assert.ok(a?.text.includes('missing GPU credentials'));
});

test('surfaces ANY operator_alert:true event as a block', () => {
  const a = activeGuardianAlert([ev({ type: 'round.reviewer_backend_failure', operator_alert: true, text: 'backend down' })]);
  assert.equal(a?.tone, 'block');
  assert.equal(a?.text, 'backend down');
});

test('a stall is a warn-tone alert', () => {
  const a = activeGuardianAlert([ev({ type: 'round.stall', text: 'no forward progress 3/3' })]);
  assert.equal(a?.tone, 'warn');
});

test('the alert clears once the mission moves on', () => {
  const a = activeGuardianAlert([
    ev({ type: 'life.lifecycle.block', reason: 'needs creds' }),
    ev({ type: 'ui.operator', text: 'here are the creds' }), // operator responded
    ev({ type: 'mission.started', text: 'resumed' }),
  ]);
  assert.equal(a, null);
});

test('the LATEST unresolved alert wins', () => {
  const a = activeGuardianAlert([
    ev({ type: 'round.stall', text: 'stalled' }),
    ev({ type: 'round.main.completed' }), // clears the stall
    ev({ type: 'life.budget.pause', text: 'daily cap hit' }), // new, unresolved
  ]);
  assert.equal(a?.tone, 'warn');
  assert.ok(a?.text.includes('daily cap'));
});

test('a denied cost reservation raises a budget alarm', () => {
  const a = activeGuardianAlert([
    ev({
      type: 'budget.reservation.denied',
      reason: 'global daily budget exhausted',
    }),
  ]);
  assert.equal(a?.tone, 'block');
  assert.equal(a?.kind, 'budget');
  assert.ok(a?.text.includes('global daily budget exhausted'));
  assert.equal(activeGuardianAlert([
    ev({
      type: 'budget.reservation.denied',
      reason: 'global daily budget exhausted',
    }),
    ev({ type: 'ui.operator', text: 'retry' }),
    ev({ type: 'round.start' }),
  ])?.kind, 'budget');
  assert.equal(activeGuardianAlert([
    ev({
      type: 'budget.reservation.denied',
      reason: 'global daily budget exhausted',
    }),
    ev({ type: 'provider.request.started' }),
  ]), null);
});
