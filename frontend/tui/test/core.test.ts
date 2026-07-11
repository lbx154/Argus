import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  authoritativeSpend,
  computeSpend,
  defaultProject,
  deriveMissionView,
  eventKey,
  filterProjects,
  resolveProjectSelection,
} from '../../core/src/index.js';

test('shared project ranking chooses a live daemon before a newer stopped project', () => {
  const stopped = { id: 'new', label: 'new', objective: '', last_active: 20, daemon_alive: false, daemon_pid: null, uptime_seconds: null };
  const live = { id: 'live', label: 'Research', objective: '', last_active: 10, daemon_alive: true, daemon_pid: 1, uptime_seconds: 5 };
  assert.equal(defaultProject([stopped, live])?.id, 'live');
});

test('shared project lookup matches name, id, objective, and daemon state', () => {
  const rows = [
    { id: 's-kernel-42', label: 'AAAI Paper', objective: 'Reproduce flash attention benchmark', last_active: 2, daemon_alive: true, daemon_pid: 1, uptime_seconds: 3 },
    { id: 's-vision-7', label: 'Vision notes', objective: 'Review VLM datasets', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
  ];
  assert.deepEqual(filterProjects(rows, 'kernel live').map((row) => row.id), ['s-kernel-42']);
  assert.deepEqual(filterProjects(rows, 'VLM stopped').map((row) => row.id), ['s-vision-7']);
  assert.deepEqual(resolveProjectSelection(rows, 'missing'), {
    id: 's-kernel-42', requested: 'missing', recovered: true,
  });
});

test('shared event keys are independent of replay position', () => {
  const event = { type: 'life.mission.completed', ts: 10, status: 'done' };
  assert.equal(eventKey(event), eventKey({ ...event }));
});

test('shared spend includes planner phases and prefers the settled server total', () => {
  const observed = computeSpend([
    { type: 'life.planner.verdict', cost_usd: 0.2 },
    { type: 'life.mission.completed', cost_usd: 0.3 },
  ]);
  assert.equal(observed.total, 0.5);
  assert.equal(authoritativeSpend(observed, 0.8), 0.8);
});

test('shared mission state reports a completed campaign even when its daemon is alive', () => {
  const view = deriveMissionView({
    session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', per_mission_cap_usd: 1, daily_cap_usd: 2, global_daily_cap_usd: 0 },
    roles: [],
    backlog: [],
    recent_events: [],
    continuous: { enabled: false, objective: 'CO2 paper', done_reason: 'done' },
  });
  assert.equal(view.state, 'complete');
  assert.equal(view.objective, 'CO2 paper');
});

test('a fresh session with a lazy daemon is ready, not offline', () => {
  const view = deriveMissionView({
    session: { id: 's-fresh', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, per_mission_cap_usd: null, daily_cap_usd: null, global_daily_cap_usd: null },
    roles: [],
    backlog: [],
    recent_events: [],
    continuous: { enabled: false, objective: '', done_reason: '' },
  });
  assert.equal(view.state, 'idle');
  assert.equal(view.stateLabel, 'ready');
});

test('armed or queued work without an executor is not reported as working', () => {
  const snapshot = {
    session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, per_mission_cap_usd: null, daily_cap_usd: null, global_daily_cap_usd: null },
    roles: [],
    recent_events: [],
    backlog: [],
    continuous: { enabled: true, objective: 'Run the benchmark', done_reason: '' },
  } as Snapshot;
  const view = deriveMissionView(snapshot);
  assert.equal(view.state, 'waiting');
  assert.equal(view.stateLabel, 'queued');
});

test('pending backlog is shown as queued instead of ready', () => {
  const snapshot = {
    session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, per_mission_cap_usd: null, daily_cap_usd: null, global_daily_cap_usd: null },
    roles: [],
    recent_events: [],
    backlog: [{
      id: 'task-1', title: 'Run the experiment', objective: '', status: 'pending',
      priority: 1, max_cost_usd: 5,
    }],
    continuous: { enabled: false, objective: '', done_reason: '' },
  } as Snapshot;
  const view = deriveMissionView(snapshot);
  assert.equal(view.stateLabel, 'queued');
  assert.equal(view.objective, 'Run the experiment');
});
