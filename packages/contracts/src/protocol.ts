import type { Snapshot } from './models.js';
import type { ApiMeta } from './api.js';
import { API_SERVICE, API_PROTOCOL, SNAPSHOT_SCHEMA_VERSION, REQUIRED_API_CAPABILITIES } from './apiProtocol.generated.js';

export interface ApiCompatibility {
  compatible: boolean;
  reason: string;
  meta?: ApiMeta;
}

export interface ApiRuntimeExpectation {
  releaseId: string;
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
  return `Argus backend is running (pid ${meta.runtime.pid})`;
}

export function inspectApiMeta(
  value: unknown,
  expected?: ApiRuntimeExpectation,
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
  if (expected && runtime.release_id !== expected.releaseId) {
    return {
      compatible: false,
      reason: 'backend and client installations are out of sync; restart or reinstall Argus',
      meta,
    };
  }
  return { compatible: true, reason: '', meta };
}

export function requireCompatibleApiMeta(
  value: unknown,
  expected?: ApiRuntimeExpectation,
): ApiMeta {
  const result = inspectApiMeta(value, expected);
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
  const requiredSnapshotFields = [
    'spend_usd',
    'spend_status',
    'usage_summary',
  ];
  const missingSnapshot = requiredSnapshotFields.filter((field) => !Object.hasOwn(snapshot, field));
  if (missingSnapshot.length > 0) {
    throw new Error(`invalid snapshot: fields missing: ${missingSnapshot.join(', ')}`);
  }
  return value as Snapshot;
}
