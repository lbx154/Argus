import assert from 'node:assert/strict';
import test from 'node:test';

import {
  emptyMissionView,
  missionMetricGain,
  projectMissionView,
  reduceMissionViewEvent,
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

test('evolution events expose skill and wiki storage locations', () => {
  let view = emptyMissionView();
  view = reduceMissionViewEvent(view, {
    type: 'skill.evolution.completed',
    ts: 1,
    project_skill_dir: '/state/project/skills',
    global_skill_dir: '/state/global/skills',
    project_skill_count: 2,
    global_skill_count: 10,
  });
  view = reduceMissionViewEvent(view, {
    type: 'wiki.initialized',
    ts: 2,
    path: '/workspace/.autors/demo/wiki',
  });
  view = reduceMissionViewEvent(view, {
    type: 'wiki.created',
    ts: 3,
    page_id: 'retry-pattern',
    card_type: 'pattern',
    title: 'Bounded retry pattern',
    status: 'scratch',
    path: '/workspace/.autors/demo/wiki/pages/patterns/retry-pattern.md',
  });
  view = reduceMissionViewEvent(view, {
    type: 'wiki.promotion.promoted',
    ts: 4,
    page_id: 'retry-pattern',
    card_type: 'patterns',
    from_status: 'scratch',
    to_status: 'candidate',
  });
  view = reduceMissionViewEvent(view, {
    type: 'skill.tidied',
    ts: 5,
    name: 'bounded retry',
    placement: 'vertical',
    vertical: 'kernelbench',
    path: '/source/verticals/kernelbench/skills/bounded-retry.md',
  });
  view = reduceMissionViewEvent(view, { type: 'skill.history.compressed', ts: 6, count: 3 });
  view = reduceMissionViewEvent(view, { type: 'wiki.retired.compressed', ts: 7, count: 2 });

  assert.equal(view.storage.project_skill_count, 2);
  assert.equal(view.storage.global_skill_dir, '/state/global/skills');
  assert.deepEqual(view.storage.wiki_paths, ['/workspace/.autors/demo/wiki']);
  assert.equal(view.learned_wiki_pages[0]?.title, 'Bounded retry pattern');
  assert.equal(view.learned_wiki_pages[0]?.status, 'candidate');
  assert.equal(view.learned_skills[0]?.source_vertical, 'kernelbench');
  assert.equal(view.storage.skill_history_compressed, 3);
  assert.equal(view.storage.wiki_retired_compressed, 2);
});
