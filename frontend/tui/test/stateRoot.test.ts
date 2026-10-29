import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import test from 'node:test';
import {
  API_SERVICE, API_PROTOCOL, REQUIRED_API_CAPABILITIES, SNAPSHOT_SCHEMA_VERSION,
  type ApiMeta,
} from '../../core/src/protocol.js';
import { RELEASE_ID } from '../../core/src/protocol.js';
import { apiStateRootError, ensureApi, probeApi } from '../src/ensureApi.js';
import { selectApiPort } from '../src/portSelection.js';

function meta(root: string | null = '/state/chosen'): ApiMeta {
  return {
    service: API_SERVICE,
    protocol: { name: API_PROTOCOL.name, major: API_PROTOCOL.major, minor: API_PROTOCOL.minServerMinor },
    snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
    capabilities: [...REQUIRED_API_CAPABILITIES],
    runtime: {
      state_root: root, package_version: 'fixture', source_root: '/code',
      pid: 123, executable: '/node',
      started_at: '', release_id: RELEASE_ID,
    },
  };
}

test('data directory identity handles POSIX and Windows canonical paths', () => {
  assert.equal(apiStateRootError(meta(), '/state/chosen/'), '');
  assert.equal(apiStateRootError(meta('C:\\Users\\Test\\state'), 'c:/users/test/state/'), '');
  assert.match(apiStateRootError(meta('/state/other'), '/state/chosen'), /different data directory/);
  assert.match(apiStateRootError(meta(null), '/state/chosen'), /cannot verify/);
  assert.match(apiStateRootError(undefined, '/state/chosen'), /cannot verify/);
});

test('real HTTP probe rejects another state directory even when the release matches', async (t) => {
  const names = ['ARGUS_TUI_LOCAL_STATE_ROOT', 'ARGUS_TUI_LOCAL_RELEASE_ID'];
  const previous = names.map((name) => process.env[name]);
  t.after(() => names.forEach((name, index) => {
    if (previous[index] === undefined) delete process.env[name];
    else process.env[name] = previous[index];
  }));
  process.env.ARGUS_TUI_LOCAL_STATE_ROOT = '/state/chosen';
  process.env.ARGUS_TUI_LOCAL_RELEASE_ID = RELEASE_ID;
  let body = meta('/state/other');
  let status = 200;
  const server = createServer((_request, response) => {
    response.statusCode = status;
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(body));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const port = (server.address() as AddressInfo).port;
  assert.equal((await probeApi('127.0.0.1', port)).stateRootMismatch, true);
  body = meta();
  assert.equal((await probeApi('127.0.0.1', port)).state, 'compatible');
  body = meta(null);
  assert.equal((await probeApi('127.0.0.1', port)).stateRootMismatch, true);
  status = 401;
  assert.equal((await probeApi('127.0.0.1', port)).stateRootMismatch, true);
});

test('another state directory is never claimed, signalled or replaced', async () => {
  const forbidden = () => { throw new Error('must not touch a foreign backend'); };
  const result = await ensureApi({
    host: '127.0.0.1', port: 8799, ownerFile: '/state/chosen/owner.json',
    dependencies: {
      probeApi: async () => ({
        state: 'incompatible', stateRootMismatch: true,
        message: 'different data directory', meta: meta('/state/other'),
      }),
      readOwnedApi: forbidden, claimApiOwnership: forbidden,
      signal: forbidden, spawnApi: forbidden,
    },
  });
  assert.equal(result.reachable, false);
  assert.equal(result.spawned, false);
  assert.match(result.message, /refusing API/);
});

test('automatic port selection skips foreign data directories without replacement', async () => {
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({
        state: 'incompatible', stateRootMismatch: true,
        message: 'different data directory', meta: meta('/state/other'),
      }),
      replaceable: async () => { throw new Error('must not replace'); },
      available: async (_host, candidate) => candidate === 8800,
    },
  );
  assert.equal(port, 8800);
});

test('another application is never claimed, signalled or replaced', async () => {
  const probe = async () => ({
    state: 'incompatible' as const, applicationMismatch: true,
    message: 'different application workflow', meta: meta(),
  });
  const forbidden = () => { throw new Error('must not touch another application'); };
  const result = await ensureApi({
    host: '127.0.0.1', port: 8799, ownerFile: '/state/chosen/owner.json',
    dependencies: {
      probeApi: probe, readOwnedApi: forbidden, claimApiOwnership: forbidden,
      signal: forbidden, spawnApi: forbidden,
    },
  });
  assert.equal(result.reachable, false);
  assert.equal(result.spawned, false);
  assert.equal(await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    { probe, replaceable: forbidden, available: async (_host, port) => port === 8800 },
  ), 8800);
});

test('HTTP probe checks application ownership in both directions', async (t) => {
  const names = ['ARGUS_APPLICATION_ROUTES', 'ARGUS_TUI_LOCAL_STATE_ROOT',
    'ARGUS_TUI_LOCAL_RELEASE_ID'];
  const previous = names.map(name => process.env[name]);
  t.after(() => names.forEach((name, index) => {
    if (previous[index] === undefined) delete process.env[name];
    else process.env[name] = previous[index];
  }));
  delete process.env.ARGUS_TUI_LOCAL_STATE_ROOT;
  process.env.ARGUS_TUI_LOCAL_RELEASE_ID = RELEASE_ID;
  process.env.ARGUS_APPLICATION_ROUTES = 'example:register';
  const body = meta();
  const server = createServer((_request, response) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(body));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const port = (server.address() as AddressInfo).port;
  assert.equal((await probeApi('127.0.0.1', port)).applicationMismatch, true);
  body.runtime.application_routes = 'example:register';
  assert.equal((await probeApi('127.0.0.1', port)).state, 'compatible');
  delete process.env.ARGUS_APPLICATION_ROUTES;
  assert.equal((await probeApi('127.0.0.1', port)).applicationMismatch, true);
  delete body.runtime.application_routes;
  assert.equal((await probeApi('127.0.0.1', port)).state, 'compatible');
});
