import type { EventMsg, Snapshot } from '../api';

/** Display tests supply the server's role channel separately from dialogue rows. */
export function roleSnapshot(role_events: EventMsg[], patch: Partial<Snapshot> = {}): Snapshot {
  return {
    session: { id: 's', display_name: '', objective: '', cwd: '', last_active: 1 },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
    roles: [], backlog: [], recent_events: [], ...patch, role_events,
  };
}
