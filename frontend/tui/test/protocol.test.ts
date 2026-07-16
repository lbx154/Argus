import assert from 'node:assert/strict';
import test, { mock } from 'node:test';
import { basename } from 'node:path';

import {
  type ApiMeta,
  inspectApiMeta,
  requireSnapshotContract,
  API_PROTOCOL,
  REQUIRED_API_CAPABILITIES,
  SNAPSHOT_SCHEMA_VERSION,
} from '../../core/src/protocol.js';
import { RELEASE_ID, RELEASE_SOURCE_DIGEST } from '../../core/src/release.generated.js';
import { ApiClient } from '../src/api.js';
import { ensureApi, probeApi, type ApiProbeResult } from '../src/ensureApi.js';
import { type ApiOwnershipRecord } from '../src/apiOwnership.js';

function meta(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    service: 'argus-skill-webapi',
    protocol: { name: 'argus.webapi', major: 1, minor: API_PROTOCOL.minServerMinor },
    snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
    capabilities: [...REQUIRED_API_CAPABILITIES],
    runtime: {
      package_version: '0.1.1',
      source_root: '/home/dev/current/argus-skill',
      configured_source_root: '/home/dev/current/argus-skill',
      source_root_matches_config: true,
      revision: 'abc123',
      pid: 123,
      python_version: '3.13.0',
      executable: '/venv/bin/python',
      started_at: '2026-07-11T00:00:00Z',
      release_id: RELEASE_ID,
      manifest_source_digest: RELEASE_SOURCE_DIGEST,
      runtime_source_digest: RELEASE_SOURCE_DIGEST,
      release_matches_source: true,
    },
    ...overrides,
  };
}

test('protocol contract accepts the current server and rejects missing capabilities', () => {
  assert.equal(inspectApiMeta(meta()).compatible, true);
  const incompatible = inspectApiMeta(meta({ capabilities: ['manager.sse.v1'] }));
  assert.equal(incompatible.compatible, false);
  assert.match(incompatible.reason, /missing capabilities: daemon\.admission\.v1/);
  const oldMinor = inspectApiMeta(meta({
    protocol: { name: 'argus.webapi', major: 1, minor: 5 },
  }));
  assert.equal(oldMinor.compatible, false);
  assert.match(oldMinor.reason, new RegExp(`older than required ${API_PROTOCOL.minServerMinor}`));
  const wrongCheckout = inspectApiMeta(meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      source_root_matches_config: false,
      configured_source_root: '/home/dev/other/argus-skill',
    },
  }));
  assert.equal(wrongCheckout.compatible, false);
  assert.match(wrongCheckout.reason, /loaded source .*ARGUS_SKILL_SOURCE_ROOT/);
  const wrongRelease = inspectApiMeta(meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_id: '0.1.0+stale',
    },
  }));
  assert.equal(wrongRelease.compatible, false);
  assert.match(wrongRelease.reason, /does not match client release/);
  // A source/editable checkout whose working tree drifted from the last release
  // build reports release_matches_source=false but keeps a matching release_id;
  // this must remain compatible so `argus-skill --web` from source is not bricked.
  const driftedSource = inspectApiMeta(meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_matches_source: false,
      runtime_source_digest: 'deadbeef',
    },
  }));
  assert.equal(driftedSource.compatible, true);
  assert.match(driftedSource.warning ?? '', /source differs from its release manifest/);
});

test('snapshot contract fails closed when budget fields are absent', () => {
  assert.throws(
    () => requireSnapshotContract({
      schema_version: SNAPSHOT_SCHEMA_VERSION,
      daemon: { alive: false },
      spend_usd: null,
      spend_status: 'empty',
      usage_summary: {},
      request_usage: {},
      partial: false,
      diagnostics: [],
    }),
    /daemon fields missing: per_mission_cap_usd/,
  );
});

test('startup probe identifies an old reachable backend as incompatible', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response('not found', { status: 404 })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'incompatible');
    assert.match(probe.message, /older Argus checkout/);

    const ensured = await ensureApi({ host: '127.0.0.1', port: 8799 });
    assert.equal(ensured.reachable, false);
    assert.equal(ensured.spawned, false);
    assert.match(ensured.message, /incompatible Argus API/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('startup probe preserves stale Argus process identity for verified local recovery', async () => {
  const originalFetch = globalThis.fetch;
  const stale = meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_id: '0.1.0+stale',
    },
  });
  globalThis.fetch = (async () => new Response(JSON.stringify(stale), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'incompatible');
    assert.equal(probe.meta?.runtime.pid, 123);
    assert.match(probe.message, /does not match client release/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('startup probe reports the backend checkout and revision', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify(meta()), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'compatible');
    assert.equal(
      probe.message,
      `/home/dev/current/argus-skill @ abc123 · release ${RELEASE_ID} (pid 123)`,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('startup probe surfaces source drift without rejecting the backend', async () => {
  const originalFetch = globalThis.fetch;
  const drifted = meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_matches_source: false,
      runtime_source_digest: 'deadbeef',
    },
  });
  globalThis.fetch = (async () => new Response(JSON.stringify(drifted), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'compatible');
    assert.match(probe.warning ?? '', /source differs from its release manifest/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ensureApi preserves and emits a compatible source-drift warning', async () => {
  const warnings: string[] = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8799,
    onWarning: (warning) => warnings.push(warning),
    dependencies: {
      probeApi: async () => ({
        state: 'compatible',
        message: 'current release',
        warning: 'backend source differs from its release manifest',
      }),
    },
  });

  assert.equal(result.reachable, true);
  assert.equal(result.warning, 'backend source differs from its release manifest');
  assert.deepEqual(warnings, ['backend source differs from its release manifest']);
});

