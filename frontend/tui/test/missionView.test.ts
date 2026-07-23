import assert from 'node:assert/strict';
import test from 'node:test';

import {
  emptyMissionView,
  projectMissionView,
  reduceMissionViewEvent,
} from '../../core/src/missionView.js';
import {
  budgetSummary,
  requestSummary,
} from '../src/components/MissionCockpit.js';
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
      global_daily_cap_usd: 200,
    },
    roles: [
      { role: 'engineer', backend: 'codex', backend_label: 'Codex', model: 'gpt', effort: 'high', active: true, label: 'Profiling', status: 'active', age_s: 0 },
    ],
    backlog: [{ id: 'task-1', title: 'Kernel v7', objective: 'Optimize kernel', status: 'running', priority: 1, deps: [] }],
    recent_events: [],
    mission_view: emptyMissionView(),
  };
}

test('shared projector applies reviewer verification without inventing research facts', () => {
  const events: EventMsg[] = [
    {
      type: 'round.review.completed',
      ts: 12,
      round_index: 7,
      status: 'done',
      reason: 'verified',
    },
  ];
  const view = projectMissionView(snapshot(), events);
  assert.equal(view.review.status, 'done');
  assert.equal(view.active_role, 'engineer');
  assert.equal(view.achievement, null);
});


test('achievement requires an explicit reviewer certification event', () => {
  const events: EventMsg[] = [
    {
      type: 'round.review.completed',
      ts: 12,
      status: 'done',
      reason: 'verified evidence',
    },
    {
      type: 'life.mission.completed',
      ts: 13,
      success: true,
      item_id: 'task-1',
      title: 'Kernel v7',
      objective: 'Optimize kernel',
    },
  ];
  const completed = projectMissionView(snapshot(), events);
  assert.equal(completed.achievement, null);

  const certified = reduceMissionViewEvent(completed, {
    type: 'research.achievement.certified',
    ts: 14,
    achievement_id: 'achievement-1',
    title: 'Kernel speedup certified',
    goal: 'Optimize kernel',
    summary: 'Reviewer accepted the measured gain.',
    evidence: ['result.json'],
    reviewer_certified: true,
  });
  assert.equal(certified.achievement?.reviewer_certified, true);
  assert.equal(certified.achievement?.title, 'Kernel speedup certified');
  assert.deepEqual(certified.achievement?.evidence, ['result.json']);
});


test('snapshot refreshes certified achievement counters from current state', () => {
  const current = snapshot();
  current.mission_view = emptyMissionView();
  current.mission_view.mission.started_at = Date.now() / 1000 - 3_600;
  current.mission_view.mission.status = 'working';
  current.mission_view.learned_skills = [{ id: 's1', name: 'skill', status: 'active' }];
  current.mission_view.review.rejected_attempts = 4;
  current.mission_view.achievement = {
    id: 'a1', title: 'certified', goal: '', summary: '',
    reviewer_certified: true, elapsed_seconds: 0,
    rejected_attempts: 0, skills_learned: 0, artifacts: 0, certified_at: 1,
  };
  const view = projectMissionView(current, [], [
    { path: 'result.md', name: 'result.md', kind: 'markdown', mime: 'text/markdown', exists: true, size: 1, mtime: 1, why: '', source: 'reviewer_evidence', group_title: 'Result' },
    { path: 'pending.md', name: 'pending.md', kind: 'markdown', mime: 'text/markdown', exists: false, size: 0, mtime: null, why: '', source: 'manager_live', group_title: 'Live' },
  ]);
  assert.ok((view.achievement?.elapsed_seconds ?? 0) >= 3_599);
  assert.equal(view.achievement?.rejected_attempts, 4);
  assert.equal(view.achievement?.skills_learned, 1);
  assert.equal(view.achievement?.artifacts, 1);
});


test('natural-language progress never invents a review verdict', () => {
  const view = projectMissionView(snapshot(), [{
    type: 'engineer.progress',
    ts: 20,
    kind: 'tool_use',
    agent_layer: 'engineer',
    text: 'Reviewer rejected; score is 999%',
  }]);
  assert.equal(view.review.status, '');
});

