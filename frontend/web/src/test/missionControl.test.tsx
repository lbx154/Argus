import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { emptyMissionView, reduceMissionViewEvent } from '../../../core/src/missionView';
import { compactMissionDag, MissionControl } from '../components/MissionControl';

describe('MissionControl', () => {
  it('renders real DAG, metric, capability, replay, and git state', () => {
    const view = emptyMissionView();
    view.mission.objective = 'Optimize FlashAttention on B200';
    view.stage = { id: 'optimize', label: 'Optimize' };
    view.round = { current: 7, max: 24 };
    view.dag = [{
      id: 'task-1', title: 'Profile kernel v7', objective: 'Measure fused memory traffic', status: 'running',
      deps: [], branch_id: 'branch-1', parent_branch_id: null,
      acceptance_check: 'Official scorer passes.',
      non_goals: ['Do not change the benchmark.'],
    }];
    view.metrics = [{
      id: 'metric-1', name: 'sol_percent', baseline: 49.4, value: 61.8,
      unit: '%', direction: 'maximize', evidence: 'result.json', primary: true,
      verification_status: 'accepted', reported_at: 1,
    }];
    view.primary_metric = view.metrics[0];
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
    view.decision_context = {
      planner_report: { plan_signal: 'continue', reason: 'Evidence is complete.' },
    };
    view.storage.project_skill_dir = '/state/project/skills';
    view.storage.project_skill_count = 1;
    view.storage.wiki_paths = ['/workspace/.autors/demo/wiki'];
    view.storage.skill_history_compressed = 4;
    view.storage.wiki_retired_compressed = 2;
    view.storage.skill_history_bytes_saved = 1024;
    view.storage.wiki_retired_bytes_saved = 512;
    view.learned_wiki_pages = [{ id: 'page-1', title: 'Fused epilogue evidence', status: 'candidate' }];
    view.timeline = [{
      id: 'event-1', ts: 1, type: 'research.metric.reported', role: 'engineer',
      title: 'Metric reported', detail: '61.8%', tone: 'metric',
    }];
    view.outcome = {
      execution_status: 'completed',
      review_status: 'done',
      stage_certification: 'not_certified',
      scientific_decision: 'no_go',
      failure_source: 'scientific_evidence_failure',
      failure_layer: 'evaluator',
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
    expect(markup).toContain('Research DAG');
    expect(markup).toContain('61.8%');
    expect(markup).toContain('Capabilities unlocked');
    expect(markup).toContain('Role work');
    expect(markup).toContain('Profile kernel v7');
    expect(markup).toContain('Measure fused memory traffic');
    expect(markup).toContain('Official scorer passes.');
    expect(markup).toContain('Do not change the benchmark.');
    expect(markup).toContain('evolved during · Profile kernel v7');
    expect(markup).toContain('# Fused epilogue');
    expect(markup).toContain('Structured decision handoff');
    expect(markup).toContain('plan_signal');
    expect(markup).toContain('Self-evolution storage');
    expect(markup).toContain('Knowledge retained');
    expect(markup).toContain('Fused epilogue evidence');
    expect(markup).toContain('/state/project/skills');
    expect(markup).toContain('/workspace/.autors/demo/wiki');
    expect(markup).toContain('cold history · skill 4 · wiki 2 · 1.5 KB saved');
    expect(markup).toContain('Mission replay');
    expect(markup).toContain('execution=completed');
    expect(markup).toContain('stage=not_certified');
    expect(markup).toContain('science=no_go');
    expect(markup).toContain('layer=evaluator');
    expect(markup).toContain('Git changes · main');
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
