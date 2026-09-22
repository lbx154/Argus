import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { request } from 'node:http';
import { readFileSync } from 'node:fs';
import { inspectApiMeta, type ReadMethod, type ReadQueryParams, type ReadQueryResults } from '@argus/contracts';
import { startReadApi } from '../src/server.js';
import { ReadApiClient } from '../src/client.js';
import { PythonQueryBackend, QueryError, type QueryBackend } from '../src/backend.js';

const token = 'read-api-fixture-token';
const fixtures = JSON.parse(readFileSync(new URL('../../contracts/fixtures/read-api.json', import.meta.url), 'utf8')) as ReadQueryResults;
class FixtureBackend implements QueryBackend {
  calls: ReadMethod[] = [];
  stopped = false;
  failure: QueryError | undefined;
  async query<M extends ReadMethod>(method: M, _params: ReadQueryParams[M]): Promise<ReadQueryResults[M]> {
    this.calls.push(method);
    if (this.failure) throw this.failure;
    return fixtures[method];
  }
  async close(): Promise<void> { this.stopped = true; }
}

test('HTTP and process boundary round-trip with the typed client', { timeout: 10_000 }, async t => {
  const worker = new PythonQueryBackend({
    executable: process.execPath, prefixArgs: [fileURLToPath(new URL('./fixtures/worker.mjs', import.meta.url)), 'valid'],
    sourceRoot: process.cwd(), globalRoot: process.cwd(),
  });
  const service = await startReadApi({ backend: worker, token }); t.after(() => service.close());
  const client = new ReadApiClient(service.url, token);
  const meta = await client.meta();
  assert.equal(meta.read_only, true);
  assert.equal(meta.runtime.implementation, 'node');
  assert.equal(inspectApiMeta(meta).compatible, false);
  assert.equal((await client.projects({ includeEmpty: true })).projects[0]?.id, 's-fixture');
  assert.equal((await client.costs()).projects[0]?.spend_usd, 0);
  assert.equal((await client.snapshot('s-fixture')).session.id, 's-fixture');
  await assert.rejects(client.snapshot('missing'), { status: 404, code: 'not_found' });
  await assert.rejects(new ReadApiClient(service.url, 'wrong-token').projects(), { status: 401 });
});

test('authentication, method and parameter failures never reach the Python backend', async t => {
  const backend = new FixtureBackend();
  const service = await startReadApi({ backend, token }); t.after(() => service.close());
  for (const [path, method, authorization, status] of [
    ['/api/projects', 'GET', '', 401], ['/api/meta', 'GET', 'Bearer wrong', 401],
    ['/api/projects', 'POST', `Bearer ${token}`, 405], ['/api/projects/s-fixture/snapshot', 'DELETE', `Bearer ${token}`, 405],
    ['/api/projects?limit=0', 'GET', `Bearer ${token}`, 422], ['/api/projects?limit=1&limit=2', 'GET', `Bearer ${token}`, 422],
    ['/api/projects?global_root=/tmp', 'GET', `Bearer ${token}`, 422], ['/api/projects?include_empty=maybe', 'GET', `Bearer ${token}`, 422],
    ['/api/projects/s-fixture/snapshot?prewarm=true', 'GET', `Bearer ${token}`, 422],
    ['/api/projects/%2fetc%2fpasswd/snapshot', 'GET', `Bearer ${token}`, 422],
    ['/api/projects/s-fixture/snapshot?events_limit=501', 'GET', `Bearer ${token}`, 422],
    ['/api/tasks', 'GET', `Bearer ${token}`, 404],
  ] as const) {
    const response = await fetch(service.url + path, { method, headers: { authorization } });
    assert.equal(response.status, status, `${method} ${path}`);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    await response.body?.cancel();
  }
  assert.deepEqual(backend.calls, ['meta']);
});

test('foreign Host and Origin headers are rejected', async t => {
  const backend = new FixtureBackend();
  const service = await startReadApi({ backend, token }); t.after(() => service.close());
  const badHost = await new Promise<number>(resolve => {
    request(service.url + '/api/projects', { headers: { host: 'other.example', authorization: `Bearer ${token}` } }, response => {
      response.resume(); resolve(response.statusCode!);
    }).end();
  });
  assert.equal(badHost, 421);
  const origin = await fetch(service.url + '/api/projects', { headers: { authorization: `Bearer ${token}`, origin: 'https://other.example' } });
  assert.equal(origin.status, 403); await origin.body?.cancel();
  assert.deepEqual(backend.calls, ['meta']);
  const localHost = await new Promise<number>(resolve => {
    request(service.url + '/api/projects', { headers: { host: `LOCALHOST:${new URL(service.url).port}`, authorization: `Bearer ${token}` } }, response => {
      response.resume(); resolve(response.statusCode!);
    }).end();
  });
  assert.equal(localHost, 200);
});

test('query failures have stable HTTP status codes and shutdown closes the backend', async () => {
  const backend = new FixtureBackend();
  const service = await startReadApi({ backend, token });
  try {
    const client = new ReadApiClient(service.url, token);
    for (const [code, status] of [['busy', 503], ['timeout', 504], ['invalid_response', 502]] as const) {
      backend.failure = new QueryError(code, 'read failed');
      await assert.rejects(client.projects(), { status, code });
    }
  } finally { await service.close(); }
  assert.equal(backend.stopped, true);
});

test('startup incompatibility closes the backend without opening a listener', async () => {
  const backend = new FixtureBackend(); backend.failure = new QueryError('invalid_response', 'wrong Python');
  await assert.rejects(startReadApi({ backend, token }), /wrong Python/);
  assert.equal(backend.stopped, true);
});

test('client disconnects cancel the underlying query', { timeout: 10_000 }, async t => {
  let started: () => void = () => {};
  let cancelled: () => void = () => {};
  const pending = new Promise<void>(resolve => { started = resolve; });
  const aborted = new Promise<void>(resolve => { cancelled = resolve; });
  const backend: QueryBackend = {
    async query<M extends ReadMethod>(method: M, _params: ReadQueryParams[M], signal?: AbortSignal): Promise<ReadQueryResults[M]> {
      if (method === 'meta') return fixtures[method];
      started();
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => { cancelled(); reject(new QueryError('aborted', 'cancelled')); }, { once: true });
      });
    },
    async close() {},
  };
  const service = await startReadApi({ backend, token }); t.after(() => service.close());
  const controller = new AbortController();
  const response = new ReadApiClient(service.url, token).projects({ signal: controller.signal });
  await pending;
  controller.abort();
  await assert.rejects(response);
  await aborted;
});
