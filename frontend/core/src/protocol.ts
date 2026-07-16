import type { Snapshot } from './types.js';

import { RELEASE_ID } from './release.generated.js';

export const API_SERVICE = 'argus-skill-webapi';
export const API_PROTOCOL = {
  name: 'argus.webapi',
  major: 1,
  minServerMinor: 8,
} as const;
export const SNAPSHOT_SCHEMA_VERSION = 5;
export const REQUIRED_API_CAPABILITIES = [
  'daemon.admission.v1',
  'daemon.status.protocol.v1',
  'daemon.command.v1',
  'cost.reservation.v1',
  'event.catalog.v1',
  'event.payload-schema.v1',
  'manager.sse.v1',
  'metrics.slo.v1',
  'mission.view.v1',
  'mission.abort.v1',
  'project.git-diff.v1',
  'research.events.v1',
  'release.identity.v1',
  'snapshot.budget.v1',
  'snapshot.schema.v1',
  'usage.recorded.v2',
] as const;

export interface ApiRuntimeIdentity {
  package_version: string;
  source_root: string;
  configured_source_root: string | null;
  source_root_matches_config: boolean | null;
  revision: string | null;
  pid: number;
  python_version: string;
  executable: string;
  started_at: string;
  release_id: string;
  manifest_source_digest: string | null;
  runtime_source_digest: string | null;
  release_matches_source: boolean | null;
}

export interface ApiMeta {
  service: string;
  protocol: {
    name: string;
    major: number;
    minor: number;
  };
  snapshot_schema_version: number;
  capabilities: string[];
  runtime: ApiRuntimeIdentity;
}

export interface ApiCompatibility {
  compatible: boolean;
  reason: string;
  meta?: ApiMeta;
}

type JsonObject = Record<string, unknown>;

function object(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

export function describeApiRuntime(meta: ApiMeta): string {
  const revision = meta.runtime.revision || 'revision unknown';
  const source = meta.runtime.source_root || 'source unknown';
  const mismatch = meta.runtime.source_root_matches_config === false
    ? `; configured source is ${meta.runtime.configured_source_root}`
    : '';
  return `${source} @ ${revision} · release ${meta.runtime.release_id} (pid ${meta.runtime.pid})${mismatch}`;
}

export function inspectApiMeta(value: unknown): ApiCompatibility {
  const root = object(value);
  const protocol = object(root?.protocol);
  const runtime = object(root?.runtime);
  const capabilities = Array.isArray(root?.capabilities)
    ? root.capabilities.filter((item): item is string => typeof item === 'string')
    : [];
  const major = number(protocol?.major);
  const minor = number(protocol?.minor);
  if (!root || !protocol || !runtime) {
    return { compatible: false, reason: 'malformed /api/meta response' };
  }
  if (
    typeof runtime.source_root !== 'string'
    || number(runtime.pid) === null
    || typeof runtime.package_version !== 'string'
    || typeof runtime.release_id !== 'string'
  ) {
    return { compatible: false, reason: 'malformed /api/meta runtime identity' };
  }
  if (root.service !== API_SERVICE) {
    return { compatible: false, reason: `unexpected service ${String(root.service || 'unknown')}` };
  }
  if (protocol.name !== API_PROTOCOL.name || major !== API_PROTOCOL.major) {
    return {
      compatible: false,
      reason: `protocol ${String(protocol.name || 'unknown')}/${String(major)} is incompatible with client ${API_PROTOCOL.name}/${API_PROTOCOL.major}`,
    };
  }
  if (minor === null || minor < API_PROTOCOL.minServerMinor) {
    return {
      compatible: false,
      reason: `server protocol minor ${String(minor)} is older than required ${API_PROTOCOL.minServerMinor}`,
    };
  }
  if (root.snapshot_schema_version !== SNAPSHOT_SCHEMA_VERSION) {
    return {
      compatible: false,
      reason: `snapshot schema ${String(root.snapshot_schema_version)} is incompatible with required ${SNAPSHOT_SCHEMA_VERSION}`,
    };
  }
  const missing = REQUIRED_API_CAPABILITIES.filter((capability) => !capabilities.includes(capability));
  if (missing.length > 0) {
    return { compatible: false, reason: `missing capabilities: ${missing.join(', ')}` };
  }
  if (runtime.source_root_matches_config === false) {
    return {
      compatible: false,
      reason: `backend loaded source ${String(runtime.source_root)} but ARGUS_SKILL_SOURCE_ROOT points to ${String(runtime.configured_source_root)}`,
    };
  }
  // NOTE: `release_matches_source === false` is intentionally NOT a hard failure.
  // It only ever fires for source/editable checkouts (a packaged wheel has no
  // `frontend/core/src`, so the backend reports `null`). For a dev checkout it
  // merely means the working tree drifted from the last release-artifact
  // regeneration — an expected, benign condition that must not brick the Web UI
  // / TUI. Genuine backend<->frontend build incompatibility is still caught by
  // the protocol name/major/minor, snapshot schema, required-capability, and
  // `release_id` checks below (release_id embeds the source digest at build
  // time, so two truly different builds never share one).
  if (runtime.release_id !== RELEASE_ID) {
    return {
      compatible: false,
      reason: `backend release ${String(runtime.release_id)} does not match client release ${RELEASE_ID}`,
    };
  }
  const meta = value as ApiMeta;
  return { compatible: true, reason: '', meta };
}

export function requireCompatibleApiMeta(value: unknown): ApiMeta {
  const result = inspectApiMeta(value);
  if (!result.compatible || !result.meta) {
    throw new Error(`incompatible Argus API: ${result.reason}`);
  }
  return result.meta;
}

export function requireSnapshotContract(value: unknown): Snapshot {
  const snapshot = object(value);
  const daemon = object(snapshot?.daemon);
  if (!snapshot || snapshot.schema_version !== SNAPSHOT_SCHEMA_VERSION) {
    throw new Error(
      `incompatible snapshot schema: expected ${SNAPSHOT_SCHEMA_VERSION}, got ${String(snapshot?.schema_version ?? 'missing')}`,
    );
  }
  if (!daemon) throw new Error('invalid snapshot: daemon section is missing');
  const requiredDaemonFields = [
    'per_mission_cap_usd',
    'daily_cap_usd',
    'global_daily_cap_usd',
    'read_status',
    'read_error',
    'protocol_compatible',
    'protocol_error',
  ];
  const missingDaemon = requiredDaemonFields.filter((field) => !Object.hasOwn(daemon, field));
  if (missingDaemon.length > 0) {
    throw new Error(`invalid snapshot: daemon fields missing: ${missingDaemon.join(', ')}`);
  }
  const requiredSnapshotFields = [
    'spend_usd',
    'spend_status',
    'usage_summary',
    'request_usage',
    'cost_control',
    'daemon_commands',
    'observability',
    'mission_view',
    'partial',
    'diagnostics',
  ];
  const missingSnapshot = requiredSnapshotFields.filter((field) => !Object.hasOwn(snapshot, field));
  if (missingSnapshot.length > 0) {
    throw new Error(`invalid snapshot: fields missing: ${missingSnapshot.join(', ')}`);
  }
  if (!Array.isArray(snapshot.diagnostics)) {
    throw new Error('invalid snapshot: diagnostics must be an array');
  }
  return value as Snapshot;
}
