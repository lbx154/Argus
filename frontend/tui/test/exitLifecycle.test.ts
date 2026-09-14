import assert from 'node:assert/strict';
import test, { mock } from 'node:test';

import { InteractiveExitLifecycle } from '../src/exitLifecycle.js';
import type { EnsureResult } from '../src/ensureApi.js';

const spawned: EnsureResult = {
  reachable: true,
  spawned: true,
  message: 'api started',
  spawnedApi: {
    ownerFile: 'C:\\state\\webapi.owner.json',
    ownership: {
      schema: 1,
      pid: 8888,
      rootPid: 7777,
      host: '127.0.0.1',
      port: 8799,
      backendBin: 'C:\\repo\\.venv\\Scripts\\argus.exe',
      startedAt: '2026-08-13T00:00:00Z',
    },
  },
};

function lifecycle(
  policy: 'detach' | 'stop-api' | 'stop-all',
  calls: string[],
  options: { stopFails?: boolean } = {},
) {
  return new InteractiveExitLifecycle({
    host: '127.0.0.1',
    port: 8799,
    policy,
    dependencies: {
      stopDaemon: async (sid) => {
        calls.push(`daemon:${sid}`);
        if (options.stopFails) throw new Error('executor busy');
      },
      cleanupApi: async () => {
        calls.push('api');
        return { stopped: true, message: 'stopped' };
      },
    },
  });
}

test('detach preserves an API after Boot accepted it', async () => {
  const calls: string[] = [];
  const owner = lifecycle('detach', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  const summary = await owner.cleanup();
  assert.deepEqual(calls, []);
  assert.equal(summary.apiStopped, false);
});

test('detach reclaims an API whose ensure result arrived after Boot exited', async () => {
  const calls: string[] = [];
  const owner = lifecycle('detach', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  const summary = await owner.cleanup();
  assert.deepEqual(calls, ['api']);
  assert.equal(summary.apiStopped, true);
});

test('a failed startup already cleaned by ensureApi does not produce a false orphan warning', async () => {
  const calls: string[] = [];
  const owner = lifecycle('detach', calls);
  owner.trackEnsure(Promise.resolve({
    reachable: false,
    spawned: true,
    message: 'spawn timed out and was signalled',
  }));
  const summary = await owner.cleanup();
  assert.deepEqual(calls, []);
  assert.deepEqual(summary.warnings, []);
});

test('stop-api affects only the invocation-owned API and leaves executor alone', async () => {
  const calls: string[] = [];
  const owner = lifecycle('stop-api', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  owner.setCurrentProject('s-live');
  const summary = await owner.cleanup();
  assert.deepEqual(calls, ['api']);
  assert.equal(summary.daemonStopped, false);
  assert.equal(summary.apiStopped, true);
});

test('stop-all follows the latest App project and stops executor before API', async () => {
  const calls: string[] = [];
  const owner = lifecycle('stop-all', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  owner.setCurrentProject('s-picker');
  owner.setCurrentProject('s-app-switch');
  const summary = await owner.cleanup();
  assert.deepEqual(calls, ['daemon:s-app-switch', 'api']);
  assert.equal(summary.daemonStopped, true);
  assert.equal(summary.apiStopped, true);
});

test('stop-all still cleans the owned API when graceful executor stop fails', async () => {
  const calls: string[] = [];
  const owner = lifecycle('stop-all', calls, { stopFails: true });
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  owner.setCurrentProject('s-busy');
  const summary = await owner.cleanup();
  assert.deepEqual(calls, ['daemon:s-busy', 'api']);
  assert.equal(summary.daemonStopped, false);
  assert.equal(summary.apiStopped, true);
  assert.match(summary.warnings.join('\n'), /executor busy/);
});

test('stop-all waits for an in-flight creation and stops its late daemon before API', async () => {
  const calls: string[] = [];
  const owner = lifecycle('stop-all', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  owner.setCurrentProject('s-previous');

  let resolveCreation!: (created: { sid: string }) => void;
  const pendingCreation = new Promise<{ sid: string }>((resolve) => {
    resolveCreation = resolve;
  });
  const trackedCreation = owner.trackDaemonCreation(pendingCreation);
  const cleanup = owner.cleanup();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);

  resolveCreation({ sid: 's-created-after-exit' });
  await trackedCreation;
  const summary = await cleanup;
  assert.deepEqual(calls, ['daemon:s-created-after-exit', 'api']);
  assert.equal(summary.daemonStopped, true);
  assert.equal(summary.apiStopped, true);
});

test('stop-api waits for creation completion but preserves its daemon', async () => {
  const calls: string[] = [];
  const owner = lifecycle('stop-api', calls);
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);

  let resolveCreation!: (created: { sid: string }) => void;
  const pendingCreation = new Promise<{ sid: string }>((resolve) => {
    resolveCreation = resolve;
  });
  owner.trackDaemonCreation(pendingCreation);
  const cleanup = owner.cleanup();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);

  resolveCreation({ sid: 's-preserved' });
  const summary = await cleanup;
  assert.deepEqual(calls, ['api']);
  assert.equal(summary.daemonStopped, false);
  assert.equal(summary.apiStopped, true);
});

test('cleanup is idempotent', async () => {
  const cleanupApi = mock.fn(async () => ({ stopped: true, message: 'stopped' }));
  const owner = new InteractiveExitLifecycle({
    host: '127.0.0.1',
    port: 8799,
    policy: 'stop-api',
    dependencies: { cleanupApi },
  });
  owner.trackEnsure(Promise.resolve(spawned));
  owner.acceptEnsureResult(spawned);
  await Promise.all([owner.cleanup(), owner.cleanup()]);
  assert.equal(cleanupApi.mock.callCount(), 1);
});
