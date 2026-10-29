import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  authoritativeSpend,
  computeSpend,
  defaultProject,
  eventKey,
  filterProjects,
  isPromptRewriteShortcut,
  resolveProjectSelection,
} from '../../core/src/index.js';

test('shared project ranking chooses a live daemon before a newer stopped project', () => {
  const stopped = { workdir: '/work', id: 'new', label: 'new', last_active: 20, daemon_alive: false, daemon_pid: null, };
  const live = { workdir: '/work', id: 'live', label: 'Research', last_active: 10, daemon_alive: true, daemon_pid: 1, };
  assert.equal(defaultProject([stopped, live])?.id, 'live');
});

test('shared project lookup matches name, id, and daemon state', () => {
  const rows = [
    { workdir: '/work', id: 's-kernel-42', label: 'AAAI Paper', last_active: 2, daemon_alive: true, daemon_pid: 1, },
    { workdir: '/work', id: 's-vision-7', label: 'Vision notes', last_active: 1, daemon_alive: false, daemon_pid: null, },
  ];
  assert.deepEqual(filterProjects(rows, 'kernel live').map((row) => row.id), ['s-kernel-42']);
  assert.deepEqual(filterProjects(rows, 'vision stopped').map((row) => row.id), ['s-vision-7']);
  assert.deepEqual(resolveProjectSelection(rows, 'missing'), {
    id: 's-kernel-42', requested: 'missing', recovered: true,
  });
});

test('prompt rewrite shortcut accepts Ctrl-R and Cmd-R only', () => {
  assert.equal(isPromptRewriteShortcut('r', true, false), true);
  assert.equal(isPromptRewriteShortcut('R', false, true), true);
  assert.equal(isPromptRewriteShortcut('r', false, false), false);
  assert.equal(isPromptRewriteShortcut('x', true, false), false);
});

test('shared event keys are independent of replay position', () => {
  const event = { type: 'life.mission.completed', ts: 10, status: 'done' };
  assert.equal(eventKey(event), eventKey({ ...event }));
});

test('shared spend ignores lifecycle totals and prefers the server ledger', () => {
  const observed = computeSpend([
    { type: 'life.planner.verdict', cost_usd: 0.2 },
    { type: 'life.mission.completed', cost_usd: 0.3 },
  ]);
  assert.equal(observed.total, 0);
  assert.equal(authoritativeSpend(observed, 0.8), 0.8);
});
