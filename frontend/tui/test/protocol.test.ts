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
import {
  cleanupSpawnedApi,
  ensureApi,
  probeApi,
  repoBackendPath,
  scheduleOutdatedDaemonUpgrades,
  uniqueWarningReporter,
  type ApiProbeResult,
} from '../src/ensureApi.js';
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

test('repository backend path follows the platform venv layout', () => {
  const windows = repoBackendPath('/repo', 'win32').replaceAll('\\', '/');
  const posix = repoBackendPath('/repo', 'linux').replaceAll('\\', '/');

  assert.match(windows, /\/repo\/\.venv\/Scripts\/argus-skill\.exe$/);
  assert.match(posix, /\/repo\/\.venv\/bin\/argus-skill$/);
});

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
  assert.equal(wrongCheckout.reason, 'backend is running from a different installation than configured');
  const wrongRelease = inspectApiMeta(meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      release_id: '0.1.0+stale',
    },
  }));
  assert.equal(wrongRelease.compatible, false);
  assert.equal(
    wrongRelease.reason,
    'backend and client installations are out of sync; restart or reinstall Argus',
  );
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
  assert.equal(
    driftedSource.warning,
    'backend code and installed artifacts differ; reinstall a complete published build',
  );
});

test('local source identity rejects a stale process even when release ids match', () => {
  const staleProcess = inspectApiMeta(meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      runtime_source_digest: 'old-process-source',
    },
  }), {
    releaseId: RELEASE_ID,
    sourceDigest: RELEASE_SOURCE_DIGEST,
  });
  assert.equal(staleProcess.compatible, false);
  assert.equal(
    staleProcess.reason,
    'backend is running code from a different local installation; restart it',
  );
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
    /daemon fields missing: global_daily_cap_usd/,
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
    assert.equal(
      probe.message,
      'backend and client installations are out of sync; restart or reinstall Argus',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('startup probe reports generic backend status', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify(meta()), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'compatible');
    assert.equal(probe.message, 'Argus backend is running (pid 123)');
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
    assert.equal(
      probe.warning,
      'backend code and installed artifacts differ; reinstall a complete published build',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('startup probe uses the source identity exported by the Python launcher', async () => {
  const originalFetch = globalThis.fetch;
  const originalDigest = process.env.ARGUS_TUI_LOCAL_SOURCE_DIGEST;
  process.env.ARGUS_TUI_LOCAL_SOURCE_DIGEST = RELEASE_SOURCE_DIGEST;
  const stale = meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      runtime_source_digest: 'old-process-source',
    },
  });
  globalThis.fetch = (async () => new Response(JSON.stringify(stale), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'incompatible');
    assert.equal(
      probe.message,
      'backend is running code from a different local installation; restart it',
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDigest === undefined) delete process.env.ARGUS_TUI_LOCAL_SOURCE_DIGEST;
    else process.env.ARGUS_TUI_LOCAL_SOURCE_DIGEST = originalDigest;
  }
});

test('launcher schedules only live daemons with incompatible runtime identity', async () => {
  const calls: string[] = [];
  const summary = await scheduleOutdatedDaemonUpgrades([
    {
      id: 'stale',
      label: 'stale',
      objective: '',
      last_active: 1,
      daemon_alive: true,
      daemon_pid: 1,
      uptime_seconds: 10,
      daemon_protocol_compatible: false,
      daemon_source_owned: true,
    },
    {
      id: 'current',
      label: 'current',
      objective: '',
      last_active: 1,
      daemon_alive: true,
      daemon_pid: 2,
      uptime_seconds: 10,
      daemon_protocol_compatible: true,
      daemon_source_owned: true,
    },
    {
      id: 'stopped',
      label: 'stopped',
      objective: '',
      last_active: 1,
      daemon_alive: false,
      daemon_pid: null,
      uptime_seconds: null,
      daemon_protocol_compatible: false,
      daemon_source_owned: true,
    },
    {
      id: 'other-install',
      label: 'other-install',
      objective: '',
      last_active: 1,
      daemon_alive: true,
      daemon_pid: 3,
      uptime_seconds: 10,
      daemon_protocol_compatible: false,
      daemon_source_owned: false,
    },
    {
      id: 'pending',
      label: 'pending',
      objective: '',
      last_active: 1,
      daemon_alive: false,
      daemon_pid: null,
      uptime_seconds: null,
      daemon_protocol_compatible: null,
      daemon_source_owned: false,
      daemon_upgrade_pending: true,
    },
  ], async (sid) => {
    calls.push(sid);
    return { scheduled: true };
  });
  assert.deepEqual(calls, ['stale', 'pending']);
  assert.deepEqual(summary, {
    outdated: ['stale', 'pending'],
    scheduled: ['stale', 'pending'],
    skipped: [],
    failed: [],
  });
});

