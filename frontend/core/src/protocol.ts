import type { Snapshot } from './types.js';

import { RELEASE_ID } from './release.generated.js';

export const API_SERVICE = 'argus-skill-webapi';
export const API_PROTOCOL = {
  name: 'argus.webapi',
  major: 1,
  minServerMinor: 11,
} as const;
export const SNAPSHOT_SCHEMA_VERSION = 6;
export const REQUIRED_API_CAPABILITIES = [
  'daemon.admission.v1',
  'daemon.status.protocol.v1',
  'daemon.command.v1',
  'daemon.upgrade-schedule.v1',
  'cost.reservation.v1',
  'event.catalog.v1',
  'event.payload-schema.v1',
  'manager.sse.v1',
  'metrics.slo.v1',
  'mission.view.v1',
  'mission.abort.v1',
  'project.git-diff.v1',
  'project.cost-feed.v1',
  'project.workdir.v1',
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
  warning?: string;
  meta?: ApiMeta;
}

export interface ApiRuntimeExpectation {
  releaseId: string;
  sourceDigest?: string;
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

export function inspectApiMeta(
  value: unknown,
  expected: ApiRuntimeExpectation = { releaseId: RELEASE_ID },
): ApiCompatibility {
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
  const meta = value as ApiMeta;
  if (protocol.name !== API_PROTOCOL.name || major !== API_PROTOCOL.major) {
    return {
      compatible: false,
      reason: `protocol ${String(protocol.name || 'unknown')}/${String(major)} is incompatible with client ${API_PROTOCOL.name}/${API_PROTOCOL.major}`,
      meta,
    };
  }
  if (minor === null || minor < API_PROTOCOL.minServerMinor) {
    return {
      compatible: false,
      reason: `server protocol minor ${String(minor)} is older than required ${API_PROTOCOL.minServerMinor}`,
      meta,
    };
  }
  if (root.snapshot_schema_version !== SNAPSHOT_SCHEMA_VERSION) {
    return {
      compatible: false,
      reason: `snapshot schema ${String(root.snapshot_schema_version)} is incompatible with required ${SNAPSHOT_SCHEMA_VERSION}`,
      meta,
    };
  }
  const missing = REQUIRED_API_CAPABILITIES.filter((capability) => !capabilities.includes(capability));
  if (missing.length > 0) {
    return { compatible: false, reason: `missing capabilities: ${missing.join(', ')}`, meta };
  }
  if (runtime.source_root_matches_config === false) {
    return {
      compatible: false,
      reason: `backend loaded source ${String(runtime.source_root)} but ARGUS_SKILL_SOURCE_ROOT points to ${String(runtime.configured_source_root)}`,
      meta,
    };
  }
  if (runtime.release_id !== expected.releaseId) {
    return {
      compatible: false,
      reason: `backend release ${String(runtime.release_id)} does not match client release ${expected.releaseId}`,
      meta,
    };
  }
  if (expected.sourceDigest) {
    if (typeof runtime.runtime_source_digest !== 'string' || !runtime.runtime_source_digest) {
      return {
        compatible: false,
        reason: 'backend process does not report the source digest required by this local checkout',
        meta,
      };
    }
    if (runtime.runtime_source_digest !== expected.sourceDigest) {
      return {
        compatible: false,
        reason:
          `backend process source ${String(runtime.runtime_source_digest).slice(0, 16)}` +
          ` does not match local source ${expected.sourceDigest.slice(0, 16)}`,
        meta,
      };
    }
  }
  // A live source digest is a release-integrity signal, not a wire-contract
  // version. Editable checkouts keep the last generated release_id while source
  // changes, so drift cannot prove incompatibility. The versioned protocol,
  // snapshot schema, and capabilities above remain the compatibility authority;
  // keep drift visible so operators still know to rebuild before release.
  const warning = runtime.release_matches_source === false
    ? 'backend source differs from its release manifest; rebuild with scripts/build_release.py before release'
    : undefined;
  return { compatible: true, reason: '', warning, meta };
}

export function requireCompatibleApiMeta(
  value: unknown,
  onWarning?: (warning: string) => void,
): ApiMeta {
  const result = inspectApiMeta(value);
  if (!result.compatible || !result.meta) {
    throw new Error(`incompatible Argus API: ${result.reason}`);
  }
  if (result.warning) onWarning?.(result.warning);
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
