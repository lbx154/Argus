import assert from 'node:assert/strict';
import test from 'node:test';

import {
  API_PROTOCOL,
  REQUIRED_API_CAPABILITIES,
  SNAPSHOT_SCHEMA_VERSION,
} from '../../core/src/protocol.js';
import { ApiClient } from '../src/api.js';
import { describeFetchFailure, fetchWithTimeout } from '../src/network.js';

function compatibleMeta(): Record<string, unknown> {
  return {
    service: 'argus-skill-webapi',
    protocol: { name: 'argus.webapi', major: 1, minor: API_PROTOCOL.minServerMinor },
    snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
    capabilities: [...REQUIRED_API_CAPABILITIES],
    runtime: {
      package_version: '0.1.1',
      source_root: 'G:\\code\\argus',
      configured_source_root: 'G:\\code\\argus',
      source_root_matches_config: true,
      revision: 'abc123',
      pid: 123,
      python_version: '3.13.0',
      executable: 'G:\\code\\argus\\.venv\\Scripts\\python.exe',
      started_at: '2026-08-13T00:00:00Z',
    },
  };
}

function hangsUntilAborted(init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const signal = init?.signal;
    if (signal?.aborted) reject(signal.reason);
    else signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
}

function responseWithStalledJsonBody(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"partial":'));
      // Deliberately never close: this reproduces headers arriving while the
      // response body stalls, which a fetch-only timeout does not catch.
    },
  });
  return new Response(stream, { headers: { 'Content-Type': 'application/json' } });
}

test('hard timeout releases a stalled fetch with an actionable local-service error', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input, init) => hangsUntilAborted(init)) as typeof fetch;
  try {
    await assert.rejects(
      () => fetchWithTimeout('http://127.0.0.1:8799/api/meta', {}, 15),
      /GET http:\/\/127\.0\.0\.1:8799\/api\/meta timed out after 15ms; the local Argus service did not respond/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ApiClient clears a timed-out metadata handshake so retry can recover', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async (_input, init) => {
    calls += 1;
    if (calls === 1) return responseWithStalledJsonBody();
    return Response.json(compatibleMeta());
  }) as typeof fetch;
  try {
    const api = new ApiClient({
      host: '127.0.0.1',
      port: 8799,
      project: 's-test',
      metaTimeoutMs: 15,
    });
    await assert.rejects(() => api.meta(), /timed out after 15ms/);
    assert.equal((await api.meta()).runtime.pid, 123);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('snapshot polling has a hard read timeout instead of staying in flight forever', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async (_input, init) => {
    calls += 1;
    if (calls === 1) return Response.json(compatibleMeta());
    return responseWithStalledJsonBody();
  }) as typeof fetch;
  try {
    const api = new ApiClient({
      host: '127.0.0.1',
      port: 8799,
      project: 's-test',
      readTimeoutMs: 15,
    });
    await assert.rejects(
      () => api.snapshot(),
      /snapshot.*timed out after 15ms; the local Argus service did not respond/,
    );
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Node fetch cause reports refusal address and port instead of only fetch failed', () => {
  const cause = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8799'), {
    code: 'ECONNREFUSED',
    address: '127.0.0.1',
    port: 8799,
  });
  const error = new TypeError('fetch failed', { cause });
  const message = describeFetchFailure(error, 'http://127.0.0.1:8799/api/projects');
  assert.match(message, /connection refused by 127\.0\.0\.1:8799 \(ECONNREFUSED\)/);
  assert.match(message, /^GET http:\/\/127\.0\.0\.1:8799\/api\/projects failed:/);
});
