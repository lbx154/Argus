import type { EventMsg, ProjectIndex, Snapshot, StatusView } from './types';
import { authHeaders, compatibleApiMeta, requestWithTimeout, requireDaemonCommand } from '../api';
import type { TimelineEntry, TimelineInput, TimelineReport } from './timeline/types';

const LOCAL_READ_TIMEOUT_MS = 12_000;

export function apiAuthHeaders(json = false): Record<string, string> {
  const value: Record<string, string> = { ...authHeaders() };
  if (json) value['Content-Type'] = 'application/json';
  return value;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  await compatibleApiMeta();
  const requestInit: RequestInit = {
    ...init,
    headers: { ...apiAuthHeaders(Boolean(init.body)), ...(init.headers ?? {}) },
    cache: 'no-store',
  };
  const method = String(init.method ?? 'GET').toUpperCase();
  const consume = async (response: Response): Promise<T> => {
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      let message = detail;
      try {
        const parsed = JSON.parse(detail) as { detail?: string };
        message = parsed.detail ?? detail;
      } catch {
        // Keep plain-text detail.
      }
      throw new Error(message || `${init.method ?? 'GET'} ${path} failed (${response.status})`);
    }
    return (await response.json()) as T;
  };
  return method === 'GET'
    ? requestWithTimeout(path, requestInit, LOCAL_READ_TIMEOUT_MS, consume)
    : consume(await fetch(path, requestInit));
}

const projectPath = (sid: string, suffix = '') =>
  `/api/projects/${encodeURIComponent(sid)}${suffix}`;
const commandId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export const api = {
  timelineExample: () => request<TimelineInput>('/api/research/timeline/example'),
  timelineEstimate: (input: TimelineInput, signal?: AbortSignal) => request<TimelineReport>(
    '/api/research/timeline/estimate', { method: 'POST', body: JSON.stringify(input), signal },
  ),
  timelineLatest: (sid: string) => request<{ latest: TimelineEntry | null }>(projectPath(sid, '/research/timeline')),
  timelineSave: (sid: string, input: TimelineInput, expectedVersion: number, reason: string) => request<TimelineEntry>(
    projectPath(sid, '/research/timeline'),
    { method: 'POST', body: JSON.stringify({ input, expected_version: expectedVersion, reason }) },
  ),
  projects: (signal?: AbortSignal) => request<ProjectIndex>('/api/projects', { signal }),

  snapshot: (sid: string, signal?: AbortSignal) =>
    request<Snapshot>(
      projectPath(sid, '/snapshot?events_limit=40&compact=false'),
      { signal },
    ),

  status: (sid: string, signal?: AbortSignal) =>
    request<StatusView>(projectPath(sid, '/status'), { signal }),

  events: (sid: string, limit = 180, signal?: AbortSignal) =>
    request<{ events: EventMsg[] }>(projectPath(sid, `/events?limit=${limit}&view=ui`), { signal })
      .then((value) => value.events),

  startDaemon: (sid: string, expectedRevision?: number) =>
    request<Record<string, unknown>>(projectPath(sid, '/daemon/start'), {
      method: 'POST',
      body: JSON.stringify({ command_id: commandId(), expected_revision: expectedRevision }),
    }).then(requireDaemonCommand),

  stopDaemon: (sid: string, drain: boolean, expectedRevision?: number) =>
    request<Record<string, unknown>>(projectPath(sid, '/daemon/stop'), {
      method: 'POST',
      body: JSON.stringify({
        drain,
        command_id: commandId(),
        expected_revision: expectedRevision,
      }),
    }).then(requireDaemonCommand),
};
