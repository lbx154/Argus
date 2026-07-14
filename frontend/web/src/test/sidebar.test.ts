import { describe, expect, it } from 'vitest';

import type { ProjectRow } from '../api';
import { recommendedSidebarScope } from '../components/Sidebar';

const rows: ProjectRow[] = [
  {
    id: 'local', label: 'Local', display_name: 'Local', objective: '',
    launch_cwd: '/workspace/local', last_active: 1, daemon_alive: false,
    daemon_pid: null, uptime_seconds: null,
  },
  {
    id: 'remote', label: 'Remote', display_name: 'Remote', objective: '',
    launch_cwd: '/workspace/remote', last_active: 1, daemon_alive: false,
    daemon_pid: null, uptime_seconds: null,
  },
];

describe('recommendedSidebarScope', () => {
  it('keeps local scope when it contains the active session', () => {
    expect(recommendedSidebarScope(rows, 'local', '/workspace/local')).toBe('local');
  });

  it('shows all sessions when the selected session is outside local scope', () => {
    expect(recommendedSidebarScope(rows, 'remote', '/workspace/local')).toBe('all');
  });

  it('shows all sessions instead of an empty local sidebar', () => {
    expect(recommendedSidebarScope(rows, null, '/workspace/missing')).toBe('all');
  });
});
