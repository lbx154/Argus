import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { emptyMissionView, reduceMissionViewEvent } from '../../../core/src/missionView';
import { compactMissionDag, MissionControl } from '../components/MissionControl';

describe('MissionControl', () => {
  it('renders real DAG, capability, replay, and git state', () => {
    const view = emptyMissionView();
    view.mission.objective = 'Optimize FlashAttention on B200';
    view.mission.status = 'working';
    view.active_role = 'engineer';
    view.stage = { id: 'optimize', label: 'Optimize' };
    view.round = { current: 7, max: 24 };
    view.dag = [{
      id: 'task-1', title: 'Profile kernel v7', objective: 'Measure fused memory traffic', status: 'running',
      deps: [], branch_id: 'branch-1', parent_branch_id: null,
      acceptance_check: 'Official scorer passes.',
      plan_hypothesis: 'Fused traffic is the remaining bottleneck.',
      goal_contribution: 'Move the official score toward the user target.',
      expected_regressions: 'Local latency may rise before fusion is tuned.',
      decision_rule: 'Replace the route if memory traffic is not causal.',
      non_goals: ['Do not change the benchmark.'],
    }];
    view.learned_skills = [{
      id: 'skill-1',
      name: 'fused epilogue',
      version: 2,
      scope: 'project',
      path: '/state/project/skills/fused.md',
      status: 'active',
      updated_at: 1,
      mission_id: 'task-1',
      mission_title: 'Profile kernel v7',
      content: '# Fused epilogue\n\nKeep the measured evidence.',
      content_truncated: true,
    }];
    view.role_work = [
      {
        id: 'planner-task',
        ts: 1,
        role: 'planner',
        kind: 'task',
        title: 'Profile kernel v7',
        detail: 'Measure fused memory traffic',
        status: 'pending',
        item_id: 'task-1',
        mission_id: 'task-1',
      },
      {
        id: 'engineer-work',
        ts: 2,
        role: 'engineer',
        kind: 'tool_use',
        title: 'Using a tool',
        detail: 'Inspecting fused and unfused memory traffic.',
        status: 'active',
        item_id: 'task-1',
        mission_id: 'task-1',
        round_index: 7,
      },
    ];
    view.storage.project_skill_dir = '/state/project/skills';
    view.storage.project_skill_count = 1;
    view.storage.wiki_paths = ['/workspace/.autors/demo/wiki'];
    view.storage.skill_history_compressed = 4;
    view.storage.wiki_retired_compressed = 2;
    view.storage.skill_history_bytes_saved = 1024;
    view.storage.wiki_retired_bytes_saved = 512;
    view.learned_wiki_pages = [{ id: 'page-1', title: 'Fused epilogue evidence', status: 'candidate' }];
    view.timeline = [{
      id: 'event-1', ts: 1, type: 'round.review.completed', role: 'reviewer',
      title: 'Evidence accepted', detail: 'Official scorer passed.', tone: 'success',
    }];
    view.outcome = {
      execution_status: 'completed',
      review_status: 'done',
      stage_certification: 'not_certified',
      interruption_kind: 'none',
      resumable: false,
    };
    const markup = renderToStaticMarkup(
      <MissionControl
        view={view}
        gitDiff={{
          available: true,
          branch: 'main',
          status: ' M kernel.py',
          stat: ' kernel.py | 2 +-',
          diff: '+fused_epilogue',
          truncated: false,
        }}
      />,
    );
    expect(markup).toContain('Optimize FlashAttention on B200');
    expect(markup).toContain('Engineer — Using a tool');
    expect(markup).not.toContain('Total elapsed');
    expect(markup).not.toContain('Round</div>');
    expect(markup).not.toContain('Mode</div>');
    expect(markup).toContain('Task route');
    expect(markup).toContain('Official scorer passed');
    expect(markup).toContain('Capabilities unlocked');
    expect(markup).toContain('Role work');
    expect(markup).toContain('Profile kernel v7');
    expect(markup).toContain('Measure fused memory traffic');
    expect(markup).toContain('Working hypothesis · can be revised');
    expect(markup).toContain('Fused traffic is the remaining bottleneck.');
    expect(markup).toContain('Move the official score toward the user target.');
    expect(markup).toContain('Local latency may rise before fusion is tuned.');
    expect(markup).toContain('Replace the route if memory traffic is not causal.');
    expect(markup).toContain('Official scorer passes.');
    expect(markup).toContain('Do not change the benchmark.');
    expect(markup).toContain('Learned during Profile kernel v7');
    expect(markup).toContain('# Fused epilogue');
    expect(markup).toContain('Content preview truncated');
    expect(markup).toContain('Saved project knowledge');
    expect(markup).toContain('Knowledge retained');
    expect(markup).toContain('Fused epilogue evidence');
    expect(markup).not.toContain('/state/project/skills');
    expect(markup).not.toContain('/workspace/.autors/demo/wiki');
    expect(markup).toContain('Mission replay');
    expect(markup).not.toContain('Work completed');
    expect(markup).not.toContain('Stage not approved');
    expect(markup).toContain('Project files changed');
    expect(markup).not.toContain('+fused_epilogue');
  });

  it('shows one plain-language status narrative for each mission state', () => {
    const healthy = emptyMissionView();
    const healthyMarkup = renderToStaticMarkup(<MissionControl view={healthy} />);
    expect(healthyMarkup).not.toContain('role="alert"');
    expect(healthyMarkup).toContain('Ready when you are — assign a mission to begin.');
    expect(healthyMarkup).toContain('No capabilities learned yet.');

    const paused = emptyMissionView();
    paused.stage.id = 'HOLD';
    expect(renderToStaticMarkup(<MissionControl view={paused} />)).toContain('Mission is paused — waiting for your input.');

    const failedStep = emptyMissionView();
    failedStep.dag = [{
      id: 'failed-step', title: 'Run checks', objective: '', status: 'failed', deps: [],
      branch_id: 'failed-step', parent_branch_id: null,
    }];
    const diagnostics = `${'D'.repeat(305)}RAW_TAIL`;
    failedStep.timeline = [{
      id: 'planner-error', ts: 1, type: 'life.planner.error', role: 'planner',
      title: 'life.planner.error', detail: diagnostics, tone: 'error',
    }];
    const failureMarkup = renderToStaticMarkup(<MissionControl view={failedStep} />);
    expect(failureMarkup).toContain('A step failed — check the task below.');
    expect(failureMarkup).toContain('Planner failed');
    expect(failureMarkup).not.toContain('life.planner.error');
    expect(failureMarkup).toContain(`${'D'.repeat(300)}…`);
    expect(failureMarkup).not.toContain('RAW_TAIL');
    expect(failureMarkup).toContain('aria-expanded="false"');
    expect(failureMarkup).toContain('Show more');

    const critical = emptyMissionView();
    critical.health = 'degraded';
    expect(renderToStaticMarkup(<MissionControl view={critical} />)).toContain('System error — health is degraded.');

    const complete = emptyMissionView();
    complete.mission.status = 'complete';
    complete.mission.elapsed_seconds = 125;
    complete.outcome.execution_status = 'completed';
    complete.frontier.change = 'The benchmark route is now stable.';

    const markup = renderToStaticMarkup(<MissionControl view={complete} />);

    expect(markup).toContain('Work completed — finished in 2m.');
    expect(markup).toContain('The benchmark route is now stable.');
  });

  it('renders escaped objective Markdown without exposing transport slashes', () => {
    const view = emptyMissionView();
    view.mission.objective = '数论猜想\n- \\*\\*问题\\*\\*: $n=2,3,\\\\dots$';

    const markup = renderToStaticMarkup(<MissionControl view={view} />);

    expect(markup).toContain('<strong');
    expect(markup).toContain('问题');
    expect(markup).not.toContain('\\*\\*问题');
  });

  it('collapses old DAG history while retaining the active branch', () => {
    const view = emptyMissionView();
    view.dag = Array.from({ length: 30 }, (_, index) => ({
      id: `task-${index}`,
      title: `Task ${index}`,
      objective: '',
      status: index === 5 ? 'running' : index < 20 ? 'skipped' : 'done',
      deps: index === 5 ? ['task-4'] : [],
      branch_id: `task-${index}`,
      parent_branch_id: null,
    }));
    const compact = compactMissionDag(view, 8);
    expect(compact.nodes.some((node) => node.id === 'task-5')).toBe(true);
    expect(compact.nodes.some((node) => node.id === 'task-4')).toBe(true);
    expect(compact.nodes.some((node) => node.id === 'task-29')).toBe(true);
    expect(compact.hidden.length).toBeGreaterThan(0);
  });

  it('resets review projection when the next mission starts', () => {
    const view = emptyMissionView();
    reduceMissionViewEvent(view, {
      type: 'round.review.completed',
      ts: 1,
      status: 'done',
      reason: 'First mission accepted.',
    });
    reduceMissionViewEvent(view, {
      type: 'life.mission.started',
      ts: 2,
      item_id: 'task-2',
      title: 'Second mission',
      objective: 'Execute the second mission.',
    });

    expect(view.review).toEqual({ status: '', reason: '', rejected_attempts: 0 });
    expect(view.roles.find((role) => role.role === 'reviewer')).toMatchObject({
      status: 'waiting',
      label: 'Awaiting engineer handoff',
    });
  });

  it('keeps live stage and active role aligned with terminal events', () => {
    const view = emptyMissionView();
    reduceMissionViewEvent(view, {
      type: 'life.manager.stage_decision',
      ts: 1,
      target_stage: 'run',
    });
    reduceMissionViewEvent(view, {
      type: 'life.manager.intent.completed',
      ts: 2,
      objective: 'Reframed mission',
      current_stage: 'research',
      stages: ['research', 'plan'],
    });
    reduceMissionViewEvent(view, {
      type: 'life.planner.start',
      ts: 3,
    });
    reduceMissionViewEvent(view, {
      type: 'life.planner.verdict',
      ts: 4,
      project_done: true,
      reason: 'Planning complete.',
    });

    expect(view.stage).toEqual({ id: 'research', label: 'research' });
    expect(view.active_role).toBe('');
  });
});
