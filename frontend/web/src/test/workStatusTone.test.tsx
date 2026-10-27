import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
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
});
