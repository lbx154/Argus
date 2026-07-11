import assert from 'node:assert/strict';
import test from 'node:test';

import {
  emptyMissionView,
  missionMetricGain,
  projectMissionView,
} from '../../core/src/missionView.js';
import type { EventMsg, Snapshot } from '../../core/src/types.js';

function snapshot(): Snapshot {
  return {
    schema_version: 5,
    session: { id: 's-1', display_name: '', objective: 'Optimize kernel', last_active: 0, cwd: '' },
    daemon: {
      alive: true,
      pid: 1,
      uptime_seconds: 10,
      backend: 'codex',
      per_mission_cap_usd: 30,
      daily_cap_usd: 50,
      global_daily_cap_usd: 200,
    },
    roles: [
      { role: 'engineer', backend: 'codex', backend_label: 'Codex', model: 'gpt', effort: 'high', active: true, label: 'Profiling', status: 'active', age_s: 0 },
    ],
    backlog: [{ id: 'task-1', title: 'Kernel v7', objective: 'Optimize kernel', status: 'running', priority: 1, max_cost_usd: 30, deps: [] }],
    recent_events: [],
    mission_view: emptyMissionView(),
  };
}

test('shared projector applies structured metric and reviewer verification', () => {
  const events: EventMsg[] = [
    {
      type: 'research.metric.reported',
      ts: 11,
      metric_id: 'm1',
      name: 'sol_percent',
      baseline: 49.4,
      value: 61.8,
      unit: '%',
      direction: 'maximize',
      evidence: 'result.json',
      round_index: 7,
      primary: true,
    },
    {
      type: 'round.review.completed',
      ts: 12,
      round_index: 7,
      status: 'done',
      reason: 'verified',
    },
  ];
  const view = projectMissionView(snapshot(), events);
  assert.equal(view.primary_metric?.value, 61.8);
  assert.equal(view.primary_metric?.verification_status, 'accepted');
  assert.ok(Math.abs((missionMetricGain(view.primary_metric) ?? 0) - 12.4) < 1e-9);
  assert.equal(view.active_role, 'engineer');
});

test('natural-language progress never invents a metric or review verdict', () => {
  const view = projectMissionView(snapshot(), [{
    type: 'engineer.progress',
    ts: 20,
    kind: 'tool_use',
    agent_layer: 'engineer',
    text: 'Reviewer rejected; score is 999%',
  }]);
  assert.equal(view.primary_metric, null);
  assert.equal(view.review.status, '');
});
