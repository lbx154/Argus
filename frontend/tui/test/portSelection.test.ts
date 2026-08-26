import assert from 'node:assert/strict';
import test from 'node:test';

import { selectApiPort } from '../src/portSelection.js';
import type { ApiProbeResult } from '../src/ensureApi.js';

const unreachable: ApiProbeResult = {
  state: 'unreachable',
  message: 'connect refused',
};

test('an explicit port is returned without probing or binding', async () => {
  let called = false;
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 9000, explicit: true },
    {
      probe: async () => {
        called = true;
        return unreachable;
      },
      available: async () => {
        called = true;
        return false;
      },
    },
  );
  assert.equal(port, 9000);
  assert.equal(called, false);
});

test('a compatible preferred backend is reused', async () => {
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({ state: 'compatible', message: 'ready' }),
      available: async () => {
        throw new Error('availability should not be checked');
      },
    },
  );
  assert.equal(port, 8799);
});

test('an outdated Argus backend stays on the preferred port for safe replacement', async () => {
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({
        state: 'incompatible',
        message: 'release mismatch',
        meta: {} as ApiProbeResult['meta'],
      }),
      available: async () => {
        throw new Error('availability should not be checked');
      },
      replaceable: async () => true,
    },
  );
  assert.equal(port, 8799);
});

test('an unowned outdated Argus backend advances to a free port', async () => {
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({
        state: 'incompatible',
        message: 'release mismatch',
        meta: {} as ApiProbeResult['meta'],
      }),
      replaceable: async () => false,
      available: async (_host, candidate) => candidate === 8800,
    },
  );
  assert.equal(port, 8800);
});

test('a remote host keeps its requested port without attempting a local bind', async () => {
  let bound = false;
  const port = await selectApiPort(
    { host: 'api.example.com', preferredPort: 8799, explicit: false },
    {
      probe: async () => unreachable,
      available: async () => {
        bound = true;
        return true;
      },
    },
  );
  assert.equal(port, 8799);
  assert.equal(bound, false);
});

test('a wildcard bind advances past an outdated occupied backend', async () => {
  const port = await selectApiPort(
    { host: '0.0.0.0', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({
        state: 'incompatible',
        message: 'release mismatch',
        meta: {} as ApiProbeResult['meta'],
      }),
      available: async (_host, candidate) => candidate === 8800,
    },
  );
  assert.equal(port, 8800);
});

test('an occupied incompatible preferred port advances to the first available port', async () => {
  const checked: number[] = [];
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async () => ({ state: 'incompatible', message: 'wrong service' }),
      available: async (_host, candidate) => {
        checked.push(candidate);
        return candidate === 8801;
      },
    },
  );
  assert.equal(port, 8801);
  assert.deepEqual(checked, [8799, 8800, 8801]);
});

test('port selection reuses a compatible Argus backend on a later port', async () => {
  const probes: number[] = [];
  const port = await selectApiPort(
    { host: '127.0.0.1', preferredPort: 8799, explicit: false },
    {
      probe: async (_host, candidate) => {
        probes.push(candidate);
        return candidate === 8800
          ? { state: 'compatible', message: 'ready' }
          : { state: 'incompatible', message: 'wrong service' };
      },
      available: async () => false,
    },
  );
  assert.equal(port, 8800);
  assert.deepEqual(probes, [8799, 8800]);
});

test('port selection fails with an actionable bounded-search error', async () => {
  await assert.rejects(
    selectApiPort(
      { host: '127.0.0.1', preferredPort: 8799, explicit: false, maxAttempts: 2 },
      {
        probe: async () => unreachable,
        available: async () => false,
      },
    ),
    /no available API port found from 8799 after 2 attempts/,
  );
});
