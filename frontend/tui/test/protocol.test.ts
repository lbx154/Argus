import assert from 'node:assert/strict';
import test from 'node:test';

import {
  inspectApiMeta,
  requireSnapshotContract,
  REQUIRED_API_CAPABILITIES,
} from '../../core/src/protocol.js';
import { RELEASE_ID, RELEASE_SOURCE_DIGEST } from '../../core/src/release.generated.js';
import { ApiClient } from '../src/api.js';
import { ensureApi, probeApi } from '../src/ensureApi.js';

function meta(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    service: 'argus-skill-webapi',
    protocol: { name: 'argus.webapi', major: 1, minor: 6 },
    snapshot_schema_version: 4,
    capabilities: [...REQUIRED_API_CAPABILITIES],
    runtime: {
      package_version: '0.1.0',
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
  assert.match(oldMinor.reason, /older than required 6/);
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
});

test('snapshot contract fails closed when budget fields are absent', () => {
  assert.throws(
    () => requireSnapshotContract({
      schema_version: 4,
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

test('startup probe reports the backend checkout and revision', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify(meta()), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })) as typeof fetch;
  try {
    const probe = await probeApi('127.0.0.1', 8799);
    assert.equal(probe.state, 'compatible');
    assert.match(
      probe.message,
      /\/home\/dev\/current\/argus-skill @ abc123 · release 0\.1\.0\+[a-f0-9]+ \(pid 123\)/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
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
    return new Response(JSON.stringify({ schema_version: 4, daemon: {} }), {
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