test('launcher does not claim a refused daemon upgrade was scheduled', async () => {
  const summary = await scheduleOutdatedDaemonUpgrades([
    {
      id: 'stale',
      label: 'stale',
      objective: '',
      last_active: 1,
      daemon_alive: true,
      daemon_pid: 1,
      uptime_seconds: 10,
      daemon_protocol_compatible: false,
      daemon_source_owned: true,
    },
  ], async () => ({
    scheduled: false,
    reason: 'daemon identity changed',
  }));
  assert.deepEqual(summary, {
    outdated: ['stale'],
    scheduled: [],
    skipped: ['stale'],
    failed: [],
  });
});

test('warning reporter emits each warning only once', () => {
  const warnings: string[] = [];
  const report = uniqueWarningReporter((warning) => warnings.push(warning));
  report('backend code and installed artifacts differ');
  report('backend code and installed artifacts differ');
  report('  backend code and installed artifacts differ  ');
  report('another warning');
  assert.deepEqual(warnings, [
    'backend code and installed artifacts differ',
    'another warning',
  ]);
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
        warning: 'backend code and installed artifacts differ',
      }),
    },
  });

  assert.equal(result.reachable, true);
  assert.equal(result.warning, 'backend code and installed artifacts differ');
  assert.deepEqual(warnings, ['backend code and installed artifacts differ']);
});

// ── Stale-release recovery ──────────────────────────────────────────────────

const staleProbe: ApiProbeResult = {
  state: 'incompatible' as const,
  message: 'backend and client installations are out of sync; restart or reinstall Argus',
};
const downProbe: ApiProbeResult = {
  state: 'unreachable',
  message: 'connection refused',
};
const currentProbe: ApiProbeResult = {
  state: 'compatible' as const,
  message: 'current release',
};
const currentProbeWithPid = (pid: number): ApiProbeResult => ({
  ...currentProbe,
  meta: meta({
    runtime: {
      ...(meta().runtime as Record<string, unknown>),
      pid,
    },
  }) as unknown as ApiMeta,
});
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
      probeApi: probeSequence(staleProbe, downProbe, currentProbeWithPid(2468)),
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
  assert.equal(rec.pid, 2468);
  assert.equal(rec.rootPid, 9876);
  assert.equal(rec.host, '127.0.0.1');
  assert.equal(rec.port, 8899);
  assert.equal(
    basename(rec.backendBin),
    process.platform === 'win32' ? 'argus-skill.exe' : 'argus-skill',
  );
  assert.equal(typeof rec.startedAt, 'string');
});

test('replaces an owned process whose source digest differs from local source', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const digestMismatch: ApiProbeResult = {
    state: 'incompatible',
    message: 'backend is running code from a different local installation; restart it',
  };
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(digestMismatch, downProbe, currentProbe),
      readOwnedApi: async () => ownedRecord,
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => undefined,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(result.spawned, true);
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
});

test('stale Windows-style ownership terminates both listener and launcher PIDs', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(staleProbe, downProbe, currentProbe),
      readOwnedApi: async () => ({ ...ownedRecord, rootPid: 8765 }),
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => undefined,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.deepEqual(signals, [[4321, 'SIGTERM'], [8765, 'SIGTERM']]);
});

test('continues restart when a concurrent launcher already stopped the owned pid', async () => {
  const spawnApi = mock.fn(async () => ({ pid: 9876 }));
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(staleProbe, downProbe, currentProbe),
      readOwnedApi: async () => ownedRecord,
      signal: () => {
        throw new Error('ESRCH');
      },
      spawnApi,
      writeOwnershipRecord: async () => undefined,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, true);
  assert.equal(spawnApi.mock.callCount(), 1);
});

