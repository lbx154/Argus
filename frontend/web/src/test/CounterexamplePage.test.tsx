import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import { CounterexamplePage } from '../research-workbench/pages/CounterexamplePage';
import type { WorkspacePageProps } from '../research-workbench/pages/pageTypes';
import type { CounterexampleCandidate } from '../research-workbench/types';

vi.mock('../research-workbench/useWorkbenchText', () => ({
  useWorkbenchText: () => ({ text: (_zh: string, en: string) => en }),
}));

function page(missionStatus: string, candidateStatus = 'queued'): WorkspacePageProps {
  const mission = emptyMissionView();
  mission.mission.title = 'Investigate 10';
  mission.mission.status = missionStatus;
  const candidates = ['1', '10'].map((id): CounterexampleCandidate => ({
    id,
    title: `Candidate ${id}`,
    description: '',
    classification: '',
    source_grade: '',
    verification_level: '',
    status: candidateStatus,
    progress: 8,
    disposition: '',
    result_summary: '',
    rejection_reason: '',
    evidence_path: '',
    parallel_files: 0,
    updated_at: Date.now(),
  }));
  return {
    sid: 'lab',
    project: {
      id: 'lab', label: 'Lab', objective: '', last_active: 0,
      daemon_alive: false, daemon_pid: null, uptime_seconds: null,
    },
    snapshot: {
      session: { id: 'lab', display_name: 'Lab', objective: '', last_active: 0, cwd: '' },
      daemon: {
        alive: false, pid: null, uptime_seconds: null, backend: null,
        global_daily_cap_usd: null,
      },
      roles: [],
      mission_view: mission,
      backlog: [],
      recent_events: [],
    },
    counterexamples: {
      schema_version: 1, generated_at: Date.now(), total: 2, counts: {}, candidates,
    },
    events: [],
    transcript: [],
    artifacts: [],
    journal: [],
    connected: true,
    snapshotUpdatedAt: 0,
    refresh: () => undefined,
    controls: {
      start: async () => undefined, stop: async () => undefined, busy: false, error: '',
    },
    navigate: () => undefined,
  };
}

describe('CounterexamplePage live projection', () => {
  it('matches whole candidate IDs instead of marking 1 active when working on 10', () => {
    const html = renderToStaticMarkup(<CounterexamplePage {...page('working')} />);
    expect((html.match(/data-active="true"/g) ?? []).length).toBe(1);
    expect(html).toContain('Investigate 10');
  });

  it('does not project a completed mission as current activity', () => {
    const html = renderToStaticMarkup(<CounterexamplePage {...page('completed')} />);
    expect(html).not.toContain('data-active="true"');
    expect(html).not.toContain('Now working');
  });

  it('does not revive a terminal candidate as active', () => {
    const html = renderToStaticMarkup(<CounterexamplePage {...page('working', 'verified')} />);
    expect(html).not.toContain('data-active="true"');
    expect(html).toContain('Refuted');
  });

  it('does not treat old construction artifacts as a currently running mission', () => {
    const html = renderToStaticMarkup(<CounterexamplePage {...page('completed', 'constructing')} />);
    expect(html).not.toContain('data-active="true"');
    expect(html).not.toContain('Now working');
  });
});