test('mission projector keeps research_incomplete distinct from failure', () => {
  const view = reduceMissionViewEvent(emptyMissionView(), {
    type: 'life.mission.completed',
    ts: 21,
    item_id: 'task-1',
    status: 'research_incomplete',
    success: false,
  });
  assert.equal(view.mission.status, 'incomplete');
  assert.equal(view.timeline.at(-1)?.title, 'Mission incomplete');
  assert.equal(view.timeline.at(-1)?.detail, 'research_incomplete');
});

test('mission projector keeps completion and stage certification independent', () => {
  const view = reduceMissionViewEvent(emptyMissionView(), {
    type: 'life.mission.completed',
    ts: 21,
    item_id: 'task-1',
    status: 'done',
    success: true,
    outcome: {
      execution_status: 'completed',
      review_status: 'done',
      stage_certification: 'not_certified',
      interruption_kind: 'none',
      resumable: false,
    },
  });
  assert.equal(view.mission.status, 'complete');
  assert.equal(view.outcome.stage_certification, 'not_certified');
});

test('mission projector forces life.mission.failed to failed even with malformed completion fields', () => {
  const view = reduceMissionViewEvent(emptyMissionView(), {
    type: 'life.mission.failed',
    ts: 22,
    item_id: 'task-1',
    title: 'Kernel v7',
    objective: 'Optimize kernel',
    status: 'research_incomplete',
    success: true,
    outcome_class: 'incomplete',
  });
  assert.equal(view.mission.status, 'failed');
  assert.equal(view.timeline.at(-1)?.title, 'Mission failed');
  assert.equal(view.timeline.at(-1)?.detail, 'Kernel v7');
});

test('idle snapshot clears stale role activity from historical events', () => {
  const idle = snapshot();
  idle.session.objective = '';
  idle.daemon.alive = false;
  idle.backlog = [];
  idle.roles = [{
    role: 'manager', backend: 'copilot', backend_label: 'Copilot', model: 'gpt',
    effort: 'high', active: false, label: 'idle', status: 'idle', age_s: 200,
  }];
  const view = projectMissionView(idle, [{
    type: 'engineer.progress',
    ts: 10,
    kind: 'assistant_message',
    agent_layer: 'manager',
    text: '你好。',
  }]);
  assert.equal(view.active_role, '');
  assert.equal(view.roles.find((role) => role.role === 'manager')?.status, 'waiting');
});

test('budget summary is always visible with global spend and cap', () => {
  assert.equal(
    budgetSummary(0.26285125, 'priced', 300),
    '$0.26 model calls / $300 daily cap',
  );
  assert.equal(budgetSummary(null, 'empty', 300), '$0.00 model calls / $300 daily cap');
});

test('request summary includes Codex, Copilot, and premium usage', () => {
  assert.equal(
    requestSummary({
      day: '2026-07-12',
      codex: {
        provider: 'codex', day: '2026-07-12', daily_calls: 34, daily_cap: 300,
        remaining: 266, completed_calls: 32, failed_calls: 2,
      },
      copilot: {
        provider: 'copilot', day: '2026-07-12', daily_calls: 246, daily_cap: 1000,
        remaining: 754, premium_requests: 360, premium_cap: 1000,
        premium_remaining: 640, blocked_until: 0, blocked_reason: '',
      },
    }),
    'Codex 34/300 · Copilot 246/1000 · premium 360.0/1000',
  );
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
  view = reduceMissionViewEvent(view, { type: 'skill.history.compressed', ts: 6, count: 3, bytes_saved: 1024 });
  view = reduceMissionViewEvent(view, { type: 'wiki.retired.compressed', ts: 7, count: 2, bytes_saved: 512 });

  assert.equal(view.storage.project_skill_count, 2);
  assert.equal(view.storage.global_skill_dir, '/state/global/skills');
  assert.deepEqual(view.storage.wiki_paths, ['/workspace/.autors/demo/wiki']);
  assert.equal(view.learned_wiki_pages[0]?.title, 'Bounded retry pattern');
  assert.equal(view.learned_wiki_pages[0]?.status, 'candidate');
  assert.equal(view.learned_skills[0]?.source_vertical, 'kernelbench');
  assert.equal(view.storage.skill_history_compressed, 3);
  assert.equal(view.storage.wiki_retired_compressed, 2);
  assert.equal(view.storage.skill_history_bytes_saved, 1024);
  assert.equal(view.storage.wiki_retired_bytes_saved, 512);
});