test('does not spawn when signaling fails and the incompatible listener remains', async () => {
  const spawnApi = mock.fn(async () => ({ pid: 9876 }));
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => staleProbe,
      readOwnedApi: async () => ownedRecord,
      signal: () => {
        throw new Error('EPERM');
      },
      spawnApi,
      sleep: async () => undefined,
    },
  });
  assert.equal(result.reachable, false);
  assert.equal(spawnApi.mock.callCount(), 0);
  assert.match(result.message, /could not signal owned pid/);
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
      probeApi: probeSequence(staleProbe, downProbe, currentProbeWithPid(2468)),
      readOwnedApi: async () => ownedRecord,
      signal: (pid, sig) => signals.push([pid, sig]),
      spawnApi: async () => ({ pid: 9876 }),
      writeOwnershipRecord: async () => { throw new Error('disk full'); },
      sleep: async () => undefined,
    },
  });
  // Stop the old process, then both listener and launcher of the new process tree.
  assert.deepEqual(signals, [
    [4321, 'SIGTERM'],
    [2468, 'SIGTERM'],
    [9876, 'SIGTERM'],
  ]);
  assert.equal(result.reachable, false);
  assert.equal(result.spawned, false);
  assert.match(result.message, /ownership write failed/);
});

// ── Normal-autostart ownership tests ───────────────────────────────────────

const unreachableProbe: ApiProbeResult = { state: 'unreachable', message: 'connection refused' };

test('normal autostart records listener and launcher PIDs after the runtime handshake', async () => {
  const ownerWrites: Array<[string, ApiOwnershipRecord]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-normal-owner.json',
    dependencies: {
      probeApi: probeSequence(unreachableProbe, currentProbeWithPid(8888)),
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
  assert.equal(rec.pid, 8888);
  assert.equal(rec.rootPid, 7777);
  assert.equal(rec.host, '127.0.0.1');
  assert.equal(rec.port, 8899);
  assert.equal(
    basename(rec.backendBin),
    process.platform === 'win32' ? 'argus-skill.exe' : 'argus-skill',
  );
  assert.equal(typeof rec.startedAt, 'string');
  assert.deepEqual(result.spawnedApi, {
    ownerFile: '/tmp/argus-normal-owner.json',
    ownership: rec,
  });
});

test('normal autostart fails immediately when the spawned backend exits', async () => {
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    dependencies: {
      probeApi: async () => unreachableProbe,
      spawnApi: async () => ({ pid: 7777, exited: Promise.resolve(7) }),
      sleep: async () => new Promise<void>(() => undefined),
    },
  });

  assert.equal(result.reachable, false);
  assert.equal(result.spawned, true);
  assert.match(result.message, /exited before becoming ready \(exit 7\)/);
});

test('normal autostart accepts a competing compatible backend after bind loss', async () => {
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    dependencies: {
      probeApi: probeSequence(unreachableProbe, currentProbeWithPid(8888)),
      spawnApi: async () => ({ pid: 7777, exited: Promise.resolve(1) }),
      sleep: async () => new Promise<void>(() => undefined),
    },
  });

  assert.equal(result.reachable, true);
  assert.equal(result.spawned, false);
});

test('spawn cleanup verifies and signals both Windows listener and launcher PIDs', async () => {
  const ownership: ApiOwnershipRecord = {
    schema: 1,
    pid: 8888,
    rootPid: 7777,
    host: '127.0.0.1',
    port: 8899,
    backendBin: 'C:\\repo\\.venv\\Scripts\\argus-skill.exe',
    startedAt: '2026-07-14T00:00:00Z',
  };
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await cleanupSpawnedApi({
    result: {
      reachable: true,
      spawned: true,
      message: 'api started',
      spawnedApi: { ownerFile: 'C:\\state\\webapi.owner.json', ownership },
    },
    dependencies: {
      readOwnedApi: async () => ownership,
      probeApi: async () => ({
        state: 'compatible',
        message: 'current',
        meta: meta({
          runtime: {
            ...(meta().runtime as Record<string, unknown>),
            pid: 8888,
            started_at: ownership.startedAt,
          },
        }) as unknown as ApiMeta,
      }),
      signal: (pid, signal) => signals.push([pid, signal]),
    },
  });
  assert.equal(result.stopped, true);
  assert.deepEqual(signals, [[8888, 'SIGTERM'], [7777, 'SIGTERM']]);
});

