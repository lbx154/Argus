import definition from '../schemas/read_api_protocol.json' with { type: 'json' };
import type { ApiMeta } from './api.js';
import type { ProjectRow, ProjectCostRow, Snapshot } from './models.js';
import { isJsonObject } from './eventValidation.js';
import { requireSnapshotContract } from './protocol.js';

export const READ_API = definition;
export type ReadMethod = 'meta' | 'projects' | 'costs' | 'snapshot';
export interface ProjectList { projects: ProjectRow[]; local_cwd: string }
export interface ProjectCosts { projects: ProjectCostRow[]; generated_at: number }
export interface ReadQueryParams {
  meta: Record<string, never>;
  projects: { limit: number; include_empty: boolean };
  costs: { limit: number };
  snapshot: { sid: string; events_limit: number; compact: boolean };
}
export interface ReadQueryResults {
  meta: ApiMeta;
  projects: ProjectList;
  costs: ProjectCosts;
  snapshot: Snapshot | null;
}

/** This profile deliberately does not advertise the full WebAPI's write capabilities. */
export interface ReadApiMeta {
  service: string;
  protocol: { name: string; major: number; minor: number };
  read_only: true;
  capabilities: ReadMethod[];
  snapshot_schema_version: number;
  runtime: { implementation: 'node'; version: string; pid: number };
  backend: ApiMeta;
}

export type ReadBridgeReply =
  | { protocol: string; version: number; id: string | null; ok: true; result: unknown }
  | { protocol: string; version: number; id: string | null; ok: false; error: { code: string; detail: string } };

export function validProjectId(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
    && [...value].length <= READ_API.max_project_id_length
    && value !== '.' && value !== '..' && !/[/\\\x00-\x1f\x7f\uD800-\uDFFF]/u.test(value);
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const nullableNumber = (value: unknown): boolean => value === null || finite(value);

export function requireProjectList(value: unknown): ProjectList {
  if (!isJsonObject(value) || !Array.isArray(value.projects) || typeof value.local_cwd !== 'string') {
    throw new Error('invalid project list');
  }
  for (const row of value.projects) {
    if (!isJsonObject(row) || !validProjectId(row.id) || typeof row.label !== 'string'
      || typeof row.objective !== 'string' || !finite(row.last_active)
      || typeof row.daemon_alive !== 'boolean' || !nullableNumber(row.daemon_pid)
      || !nullableNumber(row.uptime_seconds)) throw new Error('invalid project row');
  }
  return value as unknown as ProjectList;
}

export function requireProjectCosts(value: unknown): ProjectCosts {
  if (!isJsonObject(value) || !Array.isArray(value.projects) || !finite(value.generated_at)) {
    throw new Error('invalid project cost feed');
  }
  for (const row of value.projects) {
    if (!isJsonObject(row) || !validProjectId(row.id) || !nullableNumber(row.spend_usd)
      || !finite(row.known_cost_usd) || typeof row.spend_status !== 'string'
      || !finite(row.usage_calls) || !finite(row.premium_requests) || !finite(row.updated_at)) {
      throw new Error('invalid project cost row');
    }
  }
  return value as unknown as ProjectCosts;
}

/** Required top-level shape at the new process boundary; optional legacy payloads remain open. */
export function requireReadSnapshot(value: unknown, sid: string): Snapshot {
  const snapshot = requireSnapshotContract(value);
  if (!isJsonObject(snapshot.session) || snapshot.session.id !== sid
    || typeof snapshot.session.display_name !== 'string' || typeof snapshot.session.objective !== 'string'
    || typeof snapshot.session.cwd !== 'string' || !finite(snapshot.session.last_active)
    || !Array.isArray(snapshot.roles) || !Array.isArray(snapshot.backlog)
    || !Array.isArray(snapshot.recent_events) || typeof snapshot.partial !== 'boolean'
    || !nullableNumber(snapshot.spend_usd) || typeof snapshot.spend_status !== 'string'
    || !isJsonObject(snapshot.usage_summary)) throw new Error('invalid project snapshot');
  return snapshot;
}
