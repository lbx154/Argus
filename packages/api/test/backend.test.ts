import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { PythonQueryBackend, QueryError } from '../src/backend.js';

const fixture = fileURLToPath(new URL('./fixtures/worker.mjs', import.meta.url));
const backend = (mode = 'valid', options: { timeoutMs?: number; maxConcurrent?: number } = {}) => new PythonQueryBackend({
  executable: process.execPath, prefixArgs: [fixture, mode], sourceRoot: process.cwd(), globalRoot: process.cwd(), ...options,
});

test('worker queries preserve receipts and typed results', async t => {
  const worker = backend(); t.after(() => worker.close());
  assert.equal((await worker.query('meta', {})).runtime.source_root, process.cwd());
  assert.equal((await worker.query('projects', { limit: 1, include_empty: true })).projects[0]?.id, 's-fixture');
  assert.equal((await worker.query('costs', { limit: 1 })).projects[0]?.spend_usd, 0);
  assert.equal((await worker.query('snapshot', { sid: 's-fixture', events_limit: 0, compact: true }))?.session.id, 's-fixture');
  assert.equal(await worker.query('snapshot', { sid: 'missing', events_limit: 0, compact: true }), null);
  await assert.rejects(worker.query('snapshot', { sid: 's-other', events_limit: 0, compact: false }), { code: 'invalid_response' });
});

for (const [mode, code] of [
  ['wrong-id', 'invalid_response'], ['wrong-version', 'invalid_response'], ['wrong-root', 'invalid_response'],
  ['malformed', 'invalid_response'], ['extra-output', 'invalid_response'], ['nonzero', 'unavailable'],
  ['error', 'invalid_query'], ['overflow', 'response_too_large'],
] as const) {
  test(`worker rejects ${mode}`, { timeout: 10_000 }, async t => {
    const worker = backend(mode); t.after(() => worker.close());
    await assert.rejects(worker.query('meta', {}), error => error instanceof QueryError && error.code === code);
  });
}

test('worker deadlines terminate stalled reads', { timeout: 10_000 }, async t => {
  const worker = backend('hang', { timeoutMs: 400 }); t.after(() => worker.close());
  await assert.rejects(worker.query('projects', { limit: 1, include_empty: false }), { code: 'timeout' });
});

test('capacity, cancellation and close cannot leave a queued query or accept new work', { timeout: 10_000 }, async () => {
  const worker = backend('hang', { maxConcurrent: 1 });
  const running = worker.query('projects', { limit: 1, include_empty: false });
  await assert.rejects(worker.query('meta', {}), { code: 'busy' });
  await worker.close();
  await assert.rejects(running, { code: 'aborted' });
  await assert.rejects(worker.query('meta', {}), { code: 'unavailable' });
});

test('pre-aborted queries do not spawn a process', async t => {
  const worker = new PythonQueryBackend({ executable: 'missing-python', sourceRoot: process.cwd(), globalRoot: process.cwd() });
  t.after(() => worker.close());
  await assert.rejects(worker.query('meta', {}, AbortSignal.abort()), { code: 'aborted' });
});