// ── Stale-release recovery ──────────────────────────────────────────────────

const staleProbe: ApiProbeResult = {
  state: 'incompatible' as const,
  message: 'backend release 0.1.0+stale does not match client release',
};
const downProbe: ApiProbeResult = {
  state: 'unreachable',
  message: 'connection refused',
};
const currentProbe: ApiProbeResult = {
  state: 'compatible' as const,
  message: 'current release',
};
const staleMeta = meta({
  runtime: {
    ...(meta().runtime as Record<string, unknown>),
    pid: 4321,
    started_at: '2026-07-14T00:00:00Z',
    release_id: '0.1.0+stale',
  },
}) as unknown as ApiMeta;
const probeSequence = (...values: ApiProbeResult[]) => {
  let index = 0;
  return async () => values[Math.min(index++, values.length - 1)];
};
const ownedRecord = {
  schema: 1 as const,
  pid: 4321,
  host: '127.0.0.1',
  port: 8899,
  backendBin: '/repo/.venv/bin/argus-skill',
  startedAt: '2026-07-14T00:00:00Z',
};

test('replaces a proven owned stale API with SIGTERM only', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const ownerWrites: Array<[string, ApiOwnershipRecord]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(staleProbe, downProbe, currentProbe),
      readOwnedApi: async () => ownedRecord,
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async (path, record) => { ownerWrites.push([path, record]); },
      sleep: async () => undefined,
    },
  });
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
  assert.equal(result.reachable, true);
  // Assert exact ownership record written to the correct path.
  assert.equal(ownerWrites.length, 1);
  assert.equal(ownerWrites[0][0], '/tmp/argus-owner.json');
  const rec = ownerWrites[0][1];
  assert.equal(rec.schema, 1);
  assert.equal(rec.pid, 9876);
  assert.equal(rec.host, '127.0.0.1');
  assert.equal(rec.port, 8899);
  assert.equal(basename(rec.backendBin), 'argus-skill');
  assert.equal(typeof rec.startedAt, 'string');
});

test('bootstraps verified ownership from stale local API metadata, then replaces it', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const claims: Array<[string, ApiOwnershipRecord]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence({ ...staleProbe, meta: staleMeta }, downProbe, currentProbe),
      readOwnedApi: async () => null,
      claimApiOwnership: async (path, record) => {
        claims.push([path, record]);
        return true;
      },
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => undefined,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(claims.length, 1);
  assert.equal(claims[0][1].pid, 4321);
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
});

test('never signals a stale owner PID that disagrees with the live API metadata', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence({ ...staleProbe, meta: staleMeta }, downProbe, currentProbe),
      readOwnedApi: async () => ({ ...ownedRecord, pid: 1111 }),
      claimApiOwnership: async (_path, record) => record.pid === 4321,
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => undefined,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
});

test('compatible local API is adopted for the next automatic upgrade', async () => {
  const claims: Array<[string, ApiOwnershipRecord]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => ({
        state: 'compatible',
        message: 'current release',
        meta: meta() as unknown as ApiMeta,
      }),
      readOwnedApi: async () => null,
      claimApiOwnership: async (path, record) => {
        claims.push([path, record]);
        return true;
      },
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(claims.length, 1);
  assert.equal(claims[0][1].pid, 123);
});

test('never signals an incompatible unowned listener', async () => {
  const signal = mock.fn();
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => staleProbe,
      readOwnedApi: async () => null,
      signal,
    },
  });
  assert.equal(signal.mock.callCount(), 0);
  assert.match(result.message, /ownership could not be proven/);
});

test('does not spawn when graceful shutdown times out', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const spawnApi = mock.fn(async () => ({ pid: 9876 }));
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => staleProbe,
      readOwnedApi: async () => ownedRecord,
      signal: (pid, sig) => signals.push([pid, sig]),
      spawnApi,
      sleep: async () => undefined,
    },
  });
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
  assert.equal(spawnApi.mock.callCount(), 0);
  assert.equal(result.reachable, false);
  assert.match(result.message, /graceful shutdown timed out/);
});

