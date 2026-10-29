import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach } from 'vitest';
import type { ComponentProps } from 'react';
import type { EventMsg, Snapshot } from '../api';
import { EventStream as Stream } from '../components/EventStream';
import { roleLogKey } from '../useRoleLog';

export const roleClient = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
beforeEach(() => roleClient.clear());
export const setRoleLog = (sid: string, events: EventMsg[], round = 0) => roleClient.setQueryData(roleLogKey(sid), { events, round });

/** Display fixtures use the same independent project cache as the rows stream. */
export function roleSnapshot(events: EventMsg[], patch: Partial<Snapshot> = {}): Snapshot {
  const snapshot: Snapshot = {
    session: { id: 's', display_name: '', objective: '', workdir: '', last_active: 1 },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
    roles: [], backlog: [], work_status: { state: 'running', roles: ['manager'], role: 'manager', taskId: '', title: '', reason: '', foreground: false,
      activityAt: null, label: { 'zh-CN': '团队正在工作', en: 'The team is working' } }, ...patch,
  };
  setRoleLog(snapshot.session.id, events);
  return snapshot;
}
export function EventStream(props: ComponentProps<typeof Stream>) {
  return <QueryClientProvider client={roleClient}><Stream {...props} /></QueryClientProvider>;
}
