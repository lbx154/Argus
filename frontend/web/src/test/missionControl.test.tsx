import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { emptyMissionView } from '../../../core/src/missionView';
import { compactMissionDag, MissionControl } from '../components/MissionControl';

describe('MissionControl', () => {
  it('renders real DAG, metric, capability, replay, and git state', () => {
    const view = emptyMissionView();
    view.mission.objective = 'Optimize FlashAttention on B200';
    view.stage = { id: 'optimize', label: 'Optimize' };
    view.round = { current: 7, max: 24 };
    view.dag = [{
      id: 'task-1', title: 'Profile kernel v7', objective: '', status: 'running',
      deps: [], branch_id: 'branch-1', parent_branch_id: null,
    }];
    view.metrics = [{
      id: 'metric-1', name: 'sol_percent', baseline: 49.4, value: 61.8,
      unit: '%', direction: 'maximize', evidence: 'result.json', primary: true,
      verification_status: 'accepted', reported_at: 1,
    }];
    view.primary_metric = view.metrics[0];
    view.learned_skills = [{ id: 'skill-1', name: 'fused epilogue', status: 'active' }];
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
    expect(markup).toContain('Self-evolution storage');
    expect(markup).toContain('Knowledge retained');
    expect(markup).toContain('Fused epilogue evidence');
    expect(markup).toContain('/state/project/skills');
    expect(markup).toContain('/workspace/.autors/demo/wiki');
    expect(markup).toContain('cold history · skill 4 · wiki 2 · 1.5 KB saved');
    expect(markup).toContain('Mission replay');
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
});