test('refuses ownership recovery for a remote host even when ownerFile is set', async () => {
  const signal = mock.fn();
  const readOwnedApi = mock.fn(async () => ownedRecord);
  const result = await ensureApi({
    host: '10.0.0.5',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => staleProbe,
      readOwnedApi,
      signal,
    },
  });
  assert.equal(signal.mock.callCount(), 0, 'must not signal a remote process');
  assert.equal(readOwnedApi.mock.callCount(), 0, 'must not inspect remote ownership file');
  assert.equal(result.reachable, false);
  assert.match(result.message, /incompatible Argus API/);
});

test('SIGTERMs the just-spawned child when ownership write fails', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(staleProbe, downProbe),
      readOwnedApi: async () => ownedRecord,
      signal: (pid, sig) => signals.push([pid, sig]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => { throw new Error('disk full'); },
      sleep: async () => undefined,
    },
  });
  // SIGTERM to the old process (4321), then SIGTERM to the just-spawned process (9876).
  assert.deepEqual(signals, [[4321, 'SIGTERM'], [9876, 'SIGTERM']]);
  assert.equal(result.reachable, false);
  assert.equal(result.spawned, false);
  assert.match(result.message, /ownership write failed/);
});

// ── Normal-autostart ownership tests ───────────────────────────────────────

const unreachableProbe: ApiProbeResult = { state: 'unreachable', message: 'connection refused' };

test('normal autostart writes ownership record immediately after spawn', async () => {
  const ownerWrites: Array<[string, ApiOwnershipRecord]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-normal-owner.json',
    dependencies: {
      probeApi: probeSequence(unreachableProbe, currentProbe),
      spawnApi: async () => ({ pid: 7777 }),
      writeOwnershipRecord: async (path, record) => { ownerWrites.push([path, record]); },
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(result.spawned, true);
  assert.equal(ownerWrites.length, 1);
  assert.equal(ownerWrites[0][0], '/tmp/argus-normal-owner.json');
  const rec = ownerWrites[0][1];
  assert.equal(rec.schema, 1);
  assert.equal(rec.pid, 7777);
  assert.equal(rec.host, '127.0.0.1');
  assert.equal(rec.port, 8899);
  assert.equal(basename(rec.backendBin), 'argus-skill');
  assert.equal(typeof rec.startedAt, 'string');
});

test('normal autostart SIGTERMs spawn and returns failure when ownership write fails', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-normal-owner.json',
    dependencies: {
      probeApi: async () => unreachableProbe,
      spawnApi: async () => ({ pid: 7777 }),
      signal: (pid, sig) => signals.push([pid, sig]),
      writeOwnershipRecord: async () => { throw new Error('disk full'); },
      sleep: async () => undefined,
    },
  });
  assert.deepEqual(signals, [[7777, 'SIGTERM']]);
  assert.equal(result.reachable, false);
  assert.equal(result.spawned, false);
  assert.match(result.message, /ownership record/);
  assert.match(result.message, /SIGTERM/);
});

test('normal autostart with no ownerFile skips ownership write and polls normally', async () => {
  const ownerWrites: Array<unknown[]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    // no ownerFile
    dependencies: {
      probeApi: probeSequence(unreachableProbe, currentProbe),
      spawnApi: async () => ({ pid: 7777 }),
      writeOwnershipRecord: async (...args) => { ownerWrites.push(args); },
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(result.spawned, true);
  // ownership write must NOT be called when no ownerFile is provided
  assert.equal(ownerWrites.length, 0);
});

test('ApiClient validates snapshot schema after the one-time handshake', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    if (calls === 1) {
      return new Response(JSON.stringify(meta()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ schema_version: SNAPSHOT_SCHEMA_VERSION, daemon: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-test' });
    await assert.rejects(() => api.snapshot(), /daemon fields missing/);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ApiClient forwards compatible source-drift warnings', async () => {
  const originalFetch = globalThis.fetch;
  const warnings: string[] = [];
  let calls = 0;
  const drifted = meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_matches_source: false,
      runtime_source_digest: 'deadbeef',
    },
  });
  globalThis.fetch = (async () => {
    calls += 1;
    return Response.json(calls === 1 ? drifted : { projects: [] });
  }) as typeof fetch;
  try {
    const api = new ApiClient({
      host: '127.0.0.1',
      port: 8799,
      project: '_',
      onCompatibilityWarning: (warning) => warnings.push(warning),
    });

    await api.listProjects();

    assert.deepEqual(warnings, [
      'backend source differs from its release manifest; rebuild with scripts/build_release.py before release',
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
