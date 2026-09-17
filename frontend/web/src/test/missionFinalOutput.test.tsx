import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { emptyMissionView, projectMissionView, reduceMissionViewEvent } from '../../../core/src/missionView';
import type { EventMsg, Snapshot } from '../../../core/src/types';
import { MissionControl } from '../components/MissionControl';

const handoff: EventMsg = {
  type: 'engineer.progress', ts: 3, kind: 'agent_message', agent_layer: 'main',
  final_delivery: true,
  text: '# Last handoff\n\nkept\nDecision:\nNEXT_OWNER=reviewer\nMILESTONE_STATUS=done',
};
const history: EventMsg[] = [
  { type: 'life.mission.started', ts: 1, item_id: 'task' },
  { ...handoff, ts: 2, text: 'Superseded handoff\n'.repeat(500) },
  handoff,
  { ...handoff, ts: 4, final_delivery: false, text: 'Later progress, not a delivery' },
  { ...handoff, ts: 5, agent_layer: 'reviewer', text: 'Reviewer output' },
  { type: 'life.mission.completed', ts: 6, item_id: 'task', status: 'done', success: true, summary: 'Compact summary' },
];
const snapshot: Snapshot = {
  session: { id: 's', display_name: '', objective: '', created: 1, last_active: 0, cwd: '' },
  daemon: { alive: false, pid: 0, uptime_seconds: 0, backend: 'x', global_daily_cap_usd: 3 },
  roles: [], backlog: [], recent_events: [],
};

describe('mission final output', () => {
  it.each([false, true])('uses the last final delivery with a newer snapshot: %s', (seeded) => {
    const view = emptyMissionView();
    if (seeded) {
      Object.assign(view.mission, {
        id: 'task', status: 'complete', summary: 'Compact summary', started_at: 1, completed_at: 6,
      });
      view.last_event_ts = 10;
    }
    const projected = projectMissionView(
      { ...snapshot, mission_view: view }, [...history].reverse(),
    );
    expect(projected.mission.final_output).toBe('# Last handoff\n\nkept');
    expect(projected.mission.summary).toBe('Compact summary');
    reduceMissionViewEvent(projected, { ...handoff, ts: 11, text: 'Late unrelated message' });
    expect(projected.mission.final_output).toBe('# Last handoff\n\nkept');
  });

  it.each(['', '# Authoritative\n\n' + 'detail\n'.repeat(300)])(
    'treats the explicit completion field as authoritative, including empty output',
    (finalOutput) => {
      const events = history.map((event) => event.ts === 6
        ? { ...event, final_output: finalOutput } : event);
      for (const seeded of [false, true]) {
        const view = emptyMissionView();
        if (seeded) {
          Object.assign(view.mission, {
            id: 'task', status: 'complete', started_at: 1, completed_at: 6,
          });
          view.last_event_ts = 10;
        }
        expect(projectMissionView({ ...snapshot, mission_view: view }, events).mission.final_output)
          .toBe(finalOutput.trim());
      }
    },
  );

  it.each([
    ['missing start', history.slice(1)],
    ['wrong start', [{ ...history[0], item_id: 'other' }, ...history.slice(1)]],
    ['intervening start', [...history.slice(0, 3), { ...history[0], ts: 4, item_id: 'other' }, history[5]]],
    ['resumed same item', [...history, { ...history[0], ts: 7 }]],
    ['resumed without delivery', [...history, { ...history[0], ts: 7 }, { ...history[5], ts: 8 }]],
    ['new Manager intent', [...history, { type: 'life.manager.intent.started', ts: 7, item_id: 'task' }]],
    ['no final delivery', history.map((event) => ({ ...event, final_delivery: false }))],
    ['footer-only final delivery', history.map((event) => event.ts === 3
      ? { ...event, text: 'Decision:\nMILESTONE_STATUS=done\nNEXT_OWNER=reviewer' } : event)],
    ['second completion without start', [...history, { ...history[5], ts: 8 }]],
    ['wrong delivery item', history.map((event) => event.type === 'engineer.progress'
      ? { ...event, item_id: 'other' } : event)],
  ] satisfies [string, EventMsg[]][])('does not recover across %s', (_label, events) => {
    const view = emptyMissionView();
    Object.assign(view.mission, { id: 'task', status: 'complete' });
    view.last_event_ts = 10;
    expect(projectMissionView({ ...snapshot, mission_view: view }, events).mission.final_output).toBe('');
    expect(projectMissionView(snapshot, events).mission.final_output).toBe('');
  });

  it.each(['task', 'next-task'])('clears old output when the live backlog starts %s', (id) => {
    const view = projectMissionView(snapshot, history);
    const activeSnapshot: Snapshot = {
      ...snapshot,
      mission_view: view,
      backlog: [{
        id, title: 'Resume', objective: '', status: 'running', priority: 100,
        iterate: false, pending_question: '', started_ts: 7, finished_ts: null, deps: [],
        iteration_max_cycles: 1, iteration_cycles_done: 0,
      }],
    };
    const projected = projectMissionView(activeSnapshot, history);
    expect(projected.mission.final_output).toBe('');
    expect(projected.mission.summary).toBe('');
    expect(projected.mission.started_at).toBe(7);
    expect(view.mission.final_output).toBe('# Last handoff\n\nkept');
  });

  it('projects and renders the complete Markdown handoff without losing artifact links or the tail', () => {
    const body = '# Complete report\n\n' + 'Verified detail.\n\n'.repeat(1000)
      + '[Open result](report.md)\n\n**Final section survives.**\n\n<script>unsafe()</script>';
    const view = projectMissionView(snapshot, [
      history[0],
      { ...history[5], final_output: body },
    ]);
    const markup = renderToStaticMarkup(
      <MissionControl
        view={view}
        artifacts={[{
          path: 'report.md', name: 'report.md', kind: 'markdown', exists: true,
          why: 'Verified report', mime: 'text/markdown', size: body.length, mtime: 6,
        }]}
        onOpenArtifact={() => {}}
      />,
    );
    expect(view.mission.final_output).toBe(body);
    expect(view.mission.summary).toBe('Compact summary');
    expect(markup).toContain('<details');
    expect(markup).toContain('View full output');
    expect(markup).toContain('>Complete report</h1>');
    expect(markup).toContain('Final section survives.</strong>');
    expect(markup).toContain('Open result');
    expect(markup).toContain('data-artifact-path="report.md"');
    expect(markup).not.toContain('<script>');
  });

  it('shows output without a summary and avoids a duplicate disclosure', () => {
    const view = emptyMissionView();
    view.mission.final_output = '# Full output only';
    expect(renderToStaticMarkup(<MissionControl view={view} />)).toContain('View full output');
    view.mission.summary = '# Full output only';
    expect(renderToStaticMarkup(<MissionControl view={view} />)).not.toContain('View full output');
  });
});
