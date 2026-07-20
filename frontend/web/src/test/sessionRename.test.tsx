import { QueryClient } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { ProjectRow, Snapshot } from '../../../core/src/types';
import { DaemonManageModal } from '../components/DaemonManageModal';
import { Sidebar } from '../components/Sidebar';
import { TopBar } from '../components/TopBar';
import { cacheProjectName } from '../lib/projectName';

const sid = 's-research1';

function snapshot(): Snapshot {
  return {
    session: {
      id: sid,
      display_name: 'Original name',
      objective: 'Study agent reliability',
      last_active: 1,
      cwd: '/state/projects/s-research1',
    },
    daemon: {
      alive: false,
      pid: null,
      uptime_seconds: null,
      backend: null,
      global_daily_cap_usd: null,
    },
    roles: [],
    backlog: [],
    recent_events: [],
  };
}

interface ProjectIndex {
  projects: ProjectRow[];
  local_cwd: string;
}

function projectIndex(): ProjectIndex {
  return {
    local_cwd: '/workspace/argus',
    projects: [{
      id: sid,
      label: 'Original name',
      display_name: 'Original name',
      objective: 'Study agent reliability',
      launch_cwd: '/workspace/argus',
      last_active: 1,
      daemon_alive: false,
      daemon_pid: null,
      uptime_seconds: null,
    }],
  };
}

describe('session rename', () => {
  it('exposes an editable display-name control for an existing session', () => {
    const markup = renderToStaticMarkup(
      <DaemonManageModal
        open
        sid={sid}
        name="Original name"
        alive={false}
        busy={false}
        onClose={() => undefined}
        onRename={async () => true}
        onStart={async () => true}
        onPause={async () => true}
        onDelete={async () => true}
      />,
    );

    expect(markup).toContain('Display name');
    expect(markup).toContain('value="Original name"');
    expect(markup).toContain('type="submit"');
  });

  it('updates the cached session list and header from the persisted server name', () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(['snapshot', sid], snapshot());
    queryClient.setQueryData(['projects'], projectIndex());

    cacheProjectName(queryClient, sid, 'Operator title');

    const renamedSnapshot = queryClient.getQueryData<Snapshot>(['snapshot', sid]);
    const renamedIndex = queryClient.getQueryData<ProjectIndex>(['projects']);
    if (!renamedSnapshot || !renamedIndex) {
      throw new Error('rename cache entries were not retained');
    }
    expect(renamedSnapshot.session.display_name).toBe('Operator title');
    expect(renamedIndex.projects[0].label).toBe('Operator title');

    const header = renderToStaticMarkup(
      <TopBar
        snap={renamedSnapshot}
        streamOk
        onStart={() => undefined}
        onStop={() => undefined}
        onManage={() => undefined}
        busy={false}
      />,
    );
    const sessionList = renderToStaticMarkup(
      <Sidebar
        projects={renamedIndex.projects}
        activeId={sid}
        localCwd="/workspace/argus"
        onSelect={() => undefined}
        onManage={() => undefined}
        onOpenPanel={() => undefined}
        onNew={() => undefined}
        loading={false}
        onToggleCollapse={() => undefined}
        themeMode="light"
        onCycleTheme={() => undefined}
      />,
    );
    expect(header).toContain('Operator title');
    expect(sessionList).toContain('Operator title');
  });
});