test('spawn cleanup refuses changed ownership and PID reuse without signalling', async () => {
  const ownership: ApiOwnershipRecord = {
    schema: 1,
    pid: 8888,
    rootPid: 7777,
    host: '127.0.0.1',
    port: 8899,
    backendBin: 'C:\\repo\\.venv\\Scripts\\argus-skill.exe',
    startedAt: '2026-07-14T00:00:00Z',
  };
  const ensured = {
    reachable: true,
    spawned: true,
    message: 'api started',
    spawnedApi: { ownerFile: 'C:\\state\\webapi.owner.json', ownership },
  };
  const signal = mock.fn();
  const changedOwner = await cleanupSpawnedApi({
    result: ensured,
    dependencies: {
      readOwnedApi: async () => ({ ...ownership, rootPid: 9999 }),
      probeApi: async () => currentProbe,
      signal,
    },
  });
  assert.equal(changedOwner.stopped, false);
  assert.match(changedOwner.message, /ownership changed/);

  const reusedPid = await cleanupSpawnedApi({
    result: ensured,
    dependencies: {
      readOwnedApi: async () => ownership,
      probeApi: async () => ({
        state: 'compatible',
        message: 'reused',
        meta: meta({
          runtime: {
            ...(meta().runtime as Record<string, unknown>),
            pid: ownership.pid,
            started_at: '2026-07-15T00:00:00Z',
          },
        }) as unknown as ApiMeta,
      }),
      signal,
    },
  });
  assert.equal(reusedPid.stopped, false);
  assert.match(reusedPid.message, /runtime identity changed/);
  assert.equal(signal.mock.callCount(), 0);
});

test('spawn cleanup never inspects or signals a non-loopback receipt', async () => {
  const readOwnedApi = mock.fn(async () => null);
  const signal = mock.fn();
  const result = await cleanupSpawnedApi({
    result: {
      reachable: true,
      spawned: true,
      message: 'api started',
      spawnedApi: {
        ownerFile: '/state/remote.owner.json',
        ownership: {
          schema: 1,
          pid: 8888,
          rootPid: 7777,
          host: '10.0.0.5',
          port: 8899,
          backendBin: '/repo/.venv/bin/argus-skill',
          startedAt: '2026-07-14T00:00:00Z',
        },
      },
    },
    dependencies: { readOwnedApi, signal },
  });
  assert.equal(result.stopped, false);
  assert.match(result.message, /non-local/);
  assert.equal(readOwnedApi.mock.callCount(), 0);
  assert.equal(signal.mock.callCount(), 0);
});

test('ApiClient graceful daemon stop uses the project endpoint and never force-kills', async () => {
  const originalFetch = globalThis.fetch;
  let seenUrl = '';
  let seenBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input, init) => {
    seenUrl = String(input);
    seenBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return new Response(JSON.stringify({ rc: 0 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-live' });
    await api.stopDaemon('cmd-exit');
    assert.equal(seenUrl, 'http://127.0.0.1:8799/api/projects/s-live/daemon/stop');
    assert.deepEqual(seenBody, {
      force: false,
      drain: false,
      command_id: 'cmd-exit',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ApiClient graceful daemon stop rejects a busy executor response', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    rc: 2,
    error: 'daemon is still finishing the active mission',
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-live' });
    await assert.rejects(
      api.stopDaemon('cmd-busy'),
      /executor did not stop cleanly: daemon is still finishing the active mission/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('normal autostart SIGTERMs spawn and returns failure when ownership write fails', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-normal-owner.json',
    dependencies: {
      probeApi: probeSequence(unreachableProbe, currentProbeWithPid(8888)),
      spawnApi: async () => ({ pid: 7777 }),
      signal: (pid, sig) => signals.push([pid, sig]),
      writeOwnershipRecord: async () => { throw new Error('disk full'); },
      sleep: async () => undefined,
    },
  });
  assert.deepEqual(signals, [[8888, 'SIGTERM'], [7777, 'SIGTERM']]);
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

test('ApiClient requests Manager prewarm only when asked', async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  let calls = 0;
  globalThis.fetch = (async (input) => {
    calls += 1;
    urls.push(String(input));
    if (calls === 1) return Response.json(meta());
    return Response.json({ schema_version: SNAPSHOT_SCHEMA_VERSION, daemon: {} });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-test' });
    await assert.rejects(() => api.snapshot(1, undefined, true), /daemon fields missing/);
    await assert.rejects(() => api.snapshot(1), /daemon fields missing/);
    assert.match(urls[1], /events_limit=1&prewarm=true$/);
    assert.doesNotMatch(urls[2], /prewarm=true/);
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
      'backend code and installed artifacts differ; reinstall a complete published build',
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
