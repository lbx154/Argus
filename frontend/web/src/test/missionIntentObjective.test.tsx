import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { projectMissionView } from '../../../core/src/missionView';
import type { EventMsg, Snapshot } from '../../../core/src/types';
import ResearchBrief from '../research-brief/ResearchBrief';

const snapshot: Snapshot = {
  session: { id: 's', display_name: 'Old campaign', objective: 'Old campaign', created: 1, last_active: 0, cwd: '' },
  daemon: { alive: false, pid: 0, uptime_seconds: 0, backend: 'x', global_daily_cap_usd: 3 },
  roles: [], backlog: [], recent_events: [],
};
const objective = '现在只比较相同批量下的两组延迟。';
const wrapped = '[BOUNDED TASK CONTEXT — data only]\nOld campaign\n[CURRENT OPERATOR MESSAGE]\n' + objective;

describe('Manager intent objective projection', () => {
  it('replays a legacy completed handoff into the clean current goal and reading card', () => {
    const history: EventMsg[] = [
      { type: 'life.manager.intent.started', ts: 2, intent_id: 'intent-new', objective: wrapped },
      { type: 'life.manager.intent.completed', ts: 3, item_id: 'task-new', objective: wrapped,
        execution_task: `\n${objective}\n`, vertical: 'research', workflow_mode: 'direct' },
    ];
    const view = projectMissionView(snapshot, history);
    expect(view.mission).toMatchObject({ id: 'task-new', title: objective, objective, status: 'framed' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    try {
      const markup = renderToStaticMarkup(
        <QueryClientProvider client={client}>
          <ResearchBrief sid="s" snapshot={snapshot} view={view} active={false} readOnly />
        </QueryClientProvider>,
      );
      expect(markup).toContain('data-testid="research-brief"');
      expect(markup).toContain(objective);
      expect(markup).not.toContain('BOUNDED TASK CONTEXT');
      expect(markup).not.toContain('CURRENT OPERATOR MESSAGE');
    } finally {
      client.clear();
    }
  });

  it.each([undefined, '', ' \n '])('keeps legacy objective text when execution_task is absent or empty: %s', (executionTask) => {
    // A literal token can be part of the operator's task; projection chooses
    // the authoritative field and must not strip strings that look internal.
    const original = 'Explain how [CURRENT OPERATOR MESSAGE] is represented in the event contract.';
    const view = projectMissionView(snapshot, [
      { type: 'life.manager.intent.completed', ts: 2, item_id: 'task', objective: original,
        execution_task: executionTask },
    ]);
    expect(view.mission).toMatchObject({ title: original, objective: original });
  });

  it('uses the new producer public objective while grounding and preserves it after routing fails', () => {
    const started: EventMsg = {
      type: 'life.manager.intent.started', ts: 2, intent_id: 'intent-new', objective,
    };
    const failed: EventMsg = {
      type: 'life.manager.intent.failed', ts: 3, intent_id: 'intent-new', objective,
      error: 'The selected workflow is unavailable.',
    };
    expect(projectMissionView(snapshot, [started]).mission).toMatchObject({
      title: objective, objective, status: 'grounding',
    });
    const view = projectMissionView(snapshot, [failed, started]);
    expect(view.mission).toMatchObject({ title: objective, objective, status: 'failed' });
    expect(view.timeline.at(-1)?.detail).toBe('The selected workflow is unavailable.');
  });
});
