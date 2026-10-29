import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
import { TopBar } from '../components/TopBar';
import { WorkStatusBar } from '../components/WorkStatusBar';

describe('status dot tone', () => {
  it('shows a running dot, not "waiting for you", while the team backs off and retries', () => {
    const snapshot = {
      session: { id: 's-research', display_name: 'Research', objective: '', cwd: '/workspace', last_active: 100 },
      daemon: { alive: true, pid: 1, uptime_seconds: 20, backend: 'pi' },
      roles: [], backlog: [], recent_events: [],
    } as Snapshot;
    const view = emptyMissionView();
    view.mission.id = 'task';
    const events = [{ type: 'round.backend_failure.backoff', item_id: 'task', ts: Date.now() / 1000 - 5, seconds: 600 }];
    const html = renderToStaticMarkup(<WorkStatusBar snapshot={snapshot} view={view} events={events as never} connected />);
    expect(html).toContain('data-state="waiting"');
    expect(html).toContain('data-phase="running"');
    expect(html).toContain('data-tone="running"');
  });

  const project = (alive: boolean, roles: Snapshot['roles'] = []) => ({
    session: { id: 's', display_name: 'Timer', objective: 'Make a timer', cwd: '/w', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: null, backend: 'codex' },
    roles, backlog: [{ id: 't1', title: 'Write timer.html', objective: '', status: alive ? 'in_progress' : 'done', priority: 0 }], recent_events: [],
  }) as Snapshot;
  const said = [{ type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', item_id: 't1', text: 'I am checking the dark theme.', ts: Date.now() / 1000 - 2 }];

  it('says where the work stands in one phrase, the same at the top and above the map', () => {
    const done = project(false);
    const bar = renderToStaticMarkup(<WorkStatusBar snapshot={done} events={said as never} connected compact />);
    const top = renderToStaticMarkup(<TopBar snap={done} events={said as never} streamOk onStart={() => {}} onStop={() => {}} onManage={() => {}} busy={false} />);
    for (const html of [bar, top]) {
      expect(html.match(/>Done</g)).toHaveLength(1);
      expect(html).not.toContain('Ready for a new task');
    }
    // How long ago it last moved matters only while it runs.
    expect(bar).not.toContain('Progress');
  });

  it('names who is at work, not each step they take', () => {
    const working = project(true, [{ role: 'engineer', active: true, status: 'running', label: 'Engineer', age_s: null, backend: 'codex', backend_label: 'Codex', model: '', effort: null, tasks: ['t1'] }]);
    const top = renderToStaticMarkup(<TopBar snap={working} events={said as never} streamOk onStart={() => {}} onStop={() => {}} onManage={() => {}} busy={false} />);
    expect(top).toContain('Working on: Write timer.html');
    expect(top).not.toContain('dark theme');
  });
});
